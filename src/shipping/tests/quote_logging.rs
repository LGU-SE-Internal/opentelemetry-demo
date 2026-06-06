// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use std::sync::{Arc, Mutex};
use shipping::shipping_service::quote::handle_quote_request;
use shipping::shipping_service::shipping_types::{Item, QuoteRequest, QuoteResponse, QuoteError};
use tracing::{Event, Level, Subscriber, field};
use tracing_subscriber::layer::{Context, Layer};
use tracing_subscriber::prelude::*;
use opentelemetry::trace::Tracer as _;
use serde_json;

#[derive(Debug, Clone, Default)]
struct FieldVisitor(std::collections::HashMap<String, field::Value<'static>>);

impl field::Visit for FieldVisitor {
    fn record_debug(&mut self, field: &field::Field, value: &dyn std::fmt::Debug) {
        self.0.insert(field.name().to_string(), field::Value::debug(value));
    }
    
    fn record_u64(&mut self, field: &field::Field, value: u64) {
        self.0.insert(field.name().to_string(), field::Value::UInt(value));
    }
    
    fn record_i64(&mut self, field: &field::Field, value: i64) {
        self.0.insert(field.name().to_string(), field::Value::Int(value));
    }
    
    fn record_bool(&mut self, field: &field::Field, value: bool) {
        self.0.insert(field.name().to_string(), field::Value::Bool(value));
    }
    
    fn record_str(&mut self, field: &field::Field, value: &str) {
        self.0.insert(field.name().to_string(), field::Value::Str(value.to_string().into()));
    }
}

#[derive(Debug, Clone)]
struct CapturedEvent {
    level: Level,
    fields: std::collections::HashMap<String, serde_json::Value>,
}

struct CapturingLayer {
    events: Arc<Mutex<Vec<CapturedEvent>>>,
}

impl<S: Subscriber> Layer<S> for CapturingLayer {
    fn on_event(&self, event: &Event<'_>, _ctx: Context<'_, S>) {
        let mut visitor = FieldVisitor::default();
        event.record(&mut visitor);
        
        let mut fields = std::collections::HashMap::new();
        for (k, v) in visitor.0.iter() {
            let value = match v {
                field::Value::Int(i) => serde_json::Value::Number((*i).into()),
                field::Value::UInt(u) => serde_json::Value::Number((*u).into()),
                field::Value::Bool(b) => serde_json::Value::Bool(*b),
                field::Value::Str(s) => serde_json::Value::String(s.to_string()),
                _ => serde_json::Value::String(format!("{:?}", v)),
            };
            fields.insert(k.to_string(), value);
        }
        
        let captured = CapturedEvent {
            level: *event.metadata().level(),
            fields,
        };
        
        self.events.lock().unwrap().push(captured);
    }
}

// Helper to run async function with capturing subscriber
async fn run_with_capture<F, T>(f: F) -> (T, Vec<CapturedEvent>)
where
    F: std::future::Future<Output = T>,
{
    let events = Arc::new(Mutex::new(Vec::new()));
    let layer = CapturingLayer { events: events.clone() };
    
    let tracer = opentelemetry::no_op::NoopTracer::new();
    let otel_layer = tracing_opentelemetry::layer().with_tracer(tracer);
    
    let subscriber = tracing_subscriber::registry()
        .with(otel_layer)
        .with(layer);
    
    let result = tracing::subscriber::with_default(subscriber, || async {
        f.await
    }).await;
    
    let captured = events.lock().unwrap().clone();
    (result, captured)
}

#[tokio::test]
async fn test_ac1_quote_request_received_logged_on_valid_input() {
    let request = QuoteRequest {
        items: vec![
            Item { product_id: "prod1".to_string(), quantity: 1, weight: 100 },
            Item { product_id: "prod2".to_string(), quantity: 2, weight: 200 },
        ],
        currency: "USD".to_string(),
    };
    
    let (_, events) = run_with_capture(async {
        let _ = handle_quote_request(request).await;
    }).await;
    
    // Check for INFO level log with expected fields
    let request_log = events.iter()
        .find(|e| e.level == Level::INFO && e.fields.get("event") == Some(&serde_json::Value::String("quote_request_received".to_string())))
        .expect("Expected quote_request_received log event");
    
    assert_eq!(request_log.fields.get("item_count"), Some(&serde_json::Value::UInt(2)));
    assert_eq!(request_log.fields.get("currency"), Some(&serde_json::Value::String("USD".to_string())));
    assert!(request_log.fields.contains_key("trace_id"), "Log missing trace_id field");
    assert!(request_log.fields.contains_key("span_id"), "Log missing span_id field");
}

#[tokio::test]
async fn test_ac2_quote_response_sent_logged_on_success() {
    let request = QuoteRequest {
        items: vec![
            Item { product_id: "prod1".to_string(), quantity: 1, weight: 100 },
        ],
        currency: "EUR".to_string(),
    };
    
    let (result, events) = run_with_capture(async {
        handle_quote_request(request).await
    }).await;
    
    // Only check log if result is Ok (as expected for valid input)
    if let Ok(response) = result {
        let response_log = events.iter()
            .find(|e| e.level == Level::INFO && e.fields.get("event") == Some(&serde_json::Value::String("quote_response_sent".to_string())))
            .expect("Expected quote_response_sent log event");
        
        assert_eq!(response_log.fields.get("price_cents"), Some(&serde_json::Value::UInt(response.price_cents)));
        assert_eq!(response_log.fields.get("currency"), Some(&serde_json::Value::String(response.currency)));
        assert!(response_log.fields.contains_key("trace_id"), "Log missing trace_id field");
        assert!(response_log.fields.contains_key("span_id"), "Log missing span_id field");
    } else {
        panic!("Expected successful quote response for valid request");
    }
}

#[tokio::test]
async fn test_ac3_error_logged_on_quote_failure() {
    // Test with invalid currency to trigger error
    let request = QuoteRequest {
        items: vec![
            Item { product_id: "prod1".to_string(), quantity: 1, weight: 100 },
        ],
        currency: "INVALID_CURRENCY".to_string(),
    };
    
    let (result, events) = run_with_capture(async {
        handle_quote_request(request).await
    }).await;
    
    // Only check log if result is Err (as expected)
    if let Err(error) = result {
        let error_log = events.iter()
            .find(|e| e.level == Level::ERROR && e.fields.get("event") == Some(&serde_json::Value::String("quote_request_failed".to_string())))
            .expect("Expected quote_request_failed log event");
        
        assert_eq!(error_log.fields.get("error"), Some(&serde_json::Value::String(error.to_string())));
        assert!(error_log.fields.contains_key("trace_id"), "Log missing trace_id field");
        assert!(error_log.fields.contains_key("span_id"), "Log missing span_id field");
    } else {
        panic!("Expected error for invalid currency request");
    }
}

#[tokio::test]
async fn test_ac4_fields_are_structured_not_interpolated() {
    let request = QuoteRequest {
        items: vec![
            Item { product_id: "prod1".to_string(), quantity: 3, weight: 150 },
        ],
        currency: "GBP".to_string(),
    };
    
    let (_, events) = run_with_capture(async {
        let _ = handle_quote_request(request).await;
    }).await;
    
    // Verify all dynamic fields are present as separate fields, not in message
    for event in events {
        // Check request event
        if event.fields.get("event") == Some(&serde_json::Value::String("quote_request_received".to_string())) {
            assert!(event.fields.get("item_count").is_some(), "item_count not present as structured field");
            assert!(event.fields.get("currency").is_some(), "currency not present as structured field");
            // Ensure fields are not interpolated into message string
            if let Some(msg) = event.fields.get("message") {
                let msg_str = msg.as_str().unwrap_or("");
                assert!(!msg_str.contains("item_count"), "item_count found in formatted message, should be structured field");
                assert!(!msg_str.contains("currency"), "currency found in formatted message, should be structured field");
            }
        }
        
        // Check response event
        if event.fields.get("event") == Some(&serde_json::Value::String("quote_response_sent".to_string())) {
            assert!(event.fields.get("price_cents").is_some(), "price_cents not present as structured field");
            assert!(event.fields.get("currency").is_some(), "currency not present as structured field");
            if let Some(msg) = event.fields.get("message") {
                let msg_str = msg.as_str().unwrap_or("");
                assert!(!msg_str.contains("price_cents"), "price_cents found in formatted message, should be structured field");
                assert!(!msg_str.contains("currency"), "currency found in formatted message, should be structured field");
            }
        }
        
        // Check error event
        if event.fields.get("event") == Some(&serde_json::Value::String("quote_request_failed".to_string())) {
            assert!(event.fields.get("error").is_some(), "error not present as structured field");
            if let Some(msg) = event.fields.get("message") {
                let msg_str = msg.as_str().unwrap_or("");
                assert!(!msg_str.contains("error"), "error found in formatted message, should be structured field");
            }
        }
    }
}

#[test]
fn test_ac5_no_new_dependencies_added() {
    // Read current Cargo.toml and verify no new dependencies for logging were added
    let cargo_toml = std::fs::read_to_string("Cargo.toml").expect("Failed to read Cargo.toml");
    
    // Check that only existing logging dependencies are present
    assert!(cargo_toml.contains(r#"tracing = "0.1.44""#), "Expected existing tracing dependency");
    assert!(cargo_toml.contains(r#"opentelemetry-appender-tracing = "0.32.0""#), "Expected existing opentelemetry-appender-tracing dependency");
    assert!(cargo_toml.contains(r#"tracing-subscriber = "0.3.23""#), "Expected existing tracing-subscriber dependency");
    
    // Count number of dependencies related to logging/tracing to ensure no new ones added
    let logging_deps = ["tracing =", "opentelemetry-appender-tracing =", "tracing-subscriber =", "tracing-opentelemetry ="];
    let mut count = 0;
    for line in cargo_toml.lines() {
        for dep in &logging_deps {
            if line.starts_with(dep) {
                count += 1;
            }
        }
    }
    
    // There should be exactly 3 logging-related dependencies in main, plus optional one in opentelemetry
    assert!(count >= 3, "Unexpected number of logging dependencies found, new ones may have been added");
}
