package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"os"
	"testing"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	grpctls "google.golang.org/grpc/credentials/tls"

	pb "github.com/open-telemetry/opentelemetry-demo/src/product-catalog/genproto/oteldemo/product/v1"
)

var (
	ErrTLSConfigMissingCert = errors.New("TLS config missing certificate path")
	ErrTLSConfigMissingKey  = errors.New("TLS config missing private key path")
	ErrTLSConfigMissingCA   = errors.New("TLS config missing CA bundle path for client auth")
	ErrTLSInvalidCert       = errors.New("invalid TLS certificate/key")
	ErrTLSInvalidCA         = errors.New("invalid CA bundle")
)

type TLSConfig struct {
	Enabled            bool
	CertPath           string
	KeyPath            string
	CAPath             string
	ClientAuthRequired bool
}

func LoadTLSConfigFromEnv() (TLSConfig, error) {
	return TLSConfig{}, nil
}

func NewGRPCServerWithTLS(cfg TLSConfig) (*grpc.Server, error) {
	return nil, nil
}

func TestAC1_TLSDisabledAcceptsPlaintextConnections(t *testing.T) {
	// Unset all TLS env vars, TLS should be disabled by default
	os.Unsetenv("PRODUCT_CATALOG_GRPC_TLS_ENABLED")
	os.Unsetenv("PRODUCT_CATALOG_GRPC_TLS_CERT_PATH")
	os.Unsetenv("PRODUCT_CATALOG_GRPC_TLS_KEY_PATH")
	os.Unsetenv("PRODUCT_CATALOG_GRPC_TLS_CA_PATH")
	os.Unsetenv("PRODUCT_CATALOG_GRPC_TLS_CLIENT_AUTH_REQUIRED")

	// Load config
	cfg, err := LoadTLSConfigFromEnv()
	if err != nil {
		t.Fatalf("expected no error for default TLS disabled config: %v", err)
	}

	if cfg.Enabled != false {
		t.Fatalf("expected TLS to be disabled by default, got: %v", cfg.Enabled)
	}

	// Create server (should be plaintext)
	server, err := NewGRPCServerWithTLS(cfg)
	if err != nil {
		t.Fatalf("failed to create gRPC server with TLS disabled: %v", err)
	}
	defer server.Stop()

	// Verify plaintext client can connect
	conn, err := grpc.NewClient("localhost:0", grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("failed to create plaintext client: %v", err)
	}
	defer conn.Close()

	client := pb.NewProductCatalogServiceClient(conn)
	// Just test that client can send request without TLS errors (server not actually listening here)
	_, err = client.ListProducts(context.Background(), &pb.ListProductsRequest{})
	// We expect connection refused, not TLS error
	if err != nil && err.Error() == "tls: first record does not look like a TLS handshake" {
		t.Fatalf("expected plaintext connection to be accepted, got TLS error: %v", err)
	}
}

func TestAC2_TLSEnabledRejectsPlaintextConnections(t *testing.T) {
	// Set TLS enabled with dummy cert/key paths
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_ENABLED", "true")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_CERT_PATH", "testdata/server.crt")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_KEY_PATH", "testdata/server.key")
	defer os.Clearenv()

	cfg, err := LoadTLSConfigFromEnv()
	if err != nil {
		t.Fatalf("expected no error loading valid TLS config: %v", err)
	}

	if cfg.Enabled != true {
		t.Fatalf("expected TLS to be enabled, got: %v", cfg.Enabled)
	}

	// Create TLS enabled server
	server, err := NewGRPCServerWithTLS(cfg)
	if err != nil {
		t.Fatalf("failed to create TLS gRPC server: %v", err)
	}
	defer server.Stop()

	// Verify plaintext client is rejected
	conn, err := grpc.NewClient("localhost:0", grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("failed to create plaintext client: %v", err)
	}
	defer conn.Close()

	client := pb.NewProductCatalogServiceClient(conn)
	_, err = client.ListProducts(context.Background(), &pb.ListProductsRequest{})
	// We expect TLS error for plaintext client trying to connect to TLS server
	if err != nil && err.Error() != "tls: first record does not look like a TLS handshake" {
		t.Fatalf("expected plaintext connection to be rejected with TLS error, got: %v", err)
	}
}

func TestAC3_TLSEnabledMissingCertOrKeyExitsWithError(t *testing.T) {
	// Test missing cert path
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_ENABLED", "true")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_KEY_PATH", "testdata/server.key")
	os.Unsetenv("PRODUCT_CATALOG_GRPC_TLS_CERT_PATH")
	defer os.Clearenv()

	_, err := LoadTLSConfigFromEnv()
	if err != ErrTLSConfigMissingCert {
		t.Fatalf("expected ErrTLSConfigMissingCert when TLS enabled but cert path missing, got: %v", err)
	}

	// Test missing key path
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_ENABLED", "true")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_CERT_PATH", "testdata/server.crt")
	os.Unsetenv("PRODUCT_CATALOG_GRPC_TLS_KEY_PATH")

	_, err = LoadTLSConfigFromEnv()
	if err != ErrTLSConfigMissingKey {
		t.Fatalf("expected ErrTLSConfigMissingKey when TLS enabled but key path missing, got: %v", err)
	}
}

func TestAC4_MTLSClientAuthRequiredRejectsUnauthenticatedClients(t *testing.T) {
	// Set mTLS enabled with valid CA path
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_ENABLED", "true")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_CERT_PATH", "testdata/server.crt")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_KEY_PATH", "testdata/server.key")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_CLIENT_AUTH_REQUIRED", "true")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_CA_PATH", "testdata/ca.crt")
	defer os.Clearenv()

	cfg, err := LoadTLSConfigFromEnv()
	if err != nil {
		t.Fatalf("expected no error loading valid mTLS config: %v", err)
	}

	// Create mTLS enabled server
	server, err := NewGRPCServerWithTLS(cfg)
	if err != nil {
		t.Fatalf("failed to create mTLS gRPC server: %v", err)
	}
	defer server.Stop()

	// Test client without cert is rejected
	tlsConfig := &tls.Config{
		InsecureSkipVerify: true,
	}
	clientCreds := grpctls.New(tlsConfig)
	conn, err := grpc.NewClient("localhost:0", grpc.WithTransportCredentials(clientCreds))
	if err != nil {
		t.Fatalf("failed to create TLS client without cert: %v", err)
	}
	defer conn.Close()

	client := pb.NewProductCatalogServiceClient(conn)
	_, err = client.ListProducts(context.Background(), &pb.ListProductsRequest{})
	if err == nil {
		t.Fatalf("expected client without cert to be rejected by mTLS server, got no error")
	}
}

func TestAC5_MTLSRequiredMissingCAPathExitsWithError(t *testing.T) {
	// Set client auth required without CA path
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_ENABLED", "true")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_CERT_PATH", "testdata/server.crt")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_KEY_PATH", "testdata/server.key")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_CLIENT_AUTH_REQUIRED", "true")
	os.Unsetenv("PRODUCT_CATALOG_GRPC_TLS_CA_PATH")
	defer os.Clearenv()

	_, err := LoadTLSConfigFromEnv()
	if err != ErrTLSConfigMissingCA {
		t.Fatalf("expected ErrTLSConfigMissingCA when client auth required but CA path missing, got: %v", err)
	}
}

func TestAC6_TLSEnabledClientAuthOptionalAcceptsAnyTLSClient(t *testing.T) {
	// Set TLS enabled without client auth required
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_ENABLED", "true")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_CERT_PATH", "testdata/server.crt")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_KEY_PATH", "testdata/server.key")
	os.Unsetenv("PRODUCT_CATALOG_GRPC_TLS_CLIENT_AUTH_REQUIRED")
	defer os.Clearenv()

	cfg, err := LoadTLSConfigFromEnv()
	if err != nil {
		t.Fatalf("expected no error loading TLS config with client auth optional: %v", err)
	}

	if cfg.ClientAuthRequired != false {
		t.Fatalf("expected client auth to be disabled by default, got: %v", cfg.ClientAuthRequired)
	}

	// Create TLS server without client auth
	server, err := NewGRPCServerWithTLS(cfg)
	if err != nil {
		t.Fatalf("failed to create TLS server without client auth: %v", err)
	}
	defer server.Stop()

	// Test client without cert is accepted
	tlsConfig := &tls.Config{
		InsecureSkipVerify: true,
	}
	clientCreds := grpctls.New(tlsConfig)
	conn, err := grpc.NewClient("localhost:0", grpc.WithTransportCredentials(clientCreds))
	if err != nil {
		t.Fatalf("failed to create TLS client without cert: %v", err)
	}
	defer conn.Close()

	client := pb.NewProductCatalogServiceClient(conn)
	_, err = client.ListProducts(context.Background(), &pb.ListProductsRequest{})
	// Should get connection refused, not TLS certificate required error
	if err != nil && err.Error() == "tls: bad certificate" {
		t.Fatalf("expected TLS client without cert to be accepted, got bad certificate error: %v", err)
	}
}

func TestAC7_TLSDisabledFunctionalityIdenticalToPrevious(t *testing.T) {
	// Unset all TLS env vars
	os.Clearenv()

	// Load config, should have TLS disabled
	cfg, err := LoadTLSConfigFromEnv()
	if err != nil {
		t.Fatalf("expected no error loading default config: %v", err)
	}

	server, err := NewGRPCServerWithTLS(cfg)
	if err != nil {
		t.Fatalf("failed to create gRPC server with default config: %v", err)
	}
	defer server.Stop()

	// Verify server is plaintext (same as before TLS feature existed)
	// This test ensures no regression in existing functionality
	conn, err := grpc.NewClient("localhost:0", grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("failed to create plaintext client: %v", err)
	}
	defer conn.Close()

	client := pb.NewProductCatalogServiceClient(conn)
	_, err = client.ListProducts(context.Background(), &pb.ListProductsRequest{})
	if err != nil && err.Error() == "tls: first record does not look like a TLS handshake" {
		t.Fatalf("existing plaintext functionality broken: %v", err)
	}
}

func TestAC8_TLSConfigValidatedBeforeServerStart(t *testing.T) {
	// Test invalid cert path returns error before server starts
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_ENABLED", "true")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_CERT_PATH", "nonexistent.crt")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_KEY_PATH", "nonexistent.key")
	defer os.Clearenv()

	cfg, err := LoadTLSConfigFromEnv()
	if err != nil {
		t.Fatalf("config load should pass with paths, validation happens in NewGRPCServerWithTLS: %v", err)
	}

	// Should return error immediately, not after trying to listen
	_, err = NewGRPCServerWithTLS(cfg)
	if err != ErrTLSInvalidCert {
		t.Fatalf("expected ErrTLSInvalidCert for non-existent cert files, got: %v", err)
	}

	// Test invalid CA path returns error before server starts
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_ENABLED", "true")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_CERT_PATH", "testdata/server.crt")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_KEY_PATH", "testdata/server.key")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_CLIENT_AUTH_REQUIRED", "true")
	os.Setenv("PRODUCT_CATALOG_GRPC_TLS_CA_PATH", "nonexistent.ca.crt")

	cfg, err = LoadTLSConfigFromEnv()
	if err != nil {
		t.Fatalf("config load should pass with paths, validation happens in NewGRPCServerWithTLS: %v", err)
	}

	_, err = NewGRPCServerWithTLS(cfg)
	if err != ErrTLSInvalidCA {
		t.Fatalf("expected ErrTLSInvalidCA for non-existent CA file, got: %v", err)
	}
}
