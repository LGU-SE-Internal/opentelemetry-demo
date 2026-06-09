// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use actix_web::{get, post, web, HttpResponse, Responder};
use serde::Serialize;
use open_feature::provider::FeatureProvider;
use open_feature::EvaluationContext;
use tracing::{error, info, warn};
use std::path::{Path, PathBuf};
use std::env;
use thiserror::Error;
use tonic::transport::Server;
use tonic::transport::ServerTlsConfig;
use rustls_pemfile::{certs, pkcs8_private_keys};
use std::fs::File;
use std::io::BufReader;

#[derive(Debug, Error)]
pub enum TlsConfigError {
    #[error("Missing required environment variable: {0}")]
    MissingRequiredVariable(String),
    #[error("File not found at path: {0}")]
    FileNotFound(PathBuf),
    #[error("Invalid certificate or key: {0}")]
    InvalidCertificate(String),
    #[error("TLS configuration error: {0}")]
    TonicError(#[from] tonic::transport::Error),
    #[error("IO error: {0}")]
    IoError(#[from] std::io::Error),
}

#[derive(Debug, Clone)]
pub struct TlsConfig {
    pub enabled: bool,
    pub ca_cert_path: PathBuf,
    pub server_cert_path: PathBuf,
    pub server_key_path: PathBuf,
    pub mtls_enabled: bool,
}

fn is_truthy(s: &str) -> bool {
    matches!(s.to_lowercase().as_str(), "true" | "1" | "yes")
}

/// Load TLS configuration from environment variables, validates all paths exist when TLS is enabled
/// Returns TlsConfig if valid, returns error with descriptive message if validation fails
pub fn load_tls_config_from_env() -> Result<TlsConfig, TlsConfigError> {
    let enabled = env::var("SHIPPING_GRPC_TLS_ENABLED")
        .map(|v| is_truthy(&v))
        .unwrap_or(false);
    
    if !enabled {
        return Ok(TlsConfig {
            enabled: false,
            ca_cert_path: PathBuf::new(),
            server_cert_path: PathBuf::new(),
            server_key_path: PathBuf::new(),
            mtls_enabled: false,
        });
    }

    // TLS is enabled, load required variables
    let ca_cert_path = env::var("SHIPPING_GRPC_TLS_CA_CERT_PATH")
        .map_err(|_| TlsConfigError::MissingRequiredVariable("SHIPPING_GRPC_TLS_CA_CERT_PATH".into()))?
        .into();
    let server_cert_path = env::var("SHIPPING_GRPC_TLS_SERVER_CERT_PATH")
        .map_err(|_| TlsConfigError::MissingRequiredVariable("SHIPPING_GRPC_TLS_SERVER_CERT_PATH".into()))?
        .into();
    let server_key_path = env::var("SHIPPING_GRPC_TLS_SERVER_KEY_PATH")
        .map_err(|_| TlsConfigError::MissingRequiredVariable("SHIPPING_GRPC_TLS_SERVER_KEY_PATH".into()))?
        .into();
    
    let mtls_enabled = env::var("SHIPPING_GRPC_MTLS_ENABLED")
        .map(|v| is_truthy(&v))
        .unwrap_or(false);

    // Validate files exist
    for path in &[&ca_cert_path, &server_cert_path, &server_key_path] {
        if !Path::new(path).exists() {
            return Err(TlsConfigError::FileNotFound(path.clone()));
        }
    }

    Ok(TlsConfig {
        enabled: true,
        ca_cert_path,
        server_cert_path,
        server_key_path,
        mtls_enabled,
    })
}

/// Applies TLS configuration to the provided tonic Server builder
/// Returns configured Server builder if successful, error if certificate parsing fails
pub fn configure_tls_server(mut server: Server, config: &TlsConfig) -> Result<Server, TlsConfigError> {
    if !config.enabled {
        return Ok(server);
    }

    // Load server cert and key
    let cert_file = File::open(&config.server_cert_path)?;
    let mut cert_reader = BufReader::new(cert_file);
    let cert_chain = certs(&mut cert_reader)?
        .into_iter()
        .map(rustls::Certificate)
        .collect();

    let key_file = File::open(&config.server_key_path)?;
    let mut key_reader = BufReader::new(key_file);
    let mut keys = pkcs8_private_keys(&mut key_reader)?;
    if keys.is_empty() {
        return Err(TlsConfigError::InvalidCertificate("No private key found in server key file".into()));
    }
    let private_key = rustls::PrivateKey(keys.remove(0));

    let mut tls_config = ServerTlsConfig::new()
        .identity(tonic::transport::Identity::from_cert_and_key(cert_chain, private_key));

    if config.mtls_enabled {
        // Load CA cert for client validation
        let ca_cert_pem = std::fs::read_to_string(&config.ca_cert_path)?;
        tls_config = tls_config.client_ca_root(tonic::transport::Certificate::from_pem(ca_cert_pem));
    }

    server = server.tls_config(tls_config)?;

    Ok(server)
}

mod quote;
use quote::{check_quote_service_health, create_quote_from_count};

mod tracking;
use tracking::create_tracking_id;
mod shipping_types;
pub use shipping_types::*;
mod retry;
pub use retry::*;

#[derive(Serialize)]
struct HealthResponse {
    status: &'static str,
    dependencies: Dependencies,
}

#[derive(Serialize)]
struct Dependencies {
    quote_service: &'static str,
}

#[get("/health")]
pub async fn health_check() -> impl Responder {
    let quote_up = check_quote_service_health().await;
    let (status, quote_status, http_status) = if quote_up {
        ("healthy", "up", HttpResponse::Ok())
    } else {
        ("unhealthy", "down", HttpResponse::ServiceUnavailable())
    };

    http_status.json(HealthResponse {
        status,
        dependencies: Dependencies {
            quote_service: quote_status,
        },
    })
}

const NANOS_MULTIPLE: u32 = 10000000u32;

#[post("/get-quote")]
pub async fn get_quote(req: web::Json<GetQuoteRequest>) -> impl Responder {
    if let Err(validation_errors) = req.validate() {
        let first_error = validation_errors.errors().values().next().and_then(|v| v.first()).unwrap();
        let message = first_error.message.as_ref().unwrap_or(&"Invalid request".into()).to_string();
        return HttpResponse::BadRequest().json(serde_json::json!({ "message": message }));
    }

    let itemct: u32 = req.items.iter().map(|item| item.quantity as u32).sum();
    
    // Log incoming quote request (AC-1)
    info!(
        event = "quote_request_received",
        item_count = itemct,
        currency = "USD",
        "Quote request received"
    );

    let quote = match create_quote_from_count(itemct).await {
        Ok(q) => q,
        Err(e) => {
            // Log quote error (AC-3)
            error!(
                event = "quote_request_failed",
                error = %e,
                "Quote request failed"
            );
            return HttpResponse::InternalServerError().body(format!("Failed to get quote: {}", e));
        }
    };

    let reply = GetQuoteResponse {
        cost_usd: Some(Money {
            currency_code: "USD".into(),
            units: quote.dollars,
            nanos: quote.cents * NANOS_MULTIPLE,
        }),
    };

    // Log quote response (AC-2)
    let price_cents = quote.dollars * 100 + quote.cents as u64;
    info!(
        event = "quote_response_sent",
        price_cents = price_cents,
        currency = "USD",
        "Quote response sent"
    );

    info!(
        name = "SendingQuoteValue",
        quote.dollars = quote.dollars,
        quote.cents = quote.cents,
        message = "Sending Quote"
    );

    HttpResponse::Ok().json(reply)
}

#[post("/ship-order")]
pub async fn ship_order(
    req: web::Json<ShipOrderRequest>,
    flag_provider: web::Data<dyn FeatureProvider>,
) -> impl Responder {
    if let Err(validation_errors) = req.validate() {
        let first_error = validation_errors.errors().values().next().and_then(|v| v.first()).unwrap();
        let message = first_error.message.as_ref().unwrap_or(&"Invalid request".into()).to_string();
        return HttpResponse::BadRequest().json(serde_json::json!({ "message": message }));
    }

    let is_outside_us = req
        .address
        .as_ref()
        .map(|addr| {
            !matches!(
                addr.country.to_uppercase().trim(),
                "US" | "USA" | "UNITED STATES" | "UNITED STATES OF AMERICA"
            )
        })
        .unwrap_or(false);

    let slowdown_secs = if is_outside_us {
        flag_provider
            .resolve_int_value("intlShippingSlowdown", &EvaluationContext::default())
            .await
            .map(|res| {
                info!(
                    feature_flag.key = "intlShippingSlowdown",
                    feature_flag.provider_name = "flagd",
                    feature_flag.variant = res.variant.as_deref().unwrap_or("unknown"),
                    message = "feature flag evaluated"
                );
                res.value
            })
            .unwrap_or_else(|e| {
                warn!("Failed to evaluate feature flag intlShippingSlowdown: {:?}", e);
                0
            })
    } else {
        0
    };

    if slowdown_secs > 0 {
        info!(
            name = "IntlShippingSlowdown",
            shipping.delay_secs = slowdown_secs,
            message = "Delaying international shipment due to intlShippingSlowdown feature flag"
        );
        actix_web::rt::time::sleep(std::time::Duration::from_secs(slowdown_secs as u64)).await;
    }

    let tid = create_tracking_id();
    info!(
        name = "CreatingTrackingId",
        tracking_id = tid.as_str(),
        message = "Tracking ID Created"
    );
    HttpResponse::Ok().json(ShipOrderResponse { tracking_id: tid })
}

#[cfg(test)]
mod tests {
    use actix_web::{http::header::ContentType, test, App};
    use open_feature::provider::{MockFeatureProvider, ResolutionDetails};
    use std::sync::Arc;

    use super::*;

    fn mock_provider(value: i64) -> web::Data<dyn FeatureProvider> {
        let mut mock = MockFeatureProvider::new();
        mock.expect_resolve_int_value()
            .returning(move |_, _| Ok(ResolutionDetails::new(value)));
        web::Data::from(Arc::new(mock) as Arc<dyn FeatureProvider>)
    }

    async fn call_ship_order(
        address: Option<Address>,
        provider: web::Data<dyn FeatureProvider>,
    ) -> ShipOrderResponse {
        let app = test::init_service(
            App::new()
                .app_data(provider)
                .service(ship_order),
        )
        .await;
        let req = test::TestRequest::post()
            .uri("/ship-order")
            .insert_header(ContentType::json())
            .set_json(&ShipOrderRequest { address, items: vec![] })
            .to_request();
        let resp = test::call_service(&app, req).await;
        assert!(resp.status().is_success());
        test::read_body_json(resp).await
    }

    fn make_address(country: &str) -> Address {
        Address {
            street_address: "123 Main St".into(),
            city: "Anytown".into(),
            state: "CA".into(),
            country: country.into(),
            zip_code: "00000".into(),
        }
    }

    #[actix_web::test]
    async fn test_ship_order_no_address() {
        let order = call_ship_order(None, mock_provider(0)).await;
        assert!(!order.tracking_id.is_empty());
    }

    #[actix_web::test]
    async fn test_ship_order_us_address() {
        let order = call_ship_order(Some(make_address("US")), mock_provider(0)).await;
        assert!(!order.tracking_id.is_empty());
    }

    #[actix_web::test]
    async fn test_ship_order_international_flag_off() {
        let order = call_ship_order(Some(make_address("FR")), mock_provider(0)).await;
        assert!(!order.tracking_id.is_empty());
    }

    #[actix_web::test]
    async fn test_ship_order_international_flag_on() {
        let start = std::time::Instant::now();
        let order = call_ship_order(Some(make_address("FR")), mock_provider(1)).await;
        assert!(start.elapsed() >= std::time::Duration::from_secs(1));
        assert!(!order.tracking_id.is_empty());
    }

    #[actix_web::test]
    async fn test_ship_order_us_flag_on() {
        let start = std::time::Instant::now();
        let order = call_ship_order(Some(make_address("US")), mock_provider(10)).await;
        assert!(start.elapsed() < std::time::Duration::from_secs(1));
        assert!(!order.tracking_id.is_empty());
    }
}
