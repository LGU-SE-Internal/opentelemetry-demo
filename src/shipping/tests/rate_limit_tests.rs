use axum::{
    body::Body,
    http::{header, Request, StatusCode},
};
use serde_json::Value;
use std::env;
use tower::ServiceExt;

mod common;
use common::create_test_app;

#[tokio::test]
async fn test_ac1_rate_limit_exceeded_returns_429() {
    // Arrange
    let app = create_test_app().await;
    let client_ip = "192.168.1.1";
    let rpm = 60; // default

    // Act: send more than 60 requests to public endpoint
    for _ in 0..rpm {
        let req = Request::builder()
            .uri("/shipping/get-rate")
            .method("POST")
            .header("X-Forwarded-For", client_ip)
            .body(Body::empty())
            .unwrap();
        let resp = app.clone().oneshot(req).await.unwrap();
        assert_ne!(resp.status(), StatusCode::TOO_MANY_REQUESTS);
    }

    // Next request should be rate limited
    let req = Request::builder()
        .uri("/shipping/get-rate")
        .method("POST")
        .header("X-Forwarded-For", client_ip)
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();

    // Assert
    assert_eq!(resp.status(), StatusCode::TOO_MANY_REQUESTS);
}

#[tokio::test]
async fn test_ac2_custom_rate_limit_configured_via_env_var() {
    // Arrange
    let custom_rpm = 10;
    env::set_var("SHIPPING_RATE_LIMIT_RPM", custom_rpm.to_string());
    let app = create_test_app().await;
    let client_ip = "192.168.1.2";

    // Act: send custom_rpm requests
    for _ in 0..custom_rpm {
        let req = Request::builder()
            .uri("/shipping/ship-order")
            .method("POST")
            .header("X-Forwarded-For", client_ip)
            .body(Body::empty())
            .unwrap();
        let resp = app.clone().oneshot(req).await.unwrap();
        assert_ne!(resp.status(), StatusCode::TOO_MANY_REQUESTS);
    }

    // Next request should be rate limited
    let req = Request::builder()
        .uri("/shipping/ship-order")
        .method("POST")
        .header("X-Forwarded-For", client_ip)
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();

    // Assert
    assert_eq!(resp.status(), StatusCode::TOO_MANY_REQUESTS);
    env::remove_var("SHIPPING_RATE_LIMIT_RPM");
}

#[tokio::test]
async fn test_ac3_429_response_has_retry_after_header() {
    // Arrange
    let app = create_test_app().await;
    let client_ip = "192.168.1.3";
    let rpm = 60;

    // Exhaust rate limit
    for _ in 0..rpm {
        let req = Request::builder()
            .uri("/shipping/get-rate")
            .method("POST")
            .header("X-Forwarded-For", client_ip)
            .body(Body::empty())
            .unwrap();
        let _ = app.clone().oneshot(req).await.unwrap();
    }

    // Act
    let req = Request::builder()
        .uri("/shipping/get-rate")
        .method("POST")
        .header("X-Forwarded-For", client_ip)
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();

    // Assert
    assert_eq!(resp.status(), StatusCode::TOO_MANY_REQUESTS);
    let retry_after = resp.headers().get(header::RETRY_AFTER);
    assert!(retry_after.is_some());
    let retry_after_val = retry_after.unwrap().to_str().unwrap();
    let retry_after_sec = retry_after_val.parse::<u64>().unwrap();
    assert!(retry_after_sec > 0 && retry_after_sec <= 60);

    // Check response body matches spec
    let body = hyper::body::to_bytes(resp.into_body()).await.unwrap();
    let json: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(json["error"], "Too many requests");
    assert_eq!(json["message"], "Rate limit exceeded. Try again later.");
    assert_eq!(json["retry_after"], retry_after_sec);
}

#[tokio::test]
async fn test_ac4_rate_limited_requests_metric_increments() {
    // Arrange
    let app = create_test_app().await;
    let client_ip = "192.168.1.4";
    let endpoint = "/shipping/ship-order";
    let rpm = 60;

    // Exhaust rate limit
    for _ in 0..rpm {
        let req = Request::builder()
            .uri(endpoint)
            .method("POST")
            .header("X-Forwarded-For", client_ip)
            .body(Body::empty())
            .unwrap();
        let _ = app.clone().oneshot(req).await.unwrap();
    }

    // Get initial metric value
    let metrics_resp = app.clone().oneshot(
        Request::builder().uri("/metrics").body(Body::empty()).unwrap()
    ).await.unwrap();
    let metrics_body = hyper::body::to_bytes(metrics_resp.into_body()).await.unwrap();
    let initial_count = get_metric_value(&metrics_body, "shipping_rate_limited_requests_total", client_ip, endpoint);

    // Trigger rate limit
    let req = Request::builder()
        .uri(endpoint)
        .method("POST")
        .header("X-Forwarded-For", client_ip)
        .body(Body::empty())
        .unwrap();
    let _ = app.clone().oneshot(req).await.unwrap();

    // Get updated metric value
    let metrics_resp = app.clone().oneshot(
        Request::builder().uri("/metrics").body(Body::empty()).unwrap()
    ).await.unwrap();
    let metrics_body = hyper::body::to_bytes(metrics_resp.into_body()).await.unwrap();
    let final_count = get_metric_value(&metrics_body, "shipping_rate_limited_requests_total", client_ip, endpoint);

    // Assert
    assert_eq!(final_count, initial_count + 1);
}

#[tokio::test]
async fn test_ac5_internal_endpoints_not_rate_limited() {
    // Arrange
    let app = create_test_app().await;
    let client_ip = "192.168.1.5";
    let rpm = 60;

    // Act: send more than rpm requests to internal endpoint (e.g. /health, /metrics, internal admin endpoints)
    for _ in 0..rpm * 2 {
        let req = Request::builder()
            .uri("/health")
            .method("GET")
            .header("X-Forwarded-For", client_ip)
            .body(Body::empty())
            .unwrap();
        let resp = app.clone().oneshot(req).await.unwrap();
        assert_ne!(resp.status(), StatusCode::TOO_MANY_REQUESTS);
        assert_eq!(resp.status(), StatusCode::OK);
    }

    // Verify public endpoint still works normally for this IP (rate limit not hit on internal)
    let req = Request::builder()
        .uri("/shipping/get-rate")
        .method("POST")
        .header("X-Forwarded-For", client_ip)
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_ne!(resp.status(), StatusCode::TOO_MANY_REQUESTS);
}

#[tokio::test]
async fn test_ac6_invalid_rate_limit_config_causes_start_failure() {
    // Test non-positive integer
    env::set_var("SHIPPING_RATE_LIMIT_RPM", "0");
    let result = std::panic::catch_unwind(|| {
        tokio::runtime::Runtime::new().unwrap().block_on(create_test_app());
    });
    assert!(result.is_err());
    env::remove_var("SHIPPING_RATE_LIMIT_RPM");

    // Test negative integer
    env::set_var("SHIPPING_RATE_LIMIT_RPM", "-10");
    let result = std::panic::catch_unwind(|| {
        tokio::runtime::Runtime::new().unwrap().block_on(create_test_app());
    });
    assert!(result.is_err());
    env::remove_var("SHIPPING_RATE_LIMIT_RPM");

    // Test non-numeric value
    env::set_var("SHIPPING_RATE_LIMIT_RPM", "invalid");
    let result = std::panic::catch_unwind(|| {
        tokio::runtime::Runtime::new().unwrap().block_on(create_test_app());
    });
    assert!(result.is_err());
    env::remove_var("SHIPPING_RATE_LIMIT_RPM");
}

// Helper function to extract metric value from prometheus text format
fn get_metric_value(metrics_body: &[u8], metric_name: &str, client_ip: &str, endpoint: &str) -> u64 {
    let body_str = String::from_utf8_lossy(metrics_body);
    for line in body_str.lines() {
        if line.starts_with(metric_name) {
            if line.contains(&format!("client_ip=\"{}\"", client_ip)) && line.contains(&format!("endpoint=\"{}\"", endpoint)) {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() >= 2 {
                    return parts[1].parse().unwrap_or(0);
                }
            }
        }
    }
    0
}
