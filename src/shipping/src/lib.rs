pub mod flagd_resiliency;
pub mod retry;
pub mod shipping_service;
pub mod telemetry_conf;

pub mod feature_flags {
    pub use super::flagd_resiliency::get_feature_flag;
}

use std::env;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use governor::{Quota, RateLimiter, clock::DefaultClock, state::keyed::DashMapStateStore};
use once_cell::sync::Lazy;
use tonic::{Request, Status};
use tracing::warn;
use opentelemetry::{
    global,
    metrics::{Counter, Meter},
    KeyValue,
};

enum RateLimitIdentifier {
    Ip,
    ClientId,
}

static RATE_LIMITER: Lazy<Arc<RateLimiter<String, DashMapStateStore<String>, DefaultClock>>> = Lazy::new(|| {
    let requests_per_window: u32 = env::var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW")
        .unwrap_or("100".to_string())
        .parse()
        .unwrap_or(100);
    
    if requests_per_window == 0 {
        return Arc::new(RateLimiter::direct(Quota::per_second(std::num::NonZeroU32::new(u32::MAX).unwrap())));
    }
    
    let window_seconds: u64 = env::var("SHIPPING_RATE_LIMIT_WINDOW_SECONDS")
        .unwrap_or("60".to_string())
        .parse()
        .unwrap_or(60);
    
    let quota = Quota::with_period(Duration::from_secs(window_seconds / requests_per_window as u64))
        .unwrap()
        .allow_burst(std::num::NonZeroU32::new(requests_per_window).unwrap());
    
    Arc::new(RateLimiter::keyed(quota))
});

static IDENTIFIER_TYPE: Lazy<RateLimitIdentifier> = Lazy::new(|| {
    match env::var("SHIPPING_RATE_LIMIT_IDENTIFIER").unwrap_or("ip".to_string()).as_str() {
        "client_id" => RateLimitIdentifier::ClientId,
        _ => RateLimitIdentifier::Ip,
    }
});

static METER: Lazy<Meter> = Lazy::new(|| global::meter("shipping-service"));
static RATE_LIMITED_REQUESTS_COUNTER: Lazy<Counter<u64>> = Lazy::new(|| {
    METER.u64_counter("shipping_service_rate_limited_requests_total")
        .with_description("Total number of requests processed by the rate limiter")
        .init()
});

pub fn rate_limit_interceptor() -> impl Fn(Request<()>) -> Result<Request<()>, Status> + Clone {
    move |mut req: Request<()>| {
        let requests_per_window: u32 = env::var("SHIPPING_RATE_LIMIT_REQUESTS_PER_WINDOW")
            .unwrap_or("100".to_string())
            .parse()
            .unwrap_or(100);
        
        if requests_per_window == 0 {
            // Rate limiting is disabled
            return Ok(req);
        }

        // Get client IP
        let client_ip = req.extensions().get::<SocketAddr>().map(|addr| addr.ip().to_string()).unwrap_or_default();
        
        // Get client ID
        let client_id = req.metadata().get("x-client-id").and_then(|v| v.to_str().ok()).map(|s| s.to_string());
        
        // Get client identifier based on config
        let client_identifier = match *IDENTIFIER_TYPE {
            RateLimitIdentifier::Ip => client_ip.clone(),
            RateLimitIdentifier::ClientId => client_id.clone().unwrap_or(client_ip.clone()),
        };
        
        // Get gRPC method path
        let grpc_method = req.extensions().get::<tonic::codegen::http::uri::PathAndQuery>()
            .map(|p| p.to_string())
            .unwrap_or_else(|| "unknown".to_string());
        
        // Check rate limit
        match RATE_LIMITER.check_key(&client_identifier) {
            Ok(_) => {
                // Allowed
                RATE_LIMITED_REQUESTS_COUNTER.add(1, &[
                    KeyValue::new("client_identifier", client_identifier),
                    KeyValue::new("grpc_method", grpc_method),
                    KeyValue::new("status", "allowed"),
                ]);
                Ok(req)
            }
            Err(negative) => {
                // Denied
                let reset_time = SystemTime::now() + negative.wait_time_from(governor::clock::DefaultClock::now());
                let reset_timestamp = reset_time.duration_since(UNIX_EPOCH).unwrap().as_secs();
                
                warn!(
                    client_ip = %client_ip,
                    client_id = ?client_id,
                    grpc_method = %grpc_method,
                    rate_limit_reset_timestamp = %reset_timestamp,
                    "Rate limit exceeded for client"
                );
                
                RATE_LIMITED_REQUESTS_COUNTER.add(1, &[
                    KeyValue::new("client_identifier", client_identifier),
                    KeyValue::new("grpc_method", grpc_method),
                    KeyValue::new("status", "denied"),
                ]);
                
                Err(Status::resource_exhausted("Rate limit exceeded, try again later"))
            }
        }
    }
}
