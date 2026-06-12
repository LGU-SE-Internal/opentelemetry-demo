//! Integration tests for shipping service carrier API retry functionality
//! Tests follow the ACs defined in issue #2156

use std::time::{Duration, Instant};
use reqwest::Client;
use rustracing::span::Span;
use opentelemetry_api::metrics::{Counter, Histogram};

// Environment variable names from spec
const ENV_RETRY_MAX_ATTEMPTS: &str = "SHIPPING_RETRY_MAX_ATTEMPTS";
const ENV_RETRY_INITIAL_BACKOFF_MS: &str = "SHIPPING_RETRY_INITIAL_BACKOFF_MS";

// Metric names from spec
const METRIC_RETRY_COUNT: &str = "shipping_carrier_api_retry_count";
const METRIC_RETRY_FAILED_TOTAL: &str = "shipping_carrier_api_retry_failed_total";
const METRIC_RETRY_LATENCY: &str = "shipping_carrier_api_retry_latency_ms";

// Span name from spec
const SPAN_RETRY_NAME: &str = "shipping.carrier_api.retry";

/// AC-1: Retry GET requests on 5xx status codes up to max attempts
#[tokio::test]
async fn test_ac1_retry_on_5xx_status_codes() {
    // Setup mock carrier API that returns 503 for first N requests, then 200
    let mut server = mockito::Server::new_async().await;
    let mock = server.mock("GET", "/rates")
        .with_status(503)
        .expect(4) // Max attempts default is 3 retries = 1 initial + 3 retries = 4 total attempts
        .create_async()
        .await;
    
    // Set default env vars
    std::env::set_var(ENV_RETRY_MAX_ATTEMPTS, "3");
    std::env::set_var(ENV_RETRY_INITIAL_BACKOFF_MS, "100");
    
    // Create shipping service client with retry middleware
    let client = get_shipping_carrier_client(&server.url()).await;
    
    // Make request that gets 5xx first
    let response = client.get("/rates").send().await;
    
    // Verify we got a response after retries
    assert!(response.is_ok());
    mock.assert_async().await;
    
    // Cleanup
    std::env::remove_var(ENV_RETRY_MAX_ATTEMPTS);
    std::env::remove_var(ENV_RETRY_INITIAL_BACKOFF_MS);
}

/// AC-2: Retry GET requests on network timeouts/connection resets
#[tokio::test]
async fn test_ac2_retry_on_network_errors() {
    // Setup: endpoint that closes connection immediately (simulate reset)
    let server_url = "http://localhost:9999"; // Port that has no server running
    
    std::env::set_var(ENV_RETRY_MAX_ATTEMPTS, "2");
    std::env::set_var(ENV_RETRY_INITIAL_BACKOFF_MS, "100");
    
    let client = get_shipping_carrier_client(server_url).await;
    
    let start = Instant::now();
    let response = client.get("/rates").send().await;
    
    // Should fail after all retries
    assert!(response.is_err());
    // Total time should be at least 100 + 200 = 300ms (two retries)
    assert!(start.elapsed() >= Duration::from_millis(300));
    
    std::env::remove_var(ENV_RETRY_MAX_ATTEMPTS);
    std::env::remove_var(ENV_RETRY_INITIAL_BACKOFF_MS);
}

/// AC-3: Non-GET requests are never retried
#[tokio::test]
async fn test_ac3_no_retry_on_non_get_requests() {
    let mut server = mockito::Server::new_async().await;
    // Mock expects only 1 POST request, returns 500
    let mock = server.mock("POST", "/shipment")
        .with_status(500)
        .expect(1)
        .create_async()
        .await;
    
    std::env::set_var(ENV_RETRY_MAX_ATTEMPTS, "3");
    
    let client = get_shipping_carrier_client(&server.url()).await;
    
    let response = client.post("/shipment").send().await;
    
    // Should return error immediately, no retries
    assert!(response.is_err());
    assert_eq!(response.unwrap_err().status().unwrap().as_u16(), 500);
    mock.assert_async().await;
    
    std::env::remove_var(ENV_RETRY_MAX_ATTEMPTS);
}

/// AC-4: Max attempts 2 = total 3 requests
#[tokio::test]
async fn test_ac4_max_attempts_correct_total() {
    let mut server = mockito::Server::new_async().await;
    let mock = server.mock("GET", "/rates")
        .with_status(500)
        .expect(3) // 1 initial + 2 retries = 3 total
        .create_async()
        .await;
    
    std::env::set_var(ENV_RETRY_MAX_ATTEMPTS, "2");
    std::env::set_var(ENV_RETRY_INITIAL_BACKOFF_MS, "100");
    
    let client = get_shipping_carrier_client(&server.url()).await;
    
    let response = client.get("/rates").send().await;
    
    assert!(response.is_err());
    mock.assert_async().await;
    
    std::env::remove_var(ENV_RETRY_MAX_ATTEMPTS);
    std::env::remove_var(ENV_RETRY_INITIAL_BACKOFF_MS);
}

/// AC-5: Exponential backoff with jitter: 100ms initial, doubles each retry
#[tokio::test]
async fn test_ac5_exponential_backoff_with_jitter() {
    let mut server = mockito::Server::new_async().await;
    // 3 retries = delays of ~100, ~200, ~400ms
    let mock = server.mock("GET", "/rates")
        .with_status(500)
        .expect(4) // 1 + 3 = 4 total requests
        .create_async()
        .await;
    
    std::env::set_var(ENV_RETRY_MAX_ATTEMPTS, "3");
    std::env::set_var(ENV_RETRY_INITIAL_BACKOFF_MS, "100");
    
    let client = get_shipping_carrier_client(&server.url()).await;
    
    let start = Instant::now();
    let _ = client.get("/rates").send().await;
    let total_time = start.elapsed();
    
    // Minimum total delay is 100 + 200 + 400 = 700ms, maximum (with jitter) less than 1400ms
    assert!(total_time >= Duration::from_millis(700));
    assert!(total_time <= Duration::from_millis(1400));
    mock.assert_async().await;
    
    std::env::remove_var(ENV_RETRY_MAX_ATTEMPTS);
    std::env::remove_var(ENV_RETRY_INITIAL_BACKOFF_MS);
}

/// AC-6: Retry attempts create child spans with correct attributes
#[tokio::test]
async fn test_ac6_retry_spans_created_with_attributes() {
    let mut server = mockito::Server::new_async().await;
    let mock = server.mock("GET", "/rates")
        .with_status(500)
        .expect(2) // 1 initial + 1 retry = 2 total
        .create_async()
        .await;
    
    std::env::set_var(ENV_RETRY_MAX_ATTEMPTS, "1");
    
    // Setup tracing collector
    let (tracer, mut collector) = rustracing::mock::new_tracer();
    
    let client = get_shipping_carrier_client_with_tracer(&server.url(), tracer).await;
    
    // Start parent span
    let parent_span = tracer.span("parent_request").start();
    
    // Make request in parent span context
    let _response = parent_span.handle(|| async {
        client.get("/rates").send().await
    }).await;
    
    // Get all spans
    let spans = collector.collect();
    
    // Find retry spans
    let retry_spans: Vec<_> = spans.iter().filter(|s| s.operation_name() == SPAN_RETRY_NAME).collect();
    assert_eq!(retry_spans.len(), 1);
    
    let retry_span = retry_spans.first().unwrap();
    // Verify child of parent span
    assert_eq!(retry_span.reference().unwrap().span(), parent_span.context().span_id());
    // Verify attributes
    assert_eq!(retry_span.tags().get::<usize>("retry.attempt_number").unwrap(), &1);
    assert!(retry_span.tags().get::<String>("retry.previous_error").unwrap().contains("500"));
    
    mock.assert_async().await;
    std::env::remove_var(ENV_RETRY_MAX_ATTEMPTS);
}

/// AC-7: retry_count metric increments with correct attributes
#[tokio::test]
async fn test_ac7_retry_count_metric_increments() {
    let mut server = mockito::Server::new_async().await;
    // First two requests fail, third succeeds
    let m1 = server.mock("GET", "/rates").with_status(500).expect(1).create_async().await;
    let m2 = server.mock("GET", "/rates").with_status(500).expect(1).create_async().await;
    let m3 = server.mock("GET", "/rates").with_status(200).expect(1).create_async().await;
    
    std::env::set_var(ENV_RETRY_MAX_ATTEMPTS, "2");
    
    // Setup metric exporter
    let exporter = opentelemetry_stdout::MetricsExporter::default();
    let provider = opentelemetry_sdk::metrics::MeterProvider::builder()
        .with_reader(opentelemetry_sdk::metrics::reader::PeriodicReader::builder(exporter, opentelemetry_sdk::runtime::Tokio).build())
        .build();
    opentelemetry_api::global::set_meter_provider(provider.clone());
    
    let meter = opentelemetry_api::global::meter("shipping");
    let retry_counter: Counter<u64> = meter.u64_counter(METRIC_RETRY_COUNT).init();
    
    let client = get_shipping_carrier_client(&server.url()).await;
    
    let _ = client.get("/rates").send().await;
    
    // Verify metric was incremented 2 times, with correct attributes
    let metrics = provider.collect_metrics().unwrap();
    let retry_count_metric = metrics.iter().find(|m| m.name == METRIC_RETRY_COUNT).unwrap();
    let datapoints = retry_count_metric.data.as_sum().unwrap().data_points.iter();
    let success_point = datapoints.find(|dp| dp.attributes.iter().any(|a| a.key == "status" && a.value.as_str() == "success")).unwrap();
    let failure_point = datapoints.find(|dp| dp.attributes.iter().any(|a| a.key == "status" && a.value.as_str() == "failure")).unwrap();
    
    assert_eq!(success_point.value.as_u64(), 1); // 1 success retry
    assert_eq!(failure_point.value.as_u64(), 1); // 1 failure retry
    assert!(success_point.attributes.iter().any(|a| a.key == "endpoint" && a.value.as_str() == "/rates"));
    
    m1.assert_async().await;
    m2.assert_async().await;
    m3.assert_async().await;
    std::env::remove_var(ENV_RETRY_MAX_ATTEMPTS);
}

/// AC-8: retry_failed_total metric increments when all retries exhausted
#[tokio::test]
async fn test_ac8_retry_failed_metric_increments() {
    let mut server = mockito::Server::new_async().await;
    let mock = server.mock("GET", "/rates")
        .with_status(500)
        .expect(3) // 1 + 2 = 3 requests, all fail
        .create_async()
        .await;
    
    std::env::set_var(ENV_RETRY_MAX_ATTEMPTS, "2");
    
    // Setup metric exporter
    let exporter = opentelemetry_stdout::MetricsExporter::default();
    let provider = opentelemetry_sdk::metrics::MeterProvider::builder()
        .with_reader(opentelemetry_sdk::metrics::reader::PeriodicReader::builder(exporter, opentelemetry_sdk::runtime::Tokio).build())
        .build();
    opentelemetry_api::global::set_meter_provider(provider.clone());
    
    let client = get_shipping_carrier_client(&server.url()).await;
    
    let _ = client.get("/rates").send().await;
    
    // Verify failed metric was incremented
    let metrics = provider.collect_metrics().unwrap();
    let failed_metric = metrics.iter().find(|m| m.name == METRIC_RETRY_FAILED_TOTAL).unwrap();
    let datapoint = failed_metric.data.as_sum().unwrap().data_points.first().unwrap();
    
    assert_eq!(datapoint.value.as_u64(), 1);
    assert!(datapoint.attributes.iter().any(|a| a.key == "endpoint" && a.value.as_str() == "/rates"));
    
    mock.assert_async().await;
    std::env::remove_var(ENV_RETRY_MAX_ATTEMPTS);
}

/// AC-9: retry_latency_ms histogram records total cumulative delay
#[tokio::test]
async fn test_ac9_retry_latency_metric_records_cumulative_delay() {
    let mut server = mockito::Server::new_async().await;
    let mock = server.mock("GET", "/rates")
        .with_status(500)
        .expect(3) // 1 + 2 = 3 requests, delays 100 + 200 = 300ms total
        .create_async()
        .await;
    
    std::env::set_var(ENV_RETRY_MAX_ATTEMPTS, "2");
    std::env::set_var(ENV_RETRY_INITIAL_BACKOFF_MS, "100");
    
    // Setup metric exporter
    let exporter = opentelemetry_stdout::MetricsExporter::default();
    let provider = opentelemetry_sdk::metrics::MeterProvider::builder()
        .with_reader(opentelemetry_sdk::metrics::reader::PeriodicReader::builder(exporter, opentelemetry_sdk::runtime::Tokio).build())
        .build();
    opentelemetry_api::global::set_meter_provider(provider.clone());
    
    let client = get_shipping_carrier_client(&server.url()).await;
    
    let _ = client.get("/rates").send().await;
    
    // Verify latency metric has value between 300 and 600ms (300 base + jitter up to 2x)
    let metrics = provider.collect_metrics().unwrap();
    let latency_metric = metrics.iter().find(|m| m.name == METRIC_RETRY_LATENCY).unwrap();
    let datapoint = latency_metric.data.as_histogram().unwrap().data_points.first().unwrap();
    
    assert_eq!(datapoint.count, 1);
    assert!(datapoint.sum.as_f64() >= 300.0);
    assert!(datapoint.sum.as_f64() <= 600.0);
    assert!(datapoint.attributes.iter().any(|a| a.key == "endpoint" && a.value.as_str() == "/rates"));
    
    mock.assert_async().await;
    std::env::remove_var(ENV_RETRY_MAX_ATTEMPTS);
    std::env::remove_var(ENV_RETRY_INITIAL_BACKOFF_MS);
}

// Dummy function to represent shipping carrier client with retry middleware (to be implemented)
async fn get_shipping_carrier_client(base_url: &str) -> Client {
    // This function will be implemented with reqwest-retry middleware
    unimplemented!("Retry middleware not implemented yet")
}

// Dummy function for tracer-enabled client
async fn get_shipping_carrier_client_with_tracer(base_url: &str, tracer: rustracing::mock::Tracer) -> Client {
    unimplemented!("Retry middleware with tracing not implemented yet")
}
