use shipping_service::{TlsConfig, load_tls_config_from_env, configure_tls_server, TlsConfigError};
use tonic::transport::Server;
use std::env;
use std::fs::File;
use std::path::PathBuf;
use tempfile::tempdir;

fn reset_env() {
    env::remove_var("SHIPPING_GRPC_TLS_ENABLED");
    env::remove_var("SHIPPING_GRPC_TLS_CA_CERT_PATH");
    env::remove_var("SHIPPING_GRPC_TLS_SERVER_CERT_PATH");
    env::remove_var("SHIPPING_GRPC_TLS_SERVER_KEY_PATH");
    env::remove_var("SHIPPING_GRPC_MTLS_ENABLED");
}

// AC-1: TLS disabled by default, service starts with plaintext server, no certs required
#[test]
fn test_ac1_tls_disabled_no_certs_needed() {
    reset_env();
    let config = load_tls_config_from_env().unwrap();
    assert_eq!(config.enabled, false);
    assert_eq!(config.mtls_enabled, false);
    
    // No error when configuring server with disabled TLS
    let server = Server::builder();
    let result = configure_tls_server(server, &config);
    assert!(result.is_ok());
}

#[test]
fn test_ac1_tls_disabled_false_value() {
    reset_env();
    env::set_var("SHIPPING_GRPC_TLS_ENABLED", "false");
    let config = load_tls_config_from_env().unwrap();
    assert_eq!(config.enabled, false);
}

#[test]
fn test_ac1_tls_disabled_zero_value() {
    reset_env();
    env::set_var("SHIPPING_GRPC_TLS_ENABLED", "0");
    let config = load_tls_config_from_env().unwrap();
    assert_eq!(config.enabled, false);
}

// AC-2: TLS enabled but required path variables missing, service fails to start
#[test]
fn test_ac2_tls_enabled_missing_ca_cert_path() {
    reset_env();
    env::set_var("SHIPPING_GRPC_TLS_ENABLED", "true");
    env::set_var("SHIPPING_GRPC_TLS_SERVER_CERT_PATH", "/tmp/server.crt");
    env::set_var("SHIPPING_GRPC_TLS_SERVER_KEY_PATH", "/tmp/server.key");
    
    let result = load_tls_config_from_env();
    assert!(result.is_err());
    matches!(result.err().unwrap(), TlsConfigError::MissingRequiredVariable(_));
}

#[test]
fn test_ac2_tls_enabled_missing_server_cert_path() {
    reset_env();
    env::set_var("SHIPPING_GRPC_TLS_ENABLED", "true");
    env::set_var("SHIPPING_GRPC_TLS_CA_CERT_PATH", "/tmp/ca.crt");
    env::set_var("SHIPPING_GRPC_TLS_SERVER_KEY_PATH", "/tmp/server.key");
    
    let result = load_tls_config_from_env();
    assert!(result.is_err());
    matches!(result.err().unwrap(), TlsConfigError::MissingRequiredVariable(_));
}

#[test]
fn test_ac2_tls_enabled_missing_server_key_path() {
    reset_env();
    env::set_var("SHIPPING_GRPC_TLS_ENABLED", "true");
    env::set_var("SHIPPING_GRPC_TLS_CA_CERT_PATH", "/tmp/ca.crt");
    env::set_var("SHIPPING_GRPC_TLS_SERVER_CERT_PATH", "/tmp/server.crt");
    
    let result = load_tls_config_from_env();
    assert!(result.is_err());
    matches!(result.err().unwrap(), TlsConfigError::MissingRequiredVariable(_));
}

// AC-3: TLS enabled but certificate files don't exist, service fails to start
#[test]
fn test_ac3_tls_enabled_non_existent_ca_file() {
    reset_env();
    let dir = tempdir().unwrap();
    let server_cert = dir.path().join("server.crt");
    let server_key = dir.path().join("server.key");
    File::create(&server_cert).unwrap();
    File::create(&server_key).unwrap();
    
    env::set_var("SHIPPING_GRPC_TLS_ENABLED", "true");
    env::set_var("SHIPPING_GRPC_TLS_CA_CERT_PATH", dir.path().join("non_existent_ca.crt"));
    env::set_var("SHIPPING_GRPC_TLS_SERVER_CERT_PATH", server_cert);
    env::set_var("SHIPPING_GRPC_TLS_SERVER_KEY_PATH", server_key);
    
    let result = load_tls_config_from_env();
    assert!(result.is_err());
    matches!(result.err().unwrap(), TlsConfigError::FileNotFound(_));
}

#[test]
fn test_ac3_tls_enabled_non_existent_server_cert_file() {
    reset_env();
    let dir = tempdir().unwrap();
    let ca_cert = dir.path().join("ca.crt");
    let server_key = dir.path().join("server.key");
    File::create(&ca_cert).unwrap();
    File::create(&server_key).unwrap();
    
    env::set_var("SHIPPING_GRPC_TLS_ENABLED", "true");
    env::set_var("SHIPPING_GRPC_TLS_CA_CERT_PATH", ca_cert);
    env::set_var("SHIPPING_GRPC_TLS_SERVER_CERT_PATH", dir.path().join("non_existent_server.crt"));
    env::set_var("SHIPPING_GRPC_TLS_SERVER_KEY_PATH", server_key);
    
    let result = load_tls_config_from_env();
    assert!(result.is_err());
    matches!(result.err().unwrap(), TlsConfigError::FileNotFound(_));
}

// AC-4: TLS enabled, all certs valid, mTLS disabled, server accepts TLS without client cert
#[test]
fn test_ac4_tls_enabled_no_mtls_server_config_success() {
    reset_env();
    let dir = tempdir().unwrap();
    let ca_cert = dir.path().join("ca.crt");
    let server_cert = dir.path().join("server.crt");
    let server_key = dir.path().join("server.key");
    File::create(&ca_cert).unwrap();
    File::create(&server_cert).unwrap();
    File::create(&server_key).unwrap();
    
    env::set_var("SHIPPING_GRPC_TLS_ENABLED", "true");
    env::set_var("SHIPPING_GRPC_TLS_CA_CERT_PATH", ca_cert);
    env::set_var("SHIPPING_GRPC_TLS_SERVER_CERT_PATH", server_cert);
    env::set_var("SHIPPING_GRPC_TLS_SERVER_KEY_PATH", server_key);
    env::set_var("SHIPPING_GRPC_MTLS_ENABLED", "false");
    
    let config = load_tls_config_from_env().unwrap();
    assert_eq!(config.enabled, true);
    assert_eq!(config.mtls_enabled, false);
    
    let server = Server::builder();
    let result = configure_tls_server(server, &config);
    assert!(result.is_ok());
}

// AC-5: mTLS enabled, valid certs, server validates client certificates
#[test]
fn test_ac5_mtls_enabled_server_config_success() {
    reset_env();
    let dir = tempdir().unwrap();
    let ca_cert = dir.path().join("ca.crt");
    let server_cert = dir.path().join("server.crt");
    let server_key = dir.path().join("server.key");
    File::create(&ca_cert).unwrap();
    File::create(&server_cert).unwrap();
    File::create(&server_key).unwrap();
    
    env::set_var("SHIPPING_GRPC_TLS_ENABLED", "true");
    env::set_var("SHIPPING_GRPC_TLS_CA_CERT_PATH", ca_cert);
    env::set_var("SHIPPING_GRPC_TLS_SERVER_CERT_PATH", server_cert);
    env::set_var("SHIPPING_GRPC_TLS_SERVER_KEY_PATH", server_key);
    env::set_var("SHIPPING_GRPC_MTLS_ENABLED", "true");
    
    let config = load_tls_config_from_env().unwrap();
    assert_eq!(config.enabled, true);
    assert_eq!(config.mtls_enabled, true);
    
    let server = Server::builder();
    let result = configure_tls_server(server, &config);
    assert!(result.is_ok());
}

// AC-6: No TLS env vars set, behaves exactly as before
#[test]
fn test_ac6_no_tls_env_vars_unchanged_behavior() {
    reset_env();
    let config = load_tls_config_from_env().unwrap();
    assert_eq!(config.enabled, false);
    assert_eq!(config.mtls_enabled, false);
    // No errors, service starts normally
}
