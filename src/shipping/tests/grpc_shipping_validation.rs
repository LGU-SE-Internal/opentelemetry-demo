// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use opentelemetry_proto::tonic::demo::shipping::v1::*;
use tonic::{Code, Status};
use super::common::spawn_test_server;

#[tokio::test]
async fn test_ac1_getquote_missing_address_field_returns_invalid_argument() {
    let (client, _jh) = spawn_test_server().await;

    // Request with missing street address
    let request = GetQuoteRequest {
        address: Some(Address {
            street: "".to_string(),
            city: "San Francisco".to_string(),
            state: "CA".to_string(),
            country: "US".to_string(),
            zip_code: "94105".to_string(),
        }),
        weight: 5.0,
    };

    let result = client.get_quote(request).await;
    assert!(result.is_err());
    let status = result.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert!(status.message().contains("Missing required field: street"));
}

#[tokio::test]
async fn test_ac2_getquote_weight_zero_returns_invalid_argument() {
    let (client, _jh) = spawn_test_server().await;

    let request = GetQuoteRequest {
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "San Francisco".to_string(),
            state: "CA".to_string(),
            country: "US".to_string(),
            zip_code: "94105".to_string(),
        }),
        weight: 0.0,
    };

    let result = client.get_quote(request).await;
    assert!(result.is_err());
    let status = result.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "Weight must be greater than 0");
}

#[tokio::test]
async fn test_ac2_getquote_weight_negative_returns_invalid_argument() {
    let (client, _jh) = spawn_test_server().await;

    let request = GetQuoteRequest {
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "San Francisco".to_string(),
            state: "CA".to_string(),
            country: "US".to_string(),
            zip_code: "94105".to_string(),
        }),
        weight: -2.5,
    };

    let result = client.get_quote(request).await;
    assert!(result.is_err());
    let status = result.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "Weight must be greater than 0");
}

#[tokio::test]
async fn test_ac3_getquote_valid_request_passes_validation() {
    let (client, _jh) = spawn_test_server().await;

    let request = GetQuoteRequest {
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "San Francisco".to_string(),
            state: "CA".to_string(),
            country: "US".to_string(),
            zip_code: "94105".to_string(),
        }),
        weight: 2.75,
    };

    let result = client.get_quote(request).await;
    // Should not return validation error (may return other errors from business logic, but not InvalidArgument)
    if let Err(status) = result {
        assert_ne!(status.code(), Code::InvalidArgument);
    }
}

#[tokio::test]
async fn test_ac4_shiporder_empty_address_field_returns_invalid_argument() {
    let (client, _jh) = spawn_test_server().await;

    let request = ShipOrderRequest {
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "".to_string(), // Empty city field
            state: "CA".to_string(),
            country: "US".to_string(),
            zip_code: "94105".to_string(),
        }),
        items: vec![OrderItem {
            product_id: "product-123".to_string(),
            quantity: 2,
        }],
    };

    let result = client.ship_order(request).await;
    assert!(result.is_err());
    let status = result.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert!(status.message().contains("Address field city cannot be empty"));
}

#[tokio::test]
async fn test_ac5_shiporder_invalid_zipcode_returns_invalid_argument() {
    let (client, _jh) = spawn_test_server().await;

    let request = ShipOrderRequest {
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "San Francisco".to_string(),
            state: "CA".to_string(),
            country: "US".to_string(),
            zip_code: "1234".to_string(), // Only 4 digits, invalid US zip
        }),
        items: vec![OrderItem {
            product_id: "product-123".to_string(),
            quantity: 2,
        }],
    };

    let result = client.ship_order(request).await;
    assert!(result.is_err());
    let status = result.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "Invalid zip code format");
}

#[tokio::test]
async fn test_ac5_shiporder_international_zipcode_too_long_returns_invalid_argument() {
    let (client, _jh) = spawn_test_server().await;

    let request = ShipOrderRequest {
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "London".to_string(),
            state: "London".to_string(),
            country: "GB".to_string(),
            zip_code: "SW1A 1AAXXX".to_string(), // 11 chars, too long
        }),
        items: vec![OrderItem {
            product_id: "product-123".to_string(),
            quantity: 2,
        }],
    };

    let result = client.ship_order(request).await;
    assert!(result.is_err());
    let status = result.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "Invalid zip code format");
}

#[tokio::test]
async fn test_ac6_shiporder_empty_items_list_returns_invalid_argument() {
    let (client, _jh) = spawn_test_server().await;

    let request = ShipOrderRequest {
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "San Francisco".to_string(),
            state: "CA".to_string(),
            country: "US".to_string(),
            zip_code: "94105".to_string(),
        }),
        items: vec![], // Empty items list
    };

    let result = client.ship_order(request).await;
    assert!(result.is_err());
    let status = result.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "Order items list cannot be empty");
}

#[tokio::test]
async fn test_ac7_shiporder_item_quantity_zero_returns_invalid_argument() {
    let (client, _jh) = spawn_test_server().await;

    let request = ShipOrderRequest {
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "San Francisco".to_string(),
            state: "CA".to_string(),
            country: "US".to_string(),
            zip_code: "94105".to_string(),
        }),
        items: vec![
            OrderItem {
                product_id: "product-123".to_string(),
                quantity: 0, // Invalid zero quantity
            },
            OrderItem {
                product_id: "product-456".to_string(),
                quantity: 3,
            }
        ],
    };

    let result = client.ship_order(request).await;
    assert!(result.is_err());
    let status = result.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert!(status.message().contains("Item product-123 has invalid quantity: must be greater than 0"));
}

#[tokio::test]
async fn test_ac8_shiporder_valid_request_passes_validation() {
    let (client, _jh) = spawn_test_server().await;

    let request = ShipOrderRequest {
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "San Francisco".to_string(),
            state: "CA".to_string(),
            country: "US".to_string(),
            zip_code: "94105".to_string(),
        }),
        items: vec![
            OrderItem {
                product_id: "product-123".to_string(),
                quantity: 2,
            },
            OrderItem {
                product_id: "product-456".to_string(),
                quantity: 1,
            }
        ],
    };

    let result = client.ship_order(request).await;
    if let Err(status) = result {
        assert_ne!(status.code(), Code::InvalidArgument);
    }
}

#[tokio::test]
async fn test_ac9_getshipping_empty_tracking_id_returns_invalid_argument() {
    let (client, _jh) = spawn_test_server().await;

    let request = GetShippingRequest {
        tracking_id: "".to_string(),
    };

    let result = client.get_shipping(request).await;
    assert!(result.is_err());
    let status = result.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "Tracking ID cannot be empty");
}

#[tokio::test]
async fn test_ac10_getshipping_invalid_tracking_id_format_returns_invalid_argument() {
    let (client, _jh) = spawn_test_server().await;

    let request = GetShippingRequest {
        tracking_id: "INVALID-12345".to_string(), // Does not match SHIP-XXXXXXXXXXXX format
    };

    let result = client.get_shipping(request).await;
    assert!(result.is_err());
    let status = result.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "Invalid tracking ID format: expected format SHIP-XXXXXXXXXXXX");
}

#[tokio::test]
async fn test_ac11_getshipping_valid_tracking_id_passes_validation() {
    let (client, _jh) = spawn_test_server().await;

    let request = GetShippingRequest {
        tracking_id: "SHIP-ABC123DEF456".to_string(), // Valid format
    };

    let result = client.get_shipping(request).await;
    if let Err(status) = result {
        assert_ne!(status.code(), Code::InvalidArgument);
    }
}
