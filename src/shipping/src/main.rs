// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use actix_web::{dev::ServerHandle, web, App, HttpResponse, HttpServer, ResponseError};
use actix_governor::{Governor, GovernorConfigBuilder, KeyExtractor, SimpleKeyExtractionError};
use governor::clock::DefaultClock;
use governor::middleware::RateLimitingMiddleware;
use open_feature::provider::{FeatureProvider, NoOpProvider};
use open_feature_flagd::{FlagdOptions, FlagdProvider};
use opentelemetry::{global, metrics::{Counter, Meter}};
use opentelemetry_instrumentation_actix_web::{RequestMetrics, RequestTracing};
use serde::Serialize;
use std::env;
use std::net::IpAddr;
use std::sync::Arc;
use tokio::signal::unix::{signal, SignalKind};
use tracing::{info, warn, error};
mod telemetry_conf;
use telemetry_conf::init_otel;
mod shipping_service;
use shipping_service::{get_quote, health_check, ship_order};

// Rate limit error response structure
#[derive(Serialize, Debug)]
struct RateLimitError {
    error: String,
    message: String,
    retry_after: u64,
}

impl ResponseError for RateLimitError {
    fn status_code(&self) -> actix_web::http::StatusCode {
        actix_web::http::StatusCode::TOO_MANY_REQUESTS
    }

    fn error_response(&self) -> HttpResponse {
        HttpResponse::build(self.status_code())
            .insert_header(("Retry-After", self.retry_after.to_string()))
            .json(self)
    }
}

// Custom key extractor to get client IP from X-Forwarded-For header or remote address
#[derive(Clone, Copy)]
struct ClientIpKeyExtractor;

impl KeyExtractor for ClientIpKeyExtractor {
    type Key = IpAddr;
    type KeyExtractionError = SimpleKeyExtractionError<&'static str>;

    fn extract(&self, req: &actix_web::dev::ServiceRequest) -> Result<Self::Key, Self::KeyExtractionError> {
        // Check X-Forwarded-For header first
        if let Some(forwarded_for) = req.headers().get("X-Forwarded-For") {
            if let Ok(forwarded_str) = forwarded_for.to_str() {
                // Take the first IP in the comma-separated list
                if let Some(first_ip) = forwarded_str.split(',').next() {
                    if let Ok(ip) = first_ip.trim().parse::<IpAddr>() {
                        return Ok(ip);
                    }
                }
            }
        }

        // Fall back to connection remote address
        req.connection_info()
            .peer_addr()
            .unwrap_or("127.0.0.1")
            .parse()
            .map_err(|_| SimpleKeyExtractionError::new("Could not extract client IP address"))
    }
}

// Custom middleware to increment rate limit metrics
#[derive(Clone)]
struct RateLimitMetricsMiddleware {
    counter: Counter<u64>,
}

impl RateLimitingMiddleware<IpAddr> for RateLimitMetricsMiddleware {
    fn allow(&self, _key: &IpAddr, _state: &governor::state::keyed::StateEntry<'_, IpAddr, governor::clock::QuantaInstant>) {
        // Do nothing on allow
    }

    fn deny(&self, key: &IpAddr, _state: &governor::state::keyed::StateEntry<'_, IpAddr, governor::clock::QuantaInstant>, req: &actix_web::dev::ServiceRequest) {
        let endpoint = req.path().to_string();
        self.counter.add(1, &[
            opentelemetry::KeyValue::new("client_ip", key.to_string()),
            opentelemetry::KeyValue::new("endpoint", endpoint),
        ]);
    }
}

// Load and validate rate limit configuration from environment
fn get_rate_limit_config() -> anyhow::Result<u32> {
    const DEFAULT_RPM: u32 = 60;

    match env::var("SHIPPING_RATE_LIMIT_RPM") {
        Ok(val) => {
            let rpm = val.parse::<u32>().map_err(|_| {
                anyhow::anyhow!("Invalid SHIPPING_RATE_LIMIT_RPM: must be a positive integer")
            })?;
            if rpm == 0 {
                return Err(anyhow::anyhow!("Invalid SHIPPING_RATE_LIMIT_RPM: must be greater than 0"));
            }
            Ok(rpm)
        }
        Err(_) => Ok(DEFAULT_RPM),
    }
}

pub fn get_flagd_options() -> FlagdOptions {
    // Read FLAGD_HOST environment variable, default to "localhost"
    let host = env::var("FLAGD_HOST").unwrap_or_else(|_| "localhost".to_string());

    // Read and validate FLAGD_PORT environment variable, default to 8013
    let port = match env::var("FLAGD_PORT") {
        Ok(val) => match val.parse::<u16>() {
            Ok(p) if (1..=65535).contains(&p) => p,
            Ok(_) => {
                warn!("Invalid FLAGD_PORT: must be between 1 and 65535, using default 8013");
                8013
            }
            Err(_) => {
                warn!("Invalid FLAGD_PORT: not a valid integer, using default 8013");
                8013
            }
        },
        Err(_) => 8013,
    };

    // Read and validate FLAGD_CONNECTION_TIMEOUT environment variable, default to 5000ms
    let connection_timeout_ms = match env::var("FLAGD_CONNECTION_TIMEOUT") {
        Ok(val) => match val.parse::<u64>() {
            Ok(t) if t >= 1 => t,
            Ok(_) => {
                warn!("Invalid FLAGD_CONNECTION_TIMEOUT: must be positive integer, using default 5000ms");
                5000
            }
            Err(_) => {
                warn!("Invalid FLAGD_CONNECTION_TIMEOUT: not a valid integer, using default 5000ms");
                5000
            }
        },
        Err(_) => 5000,
    };

    // Read and validate FLAGD_RETRY_MAX_ATTEMPTS environment variable, default to 3
    let retry_max_attempts = match env::var("FLAGD_RETRY_MAX_ATTEMPTS") {
        Ok(val) => match val.parse::<u32>() {
            Ok(r) if r >= 0 => r,
            Ok(_) => {
                warn!("Invalid FLAGD_RETRY_MAX_ATTEMPTS: must be non-negative integer, using default 3");
                3
            }
            Err(_) => {
                warn!("Invalid FLAGD_RETRY_MAX_ATTEMPTS: not a valid integer, using default 3");
                3
            }
        },
        Err(_) => 3,
    };

    FlagdOptions {
        host: Some(host),
        port: Some(port),
        connection_timeout_ms: Some(connection_timeout_ms),
        retry_max_attempts: Some(retry_max_attempts),
        cache_settings: None,
        ..Default::default()
    }
}

pub async fn init_flagd_provider() -> Arc<dyn FeatureProvider> {
    let options = get_flagd_options();

    match FlagdProvider::new(options).await {
        Ok(provider) => Arc::new(provider) as Arc<dyn FeatureProvider>,
        Err(e) => {
            error!("Failed to initialize flagd provider: {e}, falling back to no-op provider");
            Arc::new(NoOpProvider::default()) as Arc<dyn FeatureProvider>
        }
    }
}

// Expose options accessor for tests
#[cfg(test)]
pub async fn init_flagd_provider_concrete() -> FlagdProvider {
    let options = get_flagd_options();
    FlagdProvider::new(options).await.unwrap()
}

pub async fn app() -> App {
    let provider = init_flagd_provider().await;
    let flag_provider = web::Data::from(provider);
    
    // Load rate limit config
    let rpm = get_rate_limit_config().expect("Invalid rate limit configuration");
    
    // Create rate limit counter metric
    let meter = global::meter("shipping");
    let rate_limit_counter = meter.u64_counter("shipping_rate_limited_requests_total")
        .with_description("Total number of requests that were rejected due to rate limiting")
        .init();
    
    // Build governor configuration
    let governor_config = GovernorConfigBuilder::default()
        .per_second(60 * 60 / rpm as u64) // Calculate interval between requests for RPM
        .burst_size(rpm)
        .key_extractor(ClientIpKeyExtractor)
        .middleware(RateLimitMetricsMiddleware { counter: rate_limit_counter })
        .error_handler(|quota| {
            RateLimitError {
                error: "Too many requests".to_string(),
                message: "Rate limit exceeded. Try again later.".to_string(),
                retry_after: quota.as_secs(),
            }
        })
        .use_headers()
        .finish()
        .expect("Failed to build rate limit configuration");

    // Create public routes scope with rate limiting
    let public_routes = web::scope("")
        .wrap(Governor::new(&governor_config))
        .service(get_quote)
        .service(ship_order);
    
    App::new()
        .app_data(flag_provider.clone())
        .wrap(RequestTracing::new())
        .wrap(RequestMetrics::default())
        .service(public_routes)
        .service(health_check)
}

async fn handle_shutdown(handle: ServerHandle) {
    // Listen for SIGINT and SIGTERM
    let mut sigint = signal(SignalKind::interrupt()).expect("Failed to register SIGINT handler");
    let mut sigterm = signal(SignalKind::terminate()).expect("Failed to register SIGTERM handler");

    // Wait for either signal
    tokio::select! {
        _ = sigint.recv() => {},
        _ = sigterm.recv() => {},
    }

    warn!("Received shutdown signal, starting graceful shutdown. Waiting up to 30 seconds for in-flight requests to complete.");

    // Trigger graceful shutdown with timeout
    match tokio::time::timeout(tokio::time::Duration::from_secs(30), handle.stop(true)).await {
        Ok(_) => {
            info!("Graceful shutdown completed successfully. All in-flight requests processed.");
        }
        Err(_) => {
            warn!("Graceful shutdown timed out after 30 seconds. Terminating remaining in-flight requests.");
        }
    }
}

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    match init_otel() {
        Ok(_) => {
            info!("Successfully configured OTel");
        }
        Err(err) => {
            panic!("Couldn't start OTel: {0}", err);
        }
    };

    let port: u16 = env::var("SHIPPING_PORT")
        .expect("$SHIPPING_PORT is not set")
        .parse()
        .expect("$SHIPPING_PORT is not a valid port");

    let mut ip = "0.0.0.0".to_string();

    if let Ok(ipv6_enabled) = env::var("IPV6_ENABLED") {
        if ipv6_enabled == "true" {
            ip = "[::]".to_string();
            info!("Overwriting Localhost IP:  {ip}");
        }
    }

    let addr = format!("{}:{}", ip, port);
    info!(
        name = "ServerStartedSuccessfully",
        addr = addr.as_str(),
        message = "Shipping service is running"
    );

    let server = HttpServer::new(move || app())
        .shutdown_timeout(30)
        .bind(&addr)?
        .run();

    // Get server handle for shutdown
    let handle = server.handle();
    // Spawn shutdown handler task
    tokio::spawn(handle_shutdown(handle));

    server.await
}
