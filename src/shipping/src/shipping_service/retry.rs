use std::future::Future;
use std::pin::Pin;
use std::time::{Duration, Instant};
use std::env;

use tracing::{info, error};

/// Trait for errors that can be retried
pub trait RetryableError: std::fmt::Display + std::fmt::Debug {
    /// Return true if this error is transient and should be retried
    fn is_retryable(&self) -> bool;
}

/// Configuration for retry logic
#[derive(Debug, Clone, Copy)]
pub struct RetryConfig {
    /// Maximum number of retry attempts
    pub max_retries: u32,
    /// Initial delay before first retry in milliseconds
    pub initial_delay_ms: u32,
    /// Jitter factor to add randomness to retry delays (0.0 to 1.0)
    pub jitter_factor: f64,
}

impl Default for RetryConfig {
    fn default() -> Self {
        Self {
            max_retries: 3,
            initial_delay_ms: 100,
            jitter_factor: 0.1,
        }
    }
}

impl RetryConfig {
    /// Create RetryConfig from environment variables
    pub fn from_env() -> Self {
        let max_retries = env::var("QUOTE_SERVICE_MAX_RETRIES")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(3);
        
        let initial_delay_ms = env::var("QUOTE_SERVICE_RETRY_INITIAL_DELAY_MS")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(100);
        
        Self {
            max_retries,
            initial_delay_ms,
            ..Default::default()
        }
    }
}

/// Execute an operation with exponential backoff retry logic
pub async fn with_retry<T, E, F>(
    config: RetryConfig,
    mut operation: impl FnMut() -> F,
) -> Result<T, E>
where
    E: RetryableError,
    F: Future<Output = Result<T, E>>,
{
    let mut attempt = 0;
    let start_time = Instant::now();
    
    loop {
        match operation().await {
            Ok(result) => return Ok(result),
            Err(err) => {
                attempt += 1;
                
                if !err.is_retryable() || attempt > config.max_retries {
                    if attempt > config.max_retries {
                        // Log retry exhaustion
                        error!(
                            event = "quote_service_retries_exhausted",
                            total_attempts = attempt,
                            total_elapsed_ms = start_time.elapsed().as_millis() as u64,
                            error_type = std::any::type_name::<E>(),
                            error_message = %err,
                            "All quote service retry attempts exhausted"
                        );
                    }
                    return Err(err);
                }
                
                // Calculate exponential backoff delay
                let delay_ms = config.initial_delay_ms * (2_u32).pow(attempt - 1);
                
                // Apply jitter if configured
                let jitter = if config.jitter_factor > 0.0 {
                    let jitter_range = (delay_ms as f64) * config.jitter_factor;
                    (rand::random::<f64>() * jitter_range) as u32
                } else {
                    0
                };
                
                let total_delay_ms = delay_ms + jitter;
                
                // Log retry attempt
                info!(
                    event = "quote_service_retry_attempt",
                    attempt_number = attempt,
                    max_attempts = config.max_retries,
                    delay_ms = total_delay_ms,
                    error_type = std::any::type_name::<E>(),
                    error_message = %err,
                    "Retrying quote service request"
                );
                
                // Wait before next attempt
                tokio::time::sleep(Duration::from_millis(total_delay_ms as u64)).await;
            }
        }
    }
}
