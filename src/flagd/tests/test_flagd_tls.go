package tests

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	pb "go.opentelemetry.io/otel-demo/src/flagd/evaluation/v1"
)

const (
	flagdBinaryPath = "../flagd"
	testCertPath    = "testdata/server.crt"
	testKeyPath     = "testdata/server.key"
	testCACertPath  = "testdata/ca.crt"
	testClientCertPath = "testdata/client.crt"
	testClientKeyPath = "testdata/client.key"
)

// AC-1: When TLS_CERT and TLS_KEY are not set, flagd serves plaintext endpoints
func Test_AC1_PlaintextDefaultBehavior(t *testing.T) {
	// Unset all TLS env vars
	os.Unsetenv("FLAGD_TLS_CERT_PATH")
	os.Unsetenv("FLAGD_TLS_KEY_PATH")
	os.Unsetenv("FLAGD_TLS_CA_CERT_PATH")

	// Start flagd process
	cmd := startFlagd(t)
	defer cmd.Process.Kill()

	// Wait for service to start
	waitForPort(t, "8013") // HTTP port
	waitForPort(t, "8014") // gRPC port

	// Test HTTP connection (plaintext works)
	resp, err := http.Get("http://localhost:8013/healthz")
	require.NoError(t, err)
	assert.Equal(t, http.StatusOK, resp.StatusCode)
	resp.Body.Close()

	// Test gRPC connection (plaintext works)
	conn, err := grpc.Dial("localhost:8014", grpc.WithInsecure())
	require.NoError(t, err)
	defer conn.Close()

	client := pb.NewEvaluationServiceClient(conn)
	_, err = client.Health(context.Background(), &pb.HealthRequest{})
	assert.NoError(t, err)

	// Verify TLS connections fail
	tlsConfig := &tls.Config{InsecureSkipVerify: true}
	_, err = tls.Dial("tcp", "localhost:8013", tlsConfig)
	assert.Error(t, err)
	_, err = tls.Dial("tcp", "localhost:8014", tlsConfig)
	assert.Error(t, err)
}

// AC-2: Valid TLS cert/key config enables TLS 1.2+ endpoints
func Test_AC2_TLSEndpointsEnabled(t *testing.T) {
	// Set valid TLS config
	os.Setenv("FLAGD_TLS_CERT_PATH", testCertPath)
	os.Setenv("FLAGD_TLS_KEY_PATH", testKeyPath)
	os.Unsetenv("FLAGD_TLS_CA_CERT_PATH")

	// Start flagd process
	cmd := startFlagd(t)
	defer cmd.Process.Kill()

	// Wait for service to start
	waitForPort(t, "8013")
	waitForPort(t, "8014")

	// Test plaintext HTTP fails
	_, err := http.Get("http://localhost:8013/healthz")
	assert.Error(t, err)

	// Test plaintext gRPC fails
	_, err = grpc.Dial("localhost:8014", grpc.WithInsecure(), grpc.WithBlock(), grpc.WithTimeout(2*time.Second))
	assert.Error(t, err)

	// Test TLS HTTP works
	tlsConfig := &tls.Config{
		InsecureSkipVerify: true,
		MinVersion:         tls.VersionTLS12,
	}
	client := &http.Client{
		Transport: &http.Transport{TLSClientConfig: tlsConfig},
	}
	resp, err := client.Get("https://localhost:8013/healthz")
	require.NoError(t, err)
	assert.Equal(t, http.StatusOK, resp.StatusCode)
	resp.Body.Close()
	assert.GreaterOrEqual(t, resp.TLS.Version, uint16(tls.VersionTLS12))

	// Test TLS gRPC works
	creds := credentials.NewTLS(tlsConfig)
	conn, err := grpc.Dial("localhost:8014", grpc.WithTransportCredentials(creds))
	require.NoError(t, err)
	defer conn.Close()

	evalClient := pb.NewEvaluationServiceClient(conn)
	_, err = evalClient.Health(context.Background(), &pb.HealthRequest{})
	assert.NoError(t, err)
}

// AC-3: CA cert configured enables mTLS client validation
func Test_AC3_mTLSClientValidation(t *testing.T) {
	// Set TLS config including CA cert
	os.Setenv("FLAGD_TLS_CERT_PATH", testCertPath)
	os.Setenv("FLAGD_TLS_KEY_PATH", testKeyPath)
	os.Setenv("FLAGD_TLS_CA_CERT_PATH", testCACertPath)

	// Start flagd process
	cmd := startFlagd(t)
	defer cmd.Process.Kill()

	// Wait for service to start
	waitForPort(t, "8013")
	waitForPort(t, "8014")

	// Test connection without client cert fails
	tlsConfigNoClientCert := &tls.Config{InsecureSkipVerify: true}
	clientNoCert := &http.Client{
		Transport: &http.Transport{TLSClientConfig: tlsConfigNoClientCert},
	}
	_, err := clientNoCert.Get("https://localhost:8013/healthz")
	assert.Error(t, err)

	// Test connection with valid client cert works
	caCert, err := os.ReadFile(testCACertPath)
	require.NoError(t, err)
	caCertPool := x509.NewCertPool()
	caCertPool.AppendCertsFromPEM(caCert)

	clientCert, err := tls.LoadX509KeyPair(testClientCertPath, testClientKeyPath)
	require.NoError(t, err)

	tlsConfigWithClientCert := &tls.Config{
		RootCAs:      caCertPool,
		Certificates: []tls.Certificate{clientCert},
	}
	clientWithCert := &http.Client{
		Transport: &http.Transport{TLSClientConfig: tlsConfigWithClientCert},
	}
	resp, err := clientWithCert.Get("https://localhost:8013/healthz")
	require.NoError(t, err)
	assert.Equal(t, http.StatusOK, resp.StatusCode)
	resp.Body.Close()

	// Test gRPC without client cert fails
	credsNoCert := credentials.NewTLS(tlsConfigNoClientCert)
	_, err = grpc.Dial("localhost:8014", grpc.WithTransportCredentials(credsNoCert), grpc.WithBlock(), grpc.WithTimeout(2*time.Second))
	assert.Error(t, err)

	// Test gRPC with valid client cert works
	credsWithCert := credentials.NewTLS(tlsConfigWithClientCert)
	conn, err := grpc.Dial("localhost:8014", grpc.WithTransportCredentials(credsWithCert))
	require.NoError(t, err)
	defer conn.Close()

	evalClient := pb.NewEvaluationServiceClient(conn)
	_, err = evalClient.Health(context.Background(), &pb.HealthRequest{})
	assert.NoError(t, err)
}

// AC-4: Only one of cert/key provided causes startup failure
func Test_AC4_SingleCertKeyConfigFails(t *testing.T) {
	testCases := []struct {
		name        string
		certPath    string
		keyPath     string
		expectError bool
	}{
		{
			name:        "Only cert path provided",
			certPath:    testCertPath,
			keyPath:     "",
			expectError: true,
		},
		{
			name:        "Only key path provided",
			certPath:    "",
			keyPath:     testKeyPath,
			expectError: true,
		},
		{
			name:        "Both provided (control case)",
			certPath:    testCertPath,
			keyPath:     testKeyPath,
			expectError: false,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			if tc.certPath != "" {
				os.Setenv("FLAGD_TLS_CERT_PATH", tc.certPath)
			} else {
				os.Unsetenv("FLAGD_TLS_CERT_PATH")
			}
			if tc.keyPath != "" {
				os.Setenv("FLAGD_TLS_KEY_PATH", tc.keyPath)
			} else {
				os.Unsetenv("FLAGD_TLS_KEY_PATH")
			}
			os.Unsetenv("FLAGD_TLS_CA_CERT_PATH")

			cmd := exec.Command(flagdBinaryPath, "start", "--uri", "../demo.flagd.json")
			err := cmd.Start()
			require.NoError(t, err)
			defer cmd.Process.Kill()

			// Check if process exits with error within 2 seconds
			done := make(chan error, 1)
			go func() {
				done <- cmd.Wait()
			}()

			select {
			case err := <-done:
				if tc.expectError {
					assert.Error(t, err, "Expected flagd to fail startup with partial TLS config")
				} else {
					assert.NoError(t, err, "Expected flagd to start with valid TLS config")
				}
			case <-time.After(2 * time.Second):
				if tc.expectError {
					t.Fatal("Flagd did not exit with error as expected for partial TLS config")
				}
			}
		})
	}
}

// AC-5: Unreadable cert/key/CA file causes startup failure
func Test_AC5_UnreadableFileFails(t *testing.T) {
	testCases := []struct {
		name         string
		certPath     string
		keyPath      string
		caCertPath   string
		expectFailed bool
	}{
		{
			name:         "Unreadable cert file",
			certPath:     "/nonexistent/path/server.crt",
			keyPath:      testKeyPath,
			expectFailed: true,
		},
		{
			name:         "Unreadable key file",
			certPath:     testCertPath,
			keyPath:      "/nonexistent/path/server.key",
			expectFailed: true,
		},
		{
			name:         "Unreadable CA cert file",
			certPath:     testCertPath,
			keyPath:      testKeyPath,
			caCertPath:   "/nonexistent/path/ca.crt",
			expectFailed: true,
		},
		{
			name:         "All valid (control case)",
			certPath:     testCertPath,
			keyPath:      testKeyPath,
			caCertPath:   testCACertPath,
			expectFailed: false,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			os.Setenv("FLAGD_TLS_CERT_PATH", tc.certPath)
			os.Setenv("FLAGD_TLS_KEY_PATH", tc.keyPath)
			if tc.caCertPath != "" {
				os.Setenv("FLAGD_TLS_CA_CERT_PATH", tc.caCertPath)
			} else {
				os.Unsetenv("FLAGD_TLS_CA_CERT_PATH")
			}

			cmd := exec.Command(flagdBinaryPath, "start", "--uri", "../demo.flagd.json")
			err := cmd.Start()
			require.NoError(t, err)
			defer cmd.Process.Kill()

			done := make(chan error, 1)
			go func() {
				done <- cmd.Wait()
			}()

			select {
			case err := <-done:
				if tc.expectFailed {
					assert.Error(t, err, "Expected flagd to fail startup with unreadable file")
				} else {
					assert.NoError(t, err, "Expected flagd to start with all valid files")
				}
			case <-time.After(2 * time.Second):
				if tc.expectFailed {
					t.Fatal("Flagd did not exit with error as expected for unreadable file")
				}
			}
		})
	}
}

// AC-6: Malformed PEM data causes startup failure
func Test_AC6_MalformedCertificateFails(t *testing.T) {
	// Create temporary malformed cert file
	tmpCert, err := os.CreateTemp("", "malformed-*.crt")
	require.NoError(t, err)
	defer os.Remove(tmpCert.Name())
	tmpCert.WriteString("invalid PEM data")
	tmpCert.Close()

	os.Setenv("FLAGD_TLS_CERT_PATH", tmpCert.Name())
	os.Setenv("FLAGD_TLS_KEY_PATH", testKeyPath)
	os.Unsetenv("FLAGD_TLS_CA_CERT_PATH")

	cmd := exec.Command(flagdBinaryPath, "start", "--uri", "../demo.flagd.json")
	err = cmd.Start()
	require.NoError(t, err)
	defer cmd.Process.Kill()

	done := make(chan error, 1)
	go func() {
		done <- cmd.Wait()
	}()

	select {
	case err := <-done:
		assert.Error(t, err, "Expected flagd to fail startup with malformed cert")
	case <-time.After(2 * time.Second):
		t.Fatal("Flagd did not exit with error as expected for malformed cert")
	}
}

// AC-7: All existing functionality works over TLS same as plaintext
func Test_AC7_FunctionalityIdenticalOverTLS(t *testing.T) {
	// First test plaintext behavior (baseline)
	os.Unsetenv("FLAGD_TLS_CERT_PATH")
	os.Unsetenv("FLAGD_TLS_KEY_PATH")
	os.Unsetenv("FLAGD_TLS_CA_CERT_PATH")

	cmdPlain := exec.Command(flagdBinaryPath, "start", "--uri", "../demo.flagd.json")
	err := cmdPlain.Start()
	require.NoError(t, err)
	defer cmdPlain.Process.Kill()
	waitForPort(t, "8013")

	// Get baseline flag evaluation result
	respPlain, err := http.Post("http://localhost:8013/flagd.evaluation.v1.Service/ResolveBoolean", "application/json", []byte(`{"flagKey":"demoUI-enabled","context":{}}`))
	require.NoError(t, err)
	plaintextBody := make([]byte, respPlain.ContentLength)
	respPlain.Body.Read(plaintextBody)
	respPlain.Body.Close()
	cmdPlain.Process.Kill()

	// Now test over TLS
	os.Setenv("FLAGD_TLS_CERT_PATH", testCertPath)
	os.Setenv("FLAGD_TLS_KEY_PATH", testKeyPath)
	os.Unsetenv("FLAGD_TLS_CA_CERT_PATH")

	cmdTLS := exec.Command(flagdBinaryPath, "start", "--uri", "../demo.flagd.json")
	err = cmdTLS.Start()
	require.NoError(t, err)
	defer cmdTLS.Process.Kill()
	waitForPort(t, "8013")

	tlsConfig := &tls.Config{InsecureSkipVerify: true}
	client := &http.Client{Transport: &http.Transport{TLSClientConfig: tlsConfig}}
	respTLS, err := client.Post("https://localhost:8013/flagd.evaluation.v1.Service/ResolveBoolean", "application/json", []byte(`{"flagKey":"demoUI-enabled","context":{}}`))
	require.NoError(t, err)
	tlsBody := make([]byte, respTLS.ContentLength)
	respTLS.Body.Read(tlsBody)
	respTLS.Body.Close()

	// Verify identical response
	assert.Equal(t, respPlain.StatusCode, respTLS.StatusCode)
	assert.Equal(t, plaintextBody, tlsBody)
}

// Helper functions
func startFlagd(t *testing.T) *exec.Cmd {
	cmd := exec.Command(flagdBinaryPath, "start", "--uri", "../demo.flagd.json")
	err := cmd.Start()
	require.NoError(t, err)
	return cmd
}

func waitForPort(t *testing.T, port string) {
	timeout := time.After(10 * time.Second)
	for {
		select {
		case <-timeout:
			t.Fatalf("Timeout waiting for port %s to be open", port)
		default:
			conn, err := net.Dial("tcp", fmt.Sprintf("localhost:%s", port))
			if err == nil {
				conn.Close()
				return
			}
			time.Sleep(100 * time.Millisecond)
		}
	}
}
