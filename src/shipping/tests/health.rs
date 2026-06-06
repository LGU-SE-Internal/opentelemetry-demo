use reqwest::Client;
use serde::Deserialize;
use std::time::Duration;

#[derive(Deserialize, Debug)]
struct HealthResponse {
    status: String,
    dependencies: Dependencies,
    // Fail if extra top-level fields exist (AC-4)
    #[serde(flatten)]
    extra: serde_json::Value,
}

#[derive(Deserialize, Debug)]
struct Dependencies {
    quote_service: String,
    // Fail if extra dependency fields exist (AC-4)
    #[serde(flatten)]
    extra: serde_json::Value,
}

const SHIPPING_SERVICE_URL: &str = "http://localhost:8080";

#[tokio::test]
async fn test_ac1_quote_service_up_returns_200_healthy() {
    // AC-1: When quote service is reachable, returns 200 with healthy status and quote_service up
    let client = Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();

    let response = client.get(&format!("{SHIPPING_SERVICE_URL}/health"))
        .send()
        .await
        .expect("Failed to send request to /health endpoint");

    assert_eq!(response.status().as_u16(), 200, "Expected HTTP 200 OK when quote service is up");
    
    let health: HealthResponse = response.json()
        .await
        .expect("Response did not match expected JSON schema");
    
    assert!(health.extra.is_null(), "Response contains extra top-level fields (violates AC-4)");
    assert!(health.dependencies.extra.is_null(), "Dependencies contain extra fields (violates AC-4)");
    
    assert_eq!(health.status, "healthy", "Expected status 'healthy' when quote service is up");
    assert_eq!(health.dependencies.quote_service, "up", "Expected quote_service status 'up' when quote service is reachable");
}

#[tokio::test]
async fn test_ac2_quote_service_down_returns_503_unhealthy() {
    // AC-2: When quote service is unreachable, returns 503 with unhealthy status and quote_service down
    let client = Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();

    let response = client.get(&format!("{SHIPPING_SERVICE_URL}/health"))
        .send()
        .await
        .expect("Failed to send request to /health endpoint");

    assert_eq!(response.status().as_u16(), 503, "Expected HTTP 503 Service Unavailable when quote service is down");
    
    let health: HealthResponse = response.json()
        .await
        .expect("Response did not match expected JSON schema");
    
    assert!(health.extra.is_null(), "Response contains extra top-level fields (violates AC-4)");
    assert!(health.dependencies.extra.is_null(), "Dependencies contain extra fields (violates AC-4)");
    
    assert_eq!(health.status, "unhealthy", "Expected status 'unhealthy' when quote service is down");
    assert_eq!(health.dependencies.quote_service, "down", "Expected quote_service status 'down' when quote service is unreachable");
}

#[tokio::test]
async fn test_ac3_health_endpoint_responds_in_under_1_second() {
    // AC-3: Endpoint responds in <= 1 second under all conditions
    let client = Client::builder()
        .timeout(Duration::from_secs(1))
        .build()
        .unwrap();

    let start = std::time::Instant::now();
    let _response = client.get(&format!("{SHIPPING_SERVICE_URL}/health"))
        .send()
        .await
        .expect("Request timed out after 1 second (violates AC-3)");
    
    let duration = start.elapsed();
    assert!(duration <= Duration::from_secs(1), "Request took {:?}, which exceeds 1 second limit (violates AC-3)", duration);
}

#[tokio::test]
async fn test_ac4_response_follows_schema_no_extra_fields() {
    // AC-4: Response follows defined JSON schema with no extra top-level fields
    let client = Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .unwrap();

    let response = client.get(&format!("{SHIPPING_SERVICE_URL}/health"))
        .send()
        .await
        .expect("Failed to send request to /health endpoint");

    let response_json: serde_json::Value = response.json()
        .await
        .expect("Response is not valid JSON");
    
    // Verify only expected top-level fields exist
    let top_level_keys = response_json.as_object().unwrap().keys();
    assert_eq!(top_level_keys.len(), 2, "Response has extra top-level fields");
    assert!(response_json.get("status").is_some(), "Response missing 'status' field");
    assert!(response_json.get("dependencies").is_some(), "Response missing 'dependencies' field");
    
    // Verify dependencies only has quote_service field
    let dependencies = response_json.get("dependencies").unwrap().as_object().unwrap();
    let dep_keys = dependencies.keys();
    assert_eq!(dep_keys.len(), 1, "Dependencies has extra fields");
    assert!(dependencies.get("quote_service").is_some(), "Dependencies missing 'quote_service' field");
}
