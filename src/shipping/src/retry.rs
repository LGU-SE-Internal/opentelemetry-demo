use std::pin::Pin;
use std::future::Future;
use std::time::Duration;

use rand::Rng;
use tokio::time::sleep;
use opentelemetry::Context;
use tracing::warn;

/// Configuration for exponential backoff retry policy
#[derive(Debug, Clone)]
pub struct RetryConfig {
    /// Initial backoff duration in milliseconds
    pub initial_backoff_ms: u64,
    /// Maximum backoff duration in milliseconds
    pub max_backoff_ms: u64,
    /// Maximum number of retry attempts (total attempts = 1 + max_retries)
    pub max_retries: u32,
    /// Jitter factor (0.0 = no jitter, 1.0 = full random jitter)
    pub jitter_factor: f64,
}

impl Default for RetryConfig {
    fn default() -> Self {
        Self {
            initial_backoff_ms: 100,
            max_backoff_ms: 2000,
            max_retries: 3,
            jitter_factor: 0.5,
        }
    }
}

/// Trait to determine if an error is retryable
pub trait RetryableError {
    fn is_retryable(&self) -> bool;
}

/// Wraps an async HTTP/GRPC call with exponential backoff retry logic
/// Preserves OpenTelemetry tracing context across all attempts
/// # Errors
/// Returns the last error encountered when all retries are exhausted
/// Returns immediately on non-retryable errors
pub async fn with_retry<T, E, F>(
    config: RetryConfig,
    operation: F,
) -> Result<T, E>
where
    F: Fn() -> Pin<Box<dyn Future<Output = Result<T, E>>>>,
    E: RetryableError + std::fmt::Display,
{
    let mut attempt = 0;

    // Capture the current OpenTelemetry context to preserve across retries
    let current_context = Context::current();

    loop {
        attempt += 1;

        // Attach the preserved context for this attempt
        let result = current_context.attach(|| operation()).await;

        match result {
            Ok(value) => return Ok(value),
            Err(error) => {
                if !error.is_retryable() || attempt > config.max_retries {
                    return Err(error);
                }

                // Calculate backoff duration
                let base_backoff = config.initial_backoff_ms * (2u64.pow(attempt - 1));
                let base_backoff = base_backoff.min(config.max_backoff_ms);

                // Apply jitter
                let jitter = rand::thread_rng().gen_range(0.0..config.jitter_factor);
                let backoff_ms = (base_backoff as f64 * (1.0 + jitter)) as u64;
                let backoff_duration = Duration::from_millis(backoff_ms);

                // Log retry attempt
                warn!(
                    attempt = attempt,
                    max_attempts = config.max_retries + 1,
                    backoff_ms = backoff_ms,
                    error = %error,
                    "Retrying failed request after transient error"
                );

                // Wait for backoff duration
                sleep(backoff_duration).await;
            }
        }
    }
}

// Implement RetryableError for reqwest::Error
#[cfg(feature = "reqwest")]
impl RetryableError for reqwest::Error {
    fn is_retryable(&self) -> bool {
        if self.is_timeout() || self.is_connect() {
            return true;
        }
        if let Some(status) = self.status() {
            return status.is_server_error();
        }
        false
    }
}

// Implement RetryableError for tonic::Status
#[cfg(feature = "tonic")]
impl RetryableError for tonic::Status {
    fn is_retryable(&self) -> bool {
        matches!(
            self.code(),
            tonic::Code::Aborted | tonic::Code::Unavailable | tonic::Code::DeadlineExceeded
        )
    }
}
