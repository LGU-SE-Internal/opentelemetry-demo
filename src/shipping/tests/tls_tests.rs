//! Integration tests for TLS/mTLS functionality of the shipping service

use std::process::Command;
use std::time::Duration;
use reqwest::{Client, Certificate, Identity};
use tokio::time::sleep;
use axum_server::tls_rustls::RustlsConfig;

mod common;
use common::TestContext;

const TEST_PORT: u16 = 8080;
const HEALTH_ENDPOINT: &str = "/health";

/// AC-1: TLS disabled (default) - plain HTTP works, no TLS handshake
#[tokio::test]
async fn test_ac1_tls_disabled_plain_http_works() {
    let ctx = TestContext::new();
    ctx.set_env("SHIPPING_SERVICE_TLS_ENABLED", "false");
    
    let mut server = ctx.start_server().await;
    
    // Send plain HTTP request
    let client = Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();
    
    let resp = client.get(format!("http://localhost:{}{}", TEST_PORT, HEALTH_ENDPOINT))
        .send()
        .await;
    
    assert!(resp.is_ok());
    assert_eq!(resp.unwrap().status(), 200);
    
    // Verify HTTPS requests fail (no TLS listener)
    let tls_resp = client.get(format!("https://localhost:{}{}", TEST_PORT, HEALTH_ENDPOINT))
        .send()
        .await;
    assert!(tls_resp.is_err());
    
    server.kill().await;
}

/// AC-2: TLS enabled with valid cert/key - HTTPS works, HTTP rejected
#[tokio::test]
async fn test_ac2_tls_enabled_https_works() {
    let ctx = TestContext::new();
    ctx.set_env("SHIPPING_SERVICE_TLS_ENABLED", "true");
    ctx.set_env("SHIPPING_SERVICE_TLS_CERT_PATH", ctx.test_cert_path().to_str().unwrap());
    ctx.set_env("SHIPPING_SERVICE_TLS_KEY_PATH", ctx.test_key_path().to_str().unwrap());
    ctx.set_env("SHIPPING_SERVICE_MTLS_ENABLED", "false");
    
    let mut server = ctx.start_server().await;
    sleep(Duration::from_secs(1)).await; // Give server time to start
    
    // Send HTTPS request with valid cert verification
    let root_cert = Certificate::from_pem(&std::fs::read(ctx.test_cert_path()).unwrap()).unwrap();
    let client = Client::builder()
        .add_root_certificate(root_cert)
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();
    
    let resp = client.get(format!("https://localhost:{}{}", TEST_PORT, HEALTH_ENDPOINT))
        .send()
        .await;
    
    assert!(resp.is_ok());
    assert_eq!(resp.unwrap().status(), 200);
    
    server.kill().await;
}

/// AC-2: TLS enabled - plain HTTP requests are rejected
#[tokio::test]
async fn test_ac2_tls_enabled_http_rejected() {
    let ctx = TestContext::new();
    ctx.set_env("SHIPPING_SERVICE_TLS_ENABLED", "true");
    ctx.set_env("SHIPPING_SERVICE_TLS_CERT_PATH", ctx.test_cert_path().to_str().unwrap());
    ctx.set_env("SHIPPING_SERVICE_TLS_KEY_PATH", ctx.test_key_path().to_str().unwrap());
    
    let mut server = ctx.start_server().await;
    sleep(Duration::from_secs(1)).await;
    
    // Plain HTTP request should fail
    let client = Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();
    
    let resp = client.get(format!("http://localhost:{}{}", TEST_PORT, HEALTH_ENDPOINT))
        .send()
        .await;
    
    assert!(resp.is_err());
    
    server.kill().await;
}

/// AC-3: TLS enabled with missing cert path - service exits with code 1 and logs error
#[tokio::test]
async fn test_ac3_tls_enabled_missing_cert_exits_with_error() {
    let ctx = TestContext::new();
    ctx.set_env("SHIPPING_SERVICE_TLS_ENABLED", "true");
    ctx.set_env("SHIPPING_SERVICE_TLS_CERT_PATH", "/non/existent/cert.pem");
    ctx.set_env("SHIPPING_SERVICE_TLS_KEY_PATH", ctx.test_key_path().to_str().unwrap());
    
    let status = ctx.start_server_get_exit_status().await;
    
    assert!(status.is_some());
    assert_eq!(status.unwrap(), 1);
    assert!(ctx.server_logs().await.contains("Failed to load TLS certificates: /non/existent/cert.pem"));
}

/// AC-4: mTLS enabled - valid client certificate works
#[tokio::test]
async fn test_ac4_mtls_enabled_valid_client_cert_works() {
    let ctx = TestContext::new();
    ctx.set_env("SHIPPING_SERVICE_TLS_ENABLED", "true");
    ctx.set_env("SHIPPING_SERVICE_TLS_CERT_PATH", ctx.test_cert_path().to_str().unwrap());
    ctx.set_env("SHIPPING_SERVICE_TLS_KEY_PATH", ctx.test_key_path().to_str().unwrap());
    ctx.set_env("SHIPPING_SERVICE_MTLS_ENABLED", "true");
    ctx.set_env("SHIPPING_SERVICE_TLS_CA_CERT_PATH", ctx.test_ca_cert_path().to_str().unwrap());
    
    let mut server = ctx.start_server().await;
    sleep(Duration::from_secs(1)).await;
    
    // Create client with valid client cert
    let root_cert = Certificate::from_pem(&std::fs::read(ctx.test_cert_path()).unwrap()).unwrap();
    let client_cert = std::fs::read(ctx.test_client_cert_path()).unwrap();
    let client_identity = Identity::from_pem(&client_cert).unwrap();
    
    let client = Client::builder()
        .add_root_certificate(root_cert)
        .identity(client_identity)
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();
    
    let resp = client.get(format!("https://localhost:{}{}", TEST_PORT, HEALTH_ENDPOINT))
        .send()
        .await;
    
    assert!(resp.is_ok());
    assert_eq!(resp.unwrap().status(), 200);
    
    server.kill().await;
}

/// AC-4: mTLS enabled - no client certificate returns 401
#[tokio::test]
async fn test_ac4_mtls_enabled_no_client_cert_returns_401() {
    let ctx = TestContext::new();
    ctx.set_env("SHIPPING_SERVICE_TLS_ENABLED", "true");
    ctx.set_env("SHIPPING_SERVICE_TLS_CERT_PATH", ctx.test_cert_path().to_str().unwrap());
    ctx.set_env("SHIPPING_SERVICE_TLS_KEY_PATH", ctx.test_key_path().to_str().unwrap());
    ctx.set_env("SHIPPING_SERVICE_MTLS_ENABLED", "true");
    ctx.set_env("SHIPPING_SERVICE_TLS_CA_CERT_PATH", ctx.test_ca_cert_path().to_str().unwrap());
    
    let mut server = ctx.start_server().await;
    sleep(Duration::from_secs(1)).await;
    
    // Client without certificate
    let root_cert = Certificate::from_pem(&std::fs::read(ctx.test_cert_path()).unwrap()).unwrap();
    let client = Client::builder()
        .add_root_certificate(root_cert)
        .timeout(Duration::from_secs(2))
        .danger_accept_invalid_certs(true) // Bypass cert check since handshake will fail early
        .build()
        .unwrap();
    
    let resp = client.get(format!("https://localhost:{}{}", TEST_PORT, HEALTH_ENDPOINT))
        .send()
        .await;
    
    // Should get 401 or connection error due to TLS rejection
    assert!(resp.is_err() || resp.unwrap().status() == 401);
    
    server.kill().await;
}

/// AC-4: mTLS enabled - untrusted client certificate returns 401
#[tokio::test]
async fn test_ac4_mtls_enabled_untrusted_client_cert_returns_401() {
    let ctx = TestContext::new();
    ctx.set_env("SHIPPING_SERVICE_TLS_ENABLED", "true");
    ctx.set_env("SHIPPING_SERVICE_TLS_CERT_PATH", ctx.test_cert_path().to_str().unwrap());
    ctx.set_env("SHIPPING_SERVICE_TLS_KEY_PATH", ctx.test_key_path().to_str().unwrap());
    ctx.set_env("SHIPPING_SERVICE_MTLS_ENABLED", "true");
    ctx.set_env("SHIPPING_SERVICE_TLS_CA_CERT_PATH", ctx.test_ca_cert_path().to_str().unwrap());
    
    let mut server = ctx.start_server().await;
    sleep(Duration::from_secs(1)).await;
    
    // Client with untrusted cert
    let root_cert = Certificate::from_pem(&std::fs::read(ctx.test_cert_path()).unwrap()).unwrap();
    let untrusted_cert = std::fs::read(ctx.test_untrusted_client_cert_path()).unwrap();
    let client_identity = Identity::from_pem(&untrusted_cert).unwrap();
    
    let client = Client::builder()
        .add_root_certificate(root_cert)
        .identity(client_identity)
        .timeout(Duration::from_secs(2))
        .danger_accept_invalid_certs(true)
        .build()
        .unwrap();
    
    let resp = client.get(format!("https://localhost:{}{}", TEST_PORT, HEALTH_ENDPOINT))
        .send()
        .await;
    
    assert!(resp.is_err() || resp.unwrap().status() == 401);
    
    server.kill().await;
}

/// AC-5: mTLS disabled - no client certificate works
#[tokio::test]
async fn test_ac5_mtls_disabled_no_client_cert_works() {
    let ctx = TestContext::new();
    ctx.set_env("SHIPPING_SERVICE_TLS_ENABLED", "true");
    ctx.set_env("SHIPPING_SERVICE_TLS_CERT_PATH", ctx.test_cert_path().to_str().unwrap());
    ctx.set_env("SHIPPING_SERVICE_TLS_KEY_PATH", ctx.test_key_path().to_str().unwrap());
    ctx.set_env("SHIPPING_SERVICE_MTLS_ENABLED", "false");
    
    let mut server = ctx.start_server().await;
    sleep(Duration::from_secs(1)).await;
    
    // Client without certificate should work
    let root_cert = Certificate::from_pem(&std::fs::read(ctx.test_cert_path()).unwrap()).unwrap();
    let client = Client::builder()
        .add_root_certificate(root_cert)
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();
    
    let resp = client.get(format!("https://localhost:{}{}", TEST_PORT, HEALTH_ENDPOINT))
        .send()
        .await;
    
    assert!(resp.is_ok());
    assert_eq!(resp.unwrap().status(), 200);
    
    server.kill().await;
}
