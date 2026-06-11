#include "currency_service_credentials.h"
#include <cstdlib>
#include <fstream>
#include <stdexcept>
#include <grpcpp/security/tls_credentials_options.h>

using grpc::experimental::TlsServerCredentialsOptions;
using grpc::experimental::IdentityKeyCertPair;
using grpc::experimental::TlsKeyMaterialsConfig;
using grpc::experimental::TlsServerAuthorizationCheckConfig;

std::string read_file(const std::string& path) {
    std::ifstream file(path);
    if (!file.is_open()) {
        throw std::runtime_error("Failed to open file: " + path);
    }
    return std::string((std::istreambuf_iterator<char>(file)), std::istreambuf_iterator<char>());
}

std::shared_ptr<grpc::ServerCredentials> BuildCurrencyServiceCredentials() {
    const char* tls_cert_path = std::getenv("OTEL_CPP_CURRENCY_SERVICE_TLS_CERT_PATH");
    const char* tls_key_path = std::getenv("OTEL_CPP_CURRENCY_SERVICE_TLS_KEY_PATH");
    const char* tls_ca_path = std::getenv("OTEL_CPP_CURRENCY_SERVICE_TLS_CA_CERT_PATH");

    // Case 1: No TLS env vars set - return insecure credentials (default plaintext)
    if (!tls_cert_path && !tls_key_path && !tls_ca_path) {
        return grpc::InsecureServerCredentials();
    }

    // Validate that both cert and key are set if either is set
    if ((tls_cert_path && !tls_key_path) || (!tls_cert_path && tls_key_path)) {
        throw std::runtime_error("Both OTEL_CPP_CURRENCY_SERVICE_TLS_CERT_PATH and OTEL_CPP_CURRENCY_SERVICE_TLS_KEY_PATH must be set to enable TLS");
    }

    // Read server cert and key
    std::string cert_content = read_file(tls_cert_path);
    std::string key_content = read_file(tls_key_path);

    if (cert_content.empty() || key_content.empty()) {
        throw std::runtime_error("TLS certificate or key file is empty");
    }

    IdentityKeyCertPair key_cert_pair;
    key_cert_pair.private_key = key_content;
    key_cert_pair.certificate_chain = cert_content;

    TlsServerCredentialsOptions options;
    auto key_materials_config = std::make_shared<TlsKeyMaterialsConfig>();
    key_materials_config->set_pem_key_cert_pair_list({key_cert_pair});

    // Disable TLS 1.0 and 1.1, only allow TLS 1.2+
    options.set_min_tls_version(GRPC_TLS1_2);
    options.set_max_tls_version(GRPC_TLS1_3);

    if (tls_ca_path) {
        // mTLS mode: require client certificate authentication
        std::string ca_content = read_file(tls_ca_path);
        if (ca_content.empty()) {
            throw std::runtime_error("CA certificate file is empty");
        }
        key_materials_config->set_pem_root_certs(ca_content);
        options.set_cert_request_type(GRPC_SSL_REQUEST_AND_REQUIRE_CLIENT_CERTIFICATE_AND_VERIFY);
    } else {
        // TLS only mode: no client auth required
        options.set_cert_request_type(GRPC_SSL_DONT_REQUEST_CLIENT_CERTIFICATE);
    }

    options.set_tls_key_materials_config(key_materials_config);
    options.set_server_authorization_check_config(std::make_shared<TlsServerAuthorizationCheckConfig>());

    auto creds = grpc::experimental::TlsServerCredentials(options);
    if (!creds) {
        throw std::runtime_error("Failed to create TLS server credentials: invalid certificate/key pair or configuration");
    }

    return creds;
}
