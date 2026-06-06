// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use actix_web::{dev::ServerHandle, web, App, HttpServer};
use open_feature::provider::FeatureProvider;
use open_feature_flagd::{FlagdOptions, FlagdProvider};
use opentelemetry_instrumentation_actix_web::{RequestMetrics, RequestTracing};
use std::env;
use std::sync::Arc;
use std::time::Duration;
use tokio::signal;
use tokio::signal::unix::SignalKind;
use tracing::{info, warn};

mod telemetry_conf;
pub use telemetry_conf::init_otel as init_tracing;
mod shipping_service;
pub use shipping_service::{get_quote, health_check, ship_order};

/// Create Actix Web app for testing and production use
pub fn create_app() -> App<
    impl actix_web::dev::ServiceFactory<
        actix_web::dev::ServiceRequest,
        Response = actix_web::dev::ServiceResponse<actix_web::body::BoxBody>,
        Error = actix_web::Error,
        Config = (),
        InitError = (),
    >,
> {
    // Initialize flag provider for tests
    let provider = futures::executor::block_on(async {
        FlagdProvider::new(FlagdOptions {
            cache_settings: None,
            ..Default::default()
        })
        .await
        .expect("Failed to initialize flagd provider")
    });

    let flag_provider = web::Data::from(Arc::new(provider) as Arc<dyn FeatureProvider>);

    App::new()
        .app_data(flag_provider)
        .wrap(RequestTracing::new())
        .wrap(RequestMetrics::default())
        .service(get_quote)
        .service(ship_order)
        .service(health_check)
}
