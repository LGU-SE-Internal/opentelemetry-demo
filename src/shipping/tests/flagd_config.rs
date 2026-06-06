use std::env;
use std::net::TcpListener;
use shipping::main; // import the main service init logic

/// Test AC-1: FLAGD_HOST env var usage
#[test]
fn test_ac1_flagd_host_custom_value() {
    // Set custom host
    env::set_var("FLAGD_HOST", "flagd.example.com");
    
    // Initialize service
    let provider = shipping::init_flagd_provider();
    
    // Verify host is set to custom value
    assert_eq!(provider.options().host(), "flagd.example.com");
    
    env::remove_var("FLAGD_HOST");
}

#[test]
fn test_ac1_flagd_host_default_value() {
    // Ensure no FLAGD_HOST is set
    env::remove_var("FLAGD_HOST");
    
    // Initialize service
    let provider = shipping::init_flagd_provider();
    
    // Verify default host is used
    assert_eq!(provider.options().host(), "localhost");
}

/// Test AC-2: FLAGD_PORT env var usage and validation
#[test]
fn test_ac2_flagd_port_valid_value() {
    env::set_var("FLAGD_PORT", "1234");
    
    let provider = shipping::init_flagd_provider();
    
    assert_eq!(provider.options().port(), 1234);
    
    env::remove_var("FLAGD_PORT");
}

#[test]
fn test_ac2_flagd_port_invalid_too_high() {
    env::set_var("FLAGD_PORT", "65536");
    
    let provider = shipping::init_flagd_provider();
    
    // Should fall back to default
    assert_eq!(provider.options().port(), 8013);
    // Should log warning - we can capture logs in extended test
    
    env::remove_var("FLAGD_PORT");
}

#[test]
fn test_ac2_flagd_port_invalid_zero() {
    env::set_var("FLAGD_PORT", "0");
    
    let provider = shipping::init_flagd_provider();
    
    assert_eq!(provider.options().port(), 8013);
    
    env::remove_var("FLAGD_PORT");
}

#[test]
fn test_ac2_flagd_port_invalid_non_numeric() {
    env::set_var("FLAGD_PORT", "not-a-number");
    
    let provider = shipping::init_flagd_provider();
    
    assert_eq!(provider.options().port(), 8013);
    
    env::remove_var("FLAGD_PORT");
}

#[test]
fn test_ac2_flagd_port_default_value() {
    env::remove_var("FLAGD_PORT");
    
    let provider = shipping::init_flagd_provider();
    
    assert_eq!(provider.options().port(), 8013);
}

/// Test AC-3: FLAGD_CONNECTION_TIMEOUT env var usage and validation
#[test]
fn test_ac3_connection_timeout_valid_value() {
    env::set_var("FLAGD_CONNECTION_TIMEOUT", "10000");
    
    let provider = shipping::init_flagd_provider();
    
    assert_eq!(provider.options().connection_timeout_ms(), 10000);
    
    env::remove_var("FLAGD_CONNECTION_TIMEOUT");
}

#[test]
fn test_ac3_connection_timeout_invalid_negative() {
    env::set_var("FLAGD_CONNECTION_TIMEOUT", "-500");
    
    let provider = shipping::init_flagd_provider();
    
    assert_eq!(provider.options().connection_timeout_ms(), 5000);
    
    env::remove_var("FLAGD_CONNECTION_TIMEOUT");
}

#[test]
fn test_ac3_connection_timeout_invalid_non_numeric() {
    env::set_var("FLAGD_CONNECTION_TIMEOUT", "abc");
    
    let provider = shipping::init_flagd_provider();
    
    assert_eq!(provider.options().connection_timeout_ms(), 5000);
    
    env::remove_var("FLAGD_CONNECTION_TIMEOUT");
}

#[test]
fn test_ac3_connection_timeout_default_value() {
    env::remove_var("FLAGD_CONNECTION_TIMEOUT");
    
    let provider = shipping::init_flagd_provider();
    
    assert_eq!(provider.options().connection_timeout_ms(), 5000);
}

/// Test AC-4: FLAGD_RETRY_MAX_ATTEMPTS env var usage and validation
#[test]
fn test_ac4_retry_max_attempts_valid_value() {
    env::set_var("FLAGD_RETRY_MAX_ATTEMPTS", "5");
    
    let provider = shipping::init_flagd_provider();
    
    assert_eq!(provider.options().retry_max_attempts(), 5);
    
    env::remove_var("FLAGD_RETRY_MAX_ATTEMPTS");
}

#[test]
fn test_ac4_retry_max_attempts_invalid_negative() {
    env::set_var("FLAGD_RETRY_MAX_ATTEMPTS", "-1");
    
    let provider = shipping::init_flagd_provider();
    
    assert_eq!(provider.options().retry_max_attempts(), 3);
    
    env::remove_var("FLAGD_RETRY_MAX_ATTEMPTS");
}

#[test]
fn test_ac4_retry_max_attempts_default_value() {
    env::remove_var("FLAGD_RETRY_MAX_ATTEMPTS");
    
    let provider = shipping::init_flagd_provider();
    
    assert_eq!(provider.options().retry_max_attempts(), 3);
}

/// Test AC-5: No panic on flagd initialization failure
#[test]
fn test_ac5_no_panic_on_connection_failure() {
    // Bind to a random port and immediately close it to get an unused port
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let unused_port = listener.local_addr().unwrap().port();
    drop(listener);
    
    env::set_var("FLAGD_PORT", unused_port.to_string());
    env::set_var("FLAGD_RETRY_MAX_ATTEMPTS", "0");
    
    // This should not panic
    let result = std::panic::catch_unwind(|| {
        shipping::init_flagd_provider()
    });
    
    assert!(result.is_ok());
    
    env::remove_var("FLAGD_PORT");
    env::remove_var("FLAGD_RETRY_MAX_ATTEMPTS");
}

/// Test AC-6: Fallback to no-op provider on initialization failure, returns default flag values
#[test]
fn test_ac6_fallback_to_no_op_provider() {
    // Use unused port to ensure connection fails
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let unused_port = listener.local_addr().unwrap().port();
    drop(listener);
    
    env::set_var("FLAGD_PORT", unused_port.to_string());
    env::set_var("FLAGD_RETRY_MAX_ATTEMPTS", "0");
    
    let provider = shipping::init_flagd_provider();
    
    // Verify we get a no-op provider that returns default values
    let test_flag = provider.get_bool_value("test_feature_flag", false, &[]).unwrap();
    assert_eq!(test_flag, false); // should return default
    
    env::remove_var("FLAGD_PORT");
    env::remove_var("FLAGD_RETRY_MAX_ATTEMPTS");
}

/// Test AC-7: All defaults match original hardcoded behavior when no env vars are set
#[test]
fn test_ac7_all_defaults_when_no_env_vars() {
    // Remove all flagd env vars
    env::remove_var("FLAGD_HOST");
    env::remove_var("FLAGD_PORT");
    env::remove_var("FLAGD_CONNECTION_TIMEOUT");
    env::remove_var("FLAGD_RETRY_MAX_ATTEMPTS");
    
    let provider = shipping::init_flagd_provider();
    let options = provider.options();
    
    assert_eq!(options.host(), "localhost");
    assert_eq!(options.port(), 8013);
    assert_eq!(options.connection_timeout_ms(), 5000);
    assert_eq!(options.retry_max_attempts(), 3);
}
