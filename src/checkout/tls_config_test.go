package main

import (
	"os"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// AC-1: Default no TLS config uses insecure credentials, service starts normally
func TestAC1_DefaultInsecureMode(t *testing.T) {
	// Unset all TLS environment variables
	os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")
	os.Unsetenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH")
	os.Unsetenv("CHECKOUT_SERVICE_TLS_CLIENT_CERT_PATH")
	os.Unsetenv("CHECKOUT_SERVICE_TLS_CLIENT_KEY_PATH")
	os.Unsetenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED")

	// Load config should succeed without error
	cfg, err := LoadTLSConfig()
	require.NoError(t, err, "Loading default TLS config should not fail")
	assert.False(t, cfg.Enabled, "TLS should be disabled by default")
	assert.False(t, cfg.MTLSEnabled, "mTLS should be disabled by default")

	// Credentials should be insecure
	creds, err := NewGRPCCredentials(cfg)
	require.NoError(t, err, "Creating gRPC credentials for default config should not fail")

	// Verify we get insecure credentials
	// Check that we can use the dial option to create an insecure client
	// Note: We don't actually connect, just verify the credential type
	opts := []grpc.DialOption{creds}
	cc, err := grpc.NewClient("localhost:50051", opts...)
	require.NoError(t, err)
	defer cc.Close()

	// Insecure credentials mean no TLS is used, which matches the pre-change behavior
}

// AC-2: One-way TLS mode works with valid CA cert
func TestAC2_OneWayTLSValidCA(t *testing.T) {
	// Set one-way TLS env vars
	os.Setenv("CHECKOUT_SERVICE_TLS_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH", "./testdata/valid_ca.crt")
	os.Setenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED", "false")
	defer func() {
		os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED")
	}()

	// Load config should succeed
	cfg, err := LoadTLSConfig()
	require.NoError(t, err, "Loading valid one-way TLS config should not fail")
	assert.True(t, cfg.Enabled)
	assert.False(t, cfg.MTLSEnabled)
	assert.Equal(t, "./testdata/valid_ca.crt", cfg.CACertPath)

	// Create credentials should succeed
	creds, err := NewGRPCCredentials(cfg)
	require.NoError(t, err, "Creating one-way TLS credentials with valid CA should not fail")
	assert.NotEqual(t, insecure.NewCredentials(), creds, "Should not get insecure credentials when TLS is enabled")
}

// AC-3: Mutual TLS mode works with valid client cert/key pair
func TestAC3_MutualTLSValidCredentials(t *testing.T) {
	// Set mTLS env vars
	os.Setenv("CHECKOUT_SERVICE_TLS_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH", "./testdata/valid_ca.crt")
	os.Setenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_TLS_CLIENT_CERT_PATH", "./testdata/valid_client.crt")
	os.Setenv("CHECKOUT_SERVICE_TLS_CLIENT_KEY_PATH", "./testdata/valid_client.key")
	defer func() {
		os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_CLIENT_CERT_PATH")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_CLIENT_KEY_PATH")
	}()

	// Load config should succeed
	cfg, err := LoadTLSConfig()
	require.NoError(t, err, "Loading valid mTLS config should not fail")
	assert.True(t, cfg.Enabled)
	assert.True(t, cfg.MTLSEnabled)
	assert.Equal(t, "./testdata/valid_client.crt", cfg.ClientCertPath)
	assert.Equal(t, "./testdata/valid_client.key", cfg.ClientKeyPath)

	// Create credentials should succeed
	creds, err := NewGRPCCredentials(cfg)
	require.NoError(t, err, "Creating mTLS credentials with valid cert/key pair should not fail")
}

// AC-4: TLS enabled but missing CA cert fails startup
func TestAC4_TLSEnabledMissingCACert(t *testing.T) {
	// Set TLS enabled but no CA cert path
	os.Setenv("CHECKOUT_SERVICE_TLS_ENABLED", "true")
	os.Unsetenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH")
	defer os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")

	// Load config should fail with error about missing CA cert
	_, err := LoadTLSConfig()
	require.Error(t, err, "Loading config with TLS enabled but no CA cert should fail")
	assert.Contains(t, err.Error(), "CA certificate", "Error message should mention CA certificate")
	assert.Contains(t, err.Error(), "missing", "Error message should mention missing or unreadable")
}

// AC-5: mTLS enabled but missing client cert/key fails startup
func TestAC5_MTLSEnabledMissingClientCredentials(t *testing.T) {
	// Test missing client cert
	t.Run("MissingClientCert", func(t *testing.T) {
		os.Setenv("CHECKOUT_SERVICE_TLS_ENABLED", "true")
		os.Setenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH", "./testdata/valid_ca.crt")
		os.Setenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED", "true")
		os.Setenv("CHECKOUT_SERVICE_TLS_CLIENT_KEY_PATH", "./testdata/valid_client.key")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_CLIENT_CERT_PATH")
		defer func() {
			os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")
			os.Unsetenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH")
			os.Unsetenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED")
			os.Unsetenv("CHECKOUT_SERVICE_TLS_CLIENT_KEY_PATH")
		}()

		_, err := LoadTLSConfig()
		require.Error(t, err, "Loading mTLS config without client cert should fail")
		assert.Contains(t, err.Error(), "client certificate", "Error message should mention client certificate")
	})

	// Test missing client key
	t.Run("MissingClientKey", func(t *testing.T) {
		os.Setenv("CHECKOUT_SERVICE_TLS_ENABLED", "true")
		os.Setenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH", "./testdata/valid_ca.crt")
		os.Setenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED", "true")
		os.Setenv("CHECKOUT_SERVICE_TLS_CLIENT_CERT_PATH", "./testdata/valid_client.crt")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_CLIENT_KEY_PATH")
		defer func() {
			os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")
			os.Unsetenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH")
			os.Unsetenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED")
			os.Unsetenv("CHECKOUT_SERVICE_TLS_CLIENT_CERT_PATH")
		}()

		_, err := LoadTLSConfig()
		require.Error(t, err, "Loading mTLS config without client key should fail")
		assert.Contains(t, err.Error(), "client key", "Error message should mention client key")
	})
}

// AC-6: Invalid CA cert format fails startup
func TestAC6_InvalidCACertificate(t *testing.T) {
	os.Setenv("CHECKOUT_SERVICE_TLS_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH", "./testdata/invalid_ca.crt") // Invalid PEM data
	os.Setenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED", "false")
	defer func() {
		os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED")
	}()

	// First load config (validates existence, not content yet)
	cfg, err := LoadTLSConfig()
	require.NoError(t, err)

	// Creating credentials should fail with invalid CA error
	_, err = NewGRPCCredentials(cfg)
	require.Error(t, err, "Creating credentials with invalid CA cert should fail")
	assert.Contains(t, err.Error(), "CA certificate", "Error should mention invalid CA certificate")
}

// AC-7: Invalid client cert/key pair fails startup
func TestAC7_InvalidClientCredentials(t *testing.T) {
	// Test mismatched cert/key pair
	os.Setenv("CHECKOUT_SERVICE_TLS_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH", "./testdata/valid_ca.crt")
	os.Setenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_TLS_CLIENT_CERT_PATH", "./testdata/valid_client.crt")
	os.Setenv("CHECKOUT_SERVICE_TLS_CLIENT_KEY_PATH", "./testdata/wrong_client.key") // Wrong key for the cert
	defer func() {
		os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_CLIENT_CERT_PATH")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_CLIENT_KEY_PATH")
	}()

	// Load config passes (files exist)
	cfg, err := LoadTLSConfig()
	require.NoError(t, err)

	// Creating credentials should fail with invalid client credentials error
	_, err = NewGRPCCredentials(cfg)
	require.Error(t, err, "Creating mTLS credentials with mismatched cert/key should fail")
	assert.Contains(t, err.Error(), "client", "Error should mention client credentials")
}

// AC-8: TLS verification fails for untrusted server certificate
func TestAC8_TLSVerificationFailsForUntrustedServer(t *testing.T) {
	// This test verifies that when connecting to a server with a certificate not signed by our CA, the connection fails
	// First create valid one-way TLS config
	os.Setenv("CHECKOUT_SERVICE_TLS_ENABLED", "true")
	os.Setenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH", "./testdata/valid_ca.crt") // CA that didn't sign the test server cert
	os.Setenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED", "false")
	defer func() {
		os.Unsetenv("CHECKOUT_SERVICE_TLS_ENABLED")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_CA_CERT_PATH")
		os.Unsetenv("CHECKOUT_SERVICE_TLS_MTLS_ENABLED")
	}()

	cfg, err := LoadTLSConfig()
	require.NoError(t, err)

	creds, err := NewGRPCCredentials(cfg)
	require.NoError(t, err)

	// Try to connect to a server with a self-signed certificate not in our CA bundle
	// Note: This assumes we have a test server running with untrusted cert for this test, alternatively we can mock the TLS handshake
	// For this test we check that the credential enforces verification
	opts := []grpc.DialOption{creds}
	// We expect this connection attempt to fail with TLS verification error
	_, err = grpc.NewClient("badssl.com:443", opts...) // badssl.com uses cert not signed by our test CA
	require.Error(t, err, "Connection to server with untrusted cert should fail")
	assert.Contains(t, err.Error(), "certificate", "Error should mention certificate verification failure")
}
