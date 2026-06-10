// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::env;
use std::time::Duration;
use regex::Regex;
use tonic::{transport::Server, Request, Status, Code, service::Interceptor};
use tower_governor::{Governor, GovernorConfig, GovernorConfigBuilder, key_extractor::KeyExtractor, error::GovernorError};
use governor::Quota;
use std::num::NonZeroU32;
use opentelemetry::metrics::Counter;
use opentelemetry_proto::oteldemo::shipping_service_server::{ShippingService, ShippingServiceServer};
use opentelemetry_proto::oteldemo::{GetQuoteRequest, GetQuoteResponse, ShipOrderRequest, ShipOrderResponse, GetShippingRequest, GetShippingResponse};
use tokio::signal::unix::{signal, SignalKind};
use chrono::Utc;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum ServiceState {
    Running = 0,
    ShuttingDown = 1,
    Exiting = 2,
}

impl From<usize> for ServiceState {
    fn from(v: usize) -> Self {
        match v {
            0 => ServiceState::Running,
            1 => ServiceState::ShuttingDown,
            2 => ServiceState::Exiting,
            _ => ServiceState::Running,
        }
    }
}

// Active request tracker interceptor to count in-flight requests for shutdown logging
#[derive(Debug, Clone)]
struct ActiveRequestTracker {
    count: Arc<AtomicUsize>,
}

impl Interceptor for ActiveRequestTracker {
    fn call(&mut self, request: Request<()>) -> Result<Request<()>, Status> {
        let count = self.count.clone();
        // Increment on request start
        count.fetch_add(1, Ordering::SeqCst);
        // Decrement when request completes (using extension to track)
        request.extensions_mut().insert(ActiveRequestGuard { count });
        Ok(request)
    }
}

// Guard to decrement active request count when request is dropped
#[derive(Debug)]
struct ActiveRequestGuard {
    count: Arc<AtomicUsize>,
}

impl Drop for ActiveRequestGuard {
    fn drop(&mut self) {
        self.count.fetch_sub(1, Ordering::SeqCst);
    }
}

// gRPC Shipping Service implementation
#[derive(Debug, Clone)]
struct ShippingServiceImpl;

lazy_static::lazy_static! {
    static ref ZIP_CODE_REGEX: Regex = Regex::new(r"^\d{5}(-\d{4})?$").unwrap();
    static ref TRACKING_ID_REGEX: Regex = Regex::new(r"^OTEL-DEMO-SHIP-[A-F0-9]{12}$").unwrap();
}

impl ShippingServiceImpl {
    // Validate GetQuoteRequest
    fn validate_get_quote_request(req: &GetQuoteRequest) -> Result<(), Status> {
        if req.items.is_empty() {
            return Err(Status::invalid_argument("items list cannot be empty"));
        }
        for (i, item) in req.items.iter().enumerate() {
            if item.quantity <= 0 {
                return Err(Status::invalid_argument(format!(
                    "item at index {} has invalid non-positive quantity: {}",
                    i, item.quantity
                )));
            }
        }
        Ok(())
    }

    // Validate ShipOrderRequest
    fn validate_ship_order_request(req: &ShipOrderRequest) -> Result<(), Status> {
        if req.items.is_empty() {
            return Err(Status::invalid_argument("items list cannot be empty"));
        }
        let address = req.address.as_ref().ok_or_else(|| Status::invalid_argument("address is required"))?;
        // Check all address fields are non-empty
        if address.street.is_empty() {
            return Err(Status::invalid_argument("address field street cannot be empty"));
        }
        if address.city.is_empty() {
            return Err(Status::invalid_argument("address field city cannot be empty"));
        }
        if address.state.is_empty() {
            return Err(Status::invalid_argument("address field state cannot be empty"));
        }
        if address.zip_code.is_empty() {
            return Err(Status::invalid_argument("address field zip_code cannot be empty"));
        }
        if address.country.is_empty() {
            return Err(Status::invalid_argument("address field country cannot be empty"));
        }
        // Validate zip code format
        if !ZIP_CODE_REGEX.is_match(&address.zip_code) {
            return Err(Status::invalid_argument(format!(
                "invalid zip_code format: {}, expected 5-digit or 5-4 digit US ZIP",
                address.zip_code
            )));
        }
        Ok(())
    }

    // Validate GetShippingRequest
    fn validate_get_shipping_request(req: &GetShippingRequest) -> Result<(), Status> {
        if req.tracking_id.is_empty() {
            return Err(Status::invalid_argument("tracking_id cannot be empty"));
        }
        if !TRACKING_ID_REGEX.is_match(&req.tracking_id) {
            return Err(Status::invalid_argument(format!(
                "invalid tracking_id format: {}, expected format OTEL-DEMO-SHIP-<12 hex characters>",
                req.tracking_id
            )));
        }
        Ok(())
    }
}

#[tonic::async_trait]
impl ShippingService for ShippingServiceImpl {
    async fn get_quote(&self, request: Request<GetQuoteRequest>) -> Result<tonic::Response<GetQuoteResponse>, Status> {
        // Delegate to existing get_quote logic
        let req = request.into_inner();
        // Validate request before processing
        Self::validate_get_quote_request(&req)?;
        let itemct: u32 = req.items.iter().map(|item| item.quantity as u32).sum();
        let quote = match crate::quote::create_quote_from_count(itemct).await {
            Ok(q) => q,
            Err(e) => return Err(Status::internal(format!("Failed to get quote: {}", e))),
        };
        Ok(tonic::Response::new(GetQuoteResponse {
            cost_usd: Some(crate::shipping_types::Money {
                currency_code: "USD".into(),
                units: quote.dollars,
                nanos: quote.cents * NANOS_MULTIPLE,
            }),
        }))
    }

    async fn ship_order(&self, request: Request<ShipOrderRequest>) -> Result<tonic::Response<ShipOrderResponse>, Status> {
        // Delegate to existing ship_order logic
        let req = request.into_inner();
        // Validate request before processing
        Self::validate_ship_order_request(&req)?;
        let tid = crate::tracking::create_tracking_id();
        Ok(tonic::Response::new(ShipOrderResponse { tracking_id: tid }))
    }

    async fn get_shipping(&self, request: Request<GetShippingRequest>) -> Result<tonic::Response<GetShippingResponse>, Status> {
        let req = request.into_inner();
        // Validate request before processing
        Self::validate_get_shipping_request(&req)?;
        // TODO: Implement actual tracking lookup logic (out of scope for this task)
        // For now, return a dummy response for valid tracking IDs
        Ok(tonic::Response::new(GetShippingResponse {
            tracking_id: req.tracking_id,
            status: "SHIPPED".to_string(),
            estimated_delivery_date: Utc::now().naive_utc().date().to_string(),
        }))
    }
}

// Endpoint key extractor for gRPC rate limiting
#[derive(Clone, Copy, Debug)]
struct GrpcEndpointKeyExtractor;

impl KeyExtractor for GrpcEndpointKeyExtractor {
    type Key = String;
    type KeyExtractionError = Status;

    fn extract<T>(&self, req: &Request<T>) -> Result<Self::Key, Self::KeyExtractionError> {
        Ok(req.path().to_string())
    }
}

// Load per-endpoint rate limit configuration
fn load_endpoint_rate_limits() -> HashMap<String, (NonZeroU32, NonZeroU32)> {
    let mut limits = HashMap::new();
    let endpoints = vec![
        ("GetQuote", "/oteldemo.ShippingService/GetQuote"),
        ("ShipOrder", "/oteldemo.ShippingService/ShipOrder"),
    ];

    for (name, full_path) in endpoints {
        let rps_env = format!("SHIPPING_{}_RPS", name.to_uppercase());
        let burst_env = format!("SHIPPING_{}_BURST", name.to_uppercase());

        if let Ok(rps_str) = env::var(&rps_env) {
            if let Ok(rps) = rps_str.parse::<NonZeroU32>() {
                let burst = env::var(&burst_env)
                    .ok()
                    .and_then(|b| b.parse::<NonZeroU32>().ok())
                    .unwrap_or(rps);
                limits.insert(full_path.to_string(), (rps, burst));
            }
        }
    }
    limits
}

// Build rate limiting interceptor for gRPC
fn build_rate_limit_interceptor(
    rate_limit_counter: Counter<u64>,
) -> impl Fn(Request<()>) -> Result<Request<()>, Status> + Clone {
    let limits = load_endpoint_rate_limits();
    let mut governors: HashMap<String, (NonZeroU32, NonZeroU32, Arc<Governor<GrpcEndpointKeyExtractor>>)> = HashMap::new();

    for (endpoint, (rps, burst)) in limits {
        let config = GovernorConfigBuilder::default()
            .per_second(rps.get() as u64)
            .burst_size(burst.get())
            .key_extractor(GrpcEndpointKeyExtractor)
            .finish()
            .unwrap();
        governors.insert(endpoint, (rps, burst, Arc::new(Governor::new(&config))));
    }

    move |mut req: Request<()>| {
        let path = req.path().to_string();
        if let Some((rps, burst, governor)) = governors.get(&path) {
            match governor.check(&req) {
                Ok(_) => Ok(req),
                Err(GovernorError::TooManyRequests { .. }) => {
                    let msg = format!(
                        "Rate limit exceeded for endpoint {}: limit is {} requests per second, burst {} capacity",
                        path, rps, burst
                    );
                    // Increment metric
                    rate_limit_counter.add(1, &[
                        opentelemetry::KeyValue::new("endpoint", path.clone()),
                        opentelemetry::KeyValue::new("limit_rps", rps.to_string()),
                    ]);
                    Err(Status::new(Code::ResourceExhausted, msg))
                }
                Err(_) => Err(Status::internal("Rate limit check failed")),
            }
        } else {
            Ok(req)
        }
    }
}

// TLS configuration errors
#[derive(Debug, thiserror::Error)]
enum TlsConfigError {
    #[error("MissingTlsComponent: both SHIPPING_SERVICE_TLS_CERT_PATH and SHIPPING_SERVICE_TLS_KEY_PATH are required for TLS configuration")]
    MissingTlsComponent,
    
    #[error("FileReadError: failed to read {path}: {message}")]
    FileReadError { path: String, message: String },
    
    #[error("InvalidCertificate: server TLS certificate/key pair is invalid or mismatched: {0}")]
    InvalidCertificate(String),
    
    #[error("InvalidCaCertificate: mTLS CA certificate is invalid: {0}")]
    InvalidCaCertificate(String),
}

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

// Load and validate TLS configuration from environment variables
fn load_tls_config() -> Result<Option<ServerConfig>, TlsConfigError> {
    let cert_path = env::var("SHIPPING_SERVICE_TLS_CERT_PATH").ok();
    let key_path = env::var("SHIPPING_SERVICE_TLS_KEY_PATH").ok();
    let ca_cert_path = env::var("SHIPPING_SERVICE_MTLS_CA_CERT_PATH").ok();
    
    // If neither cert nor key are set, return None (no TLS)
    if cert_path.is_none() && key_path.is_none() {
        return Ok(None);
    }
    
    // If only one is set, return error
    let (cert_path, key_path) = match (cert_path, key_path) {
        (Some(c), Some(k)) => (c, k),
        _ => return Err(TlsConfigError::MissingTlsComponent),
    };
    
    // Read and parse server certificate
    let cert_file = File::open(&cert_path).map_err(|e| TlsConfigError::FileReadError {
        path: cert_path.clone(),
        message: e.to_string(),
    })?;
    let mut cert_reader = BufReader::new(cert_file);
    let cert_chain = certs(&mut cert_reader)
        .map_err(|e| TlsConfigError::InvalidCertificate(format!("Failed to parse certificate: {e}")))?
        .into_iter()
        .map(Certificate)
        .collect();
    
    // Read and parse server private key
    let key_file = File::open(&key_path).map_err(|e| TlsConfigError::FileReadError {
        path: key_path.clone(),
        message: e.to_string(),
    })?;
    let mut key_reader = BufReader::new(key_file);
    let mut keys = pkcs8_private_keys(&mut key_reader)
        .map_err(|e| TlsConfigError::InvalidCertificate(format!("Failed to parse private key: {e}")))?;
    
    if keys.is_empty() {
        return Err(TlsConfigError::InvalidCertificate("No private keys found in key file".to_string()));
    }
    let private_key = keys.remove(0);
    
    // Create base TLS config
    let config = ServerConfig::builder()
        .with_safe_defaults();
    
    let config = if let Some(ca_path) = ca_cert_path {
        // mTLS enabled: load CA cert and require client auth
        let ca_file = File::open(&ca_path).map_err(|e| TlsConfigError::FileReadError {
            path: ca_path.clone(),
            message: e.to_string(),
        })?;
        let mut ca_reader = BufReader::new(ca_file);
        let ca_certs = certs(&mut ca_reader)
            .map_err(|e| TlsConfigError::InvalidCaCertificate(format!("Failed to parse CA certificate: {e}")))?;
        
        let mut root_store = RootCertStore::empty();
        for ca in ca_certs {
            root_store.add(&Certificate(ca))
                .map_err(|e| TlsConfigError::InvalidCaCertificate(format!("Failed to add CA to root store: {e}")))?;
        }
        
        let client_auth = rustls::server::AllowAnyAuthenticatedClient::new(root_store);
        config.with_client_cert_verifier(client_auth)
    } else {
        // No mTLS: no client auth required
        config.with_no_client_auth()
    };
    
    let mut server_config = config
        .with_single_cert(cert_chain, private_key.into())
        .map_err(|e| TlsConfigError::InvalidCertificate(format!("Certificate/key mismatch: {e}")))?;
    
    server_config.alpn_protocols = vec![b"h2".to_vec(), b"http/1.1".to_vec()];
    
    Ok(Some(server_config))
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

const NANOS_MULTIPLE: u32 = 10000000u32;

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

    let addr = format!("{}:{}", ip, port).parse().unwrap();
    
    // Initialize rate limit metric
    let meter = opentelemetry::global::meter("shipping");
    let rate_limit_counter = meter.u64_counter("shipping_service_rate_limited_requests_total")
        .with_description("Total number of requests that were rejected due to rate limiting")
        .init();

    // Build rate limiting interceptor
    let rate_limit_interceptor = build_rate_limit_interceptor(rate_limit_counter);

    // Create active request tracker
    let active_requests = Arc::new(AtomicUsize::new(0));
    let request_tracker = ActiveRequestTracker { count: active_requests.clone() };
    
    // Initialize service state
    pub static SERVICE_STATE: once_cell::sync::Lazy<Arc<AtomicUsize>> = once_cell::sync::Lazy::new(|| Arc::new(AtomicUsize::new(ServiceState::Running as usize)));
    let service_state = SERVICE_STATE.clone();
    
    // Shutdown interceptor: reject new requests when service is shutting down
    let shutdown_interceptor = move |req: Request<()>| -> Result<Request<()>, Status> {
        let state: ServiceState = SERVICE_STATE.load(Ordering::SeqCst).into();
        if state == ServiceState::ShuttingDown {
            return Err(Status::unavailable("Service is shutting down"));
        }
        Ok(req)
    };

    // Chain interceptors: shutdown check first, then request tracker, then rate limiter
    let service = tower::ServiceBuilder::new()
        .layer(tonic::service::interceptor(shutdown_interceptor))
        .layer(tonic::service::interceptor(request_tracker))
        .layer(tonic::service::interceptor(rate_limit_interceptor))
        .service(ShippingServiceServer::new(ShippingServiceImpl));

    info!(
        name = "ServerStartedSuccessfully",
        addr = addr.to_string(),
        message = "Shipping gRPC service is running"
    );

    // Create server and get shutdown handle
    let server = Server::builder()
        .add_service(service);

    let (shutdown_handle, server_future) = server.serve_with_graceful_shutdown(addr, async move {
        // Empty future, we will trigger shutdown explicitly via handle
    });

    // Spawn server task
    let server_handle = tokio::spawn(server_future);

    // Set up signal handlers
    let mut sigint = signal(SignalKind::interrupt()).expect("Failed to set up SIGINT handler");
    let mut sigterm = signal(SignalKind::terminate()).expect("Failed to set up SIGTERM handler");

    // Wait for signal
    let signal_name = tokio::select! {
        _ = sigint.recv() => "SIGINT",
        _ = sigterm.recv() => "SIGTERM",
    };

    // Update service state to shutting down
    service_state.store(ServiceState::ShuttingDown as usize, Ordering::SeqCst);

    // Log signal received event
    info!(
        name = "signal_received",
        signal = signal_name,
        timestamp = %Utc::now().to_rfc3339(),
        "Received shutdown signal"
    );

    // Log shutdown started event
    info!(
        name = "shutdown_started",
        timeout_seconds = 30,
        timestamp = %Utc::now().to_rfc3339(),
        "Starting graceful shutdown with 30s timeout"
    );

    // Wait for either graceful shutdown completion or timeout
    let shutdown_result = tokio::time::timeout(
        Duration::from_secs(30),
        shutdown_handle.shutdown()
    ).await;

    match shutdown_result {
        Ok(_) => {
            // Shutdown completed successfully within timeout
            let completed = active_requests.load(Ordering::SeqCst);
            info!(
                name = "shutdown_complete",
                in_flight_requests_completed = completed,
                timestamp = %Utc::now().to_rfc3339(),
                "Graceful shutdown completed successfully"
            );
        }
        Err(_) => {
            // Shutdown timed out
            let dropped = active_requests.load(Ordering::SeqCst);
            info!(
                name = "shutdown_timed_out",
                in_flight_requests_dropped = dropped,
                timestamp = %Utc::now().to_rfc3339(),
                "Graceful shutdown timed out, dropping remaining requests"
            );
            // Force cancel any remaining server tasks
            server_handle.abort();
        }
    }

    Ok(())
}
