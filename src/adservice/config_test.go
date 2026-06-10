package main

import (
	"os"
	"testing"
)

// TestAC1_DefaultConfigurationNoEnvVars tests that when no env vars are set, config matches default values
func TestAC1_DefaultConfigurationNoEnvVars(t *testing.T) {
	// Clear all relevant env vars first
	os.Unsetenv("AD_SERVICE_PORT")
	os.Unsetenv("AD_DB_HOST")
	os.Unsetenv("AD_DB_PORT")
	os.Unsetenv("AD_DB_USER")
	os.Unsetenv("AD_DB_PASSWORD")
	os.Unsetenv("AD_DB_NAME")

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("Expected no error for default config, got %v", err)
	}

	if cfg.ServicePort != 9555 {
		t.Errorf("Expected default ServicePort 9555, got %d", cfg.ServicePort)
	}
	if cfg.DBHost != "localhost" {
		t.Errorf("Expected default DBHost localhost, got %s", cfg.DBHost)
	}
	if cfg.DBPort != 5432 {
		t.Errorf("Expected default DBPort 5432, got %d", cfg.DBPort)
	}
	if cfg.DBUser != "postgres" {
		t.Errorf("Expected default DBUser postgres, got %s", cfg.DBUser)
	}
	if cfg.DBPassword != "postgres" {
		t.Errorf("Expected default DBPassword postgres, got %s", cfg.DBPassword)
	}
	if cfg.DBName != "ads" {
		t.Errorf("Expected default DBName ads, got %s", cfg.DBName)
	}
}

// TestAC2_CustomServicePort tests that AD_SERVICE_PORT env var overrides default port
func TestAC2_CustomServicePort(t *testing.T) {
	os.Setenv("AD_SERVICE_PORT", "8080")
	defer os.Unsetenv("AD_SERVICE_PORT")

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("Expected no error for valid service port, got %v", err)
	}

	if cfg.ServicePort != 8080 {
		t.Errorf("Expected ServicePort 8080, got %d", cfg.ServicePort)
	}
}

// TestAC3_CustomDBHost tests that AD_DB_HOST env var overrides default host
func TestAC3_CustomDBHost(t *testing.T) {
	os.Setenv("AD_DB_HOST", "staging-db.internal")
	defer os.Unsetenv("AD_DB_HOST")

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("Expected no error for custom DB host, got %v", err)
	}

	if cfg.DBHost != "staging-db.internal" {
		t.Errorf("Expected DBHost staging-db.internal, got %s", cfg.DBHost)
	}
}

// TestAC4_CustomDBPort tests that AD_DB_PORT env var overrides default port
func TestAC4_CustomDBPort(t *testing.T) {
	os.Setenv("AD_DB_PORT", "5433")
	defer os.Unsetenv("AD_DB_PORT")

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("Expected no error for valid DB port, got %v", err)
	}

	if cfg.DBPort != 5433 {
		t.Errorf("Expected DBPort 5433, got %d", cfg.DBPort)
	}
}

// TestAC5_CustomDBUser tests that AD_DB_USER env var overrides default user
func TestAC5_CustomDBUser(t *testing.T) {
	os.Setenv("AD_DB_USER", "staging-user")
	defer os.Unsetenv("AD_DB_USER")

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("Expected no error for custom DB user, got %v", err)
	}

	if cfg.DBUser != "staging-user" {
		t.Errorf("Expected DBUser staging-user, got %s", cfg.DBUser)
	}
}

// TestAC6_CustomDBPassword tests that AD_DB_PASSWORD env var overrides default password
func TestAC6_CustomDBPassword(t *testing.T) {
	os.Setenv("AD_DB_PASSWORD", "staging-pass-123")
	defer os.Unsetenv("AD_DB_PASSWORD")

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("Expected no error for custom DB password, got %v", err)
	}

	if cfg.DBPassword != "staging-pass-123" {
		t.Errorf("Expected DBPassword staging-pass-123, got %s", cfg.DBPassword)
	}
}

// TestAC7_CustomDBName tests that AD_DB_NAME env var overrides default name
func TestAC7_CustomDBName(t *testing.T) {
	os.Setenv("AD_DB_NAME", "staging-ads")
	defer os.Unsetenv("AD_DB_NAME")

	cfg, err := LoadConfig()
	if err != nil {
		t.Fatalf("Expected no error for custom DB name, got %v", err)
	}

	if cfg.DBName != "staging-ads" {
		t.Errorf("Expected DBName staging-ads, got %s", cfg.DBName)
	}
}

// TestAC8_InvalidServicePortNonInteger tests that non-integer AD_SERVICE_PORT returns error
func TestAC8_InvalidServicePortNonInteger(t *testing.T) {
	os.Setenv("AD_SERVICE_PORT", "invalid")
	defer os.Unsetenv("AD_SERVICE_PORT")

	_, err := LoadConfig()
	if err == nil {
		t.Fatalf("Expected error for non-integer service port, got nil")
	}
}

// TestAC9_InvalidServicePortTooHigh tests that AD_SERVICE_PORT > 65535 returns error
func TestAC9_InvalidServicePortTooHigh(t *testing.T) {
	os.Setenv("AD_SERVICE_PORT", "70000")
	defer os.Unsetenv("AD_SERVICE_PORT")

	_, err := LoadConfig()
	if err == nil {
		t.Fatalf("Expected error for service port > 65535, got nil")
	}
}

// TestAC10_InvalidDBPortZero tests that AD_DB_PORT = 0 returns error
func TestAC10_InvalidDBPortZero(t *testing.T) {
	os.Setenv("AD_DB_PORT", "0")
	defer os.Unsetenv("AD_DB_PORT")

	_, err := LoadConfig()
	if err == nil {
		t.Fatalf("Expected error for DB port 0, got nil")
	}
}

// Helper to unset all POSTGRES_SSL* environment variables
func unsetPostgresSSLEnvVars(t *testing.T) {
	t.Helper()
	os.Unsetenv("POSTGRES_SSLMODE")
	os.Unsetenv("POSTGRES_SSLROOTCERT")
	os.Unsetenv("POSTGRES_SSLCERT")
	os.Unsetenv("POSTGRES_SSLKEY")
}

// TestAC1_DefaultSSLModeDisableWhenNoTLSConfig tests AC-1: no TLS env vars set defaults to sslmode=disable
func TestAC1_DefaultSSLModeDisableWhenNoTLSConfig(t *testing.T) {
	unsetPostgresSSLEnvVars(t)

	cfg, err := NewPostgresConfig()
	if err != nil {
		t.Fatalf("Expected no error with default TLS config, got %v", err)
	}
	if cfg.SSLMode != "disable" {
		t.Errorf("Expected default SSLMode to be 'disable', got '%s'", cfg.SSLMode)
	}
	if cfg.SSLRootCert != "" {
		t.Errorf("Expected default SSLRootCert to be empty, got '%s'", cfg.SSLRootCert)
	}
	if cfg.SSLCert != "" {
		t.Errorf("Expected default SSLCert to be empty, got '%s'", cfg.SSLCert)
	}
	if cfg.SSLKey != "" {
		t.Errorf("Expected default SSLKey to be empty, got '%s'", cfg.SSLKey)
	}
}

// TestAC2_InvalidSSLModeReturnsError tests AC-2: invalid POSTGRES_SSLMODE value returns error
func TestAC2_InvalidSSLModeReturnsError(t *testing.T) {
	unsetPostgresSSLEnvVars(t)
	os.Setenv("POSTGRES_SSLMODE", "invalid_mode")
	defer os.Unsetenv("POSTGRES_SSLMODE")

	_, err := NewPostgresConfig()
	if err == nil {
		t.Fatalf("Expected error for invalid sslmode 'invalid_mode', got nil")
	}
	if err.Error() == "" {
		t.Errorf("Expected error message to be non-empty for invalid sslmode")
	}
}

// TestAC3_VerifyCAModeWithoutRootCertReturnsError tests AC-3: sslmode=verify-ca without root cert returns error
func TestAC3_VerifyCAModeWithoutRootCertReturnsError(t *testing.T) {
	unsetPostgresSSLEnvVars(t)
	os.Setenv("POSTGRES_SSLMODE", "verify-ca")
	defer os.Unsetenv("POSTGRES_SSLMODE")

	_, err := NewPostgresConfig()
	if err == nil {
		t.Fatalf("Expected error for verify-ca sslmode without root cert, got nil")
	}
}

// TestAC3_VerifyFullModeWithoutRootCertReturnsError tests AC-3: sslmode=verify-full without root cert returns error
func TestAC3_VerifyFullModeWithoutRootCertReturnsError(t *testing.T) {
	unsetPostgresSSLEnvVars(t)
	os.Setenv("POSTGRES_SSLMODE", "verify-full")
	defer os.Unsetenv("POSTGRES_SSLMODE")

	_, err := NewPostgresConfig()
	if err == nil {
		t.Fatalf("Expected error for verify-full sslmode without root cert, got nil")
	}
}

// TestAC4_NonExistentRootCertReturnsError tests AC-4: non-existent root cert file returns error
func TestAC4_NonExistentRootCertReturnsError(t *testing.T) {
	unsetPostgresSSLEnvVars(t)
	os.Setenv("POSTGRES_SSLMODE", "verify-ca")
	os.Setenv("POSTGRES_SSLROOTCERT", "/non/existent/path/root.crt")
	defer func() {
		os.Unsetenv("POSTGRES_SSLMODE")
		os.Unsetenv("POSTGRES_SSLROOTCERT")
	}()

	_, err := NewPostgresConfig()
	if err == nil {
		t.Fatalf("Expected error for non-existent root cert file, got nil")
	}
}

// TestAC4_NonExistentClientCertReturnsError tests AC-4: non-existent client cert file returns error
func TestAC4_NonExistentClientCertReturnsError(t *testing.T) {
	unsetPostgresSSLEnvVars(t)
	os.Setenv("POSTGRES_SSLCERT", "/non/existent/path/client.crt")
	os.Setenv("POSTGRES_SSLKEY", "/tmp/existent.key")
	defer func() {
		os.Unsetenv("POSTGRES_SSLCERT")
		os.Unsetenv("POSTGRES_SSLKEY")
	}()

	// Create dummy key file to pass key existence check
	os.WriteFile("/tmp/existent.key", []byte("dummy"), 0600)
	defer os.Remove("/tmp/existent.key")

	_, err := NewPostgresConfig()
	if err == nil {
		t.Fatalf("Expected error for non-existent client cert file, got nil")
	}
}

// TestAC5_ClientCertWithoutKeyReturnsError tests AC-5: client cert provided without key returns error
func TestAC5_ClientCertWithoutKeyReturnsError(t *testing.T) {
	unsetPostgresSSLEnvVars(t)
	os.Setenv("POSTGRES_SSLCERT", "/tmp/client.crt")
	defer os.Unsetenv("POSTGRES_SSLCERT")

	_, err := NewPostgresConfig()
	if err == nil {
		t.Fatalf("Expected error for client cert without key, got nil")
	}
}

// TestAC6_ClientKeyWithoutCertReturnsError tests AC-6: client key provided without cert returns error
func TestAC6_ClientKeyWithoutCertReturnsError(t *testing.T) {
	unsetPostgresSSLEnvVars(t)
	os.Setenv("POSTGRES_SSLKEY", "/tmp/client.key")
	defer os.Unsetenv("POSTGRES_SSLKEY")

	_, err := NewPostgresConfig()
	if err == nil {
		t.Fatalf("Expected error for client key without cert, got nil")
	}
}

// TestAC7_ValidTLSConfigReturnsNoError tests AC-7: valid TLS configuration returns no error
func TestAC7_ValidTLSConfigReturnsNoError(t *testing.T) {
	unsetPostgresSSLEnvVars(t)
	os.Setenv("POSTGRES_SSLMODE", "verify-full")
	os.Setenv("POSTGRES_SSLROOTCERT", "/tmp/root.crt")
	os.Setenv("POSTGRES_SSLCERT", "/tmp/client.crt")
	os.Setenv("POSTGRES_SSLKEY", "/tmp/client.key")
	defer unsetPostgresSSLEnvVars(t)

	// Create dummy valid files
	os.WriteFile("/tmp/root.crt", []byte("dummy root cert"), 0644)
	os.WriteFile("/tmp/client.crt", []byte("dummy client cert"), 0644)
	os.WriteFile("/tmp/client.key", []byte("dummy client key"), 0600)
	defer func() {
		os.Remove("/tmp/root.crt")
		os.Remove("/tmp/client.crt")
		os.Remove("/tmp/client.key")
	}()

	cfg, err := NewPostgresConfig()
	if err != nil {
		t.Fatalf("Expected no error for valid TLS config, got %v", err)
	}
	if cfg.SSLMode != "verify-full" {
		t.Errorf("Expected SSLMode to be 'verify-full', got '%s'", cfg.SSLMode)
	}
	if cfg.SSLRootCert != "/tmp/root.crt" {
		t.Errorf("Expected SSLRootCert to match provided path, got '%s'", cfg.SSLRootCert)
	}
	if cfg.SSLCert != "/tmp/client.crt" {
		t.Errorf("Expected SSLCert to match provided path, got '%s'", cfg.SSLCert)
	}
	if cfg.SSLKey != "/tmp/client.key" {
		t.Errorf("Expected SSLKey to match provided path, got '%s'", cfg.SSLKey)
	}
}
