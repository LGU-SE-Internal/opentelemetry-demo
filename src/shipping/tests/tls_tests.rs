use actix_web::{test, web, App, HttpResponse};
use std::env;
use std::fs;
use std::process::Command;
use common::spawn_app;

// Test AC-1: No TLS env vars set -> service starts on HTTP
#[actix_web::test]
async fn test_ac1_no_tls_vars_starts_http() {
    // Unset all TLS env vars
    env::remove_var("SHIPPING_SERVICE_TLS_CERT_PATH");
    env::remove_var("SHIPPING_SERVICE_TLS_KEY_PATH");
    env::remove_var("SHIPPING_SERVICE_MTLS_CA_CERT_PATH");

    // Spawn app
    let app = spawn_app().await;
    
    // Test HTTP request succeeds
    let resp = reqwest::get(&format!("http://{}", app.address))
        .await
        .expect("HTTP request should succeed");
    assert!(resp.status().is_success());
    
    // Test HTTPS request fails (no TLS configured)
    let https_resp = reqwest::get(&format!("https://{}", app.address)).await;
    assert!(https_resp.is_err());
}

// Test AC-2: Valid TLS cert and key set -> service accepts HTTPS only
#[actix_web::test]
async fn test_ac2_valid_tls_vars_accepts_https_only() {
    // Set valid TLS paths (we'll use test certs that exist in test data)
    env::set_var("SHIPPING_SERVICE_TLS_CERT_PATH", "./test_data/cert.pem");
    env::set_var("SHIPPING_SERVICE_TLS_KEY_PATH", "./test_data/key.pem");
    env::remove_var("SHIPPING_SERVICE_MTLS_CA_CERT_PATH");

    let app = spawn_app().await;

    // Test HTTP request is rejected
    let http_resp = reqwest::get(&format!("http://{}", app.address)).await;
    assert!(http_resp.is_err());

    // Test HTTPS request succeeds
    let client = reqwest::Client::builder()
        .danger_accept_invalid_certs(true) // Test cert is self-signed
        .build()
        .unwrap();
    let resp = client.get(&format!("https://{}", app.address))
        .send()
        .await
        .expect("HTTPS request should succeed");
    assert!(resp.status().is_success());
}

// Test AC-3: Only one TLS var set -> service fails to start
#[test]
fn test_ac3_single_tls_var_fails_start() {
    // Only set cert path
    env::set_var("SHIPPING_SERVICE_TLS_CERT_PATH", "./test_data/cert.pem");
    env::remove_var("SHIPPING_SERVICE_TLS_KEY_PATH");
    env::remove_var("SHIPPING_SERVICE_MTLS_CA_CERT_PATH");

    let output = Command::new("cargo")
        .arg("run")
        .current_dir("../")
        .output()
        .expect("Failed to run service");
    
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("MissingTlsComponent") || stderr.contains("both TLS cert and key paths are required"));
}

// Test AC-4: TLS path points to non-existent file -> service fails to start
#[test]
fn test_ac4_invalid_tls_path_fails_start() {
    env::set_var("SHIPPING_SERVICE_TLS_CERT_PATH", "./test_data/nonexistent_cert.pem");
    env::set_var("SHIPPING_SERVICE_TLS_KEY_PATH", "./test_data/key.pem");
    env::remove_var("SHIPPING_SERVICE_MTLS_CA_CERT_PATH");

    let output = Command::new("cargo")
        .arg("run")
        .current_dir("../")
        .output()
        .expect("Failed to run service");
    
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("FileReadError") || stderr.contains("does not exist") || stderr.contains("not readable"));
}

// Test AC-5: Malformed TLS cert/key -> service fails to start
#[test]
fn test_ac5_malformed_tls_data_fails_start() {
    // Create a malformed cert file
    fs::write("/tmp/malformed.pem", "not a valid PEM file").unwrap();
    
    env::set_var("SHIPPING_SERVICE_TLS_CERT_PATH", "/tmp/malformed.pem");
    env::set_var("SHIPPING_SERVICE_TLS_KEY_PATH", "./test_data/key.pem");
    env::remove_var("SHIPPING_SERVICE_MTLS_CA_CERT_PATH");

    let output = Command::new("cargo")
        .arg("run")
        .current_dir("../")
        .output()
        .expect("Failed to run service");
    
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("InvalidCertificate") || stderr.contains("certificate validation error"));
    
    // Cleanup
    let _ = fs::remove_file("/tmp/malformed.pem");
}

// Test AC-6: Valid mTLS CA provided -> requires client cert
#[actix_web::test]
async fn test_ac6_valid_mtls_ca_requires_client_cert() {
    env::set_var("SHIPPING_SERVICE_TLS_CERT_PATH", "./test_data/cert.pem");
    env::set_var("SHIPPING_SERVICE_TLS_KEY_PATH", "./test_data/key.pem");
    env::set_var("SHIPPING_SERVICE_MTLS_CA_CERT_PATH", "./test_data/ca.pem");

    let app = spawn_app().await;

    // Test request without client cert is rejected with 401
    let client = reqwest::Client::builder()
        .danger_accept_invalid_certs(true)
        .build()
        .unwrap();
    let resp = client.get(&format!("https://{}", app.address))
        .send()
        .await
        .expect("Request should send");
    assert_eq!(resp.status(), 401);

    // Test request with valid client cert succeeds
    let client_cert = fs::read("./test_data/client.p12").unwrap();
    let pkcs12 = reqwest::Identity::from_pkcs12_der(&client_cert, "password").unwrap();
    let client = reqwest::Client::builder()
        .identity(pkcs12)
        .danger_accept_invalid_certs(true)
        .build()
        .unwrap();
    let resp = client.get(&format!("https://{}", app.address))
        .send()
        .await
        .expect("Request with client cert should succeed");
    assert!(resp.status().is_success());
}

// Test AC-7: Invalid/malformed mTLS CA -> service fails to start
#[test]
fn test_ac7_invalid_mtls_ca_fails_start() {
    env::set_var("SHIPPING_SERVICE_TLS_CERT_PATH", "./test_data/cert.pem");
    env::set_var("SHIPPING_SERVICE_TLS_KEY_PATH", "./test_data/key.pem");
    env::set_var("SHIPPING_SERVICE_MTLS_CA_CERT_PATH", "./test_data/nonexistent_ca.pem");

    let output = Command::new("cargo")
        .arg("run")
        .current_dir("../")
        .output()
        .expect("Failed to run service");
    
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("InvalidCaCertificate") || stderr.contains("CA certificate validation error"));
}

// Test AC-8: API endpoints return identical responses over HTTPS as HTTP
#[actix_web::test]
async fn test_ac8_https_responses_match_http() {
    // First test HTTP response
    env::remove_var("SHIPPING_SERVICE_TLS_CERT_PATH");
    env::remove_var("SHIPPING_SERVICE_TLS_KEY_PATH");
    env::remove_var("SHIPPING_SERVICE_MTLS_CA_CERT_PATH");
    let app_http = spawn_app().await;
    let http_resp = reqwest::get(&format!("http://{}/health", app_http.address))
        .await
        .unwrap();
    let http_status = http_resp.status();
    let http_body = http_resp.text().await.unwrap();

    // Then test HTTPS response
    env::set_var("SHIPPING_SERVICE_TLS_CERT_PATH", "./test_data/cert.pem");
    env::set_var("SHIPPING_SERVICE_TLS_KEY_PATH", "./test_data/key.pem");
    let app_https = spawn_app().await;
    let client = reqwest::Client::builder()
        .danger_accept_invalid_certs(true)
        .build()
        .unwrap();
    let https_resp = client.get(&format!("https://{}/health", app_https.address))
        .send()
        .await
        .unwrap();
    let https_status = https_resp.status();
    let https_body = https_resp.text().await.unwrap();

    // Compare responses
    assert_eq!(http_status, https_status);
    assert_eq!(http_body, https_body);
}
