package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
)

const (
	testServerAddr = "localhost:5050" // default checkout service port
)

// AC-1: When CHECKOUT_SERVICE_TLS_ENABLED is not set or set to false, the gRPC server starts without TLS,
// accepts unencrypted connections, and behaves identically to previous versions (backward compatibility).
func Test_AC1_TLSDisabled_AcceptsUnencryptedConnections(t *testing.T) {
	// Unset all TLS related env vars
	os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")
	os.Unsetenv("CHECKOUT_SERVICE_TLS_CERT_PATH")
	os.Unsetenv("CHECKOUT_SERVICE_TLS_KEY_PATH")
	os.Unsetenv("CHECKOUT_SERVICE_MTLS_ENABLED")
	os.Unsetenv("CHECKOUT_SERVICE_MTLS_CA_CERT_PATH")

	// Start server in background
	serverErrChan := make(chan error, 1)
	go func() {
		err := runServer() // assume main package exports runServer or we test startup
		serverErrChan <- err
	}()

	// Wait for server to start
	time.Sleep(2 * time.Second)

	// Test insecure connection succeeds
	conn, err := grpc.NewClient(testServerAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	require.NoError(t, err)
	defer conn.Close()

	healthClient := healthpb.NewHealthClient(conn)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	resp, err := healthClient.Check(ctx, &healthpb.HealthCheckRequest{})
	require.NoError(t, err)
	assert.Equal(t, healthpb.HealthCheckResponse_SERVING, resp.Status)

	// Shutdown server
	// Assume we have a shutdown mechanism, skip for test skeleton

	// Check server didn't crash
	select {
	case err := <-serverErrChan:
		t.Fatalf("server exited unexpectedly: %v", err)
	default:
		// Server running as expected
	}
}

// AC-2: When CHECKOUT_SERVICE_TLS_ENABLED=true and valid cert/key paths are provided,
// the gRPC server only accepts TLS 1.2+ encrypted connections, rejects unencrypted connections,
// and serves the configured certificate.
func Test_AC2_TLSEnabled_RejectsUnencryptedAcceptsTLS12Plus(t *testing.T) {
	// Set TLS env vars with valid test cert paths (we'll use test certs in testdata dir)
	os.Setenv("CHECKOUT_SERVICE_TLS_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_TLS_CERT_PATH", "testdata/server.crt")
	os.Setenv("CHECKOUT_SERVICE_TLS_KEY_PATH", "testdata/server.key")
	defer os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")
	defer os.Unsetenv("CHECKOUT_SERVICE_TLS_CERT_PATH")
	defer os.Unsetenv("CHECKOUT_SERVICE_TLS_KEY_PATH")

	// Start server
	serverErrChan := make(chan error, 1)
	go func() {
		err := runServer()
		serverErrChan <- err
	}()

	time.Sleep(2 * time.Second)

	// Test 1: Unencrypted connection fails
	insecureConn, err := grpc.NewClient(testServerAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	require.NoError(t, err)
	defer insecureConn.Close()

	healthClient := healthpb.NewHealthClient(insecureConn)
	ctx, cancel := context.WithTimeout(context.Background(), 1*time.Second)
	defer cancel()
	_, err = healthClient.Check(ctx, &healthpb.HealthCheckRequest{})
	assert.Error(t, err, "unencrypted connection should fail when TLS is enabled")

	// Test 2: TLS 1.2 connection succeeds
	tlsConfig := &tls.Config{
		InsecureSkipVerify: true, // test only, skip verification for this check
		MinVersion:         tls.VersionTLS12,
	}
	tlsConn, err := grpc.NewClient(testServerAddr, grpc.WithTransportCredentials(credentials.NewTLS(tlsConfig)))
	require.NoError(t, err)
	defer tlsConn.Close()

	tlsHealthClient := healthpb.NewHealthClient(tlsConn)
	ctx2, cancel2 := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel2()
	resp, err := tlsHealthClient.Check(ctx2, &healthpb.HealthCheckRequest{})
	require.NoError(t, err)
	assert.Equal(t, healthpb.HealthCheckResponse_SERVING, resp.Status)

	// Test 3: TLS 1.1 connection fails
	tlsConfig11 := &tls.Config{
		InsecureSkipVerify: true,
		MinVersion:         tls.VersionTLS11,
		MaxVersion:         tls.VersionTLS11,
	}
	tlsConn11, err := grpc.NewClient(testServerAddr, grpc.WithTransportCredentials(credentials.NewTLS(tlsConfig11)))
	require.NoError(t, err)
	defer tlsConn11.Close()

	tlsHealthClient11 := healthpb.NewHealthClient(tlsConn11)
	ctx3, cancel3 := context.WithTimeout(context.Background(), 1*time.Second)
	defer cancel3()
	_, err = tlsHealthClient11.Check(ctx3, &healthpb.HealthCheckRequest{})
	assert.Error(t, err, "TLS 1.1 connection should be rejected")
}

// AC-3: When CHECKOUT_SERVICE_TLS_ENABLED=true and cert/key paths are missing/invalid/unreadable,
// the service fails to start with a clear error message and non-zero exit code.
func Test_AC3_TLSEnabled_InvalidCertKeyPaths_FailsStartup(t *testing.T) {
	// Test 1: Missing cert path
	os.Setenv("CHECKOUT_SERVICE_TLS_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_TLS_KEY_PATH", "testdata/server.key")
	os.Unsetenv("CHECKOUT_SERVICE_TLS_CERT_PATH")

	err := runServer()
	assert.Error(t, err, "server should fail to start when cert path is missing")
	assert.NotZero(t, exitCodeFromError(err), "exit code should be non-zero")

	// Test 2: Missing key path
	os.Setenv("CHECKOUT_SERVICE_TLS_CERT_PATH", "testdata/server.crt")
	os.Unsetenv("CHECKOUT_SERVICE_TLS_KEY_PATH")

	err = runServer()
	assert.Error(t, err, "server should fail to start when key path is missing")
	assert.NotZero(t, exitCodeFromError(err), "exit code should be non-zero")

	// Test 3: Non-existent cert file
	os.Setenv("CHECKOUT_SERVICE_TLS_KEY_PATH", "testdata/server.key")
	os.Setenv("CHECKOUT_SERVICE_TLS_CERT_PATH", "testdata/nonexistent.crt")

	err = runServer()
	assert.Error(t, err, "server should fail to start when cert file does not exist")
	assert.NotZero(t, exitCodeFromError(err), "exit code should be non-zero")
	assert.Contains(t, err.Error(), "cert", "error message should mention certificate")

	// Test 4: Non-existent key file
	os.Setenv("CHECKOUT_SERVICE_TLS_CERT_PATH", "testdata/server.crt")
	os.Setenv("CHECKOUT_SERVICE_TLS_KEY_PATH", "testdata/nonexistent.key")

	err = runServer()
	assert.Error(t, err, "server should fail to start when key file does not exist")
	assert.NotZero(t, exitCodeFromError(err), "exit code should be non-zero")
	assert.Contains(t, err.Error(), "key", "error message should mention private key")
}

// AC-4: When CHECKOUT_SERVICE_MTLS_ENABLED=true with valid CA cert path provided,
// the gRPC server only accepts connections from clients presenting a valid certificate signed by the configured CA,
// rejects connections without client certificates or with invalid client certificates.
func Test_AC4_MTLSEnabled_RejectsInvalidClientCerts(t *testing.T) {
	os.Setenv("CHECKOUT_SERVICE_TLS_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_TLS_CERT_PATH", "testdata/server.crt")
	os.Setenv("CHECKOUT_SERVICE_TLS_KEY_PATH", "testdata/server.key")
	os.Setenv("CHECKOUT_SERVICE_MTLS_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_MTLS_CA_CERT_PATH", "testdata/ca.crt")
	defer os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")
	defer os.Unsetenv("CHECKOUT_SERVICE_TLS_CERT_PATH")
	defer os.Unsetenv("CHECKOUT_SERVICE_TLS_KEY_PATH")
	defer os.Unsetenv("CHECKOUT_SERVICE_MTLS_ENABLED")
	defer os.Unsetenv("CHECKOUT_SERVICE_MTLS_CA_CERT_PATH")

	// Start server
	serverErrChan := make(chan error, 1)
	go func() {
		err := runServer()
		serverErrChan <- err
	}()

	time.Sleep(2 * time.Second)

	// Test 1: No client certificate provided - fails
	tlsConfigNoCert := &tls.Config{
		InsecureSkipVerify: true,
	}
	connNoCert, err := grpc.NewClient(testServerAddr, grpc.WithTransportCredentials(credentials.NewTLS(tlsConfigNoCert)))
	require.NoError(t, err)
	defer connNoCert.Close()

	healthClientNoCert := healthpb.NewHealthClient(connNoCert)
	ctx, cancel := context.WithTimeout(context.Background(), 1*time.Second)
	defer cancel()
	_, err = healthClientNoCert.Check(ctx, &healthpb.HealthCheckRequest{})
	assert.Error(t, err, "connection without client cert should fail when mTLS is enabled")

	// Test 2: Valid client certificate - succeeds
	clientCert, err := tls.LoadX509KeyPair("testdata/client.crt", "testdata/client.key")
	require.NoError(t, err)

	caCert, err := os.ReadFile("testdata/ca.crt")
	require.NoError(t, err)
	caCertPool := x509.NewCertPool()
	caCertPool.AppendCertsFromPEM(caCert)

	tlsConfigValidCert := &tls.Config{
		Certificates: []tls.Certificate{clientCert},
		RootCAs:      caCertPool,
	}
	connValidCert, err := grpc.NewClient(testServerAddr, grpc.WithTransportCredentials(credentials.NewTLS(tlsConfigValidCert)))
	require.NoError(t, err)
	defer connValidCert.Close()

	healthClientValid := healthpb.NewHealthClient(connValidCert)
	ctx2, cancel2 := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel2()
	resp, err := healthClientValid.Check(ctx2, &healthpb.HealthCheckRequest{})
	require.NoError(t, err)
	assert.Equal(t, healthpb.HealthCheckResponse_SERVING, resp.Status)

	// Test 3: Invalid client certificate (wrong CA) - fails
	invalidClientCert, err := tls.LoadX509KeyPair("testdata/invalid_client.crt", "testdata/invalid_client.key")
	require.NoError(t, err)

	tlsConfigInvalidCert := &tls.Config{
		Certificates: []tls.Certificate{invalidClientCert},
		InsecureSkipVerify: true,
	}
	connInvalidCert, err := grpc.NewClient(testServerAddr, grpc.WithTransportCredentials(credentials.NewTLS(tlsConfigInvalidCert)))
	require.NoError(t, err)
	defer connInvalidCert.Close()

	healthClientInvalid := healthpb.NewHealthClient(connInvalidCert)
	ctx3, cancel3 := context.WithTimeout(context.Background(), 1*time.Second)
	defer cancel3()
	_, err = healthClientInvalid.Check(ctx3, &healthpb.HealthCheckRequest{})
	assert.Error(t, err, "connection with invalid client cert should fail when mTLS is enabled")
}

// AC-5: When CHECKOUT_SERVICE_MTLS_ENABLED=true and TLS is disabled,
// the service fails to start with error indicating mTLS requires TLS to be enabled.
func Test_AC5_MTLSEnabled_TLSDisabled_FailsStartup(t *testing.T) {
	os.Setenv("CHECKOUT_SERVICE_TLS_ENABLED", "false")
	os.Setenv("CHECKOUT_SERVICE_MTLS_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_MTLS_CA_CERT_PATH", "testdata/ca.crt")
	defer os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")
	defer os.Unsetenv("CHECKOUT_SERVICE_MTLS_ENABLED")
	defer os.Unsetenv("CHECKOUT_SERVICE_MTLS_CA_CERT_PATH")

	err := runServer()
	assert.Error(t, err, "server should fail to start when mTLS is enabled but TLS is disabled")
	assert.NotZero(t, exitCodeFromError(err), "exit code should be non-zero")
	assert.Contains(t, err.Error(), "TLS", "error message should mention TLS required for mTLS")
	assert.Contains(t, err.Error(), "mTLS", "error message should mention mTLS")
}

// AC-6: When CHECKOUT_SERVICE_MTLS_ENABLED=true and CA cert path is missing/invalid/unreadable,
// the service fails to start with a clear error message and non-zero exit code.
func Test_AC6_MTLSEnabled_InvalidCACertPath_FailsStartup(t *testing.T) {
	os.Setenv("CHECKOUT_SERVICE_TLS_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_TLS_CERT_PATH", "testdata/server.crt")
	os.Setenv("CHECKOUT_SERVICE_TLS_KEY_PATH", "testdata/server.key")
	os.Setenv("CHECKOUT_SERVICE_MTLS_ENABLED", "true")
	defer os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")
	defer os.Unsetenv("CHECKOUT_SERVICE_TLS_CERT_PATH")
	defer os.Unsetenv("CHECKOUT_SERVICE_TLS_KEY_PATH")
	defer os.Unsetenv("CHECKOUT_SERVICE_MTLS_ENABLED")

	// Test 1: Missing CA cert path
	os.Unsetenv("CHECKOUT_SERVICE_MTLS_CA_CERT_PATH")

	err := runServer()
	assert.Error(t, err, "server should fail to start when mTLS CA path is missing")
	assert.NotZero(t, exitCodeFromError(err), "exit code should be non-zero")
	assert.Contains(t, err.Error(), "CA", "error message should mention CA certificate")

	// Test 2: Non-existent CA cert file
	os.Setenv("CHECKOUT_SERVICE_MTLS_CA_CERT_PATH", "testdata/nonexistent_ca.crt")

	err = runServer()
	assert.Error(t, err, "server should fail to start when mTLS CA file does not exist")
	assert.NotZero(t, exitCodeFromError(err), "exit code should be non-zero")
	assert.Contains(t, err.Error(), "CA", "error message should mention CA certificate")
	assert.Contains(t, err.Error(), "exist", "error message should mention file not found")
}

// Helper functions - these will be implemented to match actual server structure when code exists
func runServer() error {
	// Dummy implementation for test skeleton - will fail until actual server code supports TLS/mTLS
	return nil
}

func exitCodeFromError(err error) int {
	// Dummy implementation
	if err != nil {
		return 1
	}
	return 0
}
