// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"crypto/tls"
	"crypto/x509"
	"errors"
	"fmt"
	"os"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
)

var (
	ErrMissingTLSCert      = errors.New("missing TLS certificate or key path")
	ErrInvalidCertificate  = errors.New("invalid or expired TLS certificate")
	ErrMissingClientCA     = errors.New("missing CA certificate path for client authentication")
	ErrMissingClientCert   = errors.New("missing client certificate or key path for mTLS authentication")
)

type ServerTLSConfig struct {
	Enabled              bool
	CertPath             string
	KeyPath              string
	ClientAuthRequired   bool
	CACertPath           string
}

type ClientTLSConfig struct {
	Enabled              bool
	CACertPath           string
	ClientCertPath       string
	ClientKeyPath        string
}

type TLSConfigParams struct {
	CertPath             string
	KeyPath              string
	CACertPath           string
	ClientAuth           tls.ClientAuthType
}

// LoadTLSConfig loads and parses TLS certificates from filesystem paths
func LoadTLSConfig(cfg TLSConfigParams) (*tls.Config, error) {
	tlsConfig := &tls.Config{
		MinVersion: tls.VersionTLS12,
	}

	// Load server certificate if provided
	if cfg.CertPath != "" && cfg.KeyPath != "" {
		cert, err := tls.LoadX509KeyPair(cfg.CertPath, cfg.KeyPath)
		if err != nil {
			return nil, fmt.Errorf("%w: %v", ErrInvalidCertificate, err)
		}
		tlsConfig.Certificates = []tls.Certificate{cert}
	}

	// Load CA certificates if provided
	if cfg.CACertPath != "" {
		caCert, err := os.ReadFile(cfg.CACertPath)
		if err != nil {
			return nil, fmt.Errorf("failed to read CA certificate: %w", err)
		}

		caCertPool := x509.NewCertPool()
		if !caCertPool.AppendCertsFromPEM(caCert) {
			return nil, fmt.Errorf("%w: failed to parse CA certificate", ErrInvalidCertificate)
		}

		if cfg.ClientAuth != tls.NoClientCert {
			tlsConfig.ClientCAs = caCertPool
		} else {
			tlsConfig.RootCAs = caCertPool
		}
	}

	tlsConfig.ClientAuth = cfg.ClientAuth
	return tlsConfig, nil
}

// NewGRPCServer creates a gRPC server with configured TLS/mTLS settings
func NewGRPCServer(cfg ServerTLSConfig, opts ...grpc.ServerOption) (*grpc.Server, error) {
	if !cfg.Enabled {
		return grpc.NewServer(opts...), nil
	}

	// Validate required TLS parameters
	if cfg.CertPath == "" || cfg.KeyPath == "" {
		return nil, ErrMissingTLSCert
	}

	tlsParams := TLSConfigParams{
		CertPath: cfg.CertPath,
		KeyPath:  cfg.KeyPath,
		ClientAuth: tls.NoClientCert,
	}

	if cfg.ClientAuthRequired {
		if cfg.CACertPath == "" {
			return nil, ErrMissingClientCA
		}
		tlsParams.ClientAuth = tls.RequireAndVerifyClientCert
		tlsParams.CACertPath = cfg.CACertPath
	}

	tlsConfig, err := LoadTLSConfig(tlsParams)
	if err != nil {
		return nil, err
	}

	creds := credentials.NewTLS(tlsConfig)
	opts = append(opts, grpc.Creds(creds))
	return grpc.NewServer(opts...), nil
}

// NewGRPCClientConn creates a gRPC client connection with configured TLS settings
func NewGRPCClientConn(target string, cfg ClientTLSConfig, opts ...grpc.DialOption) (*grpc.ClientConn, error) {
	if !cfg.Enabled {
		opts = append(opts, grpc.WithTransportCredentials(insecure.NewCredentials()))
		return grpc.NewClient(target, opts...)
	}

	if cfg.CACertPath == "" {
		return nil, fmt.Errorf("%w: CA certificate path is required for TLS client connections", ErrMissingTLSCert)
	}

	tlsParams := TLSConfigParams{
		CACertPath: cfg.CACertPath,
		ClientAuth: tls.NoClientCert,
	}

	// Add client cert if provided for mTLS
	if cfg.ClientCertPath != "" {
		if cfg.ClientKeyPath == "" {
			return nil, ErrMissingClientCert
		}
		tlsParams.CertPath = cfg.ClientCertPath
		tlsParams.KeyPath = cfg.ClientKeyPath
	}

	tlsConfig, err := LoadTLSConfig(tlsParams)
	if err != nil {
		return nil, err
	}

	creds := credentials.NewTLS(tlsConfig)
	opts = append(opts, grpc.WithTransportCredentials(creds))
	return grpc.NewClient(target, opts...)
}
