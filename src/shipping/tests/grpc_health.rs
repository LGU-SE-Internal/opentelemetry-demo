use tonic::Request;
use tonic::transport::Channel;
use grpc_health_v1::health_client::HealthClient;
use grpc_health_v1::{HealthCheckRequest, HealthCheckResponse};
use std::time::Duration;
use tokio::time::timeout;

mod grpc_health_v1 {
    tonic::include_proto("grpc.health.v1");
}

const SHIPPING_GRPC_ADDR: &str = "http://localhost:50051";
const SHIPPING_SERVICE_NAME: &str = "opentelemetry.demo.shipping.v1.ShippingService";

async fn get_health_client() -> HealthClient<Channel> {
    HealthClient::connect(SHIPPING_GRPC_ADDR)
        .await
        .expect("Failed to connect to shipping gRPC service")
}

#[tokio::test]
async fn test_ac1_empty_service_returns_serving_when_operational() {
    // AC-1: When service is operational, active requests <100, empty service name returns SERVING
    let mut client = get_health_client().await;

    let request = Request::new(HealthCheckRequest {
        service: "".to_string(),
    });

    let response = client.check(request)
        .await
        .expect("Health check request failed");

    assert_eq!(response.into_inner().status(), HealthCheckResponse::ServingStatus::Serving);
}

#[tokio::test]
async fn test_ac2_specific_service_returns_serving_when_operational() {
    // AC-2: When service is operational, active requests <100, specific service name returns SERVING
    let mut client = get_health_client().await;

    let request = Request::new(HealthCheckRequest {
        service: SHIPPING_SERVICE_NAME.to_string(),
    });

    let response = client.check(request)
        .await
        .expect("Health check request failed");

    assert_eq!(response.into_inner().status(), HealthCheckResponse::ServingStatus::Serving);
}

#[tokio::test]
async fn test_ac3_returns_not_serving_after_shutdown_signal() {
    // AC-3: After receiving SIGTERM/SIGINT, returns NOT_SERVING for all valid services
    let mut client = get_health_client().await;

    // Send shutdown signal to service (external test harness will handle this)
    // Wait for shutdown to propagate
    tokio::time::sleep(Duration::from_secs(2)).await;

    // Check empty service
    let req1 = Request::new(HealthCheckRequest { service: "".to_string() });
    let resp1 = client.check(req1).await.expect("Health check failed");
    assert_eq!(resp1.into_inner().status(), HealthCheckResponse::ServingStatus::NotServing);

    // Check specific service
    let req2 = Request::new(HealthCheckRequest { service: SHIPPING_SERVICE_NAME.to_string() });
    let resp2 = client.check(req2).await.expect("Health check failed");
    assert_eq!(resp2.into_inner().status(), HealthCheckResponse::ServingStatus::NotServing);
}

#[tokio::test]
async fn test_ac4_returns_not_serving_when_active_requests_over_threshold() {
    // AC-4: When active requests >=100 threshold, returns NOT_SERVING for all valid services
    let mut client = get_health_client().await;

    // Simulate high load (test harness will generate 100+ active requests)
    tokio::time::sleep(Duration::from_secs(5)).await;

    // Check empty service
    let req1 = Request::new(HealthCheckRequest { service: "".to_string() });
    let resp1 = client.check(req1).await.expect("Health check failed");
    assert_eq!(resp1.into_inner().status(), HealthCheckResponse::ServingStatus::NotServing);

    // Check specific service
    let req2 = Request::new(HealthCheckRequest { service: SHIPPING_SERVICE_NAME.to_string() });
    let resp2 = client.check(req2).await.expect("Health check failed");
    assert_eq!(resp2.into_inner().status(), HealthCheckResponse::ServingStatus::NotServing);
}

#[tokio::test]
async fn test_ac5_returns_serving_when_active_requests_drop_below_threshold() {
    // AC-5: When active requests drop below threshold after being over, returns SERVING
    let mut client = get_health_client().await;

    // First ensure we are over threshold
    tokio::time::sleep(Duration::from_secs(5)).await;
    let req_over = Request::new(HealthCheckRequest { service: "".to_string() });
    let resp_over = client.check(req_over).await.expect("Health check failed");
    assert_eq!(resp_over.into_inner().status(), HealthCheckResponse::ServingStatus::NotServing);

    // Wait for requests to complete/drop below threshold
    tokio::time::sleep(Duration::from_secs(10)).await;

    // Check status is back to SERVING
    let req_normal = Request::new(HealthCheckRequest { service: "".to_string() });
    let resp_normal = client.check(req_normal).await.expect("Health check failed");
    assert_eq!(resp_normal.into_inner().status(), HealthCheckResponse::ServingStatus::Serving);

    let req_specific = Request::new(HealthCheckRequest { service: SHIPPING_SERVICE_NAME.to_string() });
    let resp_specific = client.check(req_specific).await.expect("Health check failed");
    assert_eq!(resp_specific.into_inner().status(), HealthCheckResponse::ServingStatus::Serving);
}

#[tokio::test]
async fn test_ac6_returns_service_unknown_for_invalid_service_name() {
    // AC-6: Invalid/unknown service name returns SERVICE_UNKNOWN
    let mut client = get_health_client().await;

    let request = Request::new(HealthCheckRequest {
        service: "invalid.service.name.v1.UnknownService".to_string(),
    });

    let response = client.check(request)
        .await
        .expect("Health check request failed");

    assert_eq!(response.into_inner().status(), HealthCheckResponse::ServingStatus::ServiceUnknown);
}

#[tokio::test]
async fn test_ac7_watch_streams_status_updates_on_change() {
    // AC-7: Watch method streams status updates when serving status changes
    let mut client = get_health_client().await;

    let request = Request::new(HealthCheckRequest {
        service: "".to_string(),
    });

    let mut stream = client.watch(request)
        .await
        .expect("Watch request failed")
        .into_inner();

    // First status should be SERVING
    let first_msg = timeout(Duration::from_secs(5), stream.message())
        .await
        .expect("Timeout waiting for first watch message")
        .expect("Failed to receive first message")
        .expect("Empty first message");
    assert_eq!(first_msg.status(), HealthCheckResponse::ServingStatus::Serving);

    // Trigger status change (shutdown or threshold exceed via test harness)
    tokio::time::sleep(Duration::from_secs(3)).await;

    // Next status should be NOT_SERVING
    let second_msg = timeout(Duration::from_secs(10), stream.message())
        .await
        .expect("Timeout waiting for second watch message")
        .expect("Failed to receive second message")
        .expect("Empty second message");
    assert_eq!(second_msg.status(), HealthCheckResponse::ServingStatus::NotServing);
}
