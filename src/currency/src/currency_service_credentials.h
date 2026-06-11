#pragma once

#include <grpcpp/grpcpp.h>
#include <memory>

// Builds appropriate gRPC server credentials based on environment variable configuration
// Returns:
// - `grpc::InsecureServerCredentials()` when no TLS env vars are set (default plaintext mode)
// - `grpc::TlsServerCredentials()` with client auth disabled when only TLS cert/key are set
// - `grpc::TlsServerCredentials()` with client auth required when TLS cert/key + CA cert are set
// Throws: `std::runtime_error` with descriptive message on invalid configuration (missing/unreadable/invalid certificates, mismatched cert/key)
std::shared_ptr<grpc::ServerCredentials> BuildCurrencyServiceCredentials();
