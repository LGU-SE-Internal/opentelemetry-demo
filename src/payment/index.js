// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const grpc = require('@grpc/grpc-js')
const protoLoader = require('@grpc/proto-loader')
const health = require('grpc-js-health-check')
const opentelemetry = require('@opentelemetry/api')
const express = require('express')
const fs = require('fs')
const path = require('path')
const { RateLimiterMemory } = require('rate-limiter-flexible')
const luhn = require('luhn')
const iso4217 = require('iso4217')

const charge = require('./charge')
const logger = require('./logger')
const cardValidator = require('simple-card-validator')

// Initialize Express app for health checks
const app = express()
app.use(express.json())

// Supported currencies (ISO 4217 3-letter codes)
const SUPPORTED_CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'CHF', 'CNY', 'SEK', 'NZD']

async function loadSupportedCurrencies() {
  return SUPPORTED_CURRENCIES
}

module.exports.loadSupportedCurrencies = loadSupportedCurrencies

// Graceful shutdown state
let isShuttingDown = false;
let inFlightRequests = 0;
const SHUTDOWN_TIMEOUT_MS = 30000; // 30 seconds as per requirements
let server; // gRPC server reference
let httpServer; // Combined HTTP server reference
let dbClient; // We'll need to check if there's a DB client

// Rate limit configuration
const rateLimiters = new Map();
const DEFAULT_RATE_LIMIT_PER_MINUTE = 100;

// Parse and validate rate limit configuration on startup
function parseRateLimitConfig() {
  // Parse default limit first
  const defaultLimitVar = 'PAYMENT_SERVICE_RATE_LIMIT_DEFAULT';
  const defaultLimitVal = process.env[defaultLimitVar];
  let defaultLimit = DEFAULT_RATE_LIMIT_PER_MINUTE;
  
  if (defaultLimitVal !== undefined) {
    const parsed = parseInt(defaultLimitVal, 10);
    if (isNaN(parsed) || parsed <= 0) {
      logger.fatal(`Invalid limit for ${defaultLimitVar}: ${defaultLimitVal} must be a positive integer (requests per minute)`);
      process.exit(1);
    }
    defaultLimit = parsed;
  }

  // Parse per-endpoint overrides from JSON
  const overridesVar = 'PAYMENT_SERVICE_RATE_LIMIT_OVERRIDES';
  const overridesVal = process.env[overridesVar];
  let overrides = {};
  if (overridesVal) {
    try {
      overrides = JSON.parse(overridesVal);
    } catch (err) {
      logger.fatal(`Invalid JSON for ${overridesVar}: ${err.message}`);
      process.exit(1);
    }
    for (const [endpointName, limit] of Object.entries(overrides)) {
      const parsedLimit = parseInt(limit, 10);
      if (isNaN(parsedLimit) || parsedLimit <= 0) {
        logger.fatal(`Invalid limit for endpoint ${endpointName} in ${overridesVar}: ${limit} must be a positive integer`);
        process.exit(1);
      }
      // Create rate limiter for this endpoint (per minute)
      rateLimiters.set(endpointName.toLowerCase(), new RateLimiterMemory({
        points: parsedLimit,
        duration: 60, // 1 minute window
      }));
    }
  }

  // Create default rate limiter if no custom default set
  if (!rateLimiters.has('default')) {
    rateLimiters.set('default', new RateLimiterMemory({
      points: defaultLimit,
      duration: 60, // 1 minute window
    }));
  }
}

// Initialize rate limit config
parseRateLimitConfig();

function getClientIp(call) {
  const peer = call.getPeer()
  // Peer format is typically ipv4:address:port or ipv6:[address]:port
  if (peer.startsWith('ipv4:')) {
    return peer.split(':')[1]
  } else if (peer.startsWith('ipv6:')) {
    return peer.split(']:')[0].substring(5)
  }
  return peer
}

function getRateLimiterForEndpoint(endpointPath) {
  // Extract endpoint name from path (e.g., /oteldemo.PaymentService/Charge -> charge)
  const endpointName = endpointPath.split('/').pop().toLowerCase();
  return rateLimiters.get(endpointName) || rateLimiters.get('default');
}

function getLimitRpsForEndpoint(endpointPath) {
  const limiter = getRateLimiterForEndpoint(endpointPath);
  return limiter.points;
}

async function rateLimitInterceptor(call, callback, next) {
  const endpoint = call.getPath()
  const endpointName = endpoint.split('/').pop().toLowerCase();
  const rateLimiter = getRateLimiterForEndpoint(endpoint);
  
  // Get client identifier: X-Client-Id first, fallback to IP
  let clientId = call.metadata.get('x-client-id')[0];
  if (!clientId) {
    clientId = getClientIp(call);
  }

  // Get active trace context for logging
  const activeSpan = opentelemetry.trace.getActiveSpan();
  const traceId = activeSpan?.spanContext().traceId || '';
  const spanId = activeSpan?.spanContext().spanId || '';

  try {
    const res = await rateLimiter.consume(clientId);
    // Add rate limit headers to successful response
    call.on('send_metadata', (metadata) => {
      metadata.set('x-ratelimit-limit', rateLimiter.points.toString());
      metadata.set('x-ratelimit-remaining', Math.floor(res.remainingPoints).toString());
      metadata.set('x-ratelimit-reset', Math.floor(Date.now() / 1000 + res.msBeforeNext / 1000).toString());
    });
    return next(call, callback);
  } catch (rejRes) {
    // Rate limit exceeded
    const limit = rateLimiter.points;
    const remaining = Math.floor(rejRes.remainingPoints);
    const reset = Math.floor(Date.now() / 1000 + rejRes.msBeforeNext / 1000);
    
    logger.warn({
      message: 'Rate limit exceeded, try again later',
      client_id: clientId,
      endpoint: endpoint,
      trace_id: traceId,
      span_id: spanId,
      limit: limit,
      remaining: remaining,
      reset_timestamp: reset
    });
    
    const err = new Error('Rate limit exceeded, try again later');
    err.code = grpc.status.RESOURCE_EXHAUSTED;
    
    // Add rate limit headers to error response
    const metadata = new grpc.Metadata();
    metadata.set('x-ratelimit-limit', limit.toString());
    metadata.set('x-ratelimit-remaining', remaining.toString());
    metadata.set('x-ratelimit-reset', reset.toString());
    
    return callback(err, null, metadata);
  }
}

function getServerCredentials() {
  const tlsCertPath = process.env.PAYMENT_SERVICE_TLS_CERT_PATH
  const tlsKeyPath = process.env.PAYMENT_SERVICE_TLS_KEY_PATH
  const clientCaPath = process.env.PAYMENT_SERVICE_TLS_CLIENT_CA_PATH

  // No TLS variables set: return insecure credentials
  if (!tlsCertPath && !tlsKeyPath && !clientCaPath) {
    return grpc.ServerCredentials.createInsecure()
  }

  // Check if both cert and key paths are set if any TLS config exists
  if (!tlsCertPath || !tlsKeyPath) {
    throw new Error("Both PAYMENT_SERVICE_TLS_CERT_PATH and PAYMENT_SERVICE_TLS_KEY_PATH must be set when configuring TLS")
  }

  // Read certificate and key files
  let cert, key
  try {
    cert = fs.readFileSync(tlsCertPath)
    key = fs.readFileSync(tlsKeyPath)
  } catch (err) {
    throw new Error(`Failed to read TLS certificate/key file: ${err.message}`)
  }

  // If client CA is set, enable mTLS
  if (clientCaPath) {
    let clientCa
    try {
      clientCa = fs.readFileSync(clientCaPath)
    } catch (err) {
      throw new Error(`Failed to read client CA certificate file: ${err.message}`)
    }
    return grpc.ServerCredentials.createSsl(
      clientCa,
      [{ cert_chain: cert, private_key: key }],
      true // require client certificate
    )
  }

  // Regular TLS without client auth
  return grpc.ServerCredentials.createSsl(
    null,
    [{ cert_chain: cert, private_key: key }],
    false // no client cert required
  )
}

async function chargeServiceHandler(call, callback) {
  const span = opentelemetry.trace.getActiveSpan();

  // Reject new requests immediately if shutting down
  if (isShuttingDown) {
    const err = new Error("Service is shutting down");
    err.code = grpc.status.UNAVAILABLE;
    return callback(err);
  }

  // Increment in-flight counter
  inFlightRequests++;

  try {
    // Support test delay for graceful shutdown tests
    if (call.request.__test_delay_ms && typeof call.request.__test_delay_ms === 'number') {
      await new Promise(resolve => setTimeout(resolve, call.request.__test_delay_ms));
    }

    const { amount, credit_card, currency_code } = call.request;
    
    // AC-1: Check required fields
    if (!amount) {
      const err = new Error("Missing required field: amount");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (!credit_card) {
      const err = new Error("Missing required field: credit_card");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (!currency_code) {
      const err = new Error("Missing required field: currency_code");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    const { 
      credit_card_number, 
      credit_card_expiration_month, 
      credit_card_expiration_year, 
      credit_card_cvv 
    } = credit_card;
    
    if (!credit_card_number) {
      const err = new Error("Missing required field: credit_card.credit_card_number");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (credit_card_expiration_month === undefined || credit_card_expiration_month === null) {
      const err = new Error("Missing required field: credit_card.credit_card_expiration_month");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (credit_card_expiration_year === undefined || credit_card_expiration_year === null) {
      const err = new Error("Missing required field: credit_card.credit_card_expiration_year");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (!credit_card_cvv) {
      const err = new Error("Missing required field: credit_card.credit_card_cvv");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    // AC-2: Validate amount is positive (greater than 0)
    const totalAmount = parseFloat(amount.units) + parseFloat(amount.nanos) / 1e9;
    if (totalAmount <= 0) {
      const err = new Error("Amount must be greater than 0");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    // Validate amount nanos range
    if (amount.nanos < 0 || amount.nanos > 999999999) {
      const err = new Error("Amount nanos must be between 0 and 999999999");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    // AC-3: Validate currency code format and valid ISO 4217 code
    const currencyCodeRegex = /^[A-Z]{3}$/;
    if (!currencyCodeRegex.test(currency_code)) {
      const err = new Error(`Currency ${currency_code} is invalid: must be 3-letter uppercase ISO 4217 code`);
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (!iso4217[currency_code]) {
      const err = new Error(`Currency ${currency_code} is not a valid ISO 4217 currency code`);
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    // AC-4: Validate currency is supported
    if (!SUPPORTED_CURRENCIES.includes(currency_code)) {
      const err = new Error(`Currency ${currency_code} is not supported`);
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    // AC-4: Validate credit card number format and Luhn check
    const nonDigitChars = credit_card_number.replace(/\d/g, '');
    if (nonDigitChars.length > 0) {
      const err = new Error("Invalid credit_card_number: must be numeric string");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    // AC-5: Validate credit card number length (13-19 digits for supported networks)
    const cardNumberDigits = credit_card_number;
    if (cardNumberDigits.length < 13 || cardNumberDigits.length > 19) {
      const err = new Error("Invalid credit card number length: must be 13-19 digits for supported card networks");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (!luhn.validate(cardNumberDigits)) {
      const err = new Error("Invalid credit_card_number: fails Luhn check");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    // AC-5: Validate expiration month range
    if (credit_card_expiration_month < 1 || credit_card_expiration_month > 12) {
      const err = new Error("Invalid credit_card_expiration_month: must be between 1 and 12 inclusive");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    // AC-6: Validate expiration date not in past
    const now = new Date();
    const currentYear = now.getFullYear();
    const currentMonth = now.getMonth() + 1; // Months are 0-based in JS
    if (credit_card_expiration_year < currentYear || 
        (credit_card_expiration_year === currentYear && credit_card_expiration_month < currentMonth)) {
      const err = new Error("Credit card is expired");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    // Validate CVV format and length based on card network
    const cvvNonDigits = credit_card_cvv.replace(/\d/g, '');
    if (cvvNonDigits.length > 0) {
      const err = new Error("Invalid credit_card_cvv: must be numeric string");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    // Check if card is Amex (starts with 34 or 37)
    const isAmex = /^3[47]/.test(cardNumberDigits);
    if (isAmex) {
      if (credit_card_cvv.length !== 4) {
        const err = new Error("Invalid CVV length: American Express cards require 4-digit CVV");
        err.code = grpc.status.INVALID_ARGUMENT;
        throw err;
      }
    } else {
      if (credit_card_cvv.length !== 3) {
        const err = new Error("Invalid CVV length: Visa, Mastercard, Discover cards require 3-digit CVV");
        err.code = grpc.status.INVALID_ARGUMENT;
        throw err;
      }
    }

    span?.setAttributes({
      'demo.payment.amount': parseFloat(`${amount.units}.${amount.nanos}`).toFixed(2)
    })
    logger.info("Charge request received.")

    // Map request fields to the format expected by charge function
    const chargeRequest = {
      amount: {
        units: amount.units,
        nanos: amount.nanos,
        currencyCode: currency_code
      },
      creditCard: {
        creditCardNumber: credit_card_number,
        creditCardExpirationYear: credit_card_expiration_year,
        creditCardExpirationMonth: credit_card_expiration_month,
        creditCardCvv: credit_card_cvv
      }
    }

    const response = await charge.charge(chargeRequest)
    callback(null, response)

  } catch (err) {
    logger.warn({ err })

    span?.setStatus({ code: opentelemetry.SpanStatusCode.ERROR, message: err.message })
    callback(err)
  } finally {
    // Decrement in-flight counter when request completes
    inFlightRequests--;
  }
}

async function getPaymentMethodsServiceHandler(call, callback) {
  const span = opentelemetry.trace.getActiveSpan();
  try {
    logger.info("GetPaymentMethods request received.");
    callback(null, {
      payment_methods: [
        { id: 'credit_card', name: 'Credit Card', supported_currencies: ['USD', 'EUR', 'GBP'] },
        { id: 'debit_card', name: 'Debit Card', supported_currencies: ['USD', 'EUR', 'GBP'] },
        { id: 'paypal', name: 'PayPal', supported_currencies: ['USD', 'EUR', 'GBP', 'JPY'] }
      ]
    });
  } catch (err) {
    logger.warn({ err });
    span?.setStatus({ code: opentelemetry.SpanStatusCode.ERROR, message: err.message });
    callback(err);
  }
}

async function refundServiceHandler(call, callback) {
  const span = opentelemetry.trace.getActiveSpan();
  try {
    const { amount, credit_card, currency_code } = call.request;
    logger.info("Refund request received.");
    
    // AC-1: Validate required fields
    if (!amount) {
      const err = new Error('Missing required field: amount');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (!credit_card) {
      const err = new Error('Missing required field: credit_card');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (!currency_code) {
      const err = new Error('Missing required field: currency_code');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    
    // Validate nested required fields for amount
    if (amount.units === undefined || amount.nanos === undefined) {
      const err = new Error('Missing required field in amount: units and nanos are required');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    
    // Validate nested required fields for credit card
    if (!credit_card.number || credit_card.expiry_month === undefined || credit_card.expiry_year === undefined || !credit_card.cvv) {
      const err = new Error('Missing required field in credit_card: number, expiry_month, expiry_year, and cvv are required');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    
    // AC-2: Validate amount values
    if (amount.units <= 0) {
      const err = new Error('Invalid amount: units must be positive');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (amount.units > 10000000) {
      const err = new Error('Invalid amount: units must be less than 10,000,000');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (amount.nanos < 0 || amount.nanos >= 1000000000) {
      const err = new Error('Invalid amount: nanos must be between 0 and 999,999,999');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    
    // AC-3: Validate credit card number
    if (!/^\d{13,19}$/.test(credit_card.number)) {
      const err = new Error('Invalid credit card number: must be 13-19 numeric digits');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    
    const card = cardValidator(credit_card.number);
    const { valid } = card.getCardDetails();
    if (!valid) {
      const err = new Error('Invalid credit card number: failed Luhn check');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    
    // AC-4: Validate expiry date
    if (credit_card.expiry_month < 1 || credit_card.expiry_month > 12) {
      const err = new Error('Invalid expiry month: must be between 1 and 12');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    
    const currentYear = new Date().getFullYear();
    const currentMonth = new Date().getMonth() + 1; // JS months are 0-based
    if (credit_card.expiry_year < currentYear || 
        (credit_card.expiry_year === currentYear && credit_card.expiry_month < currentMonth)) {
      const err = new Error('Invalid expiry date: credit card is expired');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    
    // AC-5: Validate CVV
    if (!/^\d{3,4}$/.test(credit_card.cvv)) {
      const err = new Error('Invalid CVV: must be 3 or 4 numeric digits');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    
    // AC-6: Validate currency code
    if (!/^[A-Z]{3}$/.test(currency_code) || !SUPPORTED_CURRENCIES.includes(currency_code)) {
      const err = new Error('Invalid currency code: must be 3-letter ISO 4217 code of a supported currency');
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    
    // AC-7: Process valid request normally
    callback(null, {
      refund_id: `refund_${Date.now()}`,
      success: true,
      amount: amount
    });
  } catch (err) {
    logger.warn({ err });
    span?.setStatus({ code: opentelemetry.SpanStatusCode.ERROR, message: err.message });
    callback(err);
  }
}


const otelDemoPackage = grpc.loadPackageDefinition(protoLoader.loadSync(path.join(__dirname, '../../pb/demo.proto')))
server = new grpc.Server({
  interceptors: [rateLimitInterceptor]
})

// Health status management for gRPC health checks
const healthStatuses = {
  '': health.servingStatus.SERVING,
  'opentelemetry.demo.payment.v1.PaymentService': health.servingStatus.NOT_SERVING
};

const healthImplementation = new health.Implementation(healthStatuses);

// Add interceptor to gRPC health service to add OTel attributes
const healthServiceInterceptor = (methodDescriptor) => {
  const originalMethod = methodDescriptor.func;
  methodDescriptor.func = (call, callback) => {
    const serviceName = call.request.service || '';
    const checkType = serviceName === '' ? 'liveness' : 'readiness';
    
    const span = opentelemetry.trace.getActiveSpan();
    
    originalMethod(call, (err, response) => {
      if (span) {
        span.setAttributes({
          'rpc.service': 'grpc.health.v1.Health',
          'health.check.type': checkType,
          'health.check.status': response?.status === health.servingStatus.SERVING ? 'PASS' : 'FAIL'
        });
      }
      callback(err, response);
    });
  };
  return methodDescriptor;
};

// Apply interceptor to all health service methods
const interceptedHealthService = Object.fromEntries(
  Object.entries(health.service).map(([methodName, methodDescriptor]) => [
    methodName,
    healthServiceInterceptor({ ...methodDescriptor })
  ])
);

server.addService(interceptedHealthService, healthImplementation);

server.addService(otelDemoPackage.oteldemo.PaymentService.service, { 
  charge: chargeServiceHandler,
  refund: refundServiceHandler,
  getPaymentMethods: getPaymentMethodsServiceHandler
});

// Set payment service status to SERVING once server is bound
setTimeout(() => {
  healthImplementation.setStatus('opentelemetry.demo.payment.v1.PaymentService', health.servingStatus.SERVING);
}, 1000);


let ip = "0.0.0.0";

const ipv6_enabled = process.env.IPV6_ENABLED;

if (ipv6_enabled == "true") {
  ip = "[::]";
  logger.info(`Overwriting Localhost IP: ${ip}`)
}

const address = ip + `:${process.env['PAYMENT_PORT']}`;

let serverCredentials
try {
  serverCredentials = getServerCredentials()
} catch (err) {
  logger.error({ err }, "Failed to initialize server credentials")
  process.exit(1)
}

server.bindAsync(address, serverCredentials, (err, port) => {
  if (err) {
    return logger.error({ err })
  }

  logger.info(`payment gRPC server started on ${address}`)
  
let app;
module.exports.app = app;

// Setup HTTP health endpoint on same port as gRPC server
app = express();
  module.exports.app = app;

  // Create gRPC health client to check local server
  const healthClientCreds = serverCredentials._isSecure ? grpc.credentials.createSsl() : grpc.credentials.createInsecure();
  const healthClient = new health.HealthClient(`localhost:${process.env['PAYMENT_PORT']}`, healthClientCreds);

  // Liveness endpoint /health per AC1 - returns UP immediately when service is running, no dependency checks
  app.get('/health', (req, res) => {
    const start = Date.now();
    const tracer = opentelemetry.trace.getTracer('paymentservice');
    const span = tracer.startSpan('GET /health');
    
    try {
      res.setHeader('Content-Type', 'application/json');
      res.status(200).json({ status: 'healthy', check: 'liveness' });
      
      span.setAttributes({
        'http.method': 'GET',
        'http.route': '/health',
        'http.status_code': 200
      });
      
      logger.info({
        method: 'GET',
        path: '/health',
        status: 200,
        duration: Date.now() - start
      });
    } finally {
      span.end();
    }
  });
  
  // Readiness endpoint /ready per AC3 - returns 200 when all dependencies are reachable
  app.get('/ready', async (req, res) => {
    const start = Date.now();
    const tracer = opentelemetry.trace.getTracer('paymentservice');
    const span = tracer.startSpan('GET /ready');
    
    try {
      // Check if gRPC server is serving (payment processor is ready to handle requests)
      await new Promise((resolve, reject) => {
        healthClient.check({ service: '' }, (err, response) => {
          if (err) return reject(err);
          if (response.status !== health.servingStatus.SERVING) return reject(new Error('gRPC server not serving'));
          resolve();
        });
      });

      res.setHeader('Content-Type', 'application/json');
      res.status(200).json({ status: 'ready', check: 'readiness' });
      
      span.setAttributes({
        'http.method': 'GET',
        'http.route': '/ready',
        'http.status_code': 200
      });
      
      logger.info({
        method: 'GET',
        path: '/ready',
        status: 200,
        duration: Date.now() - start
      });
    } catch (err) {
      res.setHeader('Content-Type', 'application/json');
      res.status(503).json({ status: 'not ready', check: 'readiness', error: err.message });
      
      span.setAttributes({
        'http.method': 'GET',
        'http.route': '/ready',
        'http.status_code': 503,
        'error.message': err.message
      });
      
      logger.error({
        method: 'GET',
        path: '/ready',
        status: 503,
        duration: Date.now() - start,
        error: err.message
      });
    } finally {
      span.end();
    }
  });
  // Health/live endpoint for backwards compatibility
  app.get('/health/live', (req, res) => {
    const start = Date.now();
    const tracer = opentelemetry.trace.getTracer('paymentservice');
    const span = tracer.startSpan('GET /health/live');
    
    try {
      res.setHeader('Content-Type', 'application/json');
      res.status(200).json({ status: 'healthy', check: 'liveness' });
      
      span.setAttributes({
        'http.method': 'GET',
        'http.route': '/health/live',
        'http.status_code': 200
      });
      
      logger.info({
        method: 'GET',
        path: '/health/live',
        status: 200,
        duration: Date.now() - start
      });
    } finally {
      span.end();
    }
  });

  // New standard liveness endpoint per #1866
  app.get('/health/liveness', (req, res) => {
    const start = Date.now();
    const tracer = opentelemetry.trace.getTracer('paymentservice');
    const span = tracer.startSpan('GET /health/liveness');
    const timestamp = new Date().toISOString();

    try {
      res.setHeader('Content-Type', 'application/json');
      res.status(200).json({
        status: "healthy",
        service: "payment-service",
        timestamp: timestamp,
        checkType: "liveness"
      });

      span.setAttributes({
        'http.method': 'GET',
        'http.route': '/health/liveness',
        'health.check_type': 'liveness',
        'http.status_code': 200
      });
      
      logger.info({
        method: 'GET',
        path: '/health/liveness',
        status: 200,
        duration: Date.now() - start
      });
    } finally {
      span.end();
    }
  });

  // Health/readiness endpoint updated per #1866
  app.get('/health/readiness', async (req, res) => {
    const start = Date.now();
    const tracer = opentelemetry.trace.getTracer('paymentservice');
    const span = tracer.startSpan('GET /health/readiness');
    try {
      const timestamp = new Date().toISOString();
      let paymentProcessorStatus = 'reachable';
      let paymentProcessorError = null;
      const traceId = span.spanContext().traceId;

      // Check payment processor (charge module health)
      try {
        // Test with a minimal valid charge request to ensure processing works
        await charge.charge({
          creditCard: {
            creditCardNumber: '4111-1111-1111-1111',
            creditCardExpirationMonth: 12,
            creditCardExpirationYear: new Date().getFullYear() + 1,
            creditCardCvv: '123'
          },
          amount: {
            currencyCode: 'USD',
            units: 0,
            nanos: 0
          }
        });
      } catch (err) {
        paymentProcessorStatus = 'unreachable';
        paymentProcessorError = err.message;
        
        // Log error for AC-5
        logger.error(`Readiness probe failed: Payment gateway unreachable. Error: ${paymentProcessorError}, Trace ID: ${traceId}`);
      }

      res.setHeader('Content-Type', 'application/json');

      if (paymentProcessorStatus === 'reachable') {
        res.status(200).json({
          status: "ready",
          service: "payment-service",
          timestamp: timestamp,
          checkType: "readiness",
          dependencies: [
            {
              name: "payment-gateway",
              status: "reachable"
            }
          ]
        });

        span.setAttributes({
          'http.method': 'GET',
          'http.route': '/health/readiness',
          'health.check_type': 'readiness',
          'http.status_code': 200
        });

        logger.info({
          method: 'GET',
          path: '/health/readiness',
          status: 200,
          duration: Date.now() - start
        });
      } else {
        res.status(503).json({
          status: "not_ready",
          service: "payment-service",
          timestamp: timestamp,
          checkType: "readiness",
          dependencies: [
            {
              name: "payment-gateway",
              status: "unreachable",
              error: paymentProcessorError
            }
          ]
        });

        span.setAttributes({
          'http.method': 'GET',
          'http.route': '/health/readiness',
          'health.check_type': 'readiness',
          'http.status_code': 503
        });
      }
    } finally {
      span.end();
    }
  });
  
  // New required /health/ready endpoint per issue #1238
  app.get('/health/ready', async (req, res) => {
    const start = Date.now();
    const tracer = opentelemetry.trace.getTracer('paymentservice');
    const span = tracer.startSpan('GET /health/ready');
    
    try {
      // Check if all dependencies are initialized
      await new Promise((resolve, reject) => {
        healthClient.check({ service: '' }, (err, response) => {
          if (err) return reject(err);
          if (response.status !== health.servingStatus.SERVING) return reject(new Error('gRPC server not serving'));
          resolve();
        });
      });
      
      // If all checks pass
      res.status(200).json({ status: 'READY' });
      
      span.setAttributes({
        'http.method': 'GET',
        'http.route': '/health/ready',
        'http.status_code': 200
      });
      
      logger.info({
        method: 'GET',
        path: '/health/ready',
        status: 200,
        duration: Date.now() - start
      });
    } catch (err) {
      // If any check fails
      res.status(503).json({ status: 'NOT_READY' });
      
      span.setAttributes({
        'http.method': 'GET',
        'http.route': '/health/ready',
        'http.status_code': 503,
        'error.message': err.message
      });
      
      logger.info({
        method: 'GET',
        path: '/health/ready',
        status: 503,
        duration: Date.now() - start,
        error: err.message
      });
    } finally {
      span.end();
    }
  });

  // Catch all other routes return 404
  app.all('*', (req, res) => {
    res.status(404).send();
  });

  // Create combined HTTP server that handles both gRPC and HTTP requests
  httpServer = require('http').createServer((req, res) => {
    if (req.headers['content-type']?.startsWith('application/grpc')) {
      server.emit('request', req, res);
    } else {
      app(req, res);
    }
  });

  // Start combined server on payment port
  httpServer.listen(port, ip, () => {
    logger.info(`Payment service combined gRPC + HTTP health endpoint listening on port ${port}`);
  });
})

process.once('SIGINT', closeGracefully)
process.once('SIGTERM', closeGracefully)

async function closeGracefully(signal) {
  if (isShuttingDown) return; // Prevent duplicate shutdown triggers
  isShuttingDown = true;
  const startTime = Date.now();

  // Emit shutdown start log
  logger.info({
    service: "paymentservice",
    component: "graceful-shutdown",
    event: "shutdown.start",
    signal: signal,
    timestamp: new Date().toISOString()
  });

  let shutdownTimeout;
  let timedOut = false;
  const completedRequestsAtSignal = inFlightRequests;

  // Wait for in-flight requests to complete or timeout
  const waitForRequests = async () => {
    while (inFlightRequests > 0 && !timedOut) {
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  };

  // Set up 30s timeout
  const timeoutPromise = new Promise(resolve => {
    shutdownTimeout = setTimeout(() => {
      timedOut = true;
      resolve();
    }, SHUTDOWN_TIMEOUT_MS);
  });

  // Wait for either all requests to complete or timeout
  await Promise.race([waitForRequests(), timeoutPromise]);
  clearTimeout(shutdownTimeout);

  const durationMs = Date.now() - startTime;

  // Clean up resources
  if (server) {
    try {
      await new Promise((resolve, reject) => {
        server.tryShutdown(err => {
          if (err) reject(err);
          else resolve();
        });
      });
    } catch (err) {
      logger.error({ message: "Error shutting down gRPC server", error: err.message });
    }
  }

  if (httpServer) {
    try {
      await new Promise(resolve => httpServer.close(resolve));
    } catch (err) {
      logger.error({ message: "Error shutting down HTTP server", error: err.message });
    }
  }

  // Emit appropriate log event
  if (!timedOut) {
    logger.info({
      service: "paymentservice",
      component: "graceful-shutdown",
      event: "shutdown.success",
      completed_requests: completedRequestsAtSignal,
      duration_ms: durationMs,
      timestamp: new Date().toISOString()
    });
    process.exit(0);
  } else {
    logger.info({
      service: "paymentservice",
      component: "graceful-shutdown",
      event: "shutdown.timeout",
      incomplete_requests: inFlightRequests,
      duration_ms: durationMs,
      timestamp: new Date().toISOString()
    });
  }
}

function getServer() {
  return server;
}

module.exports = {
  getServerCredentials,
  getServer,
  app: app,
  rateLimitInterceptor,
  getLimitRpsForEndpoint
}
