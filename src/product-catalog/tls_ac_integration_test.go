package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	pb "github.com/open-telemetry/opentelemetry-demo/src/product-catalog/genproto/oteldemo"
)

const testPort = 3551
const testCertPath = "./testdata/cert.pem"
const testKeyPath = "./testdata/key.pem"
const testCACertPath = "./testdata/ca.pem"
const testInvalidCertPath = "./testdata/invalid_cert.pem"
const testClientCertPath = "./testdata/client_cert.pem"
const testClientKeyPath = "./testdata/client_key.pem"
const testInvalidClientCertPath = "./testdata/invalid_client_cert.pem"

func setupTestEnv(t *testing.T, envVars map[string]string) func() {
	originalEnv := make(map[string]string)
	for k, v := range envVars {
		originalEnv[k] = os.Getenv(k)
		os.Setenv(k, v)
	}
	return func() {
		for k, v := range originalEnv {
			os.Setenv(k, v)
		}
	}
}

func startTestServer(t *testing.T) (func(), error) {
	ctx, cancel := context.WithCancel(context.Background())
	errChan := make(chan error, 1)
	go func() {
		err := runServer(ctx, testPort)
		errChan <- err
	}()

	select {
	case err := <-errChan:
		cancel()
		return nil, err
	case <-time.After(2 * time.Second):
	}

	return func() {
		cancel()
		<-errChan
	}, nil
}

func createClientConn(t *testing.T, tlsConfig *tls.Config) *grpc.ClientConn {
	var opts []grpc.DialOption
	if tlsConfig != nil {
		opts = append(opts, grpc.WithTransportCredentials(credentials.NewTLS(tlsConfig)))
	} else {
		opts = append(opts, grpc.WithTransportCredentials(insecure.NewCredentials()))
	}

	conn, err := grpc.Dial(fmt.Sprintf("localhost:%d", testPort), opts...)
	require.NoError(t, err)
	return conn
}

func TestAC1_TLSDisabledAcceptsUnencryptedConnections(t *testing.T) {
	// AC-1: TLS disabled, service starts and accepts unencrypted connections
	teardownEnv := setupTestEnv(t, map[string]string{
		"PRODUCT_CATALOG_TLS_ENABLED": "false",
	})
	defer teardownEnv()

	teardownServer, err := startTestServer(t)
	require.NoError(t, err, "Service should start successfully when TLS is disabled")
	defer teardownServer()

	// Test unencrypted connection works
	conn := createClientConn(t, nil)
	defer conn.Close()

	client := pb.NewProductCatalogServiceClient(conn)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()

	_, err = client.ListProducts(ctx, &pb.ListProductsRequest{})
	assert.NoError(t, err, "Unencrypted connection should succeed when TLS is disabled")
}

func TestAC2_TLSEnabledRejectsUnencryptedAcceptsTLS(t *testing.T) {
	// AC-2: TLS enabled with valid certs, rejects unencrypted, accepts TLS
	teardownEnv := setupTestEnv(t, map[string]string{
		"PRODUCT_CATALOG_TLS_ENABLED":    "true",
		"PRODUCT_CATALOG_TLS_CERT_PATH":  testCertPath,
		"PRODUCT_CATALOG_TLS_KEY_PATH":   testKeyPath,
		"PRODUCT_CATALOG_MTLS_ENABLED":   "false",
	})
	defer teardownEnv()

	teardownServer, err := startTestServer(t)
	require.NoError(t, err, "Service should start successfully with valid TLS configuration")
	defer teardownServer()

	// Test unencrypted connection is rejected
	t.Run("RejectUnencrypted", func(t *testing.T) {
		conn := createClientConn(t, nil)
		defer conn.Close()

		client := pb.NewProductCatalogServiceClient(conn)
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()

		_, err = client.ListProducts(ctx, &pb.ListProductsRequest{})
		assert.Error(t, err, "Unencrypted connection should be rejected when TLS is enabled")
	})

	// Test TLS connection succeeds
	t.Run("AcceptValidTLS", func(t *testing.T) {
		certPool := x509.NewCertPool()
		caCert, err := os.ReadFile(testCACertPath)
		require.NoError(t, err)
		certPool.AppendCertsFromPEM(caCert)

		tlsConfig := &tls.Config{
			RootCAs: certPool,
		}

		conn := createClientConn(t, tlsConfig)
		defer conn.Close()

		client := pb.NewProductCatalogServiceClient(conn)
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()

		_, err = client.ListProducts(ctx, &pb.ListProductsRequest{})
		assert.NoError(t, err, "Valid TLS connection should succeed when TLS is enabled")
	})
}

func TestAC3_MTLSEnabledValidatesClientCertificates(t *testing.T) {
	// AC-3: mTLS enabled, validates client certificates
	teardownEnv := setupTestEnv(t, map[string]string{
		"PRODUCT_CATALOG_TLS_ENABLED":        "true",
		"PRODUCT_CATALOG_TLS_CERT_PATH":      testCertPath,
		"PRODUCT_CATALOG_TLS_KEY_PATH":       testKeyPath,
		"PRODUCT_CATALOG_MTLS_ENABLED":       "true",
		"PRODUCT_CATALOG_MTLS_CA_CERT_PATH":  testCACertPath,
	})
	defer teardownEnv()

	teardownServer, err := startTestServer(t)
	require.NoError(t, err, "Service should start successfully with valid mTLS configuration")
	defer teardownServer()

	// Test unencrypted connection is rejected
	t.Run("RejectUnencrypted", func(t *testing.T) {
		conn := createClientConn(t, nil)
		defer conn.Close()

		client := pb.NewProductCatalogServiceClient(conn)
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()

		_, err = client.ListProducts(ctx, &pb.ListProductsRequest{})
		assert.Error(t, err, "Unencrypted connection should be rejected when mTLS is enabled")
	})

	// Test TLS connection without client cert is rejected
	t.Run("RejectWithoutClientCert", func(t *testing.T) {
		certPool := x509.NewCertPool()
		caCert, err := os.ReadFile(testCACertPath)
		require.NoError(t, err)
		certPool.AppendCertsFromPEM(caCert)

		tlsConfig := &tls.Config{
			RootCAs: certPool,
		}

		conn := createClientConn(t, tlsConfig)
		defer conn.Close()

		client := pb.NewProductCatalogServiceClient(conn)
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()

		_, err = client.ListProducts(ctx, &pb.ListProductsRequest{})
		assert.Error(t, err, "Connection without client certificate should be rejected when mTLS is enabled")
	})

	// Test TLS connection with invalid client cert is rejected
	t.Run("RejectInvalidClientCert", func(t *testing.T) {
		certPool := x509.NewCertPool()
		caCert, err := os.ReadFile(testCACertPath)
		require.NoError(t, err)
		certPool.AppendCertsFromPEM(caCert)

		clientCert, err := tls.LoadX509KeyPair(testInvalidClientCertPath, testClientKeyPath)
		require.NoError(t, err)

		tlsConfig := &tls.Config{
			RootCAs:      certPool,
			Certificates: []tls.Certificate{clientCert},
		}

		conn := createClientConn(t, tlsConfig)
		defer conn.Close()

		client := pb.NewProductCatalogServiceClient(conn)
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()

		_, err = client.ListProducts(ctx, &pb.ListProductsRequest{})
		assert.Error(t, err, "Connection with invalid client certificate should be rejected when mTLS is enabled")
	})

	// Test TLS connection with valid client cert succeeds
	t.Run("AcceptValidClientCert", func(t *testing.T) {
		certPool := x509.NewCertPool()
		caCert, err := os.ReadFile(testCACertPath)
		require.NoError(t, err)
		certPool.AppendCertsFromPEM(caCert)

		clientCert, err := tls.LoadX509KeyPair(testClientCertPath, testClientKeyPath)
		require.NoError(t, err)

		tlsConfig := &tls.Config{
			RootCAs:      certPool,
			Certificates: []tls.Certificate{clientCert},
		}

		conn := createClientConn(t, tlsConfig)
		defer conn.Close()

		client := pb.NewProductCatalogServiceClient(conn)
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()

		_, err = client.ListProducts(ctx, &pb.ListProductsRequest{})
		assert.NoError(t, err, "Connection with valid client certificate should succeed when mTLS is enabled")
	})
}

func TestAC4_InvalidConfigurationFailsStartup(t *testing.T) {
	// AC-4: Invalid configuration causes service start failure

	// Test case a: TLS enabled without cert path
	t.Run("TLSEnabledMissingCertPath", func(t *testing.T) {
		teardownEnv := setupTestEnv(t, map[string]string{
			"PRODUCT_CATALOG_TLS_ENABLED":    "true",
			"PRODUCT_CATALOG_TLS_CERT_PATH":  "",
			"PRODUCT_CATALOG_TLS_KEY_PATH":   testKeyPath,
		})
		defer teardownEnv()

		_, err := startTestServer(t)
		assert.ErrorContains(t, err, "TLS enabled but certificate or private key path not provided",
			"Should fail with appropriate error when cert path is missing")
	})

	// Test case b: TLS enabled with non-existent cert file
	t.Run("TLSEnabledNonExistentCertFile", func(t *testing.T) {
		teardownEnv := setupTestEnv(t, map[string]string{
			"PRODUCT_CATALOG_TLS_ENABLED":    "true",
			"PRODUCT_CATALOG_TLS_CERT_PATH":  "/nonexistent/cert.pem",
			"PRODUCT_CATALOG_TLS_KEY_PATH":   testKeyPath,
		})
		defer teardownEnv()

		_, err := startTestServer(t)
		assert.Error(t, err, "Should fail when cert file does not exist")
	})

	// Test case c: mTLS enabled without CA path
	t.Run("MTLSEnabledMissingCAPath", func(t *testing.T) {
		teardownEnv := setupTestEnv(t, map[string]string{
			"PRODUCT_CATALOG_TLS_ENABLED":        "true",
			"PRODUCT_CATALOG_TLS_CERT_PATH":      testCertPath,
			"PRODUCT_CATALOG_TLS_KEY_PATH":       testKeyPath,
			"PRODUCT_CATALOG_MTLS_ENABLED":       "true",
			"PRODUCT_CATALOG_MTLS_CA_CERT_PATH":  "",
		})
		defer teardownEnv()

		_, err := startTestServer(t)
		assert.ErrorContains(t, err, "mTLS enabled but CA certificate path not provided",
			"Should fail with appropriate error when CA path is missing for mTLS")
	})

	// Test case d: mTLS enabled with invalid CA file
	t.Run("MTLSEnabledInvalidCAFile", func(t *testing.T) {
		teardownEnv := setupTestEnv(t, map[string]string{
			"PRODUCT_CATALOG_TLS_ENABLED":        "true",
			"PRODUCT_CATALOG_TLS_CERT_PATH":      testCertPath,
			"PRODUCT_CATALOG_TLS_KEY_PATH":       testKeyPath,
			"PRODUCT_CATALOG_MTLS_ENABLED":       "true",
			"PRODUCT_CATALOG_MTLS_CA_CERT_PATH":  testInvalidCertPath,
		})
		defer teardownEnv()

		_, err := startTestServer(t)
		assert.Error(t, err, "Should fail when CA file is invalid")
	})
}
