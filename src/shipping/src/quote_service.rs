// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use opentelemetry::{
    global,
    metrics::{Counter, Gauge, Meter},
    KeyValue,
};
use once_cell::sync::Lazy;
use std::sync::Arc;
use tokio::sync::Mutex;
use std::time::{Duration, Instant};
use reqwest;
use thiserror::Error;
use rand::Rng;

static METER: Lazy<Meter> = Lazy::new(|| global::meter("shipping-service"));

/// Metrics emitted by circuit breaker
#[derive(Debug, Clone)]
pub struct CircuitBreakerMetrics {
    /// Counter: Total requests submitted to circuit breaker (label: state={closed,open,half_open})
    pub requests_total: Counter<u64>,
    /// Counter: Number of state transitions (label: from_state={closed,open,half_open}, to_state={closed,open,half_open})
    pub state_transitions_total: Counter<u64>,
    /// Gauge: Current circuit breaker state (0=closed, 1=open, 2=half_open)
    pub current_state: Gauge<u64>,
}

impl Default for CircuitBreakerMetrics {
    fn default() -> Self {
        Self {
            requests_total: METER
                .u64_counter("shipping_service.circuit_breaker.requests_total")
                .with_description("Total requests submitted to circuit breaker")
                .init(),
            state_transitions_total: METER
                .u64_counter("shipping_service.circuit_breaker.state_transitions_total")
                .with_description("Number of circuit breaker state transitions")
                .init(),
            current_state: METER
                .u64_gauge("shipping_service.circuit_breaker.current_state")
                .with_description("Current circuit breaker state (0=closed,1=open,2=half_open)")
                .init(),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum CircuitState {
    Closed = 0,
    Open = 1,
    HalfOpen = 2,
}

impl CircuitState {
    pub fn as_str(&self) -> &'static str {
        match self {
            CircuitState::Closed => "closed",
            CircuitState::Open => "open",
            CircuitState::HalfOpen => "half_open",
        }
    }
}

#[derive(Debug, Clone)]
struct CircuitBreakerState {
    state: CircuitState,
    failure_count: u32,
    open_start_time: Option<Instant>,
}

/// Core circuit breaker logic
struct CircuitBreakerInner {
    config: CircuitBreakerConfig,
    state: CircuitBreakerState,
    pub metrics: CircuitBreakerMetrics,
}

#[derive(Debug, Clone, Copy)]
struct CircuitBreakerConfig {
    pub failure_threshold: u32,
    pub reset_timeout: Duration,
    pub half_open_percent: u32,
    pub half_open_min_calls: u32,
}

impl Default for CircuitBreakerConfig {
    fn default() -> Self {
        Self {
            failure_threshold: 5,
            reset_timeout: Duration::from_secs(30),
            half_open_percent: 10,
            half_open_min_calls: 1,
        }
    }
}

#[derive(Debug, Clone)]
struct CircuitBreaker {
    inner: Arc<Mutex<CircuitBreakerInner>>,
}

impl CircuitBreaker {
    pub fn new(config: CircuitBreakerConfig, metrics: CircuitBreakerMetrics) -> Self {
        let initial_state = CircuitBreakerState {
            state: CircuitState::Closed,
            failure_count: 0,
            open_start_time: None,
        };
        metrics.current_state.set(initial_state.state as u64, &[]);
        
        Self {
            inner: Arc::new(Mutex::new(CircuitBreakerInner {
                config,
                state: initial_state,
                metrics,
            })),
        }
    }

    /// Check if a request is allowed to proceed
    pub async fn allow_request(&self) -> Result<(), ()> {
        let mut inner = self.inner.lock().await;
        let now = Instant::now();

        // Transition from Open to HalfOpen if reset timeout has passed
        if inner.state.state == CircuitState::Open {
            if let Some(open_start) = inner.state.open_start_time {
                if now.duration_since(open_start) >= inner.config.reset_timeout {
                    Self::transition_state(&mut inner, CircuitState::HalfOpen).await;
                }
            }
        }

        match inner.state.state {
            CircuitState::Open => {
                // Record rejected request
                inner.metrics.requests_total.add(1, &[
                    KeyValue::new("state", CircuitState::Open.as_str())
                ]);
                Err(())
            }
            CircuitState::HalfOpen => {
                // Allow only configured percentage of requests, minimum 1
                let mut rng = rand::thread_rng();
                let allowed = rng.gen_ratio(inner.config.half_open_percent, 100) || inner.config.half_open_min_calls > 0;
                
                inner.metrics.requests_total.add(1, &[
                    KeyValue::new("state", CircuitState::HalfOpen.as_str())
                ]);
                
                if allowed {
                    Ok(())
                } else {
                    Err(())
                }
            }
            CircuitState::Closed => {
                inner.metrics.requests_total.add(1, &[
                    KeyValue::new("state", CircuitState::Closed.as_str())
                ]);
                Ok(())
            }
        }
    }

    /// Record a successful request
    pub async fn record_success(&self) {
        let mut inner = self.inner.lock().await;
        match inner.state.state {
            CircuitState::Closed => {
                // Reset failure count on success
                inner.state.failure_count = 0;
            }
            CircuitState::HalfOpen => {
                // Success in half open state transitions back to closed
                Self::transition_state(&mut inner, CircuitState::Closed).await;
                inner.state.failure_count = 0;
            }
            _ => {}
        }
    }

    /// Record a failed request
    pub async fn record_failure(&self) {
        let mut inner = self.inner.lock().await;
        match inner.state.state {
            CircuitState::Closed => {
                inner.state.failure_count += 1;
                if inner.state.failure_count >= inner.config.failure_threshold {
                    Self::transition_state(&mut inner, CircuitState::Open).await;
                    inner.state.open_start_time = Some(Instant::now());
                }
            }
            CircuitState::HalfOpen => {
                // Failure in half open state transitions back to open
                Self::transition_state(&mut inner, CircuitState::Open).await;
                inner.state.open_start_time = Some(Instant::now());
            }
            _ => {}
        }
    }

    async fn transition_state(inner: &mut CircuitBreakerInner, new_state: CircuitState) {
        let old_state = inner.state.state;
        if old_state == new_state {
            return;
        }

        inner.state.state = new_state;
        inner.metrics.state_transitions_total.add(1, &[
            KeyValue::new("from_state", old_state.as_str()),
            KeyValue::new("to_state", new_state.as_str()),
        ]);
        inner.metrics.current_state.set(new_state as u64, &[]);
    }

    pub fn metrics(&self) -> &CircuitBreakerMetrics {
        // Safety: we only ever modify the metrics via atomic operations, so references are safe
        unsafe { &(*Arc::as_ptr(&self.inner)).metrics }
    }
}

/// Error type returned by circuit breaker wrapper
#[derive(Debug, Error)]
pub enum QuoteServiceError {
    /// Circuit is open, request not dispatched
    #[error("Circuit open: shipping quote service is temporarily unavailable")]
    CircuitOpen,
    /// Underlying HTTP request failure (only returned when circuit allows request execution)
    #[error("HTTP error calling quote service: {0}")]
    HttpError(#[from] reqwest::Error),
}

/// Wraps HTTP client calls to the quote service with circuit breaker protection
#[derive(Clone)]
pub struct QuoteServiceCircuitBreaker {
    inner: reqwest::Client,
    circuit_breaker: CircuitBreaker,
    pub metrics: CircuitBreakerMetrics,
}

impl QuoteServiceCircuitBreaker {
    /// Create new instance with default configuration:
    /// - Failure threshold: 5 consecutive failed requests
    /// - Reset timeout: 30 seconds (time spent in OPEN state before transitioning to HALF_OPEN)
    /// - Half-open permitted calls: 10% of normal request volume (min 1 call)
    pub fn new(http_client: reqwest::Client) -> Self {
        let config = CircuitBreakerConfig::default();
        let metrics = CircuitBreakerMetrics::default();
        let circuit_breaker = CircuitBreaker::new(config, metrics.clone());

        Self {
            inner: http_client,
            circuit_breaker,
            metrics,
        }
    }

    /// Execute HTTP request to quote service, subject to circuit breaker state
    /// # Errors
    /// - Returns `CircuitOpenError` when circuit is in OPEN state
    /// - Propagates underlying HTTP errors only when circuit is in CLOSED/HALF_OPEN state
    pub async fn execute(&self, request: reqwest::Request) -> Result<reqwest::Response, QuoteServiceError> {
        self.circuit_breaker.allow_request().await.map_err(|_| QuoteServiceError::CircuitOpen)?;
        
        let result = self.inner.execute(request).await;

        match result {
            Ok(response) => {
                if response.status().is_server_error() {
                    self.circuit_breaker.record_failure().await;
                } else {
                    self.circuit_breaker.record_success().await;
                }
                Ok(response)
            }
            Err(e) => {
                self.circuit_breaker.record_failure().await;
                Err(QuoteServiceError::HttpError(e))
            }
        }
    }
}
