// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use serde::{Deserialize, Serialize};
use std::fmt;
use validator::Validate;

#[derive(Debug, Deserialize, Serialize, Validate)]
pub struct LineItem {
    #[validate(length(min = 1, max = 100, message = "product_id cannot be empty and must be less than 100 characters"))]
    pub product_id: String,
    #[validate(range(min = 1, message = "quantity must be a positive integer"))]
    #[validate(range(max = 1000, message = "quantity cannot exceed 1000"))]
    pub quantity: u32,
    #[validate(range(min = 0.0, message = "price cannot be negative"))]
    pub price: f64,
}

#[derive(Debug, Deserialize, Serialize, Validate)]
pub struct Address {
    #[validate(length(min = 1, message = "street cannot be empty"))]
    #[validate(length(max = 255, message = "street exceeds maximum length of 255 characters"))]
    pub street: String,
    #[validate(length(min = 1, message = "city cannot be empty"))]
    #[validate(length(max = 255, message = "city exceeds maximum length of 255 characters"))]
    pub city: String,
    #[validate(length(min = 1, message = "state cannot be empty"))]
    #[validate(length(max = 255, message = "state exceeds maximum length of 255 characters"))]
    pub state: String,
    #[validate(length(min = 1, message = "zip cannot be empty"))]
    #[validate(length(max = 255, message = "zip exceeds maximum length of 255 characters"))]
    pub zip: String,
    #[validate(length(min = 1, message = "country cannot be empty"))]
    #[validate(length(max = 255, message = "country exceeds maximum length of 255 characters"))]
    pub country: String,
}

#[derive(Debug, Deserialize, Serialize, Validate)]
pub struct GetQuoteRequest {
    #[validate]
    pub address: Address,
    #[validate(length(min = 1, message = "items array cannot be empty"))]
    #[validate(length(max = 100, message = "items array exceeds maximum length of 100 items"))]
    #[validate]
    pub items: Vec<LineItem>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct GetQuoteResponse {
    pub cost_usd: Option<Money>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct Money {
    pub currency_code: String,
    pub units: u64,
    pub nanos: u32,
}

#[derive(Debug, Deserialize, Serialize, Validate)]
pub struct ShipOrderRequest {
    #[validate]
    pub address: Option<Address>,
    #[validate(length(min = 1, max = 100, message = "items array cannot be empty and must have at most 100 items"))]
    #[validate]
    pub items: Vec<LineItem>,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct ShipOrderResponse {
    pub tracking_id: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct Item {
    pub product_id: String,
    pub quantity: u32,
    pub weight: u32,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct QuoteRequest {
    pub items: Vec<Item>,
    pub currency: String,
}

#[derive(Debug, Deserialize, Serialize)]
pub struct QuoteResponse {
    pub price_cents: u64,
    pub currency: String,
}

#[derive(Debug)]
pub enum QuoteError {
    InvalidItem(String),
    CurrencyNotSupported(String),
    ServiceUnavailable,
    InternalError(String),
}

impl fmt::Display for QuoteError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            QuoteError::InvalidItem(msg) => write!(f, "Invalid item: {msg}"),
            QuoteError::CurrencyNotSupported(curr) => write!(f, "Currency not supported: {curr}"),
            QuoteError::ServiceUnavailable => write!(f, "Quote service unavailable"),
            QuoteError::InternalError(msg) => write!(f, "Internal error: {msg}"),
        }
    }
}

impl std::error::Error for QuoteError {}
