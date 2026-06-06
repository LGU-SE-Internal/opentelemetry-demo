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
use telemetry_conf::init_otel;
mod shipping_service;
use shipping_service::{get_quote, health_check, ship_order};

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

    let provider = FlagdProvider::new(FlagdOptions {
        cache_settings: None,
        ..Default::default()
    })
    .await
    .expect("Failed to initialize flagd provider");

    let flag_provider = web::Data::from(Arc::new(provider) as Arc<dyn FeatureProvider>);

    let server = HttpServer::new(move || {
        App::new()
            .app_data(flag_provider.clone())
            .wrap(RequestTracing::new())
            .wrap(RequestMetrics::default())
            .service(get_quote)
            .service(ship_order)
            .service(health_check)
    })
    .shutdown_timeout(30) // 30 second grace period
    .bind(&addr)?
    .run();

    let server_handle = server.handle();

    // Spawn signal handling task
    tokio::spawn(async move {
        let mut sigint = signal::unix::signal(SignalKind::interrupt())
            .expect("Failed to register SIGINT handler");
        let mut sigterm = signal::unix::signal(SignalKind::terminate())
            .expect("Failed to register SIGTERM handler");

        let signal_name = tokio::select! {
            _ = sigint.recv() => "SIGINT",
            _ = sigterm.recv() => "SIGTERM",
        };

        info!(
            signal = signal_name,
            grace_period_seconds = 30,
            "Received shutdown signal, initiating graceful shutdown"
        );

        // Stop server with graceful shutdown enabled (uses configured shutdown_timeout of 30s)
        let result = server_handle.stop(true).await;

        if result >= 0 {
            info!(
                completed_requests = result,
                "Graceful shutdown completed successfully, all active requests finished"
            );
        } else {
            warn!(
                incomplete_requests = result.abs(),
                "Grace period expired, forcing shutdown of remaining requests"
            );
        }
    });

    server.await
}
