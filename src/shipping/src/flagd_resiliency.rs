//! Flagd resiliency layer with retry, circuit breaking and fallback logic

use std::{sync::Arc, time::Duration};

use lazy_static::lazy_static;
use open_feature::{Client, EvaluationContext, OpenFeature};
use tokio::sync::Mutex;
use tower::{Service, ServiceBuilder, ServiceExt};
use tower_circuit_breaker::{CircuitBreaker, CircuitBreakerError};
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

struct FlagdRetryPolicy;

impl<E> RetryPolicy<(), FlagdError, E> for FlagdRetryPolicy {
    type Future = futures::future::Ready<Self>;

    fn retry(
        &self,
        req: &(),
        result: &Result<FlagdError, E>,
    ) -> Option<Self::Future> {
        match result {
            Ok(FlagdError::Transient) => Some(futures::future::ready(self.clone())),
            _ => None,
        }
    }

    fn clone_request(&self, req: &()) -> Option<()> {
        Some(req.clone())
    }
}

struct ResiliencyLayer {
    service: RetryService<
        FlagdRetryPolicy,
        CircuitBreaker<Box<dyn Fn() -> Duration + Send + Sync + Clone>, Box<dyn Fn(&Result<(), FlagdError>) -> bool + Send + Sync + Clone>>,
    >,
    flagd_client: Client,
}

impl ResiliencyLayer {
    fn new() -> Self {
        let backoff_config = ExponentialBackoffConfig {
            initial_delay: Duration::from_millis(*FLAGD_RETRY_INITIAL_BACKOFF_MS),
            max_delay: Duration::from_secs(2),
            multiplier: 2.0,
            jitter: 0.1,
        };

        let retry_policy = FlagdRetryPolicy;
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

        let service = ServiceBuilder::new()
            .layer(retry_layer)
            .service(circuit_breaker);

        let flagd_client = OpenFeature::global_client();

        Self {
            service,
            flagd_client,
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

    // First check if circuit is open
    let service_result = layer.service.ready().await;
    if service_result.is_err() {
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
            let _ = layer.service.call(()).await;
            return val;
        }
        Err(e) => {
            // Record failure in circuit breaker
            let _ = layer.service.call(()).await;
            
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
