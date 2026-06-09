use std::env;
use std::time::Duration;
use tonic::transport::Channel;
use tonic::{Code, Status};
use opentelemetry_proto::oteldemo::shipping_service_client::ShippingServiceClient;
use opentelemetry_proto::oteldemo::{
    GetQuoteRequest, ShipOrderRequest, GetShippingRequest, Address, OrderItem
};
use regex::Regex;

// Helper to get shipping service client
async fn get_shipping_client() -> ShippingServiceClient<Channel> {
    // Ensure service is running
    tokio::time::sleep(Duration::from_secs(1)).await;
    ShippingServiceClient::connect("http://localhost:8080")
        .await
        .expect("Failed to connect to shipping service gRPC endpoint")
}

// AC-1: GetQuote with empty items list returns InvalidArgument
#[tokio::test]
async fn test_ac1_get_quote_empty_items() {
    let mut client = get_shipping_client().await;
    
    let request = tonic::Request::new(GetQuoteRequest {
        items: vec![],
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "Anytown".to_string(),
            state: "CA".to_string(),
            zip_code: "12345".to_string(),
            country: "USA".to_string(),
        }),
    });
    
    let response = client.get_quote(request).await;
    assert!(response.is_err());
    let status = response.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "items list cannot be empty");
}

// AC-2: GetQuote with item quantity 0 returns InvalidArgument
#[tokio::test]
async fn test_ac2_get_quote_zero_quantity() {
    let mut client = get_shipping_client().await;
    
    let request = tonic::Request::new(GetQuoteRequest {
        items: vec![
            OrderItem {
                product_id: "prod1".to_string(),
                quantity: 0,
                price: 10.99,
            }
        ],
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "Anytown".to_string(),
            state: "CA".to_string(),
            zip_code: "12345".to_string(),
            country: "USA".to_string(),
        }),
    });
    
    let response = client.get_quote(request).await;
    assert!(response.is_err());
    let status = response.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "item at index 0 has invalid non-positive quantity: 0");
}

// AC-3: GetQuote with item negative quantity returns InvalidArgument
#[tokio::test]
async fn test_ac3_get_quote_negative_quantity() {
    let mut client = get_shipping_client().await;
    
    let request = tonic::Request::new(GetQuoteRequest {
        items: vec![
            OrderItem {
                product_id: "prod1".to_string(),
                quantity: -2,
                price: 10.99,
            }
        ],
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "Anytown".to_string(),
            state: "CA".to_string(),
            zip_code: "12345".to_string(),
            country: "USA".to_string(),
        }),
    });
    
    let response = client.get_quote(request).await;
    assert!(response.is_err());
    let status = response.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "item at index 0 has invalid non-positive quantity: -2");
}

// AC-4: GetQuote with valid items passes validation
#[tokio::test]
async fn test_ac4_get_quote_valid_items() {
    let mut client = get_shipping_client().await;
    
    let request = tonic::Request::new(GetQuoteRequest {
        items: vec![
            OrderItem {
                product_id: "prod1".to_string(),
                quantity: 2,
                price: 10.99,
            },
            OrderItem {
                product_id: "prod2".to_string(),
                quantity: 5,
                price: 29.99,
            }
        ],
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "Anytown".to_string(),
            state: "CA".to_string(),
            zip_code: "12345".to_string(),
            country: "USA".to_string(),
        }),
    });
    
    let response = client.get_quote(request).await;
    assert!(response.is_ok());
}

// AC-5: ShipOrder with empty items list returns InvalidArgument
#[tokio::test]
async fn test_ac5_ship_order_empty_items() {
    let mut client = get_shipping_client().await;
    
    let request = tonic::Request::new(ShipOrderRequest {
        items: vec![],
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "Anytown".to_string(),
            state: "CA".to_string(),
            zip_code: "12345".to_string(),
            country: "USA".to_string(),
        }),
    });
    
    let response = client.ship_order(request).await;
    assert!(response.is_err());
    let status = response.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "items list cannot be empty");
}

// AC-6: ShipOrder with empty address field returns InvalidArgument
#[tokio::test]
async fn test_ac6_ship_order_empty_address_field() {
    let mut client = get_shipping_client().await;
    
    let request = tonic::Request::new(ShipOrderRequest {
        items: vec![
            OrderItem {
                product_id: "prod1".to_string(),
                quantity: 2,
                price: 10.99,
            }
        ],
        address: Some(Address {
            street: "".to_string(), // Empty street
            city: "Anytown".to_string(),
            state: "CA".to_string(),
            zip_code: "12345".to_string(),
            country: "USA".to_string(),
        }),
    });
    
    let response = client.ship_order(request).await;
    assert!(response.is_err());
    let status = response.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "address field street cannot be empty");
}

// AC-7: ShipOrder with invalid zip code returns InvalidArgument
#[tokio::test]
async fn test_ac7_ship_order_invalid_zip_code() {
    let mut client = get_shipping_client().await;
    
    let request = tonic::Request::new(ShipOrderRequest {
        items: vec![
            OrderItem {
                product_id: "prod1".to_string(),
                quantity: 2,
                price: 10.99,
            }
        ],
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "Anytown".to_string(),
            state: "CA".to_string(),
            zip_code: "invalid-zip".to_string(), // Invalid format
            country: "USA".to_string(),
        }),
    });
    
    let response = client.ship_order(request).await;
    assert!(response.is_err());
    let status = response.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "invalid zip_code format: invalid-zip, expected 5-digit or 5-4 digit US ZIP");
}

// AC-8: ShipOrder with valid inputs passes validation
#[tokio::test]
async fn test_ac8_ship_order_valid_inputs() {
    let mut client = get_shipping_client().await;
    
    let request = tonic::Request::new(ShipOrderRequest {
        items: vec![
            OrderItem {
                product_id: "prod1".to_string(),
                quantity: 2,
                price: 10.99,
            }
        ],
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "Anytown".to_string(),
            state: "CA".to_string(),
            zip_code: "90210-1234".to_string(),
            country: "USA".to_string(),
        }),
    });
    
    let response = client.ship_order(request).await;
    assert!(response.is_ok());
    let reply = response.unwrap().into_inner();
    // Verify tracking ID matches expected format
    let re = Regex::new(r"^OTEL-DEMO-SHIP-[A-F0-9]{12}$").unwrap();
    assert!(re.is_match(&reply.tracking_id));
}

// AC-9: GetShipping with empty tracking ID returns InvalidArgument
#[tokio::test]
async fn test_ac9_get_shipping_empty_tracking_id() {
    let mut client = get_shipping_client().await;
    
    let request = tonic::Request::new(GetShippingRequest {
        tracking_id: "".to_string(),
    });
    
    let response = client.get_shipping(request).await;
    assert!(response.is_err());
    let status = response.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "tracking_id cannot be empty");
}

// AC-10: GetShipping with invalid tracking ID format returns InvalidArgument
#[tokio::test]
async fn test_ac10_get_shipping_invalid_tracking_format() {
    let mut client = get_shipping_client().await;
    
    let request = tonic::Request::new(GetShippingRequest {
        tracking_id: "invalid-id-123".to_string(),
    });
    
    let response = client.get_shipping(request).await;
    assert!(response.is_err());
    let status = response.err().unwrap();
    assert_eq!(status.code(), Code::InvalidArgument);
    assert_eq!(status.message(), "invalid tracking_id format: invalid-id-123, expected format OTEL-DEMO-SHIP-<12 hex characters>");
}

// AC-11: GetShipping with valid tracking ID format passes validation
#[tokio::test]
async fn test_ac11_get_shipping_valid_tracking_id() {
    let mut client = get_shipping_client().await;
    
    // First create a valid shipment to get a tracking ID
    let ship_request = tonic::Request::new(ShipOrderRequest {
        items: vec![
            OrderItem {
                product_id: "prod1".to_string(),
                quantity: 2,
                price: 10.99,
            }
        ],
        address: Some(Address {
            street: "123 Main St".to_string(),
            city: "Anytown".to_string(),
            state: "CA".to_string(),
            zip_code: "90210".to_string(),
            country: "USA".to_string(),
        }),
    });
    
    let ship_response = client.ship_order(ship_request).await.unwrap();
    let tracking_id = ship_response.into_inner().tracking_id;
    
    let get_request = tonic::Request::new(GetShippingRequest {
        tracking_id,
    });
    
    let get_response = client.get_shipping(get_request).await;
    assert!(get_response.is_ok());
}
