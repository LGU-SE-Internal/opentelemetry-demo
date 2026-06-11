//! Flagd resiliency layer with retry, circuit breaking and fallback logic

use std::{sync::Arc, time::Duration};

use lazy_static::lazy_static;
use open_feature::{Client, EvaluationContext, OpenFeature};
use tokio::sync::Mutex;
use tower::{Service, ServiceBuilder, ServiceExt};
use tower_circuit_breaker::{CircuitBreaker, CircuitBreakerError, State};
use tower_retry::{ExponentialBackoff, ExponentialBackoffConfig, RetryPolicy, RetryService};
use tracing::{error, info, warn};

lazy_static! {
    static ref FLAGD_RETRY_MAX_ATTEMPTS: u32 = std::env::var("FLAGD_RETRY_MAX_ATTEMPTS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(3);
    static ref FLAGD_RETRY_INITIAL_BACKOFF_MS: u64 = std::env::var("FLAGD_RETRY_INITIAL_BACKOFF_MS")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(50);
    static ref FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD: u32 =
        std::env::var("FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(5);
    static ref FLAGD_CIRCUIT_BREAKER_COOLDOWN_MS: u64 =
        std::env::var("FLAGD_CIRCUIT_BREAKER_COOLDOWN_MS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(30000);
    static ref FLAGD_ENDPOINT: String = std::env::var("FLAGD_ENDPOINT")
        .ok()
        .unwrap_or_default();
    static ref RESILIENCY_LAYER: Arc<Mutex<ResiliencyLayer>> =
        Arc::new(Mutex::new(ResiliencyLayer::new()));
}

#[derive(Debug, Clone)]
enum FlagdError {
    Transient,
    Permanent,
}

struct FlagdRetryPolicy {
    flag_key: String,
}

impl<E> RetryPolicy<(), FlagdError, E> for FlagdRetryPolicy {
    type Future = futures::future::Ready<Self>;

    fn retry(
        &self,
        req: &(),
        result: &Result<FlagdError, E>,
    ) -> Option<Self::Future> {
        match result {
            Ok(FlagdError::Transient) => {
                static ATTEMPT_COUNT: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
                let attempt = ATTEMPT_COUNT.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1;
                warn!(
                    flag_key = %self.flag_key,
                    service_name = "shipping",
                    flagd_endpoint = *FLAGD_ENDPOINT,
                    event = "flagd_retry_attempt",
                    attempt_number = attempt,
                    error = ?result,
                    "Retrying Flagd API call"
                );
                Some(futures::future::ready(self.clone()))
            }
            _ => None,
        }
    }

    fn clone_request(&self, req: &()) -> Option<()> {
        Some(req.clone())
    }
}

struct ResiliencyLayer {
    circuit_breaker: CircuitBreaker<Box<dyn Fn() -> Duration + Send + Sync + Clone>, Box<dyn Fn(&Result<(), FlagdError>) -> bool + Send + Sync + Clone>>,
    retry_layer: tower_retry::RetryLayer<FlagdRetryPolicy>,
    flagd_client: Client,
    last_circuit_state: State,
}

impl ResiliencyLayer {
    fn new() -> Self {
        let backoff_config = ExponentialBackoffConfig {
            initial_delay: Duration::from_millis(*FLAGD_RETRY_INITIAL_BACKOFF_MS),
            max_delay: Duration::from_secs(2),
            multiplier: 2.0,
            jitter: 0.1,
        };

        let retry_policy = FlagdRetryPolicy { flag_key: String::new() };
        let retry_layer = tower_retry::RetryLayer::new(retry_policy)
            .max_attempts(*FLAGD_RETRY_MAX_ATTEMPTS)
            .backoff(ExponentialBackoff::new(backoff_config));

        let cooldown_ms = *FLAGD_CIRCUIT_BREAKER_COOLDOWN_MS;
        let cooldown_provider = Box::new(move || Duration::from_millis(cooldown_ms));

        let failure_detector = Box::new(|result: &Result<(), FlagdError>| {
            result.is_err() || matches!(result, Ok(FlagdError::Transient))
        });

        let circuit_breaker = CircuitBreaker::new(
            *FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
            cooldown_provider,
            failure_detector,
        );

        let flagd_client = OpenFeature::global_client();

        Self {
            circuit_breaker,
            retry_layer,
            flagd_client,
            last_circuit_state: State::Closed,
        }
    }

    async fn call_circuit_breaker(&mut self, flag_key: &str, is_success: bool) {
        let prev_state = self.circuit_breaker.state();
        let result = if is_success {
            Ok(())
        } else {
            Err(FlagdError::Transient)
        };
        let _ = self.circuit_breaker.call(()).await;
        let new_state = self.circuit_breaker.state();

        if prev_state != new_state {
            match new_state {
                State::Open => {
                    error!(
                        flag_key = flag_key,
                        service_name = "shipping",
                        flagd_endpoint = *FLAGD_ENDPOINT,
                        event = "flagd_circuit_breaker_open",
                        failure_count = *FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
                        cooldown_period_ms = *FLAGD_CIRCUIT_BREAKER_COOLDOWN_MS,
                        "Flagd circuit breaker opened"
                    );
                }
                State::Closed => {
                    info!(
                        flag_key = flag_key,
                        service_name = "shipping",
                        flagd_endpoint = *FLAGD_ENDPOINT,
                        event = "flagd_circuit_breaker_closed",
                        "Flagd circuit breaker closed"
                    );
                }
                State::HalfOpen => {}
            }
            self.last_circuit_state = new_state;
        }
    }
}

/// Evaluates a feature flag against Flagd, with retry, circuit breaking, and fallback to default
/// # Arguments
/// * `flag_key` - Unique identifier for the feature flag
/// * `default_value` - Fallback value to use if Flagd is unreachable or circuit breaker is open
/// # Returns
/// * Resolved flag value (either from Flagd or default)
/// # Errors
/// * No public errors returned (all failures result in default value being returned with logging)
pub async fn get_feature_flag<T: Clone + open_feature::value::ValueVariant>(flag_key: &str, default_value: T) -> T {
    let mut layer = RESILIENCY_LAYER.lock().await;

    let flag_key_owned = flag_key.to_string();
    let default_cloned = default_value.clone();

    // Update retry policy with current flag key
    layer.retry_layer = tower_retry::RetryLayer::new(FlagdRetryPolicy { flag_key: flag_key_owned.clone() })
        .max_attempts(*FLAGD_RETRY_MAX_ATTEMPTS)
        .backoff(ExponentialBackoff::new(ExponentialBackoffConfig {
            initial_delay: Duration::from_millis(*FLAGD_RETRY_INITIAL_BACKOFF_MS),
            max_delay: Duration::from_secs(2),
            multiplier: 2.0,
            jitter: 0.1,
        }));

    // First check if circuit is open
    if layer.circuit_breaker.state() == State::Open {
        warn!(
            flag_key = flag_key_owned,
            service_name = "shipping",
            flagd_endpoint = *FLAGD_ENDPOINT,
            event = "flagd_fallback_to_default",
            reason = "circuit_breaker_open",
            "Falling back to default feature flag value due to open circuit breaker"
        );
        return default_value;
    }

    // Attempt to get flag from Flagd
    match layer.flagd_client.get_value(
        &flag_key_owned,
        default_cloned,
        EvaluationContext::default(),
    ).await {
        Ok(val) => {
            // Success, record success in circuit breaker
            layer.call_circuit_breaker(flag_key, true).await;
            return val;
        }
        Err(e) => {
            // Record failure in circuit breaker
            layer.call_circuit_breaker(flag_key, false).await;
            
            warn!(
                flag_key = flag_key_owned,
                service_name = "shipping",
                flagd_endpoint = *FLAGD_ENDPOINT,
                event = "flagd_fallback_to_default",
                reason = "flagd_unreachable",
                error = %e,
                "Falling back to default feature flag value"
            );
        }
    }

    default_value
}
