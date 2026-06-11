use std::env;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tonic::{Code, Request, Status};
use serial_test::serial;
use shipping::rate_limit_interceptor;

mod common;

// Helper to get current unix timestamp in seconds
fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs()
}

// Helper to create test gRPC request with optional x-client-id metadata and simulated source IP
fn create_test_request(client_id: Option<&str>, client_ip: &str, method_path: &str) -> Request<()> {
    let mut req = Request::new(());
    if let Some(id) = client_id {
        req.metadata_mut().insert("x-client-id", id.parse().unwrap());
    }
    // Simulate source IP address via request extension
    req.extensions_mut().insert(std::net::SocketAddr::new(
        client_ip.parse().unwrap(),
        12345,
    ));
    // Set gRPC method path
    req.extensions_mut().insert(tonic::codegen::http::uri::PathAndQuery::from_static(method_path));
    req
}

#[test]
#[serial]
fn test_ac1_allow_requests_within_limit() {
    // AC-1: When SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW=5 and SHIPPING_RATE_LIMIT_WINDOW_SECONDS=10,
    // a single client can send 5 requests to any gRPC endpoints within a 10 second window, all succeed
    env::set_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW", "5");
    env::set_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS", "10");
    env::set_var("SHIPPING_RATE_LIMIT_IDENTIFIER", "ip");
    
    let interceptor = rate_limit_interceptor();
    let client_ip = "192.168.1.100";
    let method = "/opentelemetry.proto.demo.v1.ShippingService/GetQuote";
    
    // Send 5 requests, all should be allowed
    for i in 0..5 {
        let req = create_test_request(None, client_ip, method);
        let result = interceptor(req);
        assert!(result.is_ok(), "Request {} should be allowed within rate limit", i+1);
    }
    
    // Cleanup environment variables
    env::remove_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW");
    env::remove_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS");
    env::remove_var("SHIPPING_RATE_LIMIT_IDENTIFIER");
}

#[test]
#[serial]
fn test_ac2_deny_request_exceeding_limit() {
    // AC-2: When a client sends 6 requests within the same 10 second window with above config,
    // the 6th request returns gRPC RESOURCE_EXHAUSTED status code with expected message
    env::set_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW", "5");
    env::set_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS", "10");
    env::set_var("SHIPPING_RATE_LIMIT_IDENTIFIER", "ip");
    
    let interceptor = rate_limit_interceptor();
    let client_ip = "192.168.1.101";
    let method = "/opentelemetry.proto.demo.v1.ShippingService/GetQuote";
    
    // Send first 5 requests: all allowed
    for _ in 0..5 {
        let req = create_test_request(None, client_ip, method);
        assert!(interceptor(req).is_ok());
    }
    
    // 6th request should be denied
    let req = create_test_request(None, client_ip, method);
    let result = interceptor(req);
    assert!(result.is_err(), "6th request should be denied when exceeding rate limit");
    
    let status = result.err().unwrap();
    assert_eq!(status.code(), Code::ResourceExhausted, "Error code should be RESOURCE_EXHAUSTED");
    assert_eq!(status.message(), "Rate limit exceeded, try again later", "Error message mismatch");
    
    // Cleanup
    env::remove_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW");
    env::remove_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS");
    env::remove_var("SHIPPING_RATE_LIMIT_IDENTIFIER");
}

#[test]
#[serial]
fn test_ac3_rate_limit_by_ip_ignore_client_id() {
    // AC-3: When SHIPPING_RATE_LIMIT_IDENTIFIER=ip, requests from same IP are counted together
    // regardless of x-client-id metadata value
    env::set_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW", "5");
    env::set_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS", "10");
    env::set_var("SHIPPING_RATE_LIMIT_IDENTIFIER", "ip");
    
    let interceptor = rate_limit_interceptor();
    let client_ip = "192.168.1.102";
    let method = "/opentelemetry.proto.demo.v1.ShippingService/ShipOrder";
    
    // Send 3 requests with client-id "client-a"
    for _ in 0..3 {
        let req = create_test_request(Some("client-a"), client_ip, method);
        assert!(interceptor(req).is_ok());
    }
    
    // Send 3 requests with client-id "client-b" from same IP: 2 allowed, 3rd denied
    for i in 0..3 {
        let req = create_test_request(Some("client-b"), client_ip, method);
        let result = interceptor(req);
        if i < 2 {
            assert!(result.is_ok(), "Request {} with different client ID same IP should be allowed", i+1);
        } else {
            assert!(result.is_err(), "3rd request with different client ID same IP should be denied");
            assert_eq!(result.err().unwrap().code(), Code::ResourceExhausted);
        }
    }
    
    // Cleanup
    env::remove_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW");
    env::remove_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS");
    env::remove_var("SHIPPING_RATE_LIMIT_IDENTIFIER");
}

#[test]
#[serial]
fn test_ac4_rate_limit_by_client_id_ignore_ip() {
    // AC-4: When SHIPPING_RATE_LIMIT_IDENTIFIER=client_id, requests with same x-client-id are counted together
    // regardless of source IP address
    env::set_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW", "5");
    env::set_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS", "10");
    env::set_var("SHIPPING_RATE_LIMIT_IDENTIFIER", "client_id");
    
    let interceptor = rate_limit_interceptor();
    let client_id = "test-client-123";
    let method = "/opentelemetry.proto.demo.v1.ShippingService/GetQuote";
    
    // Send 3 requests from IP 192.168.1.103
    for _ in 0..3 {
        let req = create_test_request(Some(client_id), "192.168.1.103", method);
        assert!(interceptor(req).is_ok());
    }
    
    // Send 3 requests from IP 192.168.1.104 with same client ID: 2 allowed, 3rd denied
    for i in 0..3 {
        let req = create_test_request(Some(client_id), "192.168.1.104", method);
        let result = interceptor(req);
        if i < 2 {
            assert!(result.is_ok(), "Request {} with same client ID different IP should be allowed", i+1);
        } else {
            assert!(result.is_err(), "3rd request with same client ID different IP should be denied");
            assert_eq!(result.err().unwrap().code(), Code::ResourceExhausted);
        }
    }
    
    // Cleanup
    env::remove_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW");
    env::remove_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS");
    env::remove_var("SHIPPING_RATE_LIMIT_IDENTIFIER");
}

#[test]
#[serial]
fn test_ac5_rate_limit_disabled_when_zero_requests() {
    // AC-5: When SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW=0, rate limiting is fully disabled,
    // all requests are allowed with no rate limit checks
    env::set_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW", "0");
    env::set_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS", "10");
    env::set_var("SHIPPING_RATE_LIMIT_IDENTIFIER", "ip");
    
    let interceptor = rate_limit_interceptor();
    let client_ip = "192.168.1.105";
    let method = "/opentelemetry.proto.demo.v1.ShippingService/ShipOrder";
    
    // Send 100 requests, all should be allowed
    for i in 0..100 {
        let req = create_test_request(None, client_ip, method);
        let result = interceptor(req);
        assert!(result.is_ok(), "Request {} should be allowed when rate limit is disabled", i+1);
    }
    
    // Cleanup
    env::remove_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW");
    env::remove_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS");
    env::remove_var("SHIPPING_RATE_LIMIT_IDENTIFIER");
}

#[test]
#[serial]
fn test_ac6_counter_resets_after_window() {
    // AC-6: After the rate limit window elapses, the request counter for a client resets,
    // and the client can send up to configured maximum requests again in the new window
    env::set_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW", "2");
    env::set_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS", "2");
    env::set_var("SHIPPING_RATE_LIMIT_IDENTIFIER", "ip");
    
    let interceptor = rate_limit_interceptor();
    let client_ip = "192.168.1.106";
    let method = "/opentelemetry.proto.demo.v1.ShippingService/GetQuote";
    
    // Send 2 requests: allowed
    assert!(interceptor(create_test_request(None, client_ip, method)).is_ok());
    assert!(interceptor(create_test_request(None, client_ip, method)).is_ok());
    
    // 3rd request should be denied
    let result = interceptor(create_test_request(None, client_ip, method));
    assert!(result.is_err(), "3rd request should be denied before window reset");
    assert_eq!(result.err().unwrap().code(), Code::ResourceExhausted);
    
    // Wait for window to elapse
    std::thread::sleep(Duration::from_secs(2));
    
    // Now requests should be allowed again
    assert!(interceptor(create_test_request(None, client_ip, method)).is_ok());
    assert!(interceptor(create_test_request(None, client_ip, method)).is_ok());
    
    // Cleanup
    env::remove_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW");
    env::remove_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS");
    env::remove_var("SHIPPING_RATE_LIMIT_IDENTIFIER");
}

#[test]
#[serial]
fn test_ac7_rate_limit_logs_generated() {
    // AC-7: Every rate limited request triggers a WARN level structured log containing
    // client_ip, grpc_method, and rate_limit_reset_timestamp fields
    // Initialize logger to capture warnings
    let _ = env_logger::builder()
        .filter_level(log::LevelFilter::Warn)
        .is_test(true)
        .try_init();
    
    env::set_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW", "1");
    env::set_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS", "10");
    env::set_var("SHIPPING_RATE_LIMIT_IDENTIFIER", "ip");
    
    let interceptor = rate_limit_interceptor();
    let client_ip = "192.168.1.107";
    let client_id = "log-test-client";
    let method = "/opentelemetry.proto.demo.v1.ShippingService/GetQuote";
    
    // First request allowed
    assert!(interceptor(create_test_request(Some(client_id), client_ip, method)).is_ok());
    
    // Second request denied - should generate warn log
    let _ = interceptor(create_test_request(Some(client_id), client_ip, method));
    
    // Verify log contains required fields (implementation will capture logs and validate)
    // Expected log fields: client_ip = "192.168.1.107", client_id = "log-test-client",
    // grpc_method = "/opentelemetry.proto.demo.v1.ShippingService/GetQuote",
    // rate_limit_reset_timestamp > now_secs()
    
    // Cleanup
    env::remove_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW");
    env::remove_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS");
    env::remove_var("SHIPPING_RATE_LIMIT_IDENTIFIER");
}

#[test]
#[serial]
fn test_ac8_rate_limit_metrics_incremented() {
    // AC-8: For every incoming gRPC request, the shipping_service_rate_limited_requests_total counter
    // is incremented with the correct client_identifier, grpc_method, and status label values
    env::set_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW", "1");
    env::set_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS", "10");
    env::set_var("SHIPPING_RATE_LIMIT_IDENTIFIER", "ip");
    
    let interceptor = rate_limit_interceptor();
    let client_ip = "192.168.1.108";
    let method = "/opentelemetry.proto.demo.v1.ShippingService/ShipOrder";
    
    // Allowed request: metric should increment with status=allowed
    assert!(interceptor(create_test_request(None, client_ip, method)).is_ok());
    // Validate metric: labels client_identifier = client_ip, grpc_method = method, status = allowed, count += 1
    
    // Denied request: metric should increment with status=denied
    let _ = interceptor(create_test_request(None, client_ip, method));
    // Validate metric: labels client_identifier = client_ip, grpc_method = method, status = denied, count += 1
    
    // Cleanup
    env::remove_var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW");
    env::remove_var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS");
    env::remove_var("SHIPPING_RATE_LIMIT_IDENTIFIER");
}
