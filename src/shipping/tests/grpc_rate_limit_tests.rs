use std::env;
use std::time::Duration;
use tonic::transport::Channel;
use tonic::Code;
use opentelemetry_proto::oteldemo::shipping_service_client::ShippingServiceClient;
use opentelemetry_proto::oteldemo::{GetQuoteRequest, ShipOrderRequest};

// Helper to get shipping service client
async fn get_shipping_client() -> ShippingServiceClient<Channel> {
    ShippingServiceClient::connect("http://localhost:8080")
        .await
        .expect("Failed to connect to shipping service gRPC endpoint")
}

// Helper to get metric value from Prometheus endpoint
async fn get_rate_limit_counter_value(endpoint: &str, limit_rps: u64) -> u64 {
    let client = reqwest::Client::new();
    let resp = client.get("http://localhost:8080/metrics")
        .send()
        .await
        .expect("Failed to fetch metrics");
    let body = resp.text().await.expect("Failed to read metrics body");
    
    for line in body.lines() {
        if line.starts_with("shipping_service_rate_limited_requests_total") {
            if line.contains(&format!("endpoint=\"{}\"", endpoint)) && line.contains(&format!("limit_rps=\"{}\"", limit_rps)) {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() >= 2 {
                    return parts[1].parse().unwrap_or(0);
                }
            }
        }
    }
    0
}

#[tokio::test]
async fn test_ac1_single_endpoint_rate_limit_exceeded() {
    // AC-1: When SHIPPING_GET_QUOTE_RPS=10 and SHIPPING_GET_QUOTE_BURST=20, 30 requests in 1s give 10 success, 20 RESOURCE_EXHAUSTED
    env::set_var("SHIPPING_GET_QUOTE_RPS", "10");
    env::set_var("SHIPPING_GET_QUOTE_BURST", "20");
    
    // Wait for service to start with new config
    tokio::time::sleep(Duration::from_secs(2)).await;
    let mut client = get_shipping_client().await;
    
    let mut success_count = 0;
    let mut rate_limited_count = 0;
    
    // Send 30 requests as fast as possible
    for _ in 0..30 {
        let req = tonic::Request::new(GetQuoteRequest {
            address: Some(Default::default()),
            items: vec![],
        });
        
        match client.get_quote(req).await {
            Ok(_) => success_count += 1,
            Err(status) if status.code() == Code::ResourceExhausted => rate_limited_count += 1,
            Err(e) => panic!("Unexpected error: {}", e),
        }
    }
    
    assert_eq!(success_count, 10, "Expected exactly 10 successful responses");
    assert_eq!(rate_limited_count, 20, "Expected exactly 20 rate limited responses");
    
    env::remove_var("SHIPPING_GET_QUOTE_RPS");
    env::remove_var("SHIPPING_GET_QUOTE_BURST");
}

#[tokio::test]
async fn test_ac2_multiple_endpoints_independent_rate_limits() {
    // AC-2: GetQuote RPS=10, ShipOrder RPS=5, 20 GetQuote requests +10 ShipOrder requests in 1s give respective counts
    env::set_var("SHIPPING_GET_QUOTE_RPS", "10");
    env::set_var("SHIPPING_SHIP_ORDER_RPS", "5");
    
    tokio::time::sleep(Duration::from_secs(2)).await;
    let mut client = get_shipping_client().await;
    
    // Test GetQuote endpoint
    let mut get_quote_success = 0;
    let mut get_quote_limited = 0;
    for _ in 0..20 {
        let req = tonic::Request::new(GetQuoteRequest {
            address: Some(Default::default()),
            items: vec![],
        });
        
        match client.get_quote(req).await {
            Ok(_) => get_quote_success += 1,
            Err(status) if status.code() == Code::ResourceExhausted => get_quote_limited += 1,
            Err(e) => panic!("Unexpected error on GetQuote: {}", e),
        }
    }
    
    // Test ShipOrder endpoint
    let mut ship_order_success = 0;
    let mut ship_order_limited = 0;
    for _ in 0..10 {
        let req = tonic::Request::new(ShipOrderRequest {
            address: Some(Default::default()),
            items: vec![],
        });
        
        match client.ship_order(req).await {
            Ok(_) => ship_order_success += 1,
            Err(status) if status.code() == Code::ResourceExhausted => ship_order_limited += 1,
            Err(e) => panic!("Unexpected error on ShipOrder: {}", e),
        }
    }
    
    assert_eq!(get_quote_success, 10, "GetQuote should have 10 successes");
    assert_eq!(get_quote_limited, 10, "GetQuote should have 10 limited responses");
    assert_eq!(ship_order_success, 5, "ShipOrder should have 5 successes");
    assert_eq!(ship_order_limited, 5, "ShipOrder should have 5 limited responses");
    
    env::remove_var("SHIPPING_GET_QUOTE_RPS");
    env::remove_var("SHIPPING_SHIP_ORDER_RPS");
}

#[tokio::test]
async fn test_ac3_no_rate_limit_config_all_requests_allowed() {
    // AC-3: No env vars set, 1000 requests/s give 0 rate limited responses
    // Clear any existing rate limit vars
    env::remove_var("SHIPPING_GET_QUOTE_RPS");
    env::remove_var("SHIPPING_GET_QUOTE_BURST");
    
    tokio::time::sleep(Duration::from_secs(2)).await;
    let mut client = get_shipping_client().await;
    
    let mut rate_limited_count = 0;
    
    // Send 1000 requests
    for _ in 0..1000 {
        let req = tonic::Request::new(GetQuoteRequest {
            address: Some(Default::default()),
            items: vec![],
        });
        
        if let Err(status) = client.get_quote(req).await {
            if status.code() == Code::ResourceExhausted {
                rate_limited_count += 1;
            }
        }
    }
    
    assert_eq!(rate_limited_count, 0, "No rate limited requests expected when no limit configured");
}

#[tokio::test]
async fn test_ac4_rate_limit_error_message_format() {
    // AC-4: Rate limited error message contains expected format
    env::set_var("SHIPPING_GET_QUOTE_RPS", "10");
    env::set_var("SHIPPING_GET_QUOTE_BURST", "20");
    
    tokio::time::sleep(Duration::from_secs(2)).await;
    let mut client = get_shipping_client().await;
    
    // Exhaust rate limit first
    for _ in 0..10 {
        let _ = client.get_quote(tonic::Request::new(GetQuoteRequest::default())).await;
    }
    
    // Get rate limited response
    let req = tonic::Request::new(GetQuoteRequest::default());
    let err = client.get_quote(req).await.unwrap_err();
    
    assert_eq!(err.code(), Code::ResourceExhausted);
    let expected_msg = "Rate limit exceeded for endpoint /oteldemo.ShippingService/GetQuote: limit is 10 requests per second, burst 20 capacity";
    assert!(err.message().contains(expected_msg), "Error message '{}' does not contain expected text '{}'", err.message(), expected_msg);
    
    env::remove_var("SHIPPING_GET_QUOTE_RPS");
    env::remove_var("SHIPPING_GET_QUOTE_BURST");
}

#[tokio::test]
async fn test_ac5_rate_limited_metric_incremented() {
    // AC-5: Rate limited requests increment shipping_service_rate_limited_requests_total counter with correct labels
    env::set_var("SHIPPING_SHIP_ORDER_RPS", "5");
    
    tokio::time::sleep(Duration::from_secs(2)).await;
    let mut client = get_shipping_client().await;
    
    // Get initial counter value
    let initial_count = get_rate_limit_counter_value("/oteldemo.ShippingService/ShipOrder", 5).await;
    
    // Exhaust rate limit
    for _ in 0..5 {
        let _ = client.ship_order(tonic::Request::new(ShipOrderRequest::default())).await;
    }
    
    // Trigger 3 rate limited requests
    let mut limited_count = 0;
    for _ in 0..3 {
        let req = tonic::Request::new(ShipOrderRequest::default());
        if let Err(status) = client.ship_order(req).await {
            if status.code() == Code::ResourceExhausted {
                limited_count += 1;
            }
        }
    }
    
    // Get updated counter value
    let final_count = get_rate_limit_counter_value("/oteldemo.ShippingService/ShipOrder", 5).await;
    
    assert_eq!(final_count, initial_count + limited_count, "Rate limit counter should increment by number of limited requests");
    
    env::remove_var("SHIPPING_SHIP_ORDER_RPS");
}

#[tokio::test]
async fn test_ac6_rate_limit_low_latency_overhead() {
    // AC-6: P95 latency for successful requests <1ms higher than baseline
    env::remove_var("SHIPPING_GET_QUOTE_RPS");
    tokio::time::sleep(Duration::from_secs(2)).await;
    let mut client = get_shipping_client().await;
    
    // Measure baseline latency (no rate limiting)
    let mut baseline_latencies = vec![];
    for _ in 0..1000 {
        let start = std::time::Instant::now();
        let _ = client.get_quote(tonic::Request::new(GetQuoteRequest::default())).await.unwrap();
        baseline_latencies.push(start.elapsed());
    }
    baseline_latencies.sort();
    let baseline_p95 = baseline_latencies[(baseline_latencies.len() as f64 * 0.95) as usize];
    
    // Enable rate limiting
    env::set_var("SHIPPING_GET_QUOTE_RPS", "10000"); // High limit so no rate limiting happens
    tokio::time::sleep(Duration::from_secs(2)).await;
    
    // Measure latency with rate limiting enabled
    let mut with_rl_latencies = vec![];
    for _ in 0..1000 {
        let start = std::time::Instant::now();
        let _ = client.get_quote(tonic::Request::new(GetQuoteRequest::default())).await.unwrap();
        with_rl_latencies.push(start.elapsed());
    }
    with_rl_latencies.sort();
    let with_rl_p95 = with_rl_latencies[(with_rl_latencies.len() as f64 * 0.95) as usize];
    
    // Check overhead < 1ms
    let overhead = with_rl_p95.saturating_sub(baseline_p95);
    assert!(overhead < Duration::from_millis(1), "P95 latency overhead {:?} exceeds 1ms limit", overhead);
    
    env::remove_var("SHIPPING_GET_QUOTE_RPS");
}

#[tokio::test]
async fn test_ac7_rate_limit_updated_after_restart() {
    // AC-7: Changing RPS env var and restarting increases allowed RPS
    env::set_var("SHIPPING_GET_QUOTE_RPS", "10");
    tokio::time::sleep(Duration::from_secs(2)).await;
    let mut client = get_shipping_client().await;
    
    // Verify initial limit is 10
    let mut success_count = 0;
    for _ in 0..15 {
        let req = tonic::Request::new(GetQuoteRequest::default());
        if client.get_quote(req).await.is_ok() {
            success_count += 1;
        }
    }
    assert_eq!(success_count, 10, "Initial limit should be 10 RPS");
    
    // Update env var and restart service (simulated by clearing connection and waiting)
    env::set_var("SHIPPING_GET_QUOTE_RPS", "20");
    // Restart service here (handled by test harness)
    tokio::time::sleep(Duration::from_secs(5)).await;
    let mut client = get_shipping_client().await;
    
    // Verify new limit is 20
    success_count = 0;
    let mut limited_count = 0;
    for _ in 0..25 {
        let req = tonic::Request::new(GetQuoteRequest::default());
        match client.get_quote(req).await {
            Ok(_) => success_count +=1,
            Err(status) if status.code() == Code::ResourceExhausted => limited_count +=1,
            _ => {}
        }
    }
    
    assert_eq!(success_count, 20, "Updated limit should be 20 RPS");
    assert_eq!(limited_count, 5, "Should have 5 rate limited responses after update");
    
    env::remove_var("SHIPPING_GET_QUOTE_RPS");
}
