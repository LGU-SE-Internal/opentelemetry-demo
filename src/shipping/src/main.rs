// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::env;
use std::time::Duration;
use regex::Regex;
use tonic::{transport::Server, Request, Status, Code, service::Interceptor, metadata::MetadataValue};
use tower_governor::{Governor, GovernorConfig, GovernorConfigBuilder, key_extractor::KeyExtractor, error::GovernorError};
use governor::Quota;
use std::num::NonZeroU32;
use opentelemetry::{metrics::{Counter, MeterProvider}, KeyValue};
use opentelemetry_proto::oteldemo::shipping_service_server::{ShippingService, ShippingServiceServer};
use opentelemetry_proto::oteldemo::{GetQuoteRequest, GetQuoteResponse, ShipOrderRequest, ShipOrderResponse, GetShippingRequest, GetShippingResponse};
use tokio::signal::unix::{signal, SignalKind};
use chrono::Utc;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
#[repr(usize)]
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

/// Signal handler that listens for SIGINT/SIGTERM and updates service state
pub async fn shutdown_signal_handler(state: Arc<AtomicUsize>) {
    let mut sigint = signal(SignalKind::interrupt()).expect("Failed to set up SIGINT handler");
    let mut sigterm = signal(SignalKind::terminate()).expect("Failed to set up SIGTERM handler");

    tokio::select! {
        _ = sigint.recv() => {},
        _ = sigterm.recv() => {},
    };

    state.store(ServiceState::ShuttingDown as usize, Ordering::SeqCst);
}

/// Graceful shutdown implementation for server
pub async fn graceful_shutdown<S>(server: Server, state: Arc<AtomicUsize>, timeout: Duration = Duration::from_secs(30)) -> () {
    // Wait for state to transition to ShuttingDown
    while state.load(Ordering::SeqCst) != ServiceState::ShuttingDown as usize {
        tokio::time::sleep(Duration::from_millis(100)).await;
    }

    // Trigger server shutdown
    let (shutdown_handle, server_fut) = server.serve_with_graceful_shutdown("0.0.0.0:0".parse().unwrap(), async move {});
    tokio::spawn(server_fut);

    // Wait for either shutdown completion or timeout
    let _ = tokio::time::timeout(timeout, shutdown_handle.shutdown()).await;

    // Update state to exiting
    state.store(ServiceState::Exiting as usize, Ordering::SeqCst);
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

// Client IP extractor for rate limiting
#[derive(Debug, Clone, Copy)]
struct ClientIpExtractor;

impl KeyExtractor for ClientIpExtractor {
    type Key = String;

    fn extract<T>(&self, req: &Request<T>) -> Result<Self::Key, GovernorError> {
        // First try X-Forwarded-For header
        if let Some(forwarded_for) = req.metadata().get("x-forwarded-for") {
            if let Ok(ip_str) = forwarded_for.to_str() {
                // Take the first IP in the comma-separated list
                if let Some(first_ip) = ip_str.split(',').next() {
                    return Ok(first_ip.trim().to_string());
                }
            }
        }
        // Fall back to peer address
        let addr = req.remote_addr().ok_or_else(|| {
            GovernorError::Other("Could not extract client IP address".to_string())
        })?;
        Ok(addr.ip().to_string())
    }
}

// Rate limiting interceptor
#[derive(Debug, Clone)]
struct RateLimitInterceptor {
    governor: Option<Governor<ClientIpExtractor>>,
    rate_limited_counter: Counter<u64>,
}

impl Interceptor for RateLimitInterceptor {
    fn call(&mut self, request: Request<()>) -> Result<Request<()>, Status> {
        let governor = match &self.governor {
            Some(g) => g,
            None => return Ok(request),
        };

        let client_ip = match governor.key_extractor.extract(&request) {
            Ok(ip) => ip,
            Err(_) => return Err(Status::internal("Could not extract client IP")),
        };

        // Get gRPC method name
        let method_name = request
            .uri()
            .path()
            .split('/')
            .last()
            .unwrap_or("unknown")
            .to_string();

        match governor.check_key(&client_ip) {
            Ok(_) => Ok(request),
            Err(_) => {
                // Increment rate limit metric
                self.rate_limited_counter.add(
                    1,
                    &[
                        KeyValue::new("client_ip", client_ip.clone()),
                        KeyValue::new("grpc_method", method_name),
                    ],
                );
                Err(Status::resource_exhausted("Rate limit exceeded, please try again later."))
            }
        }
    }
}

// gRPC Shipping Service implementation
#[derive(Debug, Clone)]
struct ShippingServiceImpl;

lazy_static::lazy_static! {
    static ref ZIP_CODE_REGEX: Regex = Regex::new(r"^[A-Z0-9\s-]{3,10}$").unwrap();
    static ref TRACKING_ID_REGEX: Regex = Regex::new(r"^SHIP-[A-Z0-9]{12}$").unwrap();
}

impl ShippingServiceImpl {
    // Validate GetQuoteRequest
    fn validate_get_quote_request(req: &GetQuoteRequest) -> Result<(), Status> {
        // Validate address is present and all fields are non-empty
        let address = req.address.as_ref().ok_or_else(|| Status::invalid_argument("Missing required field: address"))?;
        if address.street_address.is_empty() {
            return Err(Status::invalid_argument("Missing required field: address.street_address"));
        }
        if address.city.is_empty() {
            return Err(Status::invalid_argument("Missing required field: address.city"));
        }
        if address.state.is_empty() {
            return Err(Status::invalid_argument("Missing required field: address.state"));
        }
        if address.zip_code.is_empty() {
            return Err(Status::invalid_argument("Missing required field: address.zip_code"));
        }
        if address.country.is_empty() {
            return Err(Status::invalid_argument("Missing required field: address.country"));
        }
        
        // Validate weight value is strictly greater than 0
        if req.weight <= 0.0 {
            return Err(Status::invalid_argument("Weight must be greater than 0"));
        }
        
        Ok(())
    }

    // Validate ShipOrderRequest
    fn validate_ship_order_request(req: &ShipOrderRequest) -> Result<(), Status> {
        // Validate items list is non-empty
        if req.items.is_empty() {
            return Err(Status::invalid_argument("Order items list cannot be empty"));
        }
        
        // Validate address is present and all fields are non-empty
        let address = req.address.as_ref().ok_or_else(|| Status::invalid_argument("Address field address cannot be empty"))?;
        if address.street_address.is_empty() {
            return Err(Status::invalid_argument("Address field street_address cannot be empty"));
        }
        if address.city.is_empty() {
            return Err(Status::invalid_argument("Address field city cannot be empty"));
        }
        if address.state.is_empty() {
            return Err(Status::invalid_argument("Address field state cannot be empty"));
        }
        if address.zip_code.is_empty() {
            return Err(Status::invalid_argument("Address field zip_code cannot be empty"));
        }
        if address.country.is_empty() {
            return Err(Status::invalid_argument("Address field country cannot be empty"));
        }
        
        // Validate zip code format
        if !ZIP_CODE_REGEX.is_match(&address.zip_code) {
            return Err(Status::invalid_argument("Invalid zip code format"));
        }
        
        // Validate each item quantity is strictly greater than 0
        for item in &req.items {
            if item.quantity <= 0 {
                return Err(Status::invalid_argument(format!(
                    "Item {} has invalid quantity: must be greater than 0",
                    item.item_id
                )));
            }
        }
        
        Ok(())
    }

    // Validate GetShippingRequest
    fn validate_get_shipping_request(req: &GetShippingRequest) -> Result<(), Status> {
        if req.tracking_id.is_empty() {
            return Err(Status::invalid_argument("Tracking ID cannot be empty"));
        }
        if !TRACKING_ID_REGEX.is_match(&req.tracking_id) {
            return Err(Status::invalid_argument("Invalid tracking ID format: expected format SHIP-XXXXXXXXXXXX"));
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
) -> RateLimitInterceptor {
    // Read rate limit configuration from environment variable
    let rpm = match env::var("SHIPPING_SERVICE_RATE_LIMIT_RPM") {
        Ok(val) => match val.parse::<i32>() {
            Ok(v) => v,
            Err(_) => {
                warn!("Invalid SHIPPING_SERVICE_RATE_LIMIT_RPM value, using default 100");
                100
            }
        },
        Err(_) => 100,
    };

    // If rate limit is <= 0, disable rate limiting entirely
    if rpm <= 0 {
        return RateLimitInterceptor {
            governor: None,
            rate_limited_counter: rate_limit_counter,
        };
    }

    // Convert RPM to requests per second for governor configuration
    let requests_per_minute = NonZeroU32::new(rpm as u32).unwrap();
    let config = GovernorConfigBuilder::default()
        .per_minute(requests_per_minute.get() as u64)
        .burst_size(requests_per_minute.get())
        .key_extractor(ClientIpExtractor)
        .finish()
        .unwrap();

    RateLimitInterceptor {
        governor: Some(Governor::new(&config)),
        rate_limited_counter: rate_limit_counter,
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
fn get_rate_limit_config() -> i32 {
    const DEFAULT_RPM: i32 = 100;

    match env::var("SHIPPING_SERVICE_RATE_LIMIT_RPM") {
        Ok(val) => {
            val.parse::<i32>().unwrap_or_else(|_| {
                warn!("Invalid SHIPPING_SERVICE_RATE_LIMIT_RPM value, using default 100");
                DEFAULT_RPM
            })
        }
        Err(_) => DEFAULT_RPM,
    }
}

// Build rate limit interceptor based on environment configuration
fn build_rate_limit_interceptor(rate_limited_counter: Counter<u64>) -> RateLimitInterceptor {
    let rpm = get_rate_limit_config();

    if rpm <= 0 {
        info!("Rate limiting is disabled (SHIPPING_SERVICE_RATE_LIMIT_RPM = {})", rpm);
        return RateLimitInterceptor {
            governor: None,
            rate_limited_counter,
        };
    }

    // Create per-client IP rate limiter with token bucket
    let governor_config = GovernorConfigBuilder::default()
        .key_extractor(ClientIpExtractor)
        .per_second(rpm as u64 / 60)
        .burst_size(NonZeroU32::new(rpm as u32).unwrap())
        .use_headers()
        .build()
        .unwrap();

    let governor = Governor::new(&governor_config);

    info!("Rate limiting enabled: {} requests per minute per client IP", rpm);

    RateLimitInterceptor {
        governor: Some(governor),
        rate_limited_counter,
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

/// Global service state for shutdown coordination
pub static SERVICE_STATE: once_cell::sync::Lazy<Arc<AtomicUsize>> = once_cell::sync::Lazy::new(|| Arc::new(AtomicUsize::new(ServiceState::Running as usize)));

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
