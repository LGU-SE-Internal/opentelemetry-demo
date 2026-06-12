use std::pin::Pin;
use std::future::Future;
use std::time::Duration;
use std::env;

use rand::Rng;
use tokio::time::sleep;
use opentelemetry::{Context, global, metrics::{Counter, Histogram, Meter, KeyValue}};
use reqwest_retry::{RetryTransientMiddleware, policies::ExponentialBackoff, Retryable};
use reqwest::{Client, Request, Response};
use tracing::{warn, span, Level, Instrument};
use once_cell::sync::Lazy;

/// Metrics for shipping carrier API retries
#[derive(Debug, Clone)]
pub struct RetryMetrics {
    /// Counter for total retry attempts
    pub retry_count: Counter<u64>,
    /// Counter for requests that failed after all retries
    pub retry_failed_total: Counter<u64>,
    /// Histogram for total cumulative retry latency per request
    pub retry_latency_ms: Histogram<u64>,
}

impl Default for RetryMetrics {
    fn default() -> Self {
        let meter = global::meter("shipping-service");
        Self {
            retry_count: meter
                .u64_counter("shipping_carrier_api_retry_count")
                .with_description("Total number of retry attempts made to shipping carrier API")
                .init(),
            retry_failed_total: meter
                .u64_counter("shipping_carrier_api_retry_failed_total")
                .with_description("Number of requests that failed after all retry attempts")
                .init(),
            retry_latency_ms: meter
                .u64_histogram("shipping_carrier_api_retry_latency_ms")
                .with_description("Total cumulative delay between retries for a request")
                .init(),
        }
    }
}

static RETRY_METRICS: Lazy<RetryMetrics> = Lazy::new(RetryMetrics::default);

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
            jitter_factor: 1.0, // Full jitter per requirements
        }
    }
}

impl RetryConfig {
    /// Load retry configuration from environment variables
    pub fn from_env() -> Self {
        let max_attempts = env::var("SHIPPING_RETRY_MAX_ATTEMPTS")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(3);
        
        let initial_backoff_ms = env::var("SHIPPING_RETRY_INITIAL_BACKOFF_MS")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(100);
        
        Self {
            initial_backoff_ms,
            max_backoff_ms: initial_backoff_ms * (2u64.pow(max_attempts)),
            max_retries: max_attempts,
            jitter_factor: 1.0,
        }
    }
}

/// Custom retry policy that only retries GET requests and transient errors
#[derive(Debug, Clone)]
pub struct CarrierRetryPolicy {
    inner: ExponentialBackoff,
}

impl CarrierRetryPolicy {
    pub fn new(config: &RetryConfig) -> Self {
        let inner = ExponentialBackoff::builder()
            .base(Duration::from_millis(config.initial_backoff_ms))
            .retry_bounds(Duration::from_millis(config.initial_backoff_ms), Duration::from_millis(config.max_backoff_ms))
            .jitter(config.jitter_factor)
            .build_with_max_retries(config.max_retries);
        
        Self { inner }
    }
}

impl Retryable for CarrierRetryPolicy {
    type Error = reqwest::Error;

    fn should_retry(&self, req: &Request, res: Result<&Response, &Self::Error>) -> bool {
        // Only retry GET requests
        if req.method() != reqwest::Method::GET {
            return false;
        }

        match res {
            Ok(response) => {
                // Retry on 5xx status codes
                response.status().is_server_error()
            }
            Err(e) => {
                // Retry on network errors, timeouts, connection resets
                e.is_timeout() || e.is_connect() || e.is_request()
            }
        }
    }

    fn backoff(&self, attempt: u32) -> Duration {
        self.inner.backoff(attempt)
    }
}

/// Build a reqwest client with retry middleware configured for shipping carrier API requests
pub fn build_carrier_api_client(config: &RetryConfig) -> Client {
    let retry_policy = CarrierRetryPolicy::new(config);
    
    // Create a client with retry middleware, tracing, and metrics
    Client::builder()
        .timeout(Duration::from_secs(30))
        .connect_timeout(Duration::from_secs(10))
        .wrap(RetryTransientMiddleware::new_with_policy(retry_policy))
        .build()
        .expect("Failed to build carrier API client")
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
    let mut total_retry_delay = 0;

    // Capture the current OpenTelemetry context to preserve across retries
    let current_context = Context::current();

    loop {
        attempt += 1;

        // Attach the preserved context for this attempt
        let result = current_context.attach(|| operation()).await;

        match result {
            Ok(value) => {
                if attempt > 1 {
                    // Record total retry latency if there were retries
                    RETRY_METRICS.retry_latency_ms.record(total_retry_delay, &[]);
                }
                return Ok(value);
            }
            Err(error) => {
                if !error.is_retryable() || attempt > config.max_retries {
                    if attempt > 1 {
                        // Increment failed retry metric if all attempts failed
                        RETRY_METRICS.retry_failed_total.add(1, &[
                            KeyValue::new("endpoint", "unknown"), // TODO: populate with actual endpoint
                        ]);
                        RETRY_METRICS.retry_latency_ms.record(total_retry_delay, &[]);
                    }
                    return Err(error);
                }

                // Calculate backoff duration
                let base_backoff = config.initial_backoff_ms * (2u64.pow(attempt - 1));
                let base_backoff = base_backoff.min(config.max_backoff_ms);

                // Apply jitter
                let jitter = rand::thread_rng().gen_range(0.0..=config.jitter_factor);
                let backoff_ms = (base_backoff as f64 * jitter) as u64;
                let backoff_duration = Duration::from_millis(backoff_ms);
                
                total_retry_delay += backoff_ms;

                // Create retry span
                let retry_span = span!(
                    Level::INFO,
                    "shipping.carrier_api.retry",
                    "retry.attempt_number" = attempt,
                    "retry.previous_error" = %error,
                );

                // Log retry attempt
                warn!(
                    parent: &retry_span,
                    attempt = attempt,
                    max_attempts = config.max_retries + 1,
                    backoff_ms = backoff_ms,
                    error = %error,
                    "Retrying failed request after transient error"
                );
                
                // Increment retry count metric
                RETRY_METRICS.retry_count.add(1, &[
                    KeyValue::new("endpoint", "unknown"), // TODO: populate with actual endpoint
                    KeyValue::new("status", "attempt"),
                ]);

                // Wait for backoff duration
                sleep(backoff_duration).instrument(retry_span).await;
            }
        }
    }
}

// Implement RetryableError for reqwest::Error
impl RetryableError for reqwest::Error {
    fn is_retryable(&self) -> bool {
        if self.is_timeout() || self.is_connect() || self.is_request() {
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
