package main

import (
	"os"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func resetEnv() {
	os.Unsetenv("KAFKA_TLS_ENABLED")
	os.Unsetenv("KAFKA_TLS_CA_CERT_PATH")
	os.Unsetenv("KAFKA_TLS_CLIENT_CERT_PATH")
	os.Unsetenv("KAFKA_TLS_CLIENT_KEY_PATH")
	os.Unsetenv("KAFKA_TLS_SKIP_VERIFY")
}

// AC-1: When KAFKA_TLS_ENABLED is not set or set to "false", the kafka-collector connects without TLS
func TestAC1_TLSDisabledByDefault(t *testing.T) {
	resetEnv()
	defer resetEnv()

	cfg, err := LoadKafkaTLSConfig()
	require.NoError(t, err)
	assert.False(t, cfg.Enabled)
	assert.Empty(t, cfg.CACertPath)
	assert.Empty(t, cfg.ClientCertPath)
	assert.Empty(t, cfg.ClientKeyPath)
	assert.False(t, cfg.SkipVerify)
}

func TestAC1_TLSDisabledWhenSetToFalse(t *testing.T) {
	resetEnv()
	defer resetEnv()
	os.Setenv("KAFKA_TLS_ENABLED", "false")
	os.Setenv("KAFKA_TLS_CA_CERT_PATH", "/tmp/ca.pem")

	cfg, err := LoadKafkaTLSConfig()
	require.NoError(t, err)
	assert.False(t, cfg.Enabled)
}

// AC-2: TLS enabled with valid CA cert establishes encrypted connection
func TestAC2_TLSEnabledWithValidCACert(t *testing.T) {
	resetEnv()
	defer resetEnv()
	os.Setenv("KAFKA_TLS_ENABLED", "true")
	os.Setenv("KAFKA_TLS_CA_CERT_PATH", "./testdata/ca.pem") // Test file exists in testdata

	cfg, err := LoadKafkaTLSConfig()
	require.NoError(t, err)
	assert.True(t, cfg.Enabled)
	assert.Equal(t, "./testdata/ca.pem", cfg.CACertPath)

	// Test we can create consumer with this config
	consumer, err := NewKafkaConsumerWithTLS([]string{"localhost:9093"}, "test-group", cfg)
	require.NoError(t, err)
	require.NotNil(t, consumer)
	consumer.Close()
}

// AC-3: mTLS with valid client cert/key works
func TestAC3_MTLSEnabledWithValidCerts(t *testing.T) {
	resetEnv()
	defer resetEnv()
	os.Setenv("KAFKA_TLS_ENABLED", "true")
	os.Setenv("KAFKA_TLS_CA_CERT_PATH", "./testdata/ca.pem")
	os.Setenv("KAFKA_TLS_CLIENT_CERT_PATH", "./testdata/client.pem")
	os.Setenv("KAFKA_TLS_CLIENT_KEY_PATH", "./testdata/client.key")

	cfg, err := LoadKafkaTLSConfig()
	require.NoError(t, err)
	assert.Equal(t, "./testdata/client.pem", cfg.ClientCertPath)
	assert.Equal(t, "./testdata/client.key", cfg.ClientKeyPath)

	// Test consumer creation with mTLS config
	consumer, err := NewKafkaConsumerWithTLS([]string{"localhost:9093"}, "test-group", cfg)
	require.NoError(t, err)
	require.NotNil(t, consumer)
	consumer.Close()
}

// AC-4: TLS enabled without CA cert path fails
func TestAC4_TLSEnabledMissingCACert(t *testing.T) {
	resetEnv()
	defer resetEnv()
	os.Setenv("KAFKA_TLS_ENABLED", "true")

	_, err := LoadKafkaTLSConfig()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "CA certificate path")
}

func TestAC4_TLSEnabledCACertNonExistent(t *testing.T) {
	resetEnv()
	defer resetEnv()
	os.Setenv("KAFKA_TLS_ENABLED", "true")
	os.Setenv("KAFKA_TLS_CA_CERT_PATH", "/tmp/non-existent-ca.pem")

	_, err := LoadKafkaTLSConfig()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "no such file or directory")
}

// AC-5: Only one of client cert/key provided fails
func TestAC5_MissingClientKey(t *testing.T) {
	resetEnv()
	defer resetEnv()
	os.Setenv("KAFKA_TLS_ENABLED", "true")
	os.Setenv("KAFKA_TLS_CA_CERT_PATH", "./testdata/ca.pem")
	os.Setenv("KAFKA_TLS_CLIENT_CERT_PATH", "./testdata/client.pem")

	_, err := LoadKafkaTLSConfig()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "client key path")
}

func TestAC5_MissingClientCert(t *testing.T) {
	resetEnv()
	defer resetEnv()
	os.Setenv("KAFKA_TLS_ENABLED", "true")
	os.Setenv("KAFKA_TLS_CA_CERT_PATH", "./testdata/ca.pem")
	os.Setenv("KAFKA_TLS_CLIENT_KEY_PATH", "./testdata/client.key")

	_, err := LoadKafkaTLSConfig()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "client certificate path")
}

// AC-6: Unreadable cert file fails
func TestAC6_UnreadableCACert(t *testing.T) {
	resetEnv()
	defer resetEnv()
	os.Setenv("KAFKA_TLS_ENABLED", "true")
	os.Setenv("KAFKA_TLS_CA_CERT_PATH", "/root/ca.pem") // Should be unreadable

	_, err := LoadKafkaTLSConfig()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "permission denied")
}

// AC-7: Invalid PEM content fails
func TestAC7_InvalidPEMCACert(t *testing.T) {
	resetEnv()
	defer resetEnv()
	// Create temp file with invalid content
	f, err := os.CreateTemp("", "invalid-ca-*.pem")
	require.NoError(t, err)
	defer os.Remove(f.Name())
	f.WriteString("this is not a PEM file")
	f.Close()

	os.Setenv("KAFKA_TLS_ENABLED", "true")
	os.Setenv("KAFKA_TLS_CA_CERT_PATH", f.Name())

	_, err = LoadKafkaTLSConfig()
	require.Error(t, err)
	assert.Contains(t, err.Error(), "invalid PEM format")
}

// AC-8: Unit tests verify parsing and validation
func TestAC8_SkipVerifyParsing(t *testing.T) {
	resetEnv()
	defer resetEnv()
	os.Setenv("KAFKA_TLS_ENABLED", "true")
	os.Setenv("KAFKA_TLS_CA_CERT_PATH", "./testdata/ca.pem")
	os.Setenv("KAFKA_TLS_SKIP_VERIFY", "true")

	cfg, err := LoadKafkaTLSConfig()
	require.NoError(t, err)
	assert.True(t, cfg.SkipVerify)
}
