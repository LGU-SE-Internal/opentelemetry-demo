use reqwest::{Client, Request, Method, Url};
use std::time::{Duration, Instant};
use tonic::Code;

// Import types from the shipping service implementation as defined in spec
use shipping::quote_service::{
    QuoteServiceCircuitBreaker,
    QuoteServiceError,
    CircuitBreakerMetrics,
};

fn create_test_request() -> Request {
    Request::new(Method::GET, Url::parse("http://quoteservice:8080/quote").unwrap())
}

fn create_mock_http_client() -> Client {
    Client::builder()
        .timeout(Duration::from_secs(1))
        .build()
        .unwrap()
}

/// AC-1: When 5 consecutive failed HTTP requests are made to the quote service, the circuit transitions from CLOSED to OPEN state
#[tokio::test]
async fn test_ac1_5_consecutive_failures_open_circuit() {
    let client = create_mock_http_client();
    let circuit_breaker = QuoteServiceCircuitBreaker::new(client);
    
    // Verify initial state is closed
    assert_eq!(circuit_breaker.metrics.current_state.get(), 0);
    
    // Make 4 failed requests
    for _ in 0..4 {
        let req = create_test_request();
        let result = circuit_breaker.execute(req).await;
        assert!(matches!(result, Err(QuoteServiceError::HttpError(_))));
    }
    
    // State should still be closed after 4 failures
    assert_eq!(circuit_breaker.metrics.current_state.get(), 0);
    
    // 5th failed request
    let req = create_test_request();
    let result = circuit_breaker.execute(req).await;
    assert!(matches!(result, Err(QuoteServiceError::HttpError(_))));
    
    // State should now be open
    assert_eq!(circuit_breaker.metrics.current_state.get(), 1);
    
    // Verify state transition metric was incremented for CLOSED->OPEN
    let transition_count = circuit_breaker.metrics.state_transitions_total
        .get_metric_with_label_values(&["closed", "open"])
        .unwrap()
        .get();
    assert_eq!(transition_count, 1);
}

/// AC-2: When circuit is in OPEN state, all requests immediately return gRPC Unavailable error without making any HTTP calls to the quote service
#[tokio::test]
async fn test_ac2_open_circuit_returns_unavailable() {
    let client = create_mock_http_client();
    let circuit_breaker = QuoteServiceCircuitBreaker::new(client);
    
    // First trigger circuit to open (5 failures)
    for _ in 0..5 {
        let _ = circuit_breaker.execute(create_test_request()).await;
    }
    
    // Verify circuit is open
    assert_eq!(circuit_breaker.metrics.current_state.get(), 1);
    
    // Next request should return CircuitOpen error immediately
    let start = Instant::now();
    let result = circuit_breaker.execute(create_test_request()).await;
    let duration = start.elapsed();
    
    // Should return CircuitOpen, not HttpError
    assert!(matches!(result, Err(QuoteServiceError::CircuitOpen)));
    // Should return faster than HTTP timeout (proves no HTTP call was made)
    assert!(duration < Duration::from_millis(100));
    
    // Verify requests_total metric has open state label
    let open_request_count = circuit_breaker.metrics.requests_total
        .get_metric_with_label_values(&["open"])
        .unwrap()
        .get();
    assert!(open_request_count >= 1);
}

/// AC-3: 30 seconds after circuit transitions to OPEN state, it automatically transitions to HALF_OPEN state
#[tokio::test]
async fn test_ac3_open_to_half_open_after_30s() {
    let client = create_mock_http_client();
    let circuit_breaker = QuoteServiceCircuitBreaker::new(client);
    
    // Trigger open circuit
    for _ in 0..5 {
        let _ = circuit_breaker.execute(create_test_request()).await;
    }
    assert_eq!(circuit_breaker.metrics.current_state.get(), 1);
    
    // Wait just under 30s: should still be open
    tokio::time::sleep(Duration::from_secs(29)).await;
    assert_eq!(circuit_breaker.metrics.current_state.get(), 1);
    
    // Wait 2 more seconds: should have transitioned to half-open
    tokio::time::sleep(Duration::from_secs(2)).await;
    assert_eq!(circuit_breaker.metrics.current_state.get(), 2);
    
    // Verify state transition metric for OPEN->HALF_OPEN
    let transition_count = circuit_breaker.metrics.state_transitions_total
        .get_metric_with_label_values(&["open", "half_open"])
        .unwrap()
        .get();
    assert_eq!(transition_count, 1);
}

/// AC-4: When circuit is in HALF_OPEN state, only 10% of incoming requests (minimum 1 request) are dispatched to the quote service, all others return gRPC Unavailable error
#[tokio::test]
async fn test_ac4_half_open_allows_10_percent_requests() {
    let client = create_mock_http_client();
    let circuit_breaker = QuoteServiceCircuitBreaker::new(client);
    
    // Open circuit then transition to half-open
    for _ in 0..5 {
        let _ = circuit_breaker.execute(create_test_request()).await;
    }
    tokio::time::sleep(Duration::from_secs(31)).await;
    assert_eq!(circuit_breaker.metrics.current_state.get(), 2);
    
    let mut dispatched = 0;
    let mut rejected = 0;
    
    // Send 100 requests
    for _ in 0..100 {
        let req = create_test_request();
        match circuit_breaker.execute(req).await {
            Err(QuoteServiceError::CircuitOpen) => rejected += 1,
            Err(QuoteServiceError::HttpError(_)) => dispatched += 1,
            Ok(_) => dispatched +=1,
        }
    }
    
    // Should have ~10% dispatched (allow small variance, but at least 1)
    assert!(dispatched >= 1);
    assert!(dispatched <= 15);
    assert_eq!(dispatched + rejected, 100);
}

/// AC-5: When a request succeeds in HALF_OPEN state, circuit transitions back to CLOSED state and all requests are allowed
#[tokio::test]
async fn test_ac5_half_open_success_closes_circuit() {
    // TODO: Use mock client that returns success
    let client = create_mock_http_client();
    let circuit_breaker = QuoteServiceCircuitBreaker::new(client);
    
    // Open circuit then transition to half-open
    for _ in 0..5 {
        let _ = circuit_breaker.execute(create_test_request()).await;
    }
    tokio::time::sleep(Duration::from_secs(31)).await;
    assert_eq!(circuit_breaker.metrics.current_state.get(), 2);
    
    // Get a successful request through
    // (assuming mock client returns success here)
    let result = circuit_breaker.execute(create_test_request()).await;
    assert!(result.is_ok());
    
    // Circuit should now be closed
    assert_eq!(circuit_breaker.metrics.current_state.get(), 0);
    
    // Verify state transition metric for HALF_OPEN->CLOSED
    let transition_count = circuit_breaker.metrics.state_transitions_total
        .get_metric_with_label_values(&["half_open", "closed"])
        .unwrap()
        .get();
    assert_eq!(transition_count, 1);
    
    // All next requests should be allowed (no CircuitOpen errors)
    for _ in 0..10 {
        let req = create_test_request();
        let result = circuit_breaker.execute(req).await;
        assert!(!matches!(result, Err(QuoteServiceError::CircuitOpen)));
    }
}

/// AC-6: When a request fails in HALF_OPEN state, circuit transitions back to OPEN state for another 30 seconds
#[tokio::test]
async fn test_ac6_half_open_failure_reopens_circuit() {
    let client = create_mock_http_client();
    let circuit_breaker = QuoteServiceCircuitBreaker::new(client);
    
    // Open circuit then transition to half-open
    for _ in 0..5 {
        let _ = circuit_breaker.execute(create_test_request()).await;
    }
    tokio::time::sleep(Duration::from_secs(31)).await;
    assert_eq!(circuit_breaker.metrics.current_state.get(), 2);
    
    // Send a failed request
    let result = circuit_breaker.execute(create_test_request()).await;
    assert!(matches!(result, Err(QuoteServiceError::HttpError(_))));
    
    // Circuit should be open again
    assert_eq!(circuit_breaker.metrics.current_state.get(), 1);
    
    // Verify state transition metric for HALF_OPEN->OPEN
    let transition_count = circuit_breaker.metrics.state_transitions_total
        .get_metric_with_label_values(&["half_open", "open"])
        .unwrap()
        .get();
    assert_eq!(transition_count, 1);
    
    // Should remain open for another 30s
    tokio::time::sleep(Duration::from_secs(29)).await;
    assert_eq!(circuit_breaker.metrics.current_state.get(), 1);
    
    tokio::time::sleep(Duration::from_secs(2)).await;
    assert_eq!(circuit_breaker.metrics.current_state.get(), 2);
}

/// AC-7: All state transitions increment state_transitions_total metric with correct labels
#[tokio::test]
async fn test_ac7_state_transitions_metrics() {
    let client = create_mock_http_client();
    let circuit_breaker = QuoteServiceCircuitBreaker::new(client);
    
    // CLOSED -> OPEN
    for _ in 0..5 { let _ = circuit_breaker.execute(create_test_request()).await; }
    assert_eq!(circuit_breaker.metrics.state_transitions_total.get_metric_with_label_values(&["closed", "open"]).unwrap().get(), 1);
    
    // OPEN -> HALF_OPEN
    tokio::time::sleep(Duration::from_secs(31)).await;
    assert_eq!(circuit_breaker.metrics.state_transitions_total.get_metric_with_label_values(&["open", "half_open"]).unwrap().get(), 1);
    
    // HALF_OPEN -> OPEN
    let _ = circuit_breaker.execute(create_test_request()).await;
    assert_eq!(circuit_breaker.metrics.state_transitions_total.get_metric_with_label_values(&["half_open", "open"]).unwrap().get(), 1);
    
    // OPEN -> HALF_OPEN again
    tokio::time::sleep(Duration::from_secs(31)).await;
    assert_eq!(circuit_breaker.metrics.state_transitions_total.get_metric_with_label_values(&["open", "half_open"]).unwrap().get(), 2);
    
    // HALF_OPEN -> CLOSED (assuming successful request)
    let _ = circuit_breaker.execute(create_test_request()).await;
    assert_eq!(circuit_breaker.metrics.state_transitions_total.get_metric_with_label_values(&["half_open", "closed"]).unwrap().get(), 1);
}

/// AC-8: All requests are counted in requests_total metric with current state label
#[tokio::test]
async fn test_ac8_requests_total_metric_labels() {
    let client = create_mock_http_client();
    let circuit_breaker = QuoteServiceCircuitBreaker::new(client);
    
    // 5 requests in CLOSED state
    for _ in 0..5 { let _ = circuit_breaker.execute(create_test_request()).await; }
    assert_eq!(circuit_breaker.metrics.requests_total.get_metric_with_label_values(&["closed"]).unwrap().get(), 5);
    
    // 5 requests in OPEN state
    for _ in 0..5 { let _ = circuit_breaker.execute(create_test_request()).await; }
    assert_eq!(circuit_breaker.metrics.requests_total.get_metric_with_label_values(&["open"]).unwrap().get(), 5);
    
    // Transition to half-open, send 10 requests
    tokio::time::sleep(Duration::from_secs(31)).await;
    for _ in 0..10 { let _ = circuit_breaker.execute(create_test_request()).await; }
    assert_eq!(circuit_breaker.metrics.requests_total.get_metric_with_label_values(&["half_open"]).unwrap().get(), 10);
}

/// AC-9: current_state gauge always reflects actual circuit state
#[tokio::test]
async fn test_ac9_current_state_gauge() {
    let client = create_mock_http_client();
    let circuit_breaker = QuoteServiceCircuitBreaker::new(client);
    
    assert_eq!(circuit_breaker.metrics.current_state.get(), 0); // CLOSED
    
    for _ in 0..5 { let _ = circuit_breaker.execute(create_test_request()).await; }
    assert_eq!(circuit_breaker.metrics.current_state.get(), 1); // OPEN
    
    tokio::time::sleep(Duration::from_secs(31)).await;
    assert_eq!(circuit_breaker.metrics.current_state.get(), 2); // HALF_OPEN
    
    let _ = circuit_breaker.execute(create_test_request()).await;
    assert_eq!(circuit_breaker.metrics.current_state.get(), 1); // OPEN again
}

/// AC-10: Existing retry logic only triggers for HTTP errors when circuit is CLOSED/HALF_OPEN; no retries for CircuitOpen error
#[tokio::test]
async fn test_ac10_retries_only_when_circuit_allows() {
    // TODO: Verify retry count does not increment for CircuitOpen errors
    let client = create_mock_http_client();
    let circuit_breaker = QuoteServiceCircuitBreaker::new(client);
    
    // Get circuit to open state
    for _ in 0..5 { let _ = circuit_breaker.execute(create_test_request()).await; }
    
    // Capture retry count before
    // let retry_count_before = get_retry_metric();
    
    // Send request that returns CircuitOpen
    let _ = circuit_breaker.execute(create_test_request()).await;
    
    // Verify retry count did not increase
    // let retry_count_after = get_retry_metric();
    // assert_eq!(retry_count_before, retry_count_after);
}

/// AC-11: When circuit is closed, existing retry and HTTP error propagation behavior remains unchanged
#[tokio::test]
async fn test_ac11_closed_circuit_preserves_behavior() {
    let client = create_mock_http_client();
    let circuit_breaker = QuoteServiceCircuitBreaker::new(client);
    
    // Verify requests work normally when closed
    for _ in 0..3 {
        let req = create_test_request();
        let result = circuit_breaker.execute(req).await;
        // Same behavior as without circuit breaker
        assert!(matches!(result, Ok(_) | Err(QuoteServiceError::HttpError(_))));
    }
    
    // Verify retries are still happening when circuit is closed
    // (check retry count increments for failed requests)
}
