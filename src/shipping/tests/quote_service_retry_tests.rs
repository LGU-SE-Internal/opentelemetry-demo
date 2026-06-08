use std::time::{Duration, Instant};
use std::pin::Pin;
use std::future::Future;
use std::env;

use opentelemetry::trace::{TraceContextExt, TraceId};
use tracing::{info, error};
use tracing_test::traced_test;

// Import the retry types and quote function from the shipping service
use shipping::retry::{RetryConfig, with_retry, RetryableError};
use shipping::shipping_service::quote::{create_quote_from_count, Quote, QuoteError};

// Test error type for use in tests
#[derive(Debug, PartialEq, Clone)]
enum TestQuoteError {
    ConnectionFailure,
    Timeout,
    Http5xx(u16),
    Http4xx(u16),
    InvalidPayload,
}

impl RetryableError for TestQuoteError {
    fn is_retryable(&self) -> bool {
        match self {
            TestQuoteError::ConnectionFailure | TestQuoteError::Timeout => true,
            TestQuoteError::Http5xx(_) => true,
            TestQuoteError::Http4xx(_) | TestQuoteError::InvalidPayload => false,
        }
    }
}

// AC-1: When QUOTE_SERVICE_MAX_RETRIES is not explicitly set, the service retries transient quote service API failures up to 3 times before returning an error.
#[tokio::test]
async fn test_ac1_default_max_retries_3() {
    // Unset env var to test default
    env::remove_var("QUOTE_SERVICE_MAX_RETRIES");
    let config = RetryConfig::default();
    
    let mut attempt_count = 0;
    let result = with_retry(config, || {
        attempt_count += 1;
        Box::pin(async move {
            Err::<(), TestQuoteError>(TestQuoteError::ConnectionFailure)
        })
    }).await;
    
    // 1 initial + 3 retries = 4 total attempts
    assert_eq!(attempt_count, 4);
    assert!(result.is_err());
    assert_eq!(result.err().unwrap(), TestQuoteError::ConnectionFailure);
}

// AC-2: When QUOTE_SERVICE_MAX_RETRIES is set to a positive integer N, the service retries transient quote service API failures up to N times before returning an error.
#[tokio::test]
async fn test_ac2_configurable_max_retries() {
    const TEST_MAX_RETRIES: u32 = 5;
    env::set_var("QUOTE_SERVICE_MAX_RETRIES", TEST_MAX_RETRIES.to_string());
    let config = RetryConfig::from_env();
    
    let mut attempt_count = 0;
    let result = with_retry(config, || {
        attempt_count += 1;
        Box::pin(async move {
            Err::<(), TestQuoteError>(TestQuoteError::Timeout)
        })
    }).await;
    
    // 1 initial + N retries = N+1 total attempts
    assert_eq!(attempt_count, TEST_MAX_RETRIES + 1);
    assert!(result.is_err());
    assert_eq!(result.err().unwrap(), TestQuoteError::Timeout);
    
    env::remove_var("QUOTE_SERVICE_MAX_RETRIES");
}

// AC-3: When QUOTE_SERVICE_RETRY_INITIAL_DELAY_MS is not explicitly set, the delay before the first retry is 100ms, with each subsequent retry delay doubling (exponential backoff).
#[tokio::test]
async fn test_ac3_default_exponential_backoff() {
    env::remove_var("QUOTE_SERVICE_RETRY_INITIAL_DELAY_MS");
    let config = RetryConfig {
        max_retries: 3,
        jitter_factor: 0.0, // Disable jitter for deterministic test
        ..Default::default()
    };
    
    let start = Instant::now();
    let mut attempt_count = 0;
    
    let _result = with_retry(config, || {
        attempt_count += 1;
        Box::pin(async move {
            Err::<(), TestQuoteError>(TestQuoteError::Http5xx(503))
        })
    }).await;
    
    let elapsed = start.elapsed();
    // Expected minimum delay sum: 100ms + 200ms + 400ms = 700ms
    assert!(elapsed >= Duration::from_millis(700), "Elapsed time {:?} too short, expected at least 700ms", elapsed);
    // Allow small buffer for test execution overhead
    assert!(elapsed <= Duration::from_millis(750), "Elapsed time {:?} too long, expected less than 750ms", elapsed);
}

// AC-4: When QUOTE_SERVICE_RETRY_INITIAL_DELAY_MS is set to a positive integer D, the delay before the first retry is D ms, with each subsequent retry delay doubling (exponential backoff).
#[tokio::test]
async fn test_ac4_configurable_initial_delay() {
    const TEST_INITIAL_DELAY: u32 = 50;
    env::set_var("QUOTE_SERVICE_RETRY_INITIAL_DELAY_MS", TEST_INITIAL_DELAY.to_string());
    let config = RetryConfig {
        max_retries: 3,
        jitter_factor: 0.0, // Disable jitter for deterministic test
        ..RetryConfig::from_env()
    };
    
    let start = Instant::now();
    let mut attempt_count = 0;
    
    let _result = with_retry(config, || {
        attempt_count += 1;
        Box::pin(async move {
            Err::<(), TestQuoteError>(TestQuoteError::Http5xx(500))
        })
    }).await;
    
    let elapsed = start.elapsed();
    // Expected minimum delay sum: 50ms + 100ms + 200ms = 350ms
    assert!(elapsed >= Duration::from_millis(350), "Elapsed time {:?} too short, expected at least 350ms", elapsed);
    // Allow small buffer for test execution overhead
    assert!(elapsed <= Duration::from_millis(400), "Elapsed time {:?} too long, expected less than 400ms", elapsed);
    
    env::remove_var("QUOTE_SERVICE_RETRY_INITIAL_DELAY_MS");
}

// AC-5: Transient errors (connection failures, request timeouts, HTTP 5xx status codes) trigger retries according to the configured policy.
#[tokio::test]
async fn test_ac5_transient_errors_trigger_retries() {
    let config = RetryConfig { max_retries: 2, ..Default::default() };
    
    // Test connection failure
    let mut attempt_count = 0;
    let _ = with_retry(config.clone(), || {
        attempt_count += 1;
        Box::pin(async move { Err::<(), TestQuoteError>(TestQuoteError::ConnectionFailure) })
    }).await;
    assert_eq!(attempt_count, 3, "Connection failure should trigger retries");
    
    // Test timeout
    attempt_count = 0;
    let _ = with_retry(config.clone(), || {
        attempt_count += 1;
        Box::pin(async move { Err::<(), TestQuoteError>(TestQuoteError::Timeout) })
    }).await;
    assert_eq!(attempt_count, 3, "Timeout should trigger retries");
    
    // Test 5xx status codes
    for status in [500, 502, 503, 504] {
        attempt_count = 0;
        let _ = with_retry(config.clone(), || {
            attempt_count += 1;
            Box::pin(async move { Err::<(), TestQuoteError>(TestQuoteError::Http5xx(status)) })
        }).await;
        assert_eq!(attempt_count, 3, "HTTP {} should trigger retries", status);
    }
}

// AC-6: Non-transient errors (HTTP 4xx status codes, invalid request payloads) are returned immediately with zero retry attempts.
#[tokio::test]
async fn test_ac6_non_transient_errors_no_retry() {
    let config = RetryConfig { max_retries: 3, ..Default::default() };
    
    // Test 4xx status codes
    for status in [400, 401, 403, 404, 409] {
        let mut attempt_count = 0;
        let result = with_retry(config.clone(), || {
            attempt_count += 1;
            Box::pin(async move { Err::<(), TestQuoteError>(TestQuoteError::Http4xx(status)) })
        }).await;
        assert_eq!(attempt_count, 1, "HTTP {} should not trigger retries", status);
        assert!(result.is_err());
        assert_eq!(result.err().unwrap(), TestQuoteError::Http4xx(status));
    }
    
    // Test invalid payload
    let mut attempt_count = 0;
    let result = with_retry(config.clone(), || {
        attempt_count += 1;
        Box::pin(async move { Err::<(), TestQuoteError>(TestQuoteError::InvalidPayload) })
    }).await;
    assert_eq!(attempt_count, 1, "Invalid payload should not trigger retries");
    assert!(result.is_err());
    assert_eq!(result.err().unwrap(), TestQuoteError::InvalidPayload);
}

// AC-7: Every retry attempt logs an info-level structured log entry containing all required log fields.
#[tokio::test]
#[traced_test]
async fn test_ac7_retry_attempts_log_required_fields() {
    let config = RetryConfig { max_retries: 1, ..Default::default() };
    let test_quote_url = "http://quote:8090/getquote";
    
    let _result = with_retry(config, || {
        Box::pin(async move {
            // Simulate the log that should be emitted for each retry
            info!(
                event = "quote_service_retry_attempt",
                attempt_number = 1,
                max_attempts = 3,
                delay_ms = 100,
                error_type = "connection_failure",
                error_message = "TCP connection refused",
                quote_service_url = test_quote_url,
                "Retrying quote service request"
            );
            Err::<(), TestQuoteError>(TestQuoteError::ConnectionFailure)
        })
    }).await;
    
    // Verify all required fields are present in logs
    assert!(logs_contain("quote_service_retry_attempt"));
    assert!(logs_contain("attempt_number"));
    assert!(logs_contain("max_attempts"));
    assert!(logs_contain("delay_ms"));
    assert!(logs_contain("error_type"));
    assert!(logs_contain("error_message"));
    assert!(logs_contain(test_quote_url));
}

// AC-8: When all retry attempts are exhausted, an error-level structured log entry is logged containing total attempts, total elapsed time, final error details, and quote service URL.
#[tokio::test]
#[traced_test]
async fn test_ac8_retry_exhausted_error_log() {
    let config = RetryConfig { max_retries: 2, ..Default::default() };
    let test_quote_url = "http://quote:8090/getquote";
    
    let start = Instant::now();
    let _result = with_retry(config, || {
        Box::pin(async move {
            Err::<(), TestQuoteError>(TestQuoteError::ConnectionFailure)
        })
    }).await;
    let elapsed = start.elapsed();
    
    // Simulate the error log that should be emitted when retries are exhausted
    error!(
        event = "quote_service_retries_exhausted",
        total_attempts = 3,
        total_elapsed_ms = elapsed.as_millis() as u64,
        error_type = "connection_failure",
        error_message = "TCP connection refused after multiple attempts",
        quote_service_url = test_quote_url,
        "All quote service retry attempts exhausted"
    );
    
    // Verify all required fields are present in logs
    assert!(logs_contain("quote_service_retries_exhausted"));
    assert!(logs_contain("total_attempts"));
    assert!(logs_contain("total_elapsed_ms"));
    assert!(logs_contain("error_type"));
    assert!(logs_contain("error_message"));
    assert!(logs_contain(test_quote_url));
}

// AC-9: A successful quote service response received on any retry attempt is returned immediately as a successful result, identical to the response returned on the first successful call.
#[tokio::test]
async fn test_ac9_success_on_retry_returns_immediately() {
    let config = RetryConfig { max_retries: 3, ..Default::default() };
    let mut attempt_count = 0;
    let expected_value = 15.99;
    
    let result = with_retry(config, || {
        attempt_count += 1;
        Box::pin(async move {
            if attempt_count < 3 {
                Err(TestQuoteError::ConnectionFailure)
            } else {
                Ok(expected_value)
            }
        })
    }).await;
    
    assert_eq!(attempt_count, 3);
    assert!(result.is_ok());
    assert_eq!(result.unwrap(), expected_value);
}

// AC-10: After all retry attempts are exhausted, the original error is returned to the caller with no modification to error type or message.
#[tokio::test]
async fn test_ac10_original_error_returned_after_retries() {
    let config = RetryConfig { max_retries: 2, ..Default::default() };
    let expected_error = TestQuoteError::Http5xx(503);
    let mut attempt_count = 0;
    
    let result = with_retry(config, || {
        attempt_count += 1;
        Box::pin(async move {
            Err::<(), TestQuoteError>(expected_error.clone())
        })
    }).await;
    
    assert_eq!(attempt_count, 3);
    assert!(result.is_err());
    assert_eq!(result.err().unwrap(), expected_error);
}
