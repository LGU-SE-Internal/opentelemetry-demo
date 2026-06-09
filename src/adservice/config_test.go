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
