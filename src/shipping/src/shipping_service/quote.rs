// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use core::fmt;
use opentelemetry::global;
use opentelemetry_instrumentation_actix_web::ClientExt;
use std::{collections::HashMap, env};

use anyhow::{Context, Result};
use opentelemetry::{trace::get_active_span, KeyValue};
use tracing::info;

use super::shipping_types::Quote;

pub async fn create_quote_from_count(count: u32) -> Result<Quote, tonic::Status> {
    if count == 0 {
        return Err(tonic::Status::invalid_argument("Count must be positive, got 0"));
    }
    let f = match request_quote(count).await {
        Ok(float) => float,
        Err(err) => {
            let msg = format!("{}", err);
            return Err(tonic::Status::unknown(msg));
        }
    };

    let meter = global::meter("otel_demo.shipping.quote");
    let counter = meter.u64_counter("demo.shipping.items_count").build();
    counter.add(count as u64, &[]);

    Ok(get_active_span(|span| {
        let q = create_quote_from_float(f);
        span.add_event(
            "Received Quote".to_string(),
            vec![KeyValue::new("demo.shipping.cost.total", format!("{}", q))],
        );
        span.set_attribute(KeyValue::new("demo.shipping.cost.total", format!("{}", q)));
        q
    }))
}

async fn request_quote(count: u32) -> Result<f64, anyhow::Error> {
    let client = awc::Client::new();
    let quote_service_addr: String = format!(
        "{}{}",
        env::var("QUOTE_ADDR")
            .unwrap_or_else(|_| "http://quote:8090".to_string())
            .parse::<String>()
            .expect("Invalid quote service address"),
        "/getquote"
    );

    info!(
        name = "RequestingQuote",
        quote_service_addr = quote_service_addr.as_str(),
        message = "Requesting quote"
    );

    let mut reqbody = HashMap::new();
    reqbody.insert("numberOfItems", count);

    let mut response = client
        .post(quote_service_addr)
        .trace_request()
        .send_json(&reqbody)
        .await
        .map_err(|err| anyhow::anyhow!("Failed to call quote service: {err}"))?;

    let bytes = response
        .body()
        .await
        .context("Failed to read response body from quote service")?;

    let resp = std::str::from_utf8(&bytes)
        .context("Failed to parse quote service response as UTF-8")?
        .to_owned();

    let f = resp
        .parse::<f64>()
        .context("Failed to parse quote value as f64")?;

    Ok(f)
}

pub async fn check_quote_service_health() -> bool {
    let quote_service_addr: String = format!(
        "{}{}",
        env::var("QUOTE_ADDR")
            .unwrap_or_else(|_| "http://quote:8090".to_string())
            .parse::<String>()
            .expect("Invalid quote service address"),
        "/getquote"
    );

    let client = awc::Client::builder()
        .timeout(std::time::Duration::from_millis(500))
        .finish();

    let mut reqbody = HashMap::new();
    reqbody.insert("numberOfItems", 1);

    match client
        .post(quote_service_addr)
        .trace_request()
        .send_json(&reqbody)
        .await
    {
        Ok(mut response) => {
            response.status().is_success()
        }
        Err(_) => false
    }
}

pub fn create_quote_from_float(value: f64) -> Quote {
    Quote {
        dollars: value.floor() as u64,
        cents: ((value * 100_f64) as u32) % 100,
    }
}

impl fmt::Display for Quote {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        write!(f, "{}.{}", self.dollars, self.cents)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_create_quote_from_float() {
        let quote = create_quote_from_float(10.99);
        assert_eq!(quote.dollars, 10);
        assert_eq!(quote.cents, 99);

        let quote = create_quote_from_float(0.01);
        assert_eq!(quote.dollars, 0);
        assert_eq!(quote.cents, 1);

        let quote = create_quote_from_float(100.00);
        assert_eq!(quote.dollars, 100);
        assert_eq!(quote.cents, 0);
    }

    #[test]
    fn test_quote_display() {
        let quote = Quote {
            dollars: 10,
            cents: 99,
        };
        assert_eq!(format!("{}", quote), "10.99");

        let quote = Quote {
            dollars: 0,
            cents: 1,
        };
        assert_eq!(format!("{}", quote), "0.1");
    }

    #[actix_web::test]
    async fn test_create_quote_from_count_validation() {
        // Test invalid count (0) returns error
        let result = create_quote_from_count(0).await;
        assert!(result.is_err());
        let err = result.err().unwrap();
        assert_eq!(err.code(), tonic::Code::InvalidArgument);
        assert_eq!(err.message(), "Count must be positive, got 0");

        // Test positive counts don't return validation error (they may return other errors if service isn't running, but validation passes)
        for count in [1, 5, 10, 100] {
            let result = create_quote_from_count(count).await;
            // Should not return invalid argument error, even if service is unreachable
            if let Err(e) = result {
                assert_ne!(e.code(), tonic::Code::InvalidArgument);
            }
        }
    }
}
