use axum::http::StatusCode;
use serde_json::json;
use shipping::app;
use tower::ServiceExt;
use axum::body::Body;
use axum::http::Request;

#[tokio::test]
async fn test_ac1_get_quote_empty_items_array() {
    let app = app().await;

    let payload = json!({
        "address": {
            "street": "123 Main St",
            "city": "Anytown",
            "state": "CA",
            "zip": "12345",
            "country": "USA"
        },
        "items": []
    });

    let response = app
        .oneshot(
            Request::post("/get-quote")
                .header("Content-Type", "application/json")
                .body(Body::from(serde_json::to_vec(&payload).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = hyper::body::to_bytes(response.into_body()).await.unwrap();
    let error: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(error["message"].as_str().unwrap(), "items array cannot be empty");
}

#[tokio::test]
async fn test_ac2_get_quote_item_quantity_zero() {
    let app = app().await;

    let payload = json!({
        "address": {
            "street": "123 Main St",
            "city": "Anytown",
            "state": "CA",
            "zip": "12345",
            "country": "USA"
        },
        "items": [
            {"product_id": "PROD123", "quantity": 0, "price": 10.99}
        ]
    });

    let response = app
        .oneshot(
            Request::post("/get-quote")
                .header("Content-Type", "application/json")
                .body(Body::from(serde_json::to_vec(&payload).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = hyper::body::to_bytes(response.into_body()).await.unwrap();
    let error: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(error["message"].as_str().unwrap(), "quantity must be a positive integer");
}

#[tokio::test]
async fn test_ac3_get_quote_item_quantity_over_1000() {
    let app = app().await;

    let payload = json!({
        "address": {
            "street": "123 Main St",
            "city": "Anytown",
            "state": "CA",
            "zip": "12345",
            "country": "USA"
        },
        "items": [
            {"product_id": "PROD123", "quantity": 1001, "price": 10.99}
        ]
    });

    let response = app
        .oneshot(
            Request::post("/get-quote")
                .header("Content-Type", "application/json")
                .body(Body::from(serde_json::to_vec(&payload).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = hyper::body::to_bytes(response.into_body()).await.unwrap();
    let error: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(error["message"].as_str().unwrap(), "quantity cannot exceed 1000");
}

#[tokio::test]
async fn test_ac4_get_quote_empty_address_street() {
    let app = app().await;

    let payload = json!({
        "address": {
            "street": "",
            "city": "Anytown",
            "state": "CA",
            "zip": "12345",
            "country": "USA"
        },
        "items": [
            {"product_id": "PROD123", "quantity": 5, "price": 10.99}
        ]
    });

    let response = app
        .oneshot(
            Request::post("/get-quote")
                .header("Content-Type", "application/json")
                .body(Body::from(serde_json::to_vec(&payload).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = hyper::body::to_bytes(response.into_body()).await.unwrap();
    let error: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(error["message"].as_str().unwrap(), "street cannot be empty");
}

#[tokio::test]
async fn test_ac5_get_quote_address_street_too_long() {
    let app = app().await;

    let long_street = "a".repeat(256);
    let payload = json!({
        "address": {
            "street": long_street,
            "city": "Anytown",
            "state": "CA",
            "zip": "12345",
            "country": "USA"
        },
        "items": [
            {"product_id": "PROD123", "quantity": 5, "price": 10.99}
        ]
    });

    let response = app
        .oneshot(
            Request::post("/get-quote")
                .header("Content-Type", "application/json")
                .body(Body::from(serde_json::to_vec(&payload).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = hyper::body::to_bytes(response.into_body()).await.unwrap();
    let error: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(error["message"].as_str().unwrap(), "street exceeds maximum length of 255 characters");
}

#[tokio::test]
async fn test_ac6_get_quote_all_valid_fields() {
    let app = app().await;

    let payload = json!({
        "address": {
            "street": "123 Main St",
            "city": "Anytown",
            "state": "CA",
            "zip": "12345",
            "country": "USA"
        },
        "items": [
            {"product_id": "PROD123", "quantity": 5, "price": 10.99},
            {"product_id": "PROD456", "quantity": 2, "price": 29.99}
        ]
    });

    let response = app
        .oneshot(
            Request::post("/get-quote")
                .header("Content-Type", "application/json")
                .body(Body::from(serde_json::to_vec(&payload).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
}

#[tokio::test]
async fn test_ac7_ship_order_empty_items_array() {
    let app = app().await;

    let payload = json!({
        "items": []
    });

    let response = app
        .oneshot(
            Request::post("/ship-order")
                .header("Content-Type", "application/json")
                .body(Body::from(serde_json::to_vec(&payload).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn test_ac8_ship_order_incomplete_address() {
    let app = app().await;

    let payload = json!({
        "address": {
            "street": "123 Main St",
            "city": "Anytown",
            "state": "CA",
            "zip": "12345"
            // missing country field
        },
        "items": [
            {"product_id": "PROD123", "quantity": 5, "price": 10.99}
        ]
    });

    let response = app
        .oneshot(
            Request::post("/ship-order")
                .header("Content-Type", "application/json")
                .body(Body::from(serde_json::to_vec(&payload).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn test_ac9_ship_order_no_address_valid_items() {
    let app = app().await;

    let payload = json!({
        "items": [
            {"product_id": "PROD123", "quantity": 5, "price": 10.99}
        ]
    });

    let response = app
        .oneshot(
            Request::post("/ship-order")
                .header("Content-Type", "application/json")
                .body(Body::from(serde_json::to_vec(&payload).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);
    let body = hyper::body::to_bytes(response.into_body()).await.unwrap();
    let result: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert!(result.get("tracking_id").is_some());
}

#[tokio::test]
async fn test_ac10_ship_order_negative_item_price() {
    let app = app().await;

    let payload = json!({
        "items": [
            {"product_id": "PROD123", "quantity": 5, "price": -10.99}
        ]
    });

    let response = app
        .oneshot(
            Request::post("/ship-order")
                .header("Content-Type", "application/json")
                .body(Body::from(serde_json::to_vec(&payload).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let body = hyper::body::to_bytes(response.into_body()).await.unwrap();
    let error: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(error["message"].as_str().unwrap(), "price cannot be negative");
}
