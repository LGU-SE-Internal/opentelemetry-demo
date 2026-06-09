// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"net"
	"os"
	"testing"
	"time"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
	"google.golang.org/grpc"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
)

func generateTestCert(t *testing.T, isCA bool, name string, parentCert *x509.Certificate, parentKey *rsa.PrivateKey) (*x509.Certificate, *rsa.PrivateKey, []byte, []byte) {
	t.Helper()

	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	require.NoError(t, err)

	serialNumberLimit := new(big.Int).Lsh(big.NewInt(1), 128)
	serialNumber, err := rand.Int(rand.Reader, serialNumberLimit)
	require.NoError(t, err)

	template := x509.Certificate{
		SerialNumber: serialNumber,
		Subject: pkix.Name{
			Organization: []string{"Test Org"},
			CommonName:   name,
		},
		NotBefore:             time.Now(),
		NotAfter:              time.Now().Add(24 * time.Hour),
		KeyUsage:              x509.KeyUsageKeyEncipherment | x509.KeyUsageDigitalSignature,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth, x509.ExtKeyUsageClientAuth},
		BasicConstraintsValid: true,
		DNSNames:              []string{name, "localhost"},
		IPAddresses:           []net.IP{net.IPv4(127, 0, 0, 1)},
	}

	if isCA {
		template.IsCA = true
		template.KeyUsage |= x509.KeyUsageCertSign
	}

	if parentCert == nil {
		parentCert = &template
		parentKey = priv
	}

	derBytes, err := x509.CreateCertificate(rand.Reader, &template, parentCert, &priv.PublicKey, parentKey)
	require.NoError(t, err)

	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: derBytes})
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(priv)})

	return &template, priv, certPEM, keyPEM
}

func writeTempFile(t *testing.T, content []byte) string {
	t.Helper()
	f, err := os.CreateTemp("", "test-tls-*")
	require.NoError(t, err)
	defer f.Close()

	_, err = f.Write(content)
	require.NoError(t, err)

	t.Cleanup(func() { os.Remove(f.Name()) })
	return f.Name()
}

func TestAC1_ServerPlaintextDefault(t *testing.T) {
	cfg := ServerTLSConfig{Enabled: false}
	srv, err := NewGRPCServer(cfg)
	require.NoError(t, err)
	require.NotNil(t, srv)
	srv.Stop()
}

func TestAC2_ServerTLSEnabledValidCerts(t *testing.T) {
	_, _, certPEM, keyPEM := generateTestCert(t, false, "localhost", nil, nil)
	certPath := writeTempFile(t, certPEM)
	keyPath := writeTempFile(t, keyPEM)

	cfg := ServerTLSConfig{
		Enabled:  true,
		CertPath: certPath,
		KeyPath:  keyPath,
	}

	srv, err := NewGRPCServer(cfg)
	require.NoError(t, err)
	require.NotNil(t, srv)
	srv.Stop()
}

func TestAC3_ServermTLSRequiredValidCA(t *testing.T) {
	caCert, caKey, caPEM, _ := generateTestCert(t, true, "test-ca", nil, nil)
	_, _, serverCertPEM, serverKeyPEM := generateTestCert(t, false, "localhost", caCert, caKey)

	caPath := writeTempFile(t, caPEM)
	certPath := writeTempFile(t, serverCertPEM)
	keyPath := writeTempFile(t, serverKeyPEM)

	cfg := ServerTLSConfig{
		Enabled:              true,
		CertPath:             certPath,
		KeyPath:              keyPath,
		ClientAuthRequired:   true,
		CACertPath:           caPath,
	}

	srv, err := NewGRPCServer(cfg)
	require.NoError(t, err)
	require.NotNil(t, srv)
	srv.Stop()
}

func TestAC4_ClientPlaintextDefault(t *testing.T) {
	cfg := ClientTLSConfig{Enabled: false}
	// We don't actually connect, just check we get a valid conn config
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	require.NoError(t, err)
	defer lis.Close()

	srv := grpc.NewServer()
	go func() {
		_ = srv.Serve(lis)
	}()
	defer srv.Stop()

	conn, err := NewGRPCClientConn(lis.Addr().String(), cfg)
	require.NoError(t, err)
	require.NotNil(t, conn)
	conn.Close()
}

func TestAC5_ClientTLSEnabledValidCA(t *testing.T) {
	caCert, caKey, caPEM, _ := generateTestCert(t, true, "test-ca", nil, nil)
	_, _, serverCertPEM, serverKeyPEM := generateTestCert(t, false, "localhost", caCert, caKey)

	caPath := writeTempFile(t, caPEM)
	serverCertPath := writeTempFile(t, serverCertPEM)
	serverKeyPath := writeTempFile(t, serverKeyPEM)

	// Start TLS server
	serverTLSConfig := ServerTLSConfig{
		Enabled:  true,
		CertPath: serverCertPath,
		KeyPath:  serverKeyPath,
	}
	srv, err := NewGRPCServer(serverTLSConfig)
	require.NoError(t, err)
	healthpb.RegisterHealthServer(srv, healthpb.NewHealthServer())

	lis, err := net.Listen("tcp", "127.0.0.1:0")
	require.NoError(t, err)
	defer lis.Close()

	go func() {
		_ = srv.Serve(lis)
	}()
	defer srv.Stop()

	// Connect with client TLS
	clientCfg := ClientTLSConfig{
		Enabled:    true,
		CACertPath: caPath,
	}
	conn, err := NewGRPCClientConn(lis.Addr().String(), clientCfg)
	require.NoError(t, err)
	require.NotNil(t, conn)
	conn.Close()
}

func TestAC6_ClientmTLSCertProvided(t *testing.T) {
	caCert, caKey, caPEM, _ := generateTestCert(t, true, "test-ca", nil, nil)
	_, _, serverCertPEM, serverKeyPEM := generateTestCert(t, false, "localhost", caCert, caKey)
	_, _, clientCertPEM, clientKeyPEM := generateTestCert(t, false, "test-client", caCert, caKey)

	caPath := writeTempFile(t, caPEM)
	serverCertPath := writeTempFile(t, serverCertPEM)
	serverKeyPath := writeTempFile(t, serverKeyPEM)
	clientCertPath := writeTempFile(t, clientCertPEM)
	clientKeyPath := writeTempFile(t, clientKeyPEM)

	// Start mTLS server
	serverTLSConfig := ServerTLSConfig{
		Enabled:              true,
		CertPath:             serverCertPath,
		KeyPath:              serverKeyPath,
		ClientAuthRequired:   true,
		CACertPath:           caPath,
	}
	srv, err := NewGRPCServer(serverTLSConfig)
	require.NoError(t, err)
	healthpb.RegisterHealthServer(srv, healthpb.NewHealthServer())

	lis, err := net.Listen("tcp", "127.0.0.1:0")
	require.NoError(t, err)
	defer lis.Close()

	go func() {
		_ = srv.Serve(lis)
	}()
	defer srv.Stop()

	// Connect with client mTLS
	clientCfg := ClientTLSConfig{
		Enabled:          true,
		CACertPath:       caPath,
		ClientCertPath:   clientCertPath,
		ClientKeyPath:    clientKeyPath,
	}
	conn, err := NewGRPCClientConn(lis.Addr().String(), clientCfg)
	require.NoError(t, err)
	require.NotNil(t, conn)
	conn.Close()
}

func TestAC7_MissingTLSParamsFailsStartup(t *testing.T) {
	// Test missing server cert/key
	cfg := ServerTLSConfig{Enabled: true}
	srv, err := NewGRPCServer(cfg)
	assert.ErrorIs(t, err, ErrMissingTLSCert)
	assert.Nil(t, srv)

	// Test missing CA when client auth required
	_, _, certPEM, keyPEM := generateTestCert(t, false, "localhost", nil, nil)
	certPath := writeTempFile(t, certPEM)
	keyPath := writeTempFile(t, keyPEM)

	cfg = ServerTLSConfig{
		Enabled:              true,
		CertPath:             certPath,
		KeyPath:              keyPath,
		ClientAuthRequired:   true,
	}
	srv, err = NewGRPCServer(cfg)
	assert.ErrorIs(t, err, ErrMissingClientCA)
	assert.Nil(t, srv)

	// Test missing CA for client TLS
	clientCfg := ClientTLSConfig{Enabled: true}
	conn, err := NewGRPCClientConn("localhost:5000", clientCfg)
	assert.ErrorIs(t, err, ErrMissingTLSCert)
	assert.Nil(t, conn)

	// Test missing client key when cert provided
	caPEM := []byte{}
	caPath := writeTempFile(t, caPEM)
	certPEM = []byte{}
	certPath = writeTempFile(t, certPEM)

	clientCfg = ClientTLSConfig{
		Enabled:          true,
		CACertPath:       caPath,
		ClientCertPath:   certPath,
	}
	conn, err = NewGRPCClientConn("localhost:5000", clientCfg)
	assert.ErrorIs(t, err, ErrMissingClientCert)
	assert.Nil(t, conn)
}

func TestAC8_UnitTestsServerConfigs(t *testing.T) {
	// Covers AC1, AC2, AC3, AC7
	TestAC1_ServerPlaintextDefault(t)
	TestAC2_ServerTLSEnabledValidCerts(t)
	TestAC3_ServermTLSRequiredValidCA(t)
	TestAC7_MissingTLSParamsFailsStartup(t)
}

func TestAC9_UnitTestsClientConfigsAllServices(t *testing.T) {
	// Covers AC4, AC5, AC6
	TestAC4_ClientPlaintextDefault(t)
	TestAC5_ClientTLSEnabledValidCA(t)
	TestAC6_ClientmTLSCertProvided(t)
}

func TestAC10_IntegrationmTLSRejectInvalidClients(t *testing.T) {
	caCert, caKey, caPEM, _ := generateTestCert(t, true, "test-ca", nil, nil)
	_, _, serverCertPEM, serverKeyPEM := generateTestCert(t, false, "localhost", caCert, caKey)
	// Generate invalid client cert from a different CA
	wrongCaCert, wrongCaKey, _, _ := generateTestCert(t, true, "wrong-ca", nil, nil)
	_, _, invalidClientCertPEM, invalidClientKeyPEM := generateTestCert(t, false, "bad-client", wrongCaCert, wrongCaKey)
	_, _, validClientCertPEM, validClientKeyPEM := generateTestCert(t, false, "good-client", caCert, caKey)

	caPath := writeTempFile(t, caPEM)
	serverCertPath := writeTempFile(t, serverCertPEM)
	serverKeyPath := writeTempFile(t, serverKeyPEM)
	invalidCertPath := writeTempFile(t, invalidClientCertPEM)
	invalidKeyPath := writeTempFile(t, invalidClientKeyPEM)
	validCertPath := writeTempFile(t, validClientCertPEM)
	validKeyPath := writeTempFile(t, validClientKeyPEM)

	// Start mTLS server
	serverTLSConfig := ServerTLSConfig{
		Enabled:              true,
		CertPath:             serverCertPath,
		KeyPath:              serverKeyPath,
		ClientAuthRequired:   true,
		CACertPath:           caPath,
	}
	srv, err := NewGRPCServer(serverTLSConfig)
	require.NoError(t, err)
	healthpb.RegisterHealthServer(srv, healthpb.NewHealthServer())

	lis, err := net.Listen("tcp", "127.0.0.1:0")
	require.NoError(t, err)
	defer lis.Close()

	go func() {
		_ = srv.Serve(lis)
	}()
	defer srv.Stop()

	// Test connection with no client cert should fail
	clientCfgNoCert := ClientTLSConfig{
		Enabled:    true,
		CACertPath: caPath,
	}
	conn, err := NewGRPCClientConn(lis.Addr().String(), clientCfgNoCert, grpc.WithBlock(), grpc.WithTimeout(time.Second))
	assert.Error(t, err)
	if conn != nil {
		conn.Close()
	}

	// Test connection with invalid client cert should fail
	clientCfgInvalidCert := ClientTLSConfig{
		Enabled:          true,
		CACertPath:       caPath,
		ClientCertPath:   invalidCertPath,
		ClientKeyPath:    invalidKeyPath,
	}
	conn, err = NewGRPCClientConn(lis.Addr().String(), clientCfgInvalidCert, grpc.WithBlock(), grpc.WithTimeout(time.Second))
	assert.Error(t, err)
	if conn != nil {
		conn.Close()
	}

	// Test connection with valid client cert should succeed
	clientCfgValidCert := ClientTLSConfig{
		Enabled:          true,
		CACertPath:       caPath,
		ClientCertPath:   validCertPath,
		ClientKeyPath:    validKeyPath,
	}
	conn, err = NewGRPCClientConn(lis.Addr().String(), clientCfgValidCert, grpc.WithBlock(), grpc.WithTimeout(time.Second))
	assert.NoError(t, err)
	if conn != nil {
		client := healthpb.NewHealthClient(conn)
		resp, err := client.Check(context.Background(), &healthpb.HealthCheckRequest{})
		assert.NoError(t, err)
		assert.Equal(t, healthpb.HealthCheckResponse_SERVING, resp.Status)
		conn.Close()
	}
}
