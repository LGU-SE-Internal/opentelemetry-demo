// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use std::{env, time::Duration};
use anyhow::Error;
use tracing::{info, error};
use tokio::time::sleep;

/// Retry configuration struct holding retry policy settings
#[derive(Debug, Clone)]
pub struct RetryConfig {
    pub max_retries: u32,
    pub initial_delay_ms: u32,
}

impl Default for RetryConfig {
    fn default() -> Self {
        // Read environment variables with defaults
        let max_retries = env::var("QUOTE_SERVICE_MAX_RETRIES")
            .ok()
            .and_then(|s| s.parse::<u32>().ok())
            .unwrap_or(3);
        
        let initial_delay_ms = env::var("QUOTE_SERVICE_RETRY_INITIAL_DELAY_MS")
            .ok()
            .and_then(|s| s.parse::<u32>().ok())
            .unwrap_or(100);
        
        Self {
            max_retries,
            initial_delay_ms,
        }
    }
}

/// Trait to determine if an error is retryable
pub trait RetryableError {
    fn is_retryable(&self) -> bool;
}

impl RetryableError for Error {
    fn is_retryable(&self) -> bool {
        // Check if the error wraps a retryable error type
        if let Some(send_err) = self.downcast_ref::<awc::error::SendRequestError>() {
            return send_err.is_retryable();
        }
        // Other error types are not retryable by default
        false
    }
}

/// Executes an async function with exponential backoff retry logic
pub async fn with_retry<F, Fut, T, E>(config: RetryConfig, mut f: F) -> Result<T, E>
where
    F: FnMut() -> Fut,
    Fut: std::future::Future<Output = Result<T, E>>,
    E: std::fmt::Display + RetryableError,
{
    let mut attempt = 0;
    let start_time = std::time::Instant::now();

    loop {
        match f().await {
            Ok(result) => {
                if attempt > 0 {
                    info!(
                        event = "quote_retry_succeeded",
                        attempt_number = attempt,
                        max_attempts = config.max_retries,
                        total_elapsed_ms = start_time.elapsed().as_millis() as u64,
                        "Quote request succeeded after retry attempt"
                    );
                }
                return Ok(result);
            }
            Err(err) if attempt < config.max_retries && err.is_retryable() => {
                attempt += 1;
                let delay_ms = config.initial_delay_ms * (2u32).pow(attempt - 1);
                
                info!(
                    event = "quote_retry_attempt",
                    attempt_number = attempt,
                    max_attempts = config.max_retries,
                    delay_ms = delay_ms,
                    error_type = std::any::type_name::<E>(),
                    error_message = %err,
                    quote_service_url = %env::var("QUOTE_ADDR").unwrap_or_else(|_| "http://quote:8090/getquote".to_string()),
                    "Retrying quote service request after transient error"
                );
                
                sleep(Duration::from_millis(delay_ms as u64)).await;
            }
            Err(err) => {
                if attempt > 0 {
                    error!(
                        event = "quote_retry_exhausted",
                        total_attempts = attempt,
                        max_attempts = config.max_retries,
                        total_elapsed_ms = start_time.elapsed().as_millis() as u64,
                        error_type = std::any::type_name::<E>(),
                        error_message = %err,
                        quote_service_url = %env::var("QUOTE_ADDR").unwrap_or_else(|_| "http://quote:8090/getquote".to_string()),
                        "All quote service retry attempts exhausted"
                    );
                }
                return Err(err);
            }
        }
    }
}
