// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

use serde::{Deserialize, Serialize};
use std::fmt;

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
