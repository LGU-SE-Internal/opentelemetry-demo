use reqwest::Client;
use serde::Deserialize;
use std::time::Duration;
use tokio;
use chrono;

#[derive(Deserialize, Debug)]
struct HealthResponse {
    status: String,
    timestamp: String,
    dependencies: Dependencies,
}

#[derive(Deserialize, Debug)]
struct Dependencies {
    #[serde(rename = "quoteService")]
    quote_service: String,
}

const SHIPPING_SERVICE_URL: &str = "http://localhost:8080";
const HEALTH_ENDPOINT: &str = "/health";

#[tokio::test]
async fn test_ac1_health_returns_200_when_quote_service_reachable() {
    // Setup: Quote service is running and reachable
    let client = Client::new();
    
    let response = client
        .get(&format!("{}{}", SHIPPING_SERVICE_URL, HEALTH_ENDPOINT))
        .timeout(Duration::from_secs(2))
        .send()
        .await
        .expect("Failed to send request to /health endpoint");

    assert_eq!(response.status().as_u16(), 200, "AC1 violated: Expected 200 OK when quote service is reachable");
}

#[tokio::test]
async fn test_ac2_health_returns_503_when_quote_service_unreachable() {
    // Setup: Quote service is stopped/unreachable
    let client = Client::new();
    
    let response = client
        .get(&format!("{}{}", SHIPPING_SERVICE_URL, HEALTH_ENDPOINT))
        .timeout(Duration::from_secs(2))
        .send()
        .await
        .expect("Failed to send request to /health endpoint");

    assert_eq!(response.status().as_u16(), 503, "AC2 violated: Expected 503 Service Unavailable when quote service is unreachable");
}

#[tokio::test]
async fn test_ac3_200_response_has_correct_json_fields() {
    // Setup: Quote service is reachable
    let client = Client::new();
    
    let response = client
        .get(&format!("{}{}", SHIPPING_SERVICE_URL, HEALTH_ENDPOINT))
        .timeout(Duration::from_secs(2))
        .send()
        .await
        .expect("Failed to send request to /health endpoint");

    assert_eq!(response.status().as_u16(), 200);
    
    let body: HealthResponse = response.json().await.expect("Failed to parse response JSON");
    
    assert_eq!(body.status, "healthy", "AC3 violated: Expected status field to be 'healthy'");
    assert_eq!(body.dependencies.quote_service, "healthy", "AC3 violated: Expected dependencies.quoteService to be 'healthy'");
    
    // Verify timestamp is valid RFC3339
    chrono::DateTime::parse_from_rfc3339(&body.timestamp)
        .expect("AC3 violated: Timestamp is not valid RFC3339 format");
}

#[tokio::test]
async fn test_ac4_503_response_has_correct_json_fields() {
    // Setup: Quote service is unreachable
    let client = Client::new();
    
    let response = client
        .get(&format!("{}{}", SHIPPING_SERVICE_URL, HEALTH_ENDPOINT))
        .timeout(Duration::from_secs(2))
        .send()
        .await
        .expect("Failed to send request to /health endpoint");

    assert_eq!(response.status().as_u16(), 503);
    
    let body: HealthResponse = response.json().await.expect("Failed to parse response JSON");
    
    assert_eq!(body.status, "unhealthy", "AC4 violated: Expected status field to be 'unhealthy'");
    assert_eq!(body.dependencies.quote_service, "unhealthy", "AC4 violated: Expected dependencies.quoteService to be 'unhealthy'");
    
    // Verify timestamp is valid RFC3339
    chrono::DateTime::parse_from_rfc3339(&body.timestamp)
        .expect("AC4 violated: Timestamp is not valid RFC3339 format");
}

#[tokio::test]
async fn test_ac5_health_responds_in_under_1_second() {
    let client = Client::new();
    let start = std::time::Instant::now();
    
    let _response = client
        .get(&format!("{}{}", SHIPPING_SERVICE_URL, HEALTH_ENDPOINT))
        .timeout(Duration::from_secs(2))
        .send()
        .await
        .expect("Failed to send request to /health endpoint");

    let duration = start.elapsed();
    assert!(duration <= Duration::from_secs(1), "AC5 violated: Response took {:?}, expected <= 1s", duration);
}

#[tokio::test]
async fn test_ac6_health_accepts_unauthenticated_requests() {
    let client = Client::new();
    
    // Send request without any authentication headers
    let response = client
        .get(&format!("{}{}", SHIPPING_SERVICE_URL, HEALTH_ENDPOINT))
        .timeout(Duration::from_secs(2))
        .send()
        .await
        .expect("Failed to send request to /health endpoint");

    // Should not return 401/403
    assert_ne!(response.status().as_u16(), 401, "AC6 violated: Endpoint requires authentication (returned 401)");
    assert_ne!(response.status().as_u16(), 403, "AC6 violated: Endpoint requires authentication (returned 403)");
}

#[tokio::test]
async fn test_ac7_health_calls_are_idempotent_and_no_side_effects() {
    let client = Client::new();
    
    // Make multiple identical requests
    let response1 = client
        .get(&format!("{}{}", SHIPPING_SERVICE_URL, HEALTH_ENDPOINT))
        .timeout(Duration::from_secs(2))
        .send()
        .await
        .expect("Failed to send first request");
    
    let response2 = client
        .get(&format!("{}{}", SHIPPING_SERVICE_URL, HEALTH_ENDPOINT))
        .timeout(Duration::from_secs(2))
        .send()
        .await
        .expect("Failed to send second request");
    
    // Both responses should be identical (assuming quote service state doesn't change between calls)
    assert_eq!(response1.status(), response2.status(), "AC7 violated: Responses differ between identical calls, indicating side effects");
    
    let body1: HealthResponse = response1.json().await.expect("Failed to parse first response");
    let body2: HealthResponse = response2.json().await.expect("Failed to parse second response");
    
    assert_eq!(body1.status, body2.status, "AC7 violated: Status differs between identical calls, indicating side effects");
    assert_eq!(body1.dependencies.quote_service, body2.dependencies.quote_service, "AC7 violated: Dependency status differs between identical calls, indicating side effects");
}
