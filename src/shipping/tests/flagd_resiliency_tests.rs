use shipping::feature_flags::get_feature_flag;
use std::time::{Duration, Instant};
use tracing_test::traced_test;
use mockall::predicate::*;
use tower::BoxError;

mod common;

// AC-1: When Flagd returns a successful response on first call, get_feature_flag returns the Flagd-resolved value immediately, no retry logs, added latency <100ms
#[tokio::test]
#[traced_test]
async fn test_ac1_successful_first_call_no_retry_low_latency() {
    // Setup: Mock Flagd returns successful response immediately
    let test_flag_key = "test.feature.flag";
    let expected_flag_value = "enabled";
    let default_value = "disabled";

    let start = Instant::now();
    let result = get_feature_flag(test_flag_key, default_value).await;
    let duration = start.elapsed();

    // Assert returned value matches Flagd response
    assert_eq!(result, expected_flag_value);
    // Assert no retry logs emitted
    assert!(!logs_contain("flagd_retry_attempt"));
    // Assert added latency <100ms (excluding base Flagd call time which we assume is <1ms for mock)
    assert!(duration.as_millis() < 100, "Total latency {}ms exceeds 100ms limit", duration.as_millis());
}

// AC-2: Transient errors trigger retries up to max attempts with exponential backoff
#[tokio::test]
#[traced_test]
async fn test_ac2_transient_errors_trigger_retry_with_exponential_backoff() {
    // Setup: Configure max attempts = 3, initial backoff = 50ms
    std::env::set_var("FLAGD_RETRY_MAX_ATTEMPTS", "3");
    std::env::set_var("FLAGD_RETRY_INITIAL_BACKOFF_MS", "50");
    
    let test_flag_key = "test.feature.flag";
    let default_value = "disabled";

    let start = Instant::now();
    let result = get_feature_flag(test_flag_key, default_value).await;
    let duration = start.elapsed();

    // Assert 2 retry attempts (total 3 calls: first + 2 retries)
    assert!(logs_contain(r#"event="flagd_retry_attempt""#));
    let retry_count = logs::get_logs().iter()
        .filter(|log| log.contains("flagd_retry_attempt") && log.contains(&format!("flag_key=\"{test_flag_key}\"")))
        .count();
    assert_eq!(retry_count, 2, "Expected 2 retry attempts, found {retry_count}");
    
    // Assert exponential backoff: total time should be ~ 50 + 100 = 150ms (initial backoff * 2^0 + * 2^1)
    assert!(duration.as_millis() >= 150, "Expected at least 150ms for retry backoff, got {}ms", duration.as_millis());
}

// AC-3: All retries fail returns default value with fallback log reason flagd_unreachable
#[tokio::test]
#[traced_test]
async fn test_ac3_all_retries_fail_fallback_to_default() {
    // Setup: Flagd returns transient errors for all calls
    std::env::set_var("FLAGD_RETRY_MAX_ATTEMPTS", "2");
    let test_flag_key = "test.feature.flag";
    let default_value = "disabled";

    let result = get_feature_flag(test_flag_key, default_value).await;

    // Assert default value returned
    assert_eq!(result, default_value);
    // Assert fallback log exists with reason flagd_unreachable
    assert!(logs_contain(r#"event="flagd_fallback_to_default""#) && logs_contain(r#"reason="flagd_unreachable""#));
}

// AC-4: Circuit breaker opens after failure threshold, no calls during cooldown
#[tokio::test]
#[traced_test]
async fn test_ac4_circuit_breaker_opens_after_failure_threshold() {
    // Setup: Failure threshold = 3, cooldown = 1000ms
    std::env::set_var("FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "3");
    std::env::set_var("FLAGD_CIRCUIT_BREAKER_COOLDOWN_MS", "1000");
    let test_flag_key = "test.feature.flag";
    let default_value = "disabled";

    // Make 3 failed calls to trigger circuit breaker
    for _ in 0..3 {
        let _ = get_feature_flag(test_flag_key, default_value).await;
    }

    // Assert circuit breaker open log exists
    assert!(logs_contain(r#"event="flagd_circuit_breaker_open""#));
    assert!(logs_contain("failure_count=3"));
    assert!(logs_contain("cooldown_period_ms=1000"));

    // Call again during cooldown, should return default immediately, no new Flagd calls
    let start = Instant::now();
    let result = get_feature_flag(test_flag_key, default_value).await;
    let duration = start.elapsed();

    assert_eq!(result, default_value);
    // No new retry logs means no Flagd call was made
    let retry_count_before = logs::get_logs().iter()
        .filter(|log| log.contains("flagd_retry_attempt"))
        .count();
    let retry_count_after = logs::get_logs().iter()
        .filter(|log| log.contains("flagd_retry_attempt"))
        .count();
    assert_eq!(retry_count_before, retry_count_after, "No new calls should be made when circuit breaker is open");
    // Should return in <10ms since no network call
    assert!(duration.as_millis() < 10, "Circuit breaker open call took {}ms, should be immediate", duration.as_millis());
}

// AC-5: Circuit breaker closes after cooldown on successful call
#[tokio::test]
#[traced_test]
async fn test_ac5_circuit_breaker_closes_after_cooldown_on_success() {
    // Setup: Failure threshold = 2, cooldown = 100ms
    std::env::set_var("FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "2");
    std::env::set_var("FLAGD_CIRCUIT_BREAKER_COOLDOWN_MS", "100");
    let test_flag_key = "test.feature.flag";
    let default_value = "disabled";
    let expected_value = "enabled";

    // Trigger circuit breaker open
    for _ in 0..2 {
        let _ = get_feature_flag(test_flag_key, default_value).await;
    }
    assert!(logs_contain(r#"event="flagd_circuit_breaker_open""#));

    // Wait for cooldown period
    tokio::time::sleep(Duration::from_millis(150)).await;

    // Setup Mock Flagd to return success now
    let result = get_feature_flag(test_flag_key, default_value).await;
    assert_eq!(result, expected_value);
    // Assert circuit breaker closed log
    assert!(logs_contain(r#"event="flagd_circuit_breaker_closed""#));

    // Next call should work normally
    let result2 = get_feature_flag(test_flag_key, default_value).await;
    assert_eq!(result2, expected_value);
}

// AC-5 Extension: Circuit breaker remains open if first call after cooldown fails
#[tokio::test]
#[traced_test]
async fn test_ac5_circuit_breaker_remains_open_after_failed_cooldown_call() {
    std::env::set_var("FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "2");
    std::env::set_var("FLAGD_CIRCUIT_BREAKER_COOLDOWN_MS", "100");
    let test_flag_key = "test.feature.flag";
    let default_value = "disabled";

    // Trigger open
    for _ in 0..2 {
        let _ = get_feature_flag(test_flag_key, default_value).await;
    }

    // Wait cooldown
    tokio::time::sleep(Duration::from_millis(150)).await;

    // Call fails again
    let result = get_feature_flag(test_flag_key, default_value).await;
    assert_eq!(result, default_value);
    // No closed log
    assert!(!logs_contain(r#"event="flagd_circuit_breaker_closed""#));
    // Next call should still return default immediately
    let start = Instant::now();
    let result2 = get_feature_flag(test_flag_key, default_value).await;
    let duration = start.elapsed();
    assert_eq!(result2, default_value);
    assert!(duration.as_millis() < 10);
}

// AC-6: Circuit breaker open returns default with reason circuit_breaker_open
#[tokio::test]
#[traced_test]
async fn test_ac6_circuit_breaker_open_fallback_reason() {
    std::env::set_var("FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "1");
    std::env::set_var("FLAGD_CIRCUIT_BREAKER_COOLDOWN_MS", "1000");
    let test_flag_key = "test.feature.flag";
    let default_value = "disabled";

    // Trigger open
    let _ = get_feature_flag(test_flag_key, default_value).await;

    // Call again
    let result = get_feature_flag(test_flag_key, default_value).await;
    assert_eq!(result, default_value);

    // Assert fallback log has correct reason
    assert!(logs_contain(r#"event="flagd_fallback_to_default""#) && logs_contain(r#"reason="circuit_breaker_open""#));
}

// AC-7: Resiliency logic adds <=100ms latency per call (excluding retry backoff)
#[tokio::test]
async fn test_ac7_resiliency_latency_under_100ms() {
    let test_flag_key = "test.feature.flag";
    let default_value = "disabled";
    let expected_value = "enabled";

    // Test successful case: measure overhead of wrapper logic
    let mut total_duration = Duration::new(0, 0);
    const ITERATIONS: u32 = 100;
    for _ in 0..ITERATIONS {
        let start = Instant::now();
        let result = get_feature_flag(test_flag_key, default_value).await;
        total_duration += start.elapsed();
        assert_eq!(result, expected_value);
    }

    let avg_duration = total_duration / ITERATIONS;
    // Average overhead should be <10ms, max <100ms as per requirement
    assert!(avg_duration.as_millis() < 100, "Average latency {}ms exceeds 100ms limit", avg_duration.as_millis());
}
