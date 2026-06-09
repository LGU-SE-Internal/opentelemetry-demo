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

const charge = require('./charge')
const logger = require('./logger')

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

// Luhn algorithm check for credit card number validity
function luhnCheck(cardNumber) {
  let sum = 0;
  let shouldDouble = false;
  for (let i = cardNumber.length - 1; i >= 0; i--) {
    let digit = parseInt(cardNumber[i], 10);
    if (shouldDouble) {
      digit *= 2;
      if (digit > 9) digit -= 9;
    }
    sum += digit;
    shouldDouble = !shouldDouble;
  }
  return sum % 10 === 0;
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

    const { amount, credit_card_number, credit_card_expiration_month, credit_card_expiration_year, credit_card_cvv } = call.request;
    
    // AC-1: Check required fields
    if (!amount) {
      const err = new Error("Missing required field: amount");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (!amount.currency_code) {
      const err = new Error("Missing required field: amount.currency_code");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (!credit_card_number) {
      const err = new Error("Missing required field: credit_card_number");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (credit_card_expiration_month === undefined || credit_card_expiration_month === null) {
      const err = new Error("Missing required field: credit_card_expiration_month");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (credit_card_expiration_year === undefined || credit_card_expiration_year === null) {
      const err = new Error("Missing required field: credit_card_expiration_year");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (!credit_card_cvv) {
      const err = new Error("Missing required field: credit_card_cvv");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    // AC-2: Validate amount units non-negative
    if (amount.units < 0) {
      const err = new Error("Invalid amount.units: must be non-negative integer");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    // AC-3: Validate amount nanos range
    if (amount.nanos < 0 || amount.nanos > 999999999) {
      const err = new Error("Invalid amount.nanos: must be between 0 and 999999999 inclusive");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    // AC-3: Validate currency code format
    const currencyCodeRegex = /^[A-Z]{3}$/;
    if (!currencyCodeRegex.test(amount.currency_code)) {
      const err = new Error("Invalid amount.currency_code: must be 3-letter uppercase ISO 4217 code");
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
    const cardNumberDigits = credit_card_number;
    if (cardNumberDigits.length < 13 || cardNumberDigits.length > 19) {
      const err = new Error("Invalid credit_card_number: must be between 13 and 19 digits long");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (!luhnCheck(cardNumberDigits)) {
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
      const err = new Error("Invalid credit card expiration date: cannot be in the past");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    // AC-7: Validate CVV format
    const cvvNonDigits = credit_card_cvv.replace(/\d/g, '');
    if (cvvNonDigits.length > 0) {
      const err = new Error("Invalid credit_card_cvv: must be numeric string");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }
    if (credit_card_cvv.length < 3 || credit_card_cvv.length > 4) {
      const err = new Error("Invalid credit_card_cvv: must be between 3 and 4 digits long");
      err.code = grpc.status.INVALID_ARGUMENT;
      throw err;
    }

    span?.setAttributes({
      'demo.payment.amount': parseFloat(`${amount.units}.${amount.nanos}`).toFixed(2)
    })
    logger.info("Charge request received.")

    const response = await charge.charge(call.request)
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
    const { amount, credit_card } = call.request;
    logger.info("Refund request received.");
    // Simple refund implementation for demo
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

async function closeGracefully(signal) {
  if (isShuttingDown) return; // Prevent duplicate shutdown calls
  isShuttingDown = true;

  const startTime = Date.now();
  const initialInFlightRequests = inFlightRequests;

  // Emit shutdown start log
  logger.info({
    service: "paymentservice",
    component: "graceful-shutdown",
    event: "shutdown.start",
    signal: signal,
    timestamp: new Date().toISOString()
  });

  // First, stop accepting new requests on gRPC server
  if (server) {
    server.tryShutdown(() => {}); // This stops accepting new connections
  }

  // Wait for in-flight requests to complete or timeout
  let timeoutId;
  const waitForInFlight = new Promise((resolve) => {
    const checkInterval = setInterval(() => {
      if (inFlightRequests === 0) {
        clearInterval(checkInterval);
        resolve({ timedOut: false, completed: true });
      }
    }, 100);
  });

  const timeoutPromise = new Promise((resolve) => {
    timeoutId = setTimeout(() => {
      resolve({ timedOut: true, completed: false });
    }, SHUTDOWN_TIMEOUT_MS);
  });

  const result = await Promise.race([waitForInFlight, timeoutPromise]);
  clearTimeout(timeoutId);

  // Cleanup resources
  if (server) {
    server.forceShutdown();
  }
  if (httpServer) {
    httpServer.close();
  }
  // Close DB client if it exists (note: in current demo there is no actual DB client, so this is a no-op for now)
  if (dbClient && typeof dbClient.end === 'function') {
    await dbClient.end();
  }

  const durationMs = Date.now() - startTime;

  if (result.timedOut) {
    // Emit timeout log
    logger.info({
      service: "paymentservice",
      component: "graceful-shutdown",
      event: "shutdown.timeout",
      incomplete_requests: inFlightRequests,
      duration_ms: durationMs,
      timestamp: new Date().toISOString()
    });
    process.exit(1);
  } else {
    // Emit success log
    logger.info({
      service: "paymentservice",
      component: "graceful-shutdown",
      event: "shutdown.success",
      completed_requests: initialInFlightRequests,
      duration_ms: durationMs,
      timestamp: new Date().toISOString()
    });
    process.exit(0);
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
      res.status(200).json({ status: 'UP' });
      
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
  
  // Non-GET methods for /health return 405 Method Not Allowed
  app.all('/health', (req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.status(405).send();
  });

  // Liveness endpoint - always returns 200 OK with JSON {"status": "ok", "service": "payment", "timestamp": <unix timestamp ms>} when process is running (AC-1)
  app.get('/health/liveness', (req, res) => {
    const start = Date.now();
    const span = opentelemetry.trace.getTracer('paymentservice').startSpan('GET /health/liveness');
    const timestamp = Date.now();
    try {
      res.setHeader('Content-Type', 'application/json');
      res.status(200).json({ 
        status: 'ok',
        service: 'payment',
        timestamp: timestamp
      });
      
      span.setAttributes({
        'http.method': 'GET',
        'http.route': '/health/liveness',
        'http.status_code': 200
      });
      
      logger.info({
        method: 'GET',
        path: '/health/liveness',
        status: 200,
        duration: Date.now() - start,
        timestamp: new Date().toISOString()
      });
    } finally {
      span.end();
    }
  });
  
  // Readiness endpoint - returns 200 OK when fully initialized and able to process payment requests (AC-2, AC-3)
  app.get('/health/readiness', async (req, res) => {
    const start = Date.now();
    const tracer = opentelemetry.trace.getTracer('paymentservice');
    const span = tracer.startSpan('GET /health/readiness');
    const timestamp = Date.now();
    
    const dependencies = {
      config: 'loaded',
      paymentProcessor: 'connected'
    };
    let ready = true;
    let statusCode = 200;
    let status = 'ok';
    
    try {
      // Check if gRPC server is serving (payment processor is ready to handle requests)
      await new Promise((resolve, reject) => {
        healthClient.check({ service: '' }, (err, response) => {
          if (err) return reject(err);
          if (response.status !== health.servingStatus.SERVING) return reject(new Error('gRPC server not serving'));
          resolve();
        });
      });
    } catch (err) {
      ready = false;
      statusCode = 503;
      status = 'unavailable';
      dependencies.paymentProcessor = 'disconnected';
    }
    
    try {
      res.setHeader('Content-Type', 'application/json');
      res.status(statusCode).json({
        status: status,
        service: 'payment',
        ready: ready,
        timestamp: timestamp,
        dependencies: dependencies
      });
      
      span.setAttributes({
        'http.method': 'GET',
        'http.route': '/health/readiness',
        'http.status_code': statusCode,
        'readiness.ready': ready
      });
      
      logger.info({
        method: 'GET',
        path: '/health/readiness',
        status: statusCode,
        duration: Date.now() - start,
        ready: ready
      });
    } finally {
      span.end();
    }
  });
  
  // New required /health/live endpoint per issue #1238
  app.get('/health/live', (req, res) => {
    const start = Date.now();
    const tracer = opentelemetry.trace.getTracer('paymentservice');
    const span = tracer.startSpan('GET /health/live');
    
    try {
      res.status(200).json({ status: 'UP' });
      
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

  // Readiness endpoint /ready per AC2 and AC3 - returns READY when service can process requests
  app.get('/ready', async (req, res) => {
    const start = Date.now();
    const tracer = opentelemetry.trace.getTracer('paymentservice');
    const span = tracer.startSpan('GET /ready');
    
    try {
      // Check if gRPC server is serving (required for processing payment requests)
      await new Promise((resolve, reject) => {
        healthClient.check({ service: '' }, (err, response) => {
          if (err) return reject(err);
          if (response.status !== health.servingStatus.SERVING) return reject(new Error('gRPC server not serving'));
          resolve();
        });
      });
      
      // All checks passed
      res.setHeader('Content-Type', 'application/json');
      res.status(200).json({ status: 'READY' });
      
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
      // Check failed
      res.setHeader('Content-Type', 'application/json');
      res.status(503).json({ status: 'NOT_READY', reason: err.message });
      
      span.setAttributes({
        'http.method': 'GET',
        'http.route': '/ready',
        'http.status_code': 503,
        'error.message': err.message
      });
      
      logger.info({
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
  
  // Non-GET methods for /ready return 405 Method Not Allowed
  app.all('/ready', (req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.status(405).send();
  });

  // Readiness endpoint - returns 200 OK with READY status when service can process requests (AC-2, AC-3)
  app.get('/health/readiness', async (req, res) => {
    const start = Date.now();
    const span = opentelemetry.trace.getTracer('paymentservice').startSpan('GET /health/readiness');
    const errors = [];

    // Check gRPC server health
    try {
      await new Promise((resolve, reject) => {
        healthClient.check({ service: 'opentelemetry.demo.payment.v1.PaymentService' }, (err, response) => {
          if (err) return reject(err);
          if (response.status !== health.servingStatus.SERVING) return reject(new Error('gRPC payment service not serving'));
          resolve();
        });
      });
    } catch (err) {
      errors.push(`gRPC payment service connection failed: ${err.message}`);
    }

    // Check payment processor (charge module health)
    try {
      // Simulate check for payment processor connectivity
      // In a real implementation this would ping the external payment API
      if (!charge.isHealthy()) {
        throw new Error('Payment processor unhealthy');
      }
    } catch (err) {
      errors.push(`payment processor API unreachable: ${err.message}`);
    }

    // Check all required environment variables are present
    const requiredEnvVars = ['PAYMENT_PORT'];
    requiredEnvVars.forEach(varName => {
      if (!process.env[varName]) {
        errors.push(`Missing required environment variable: ${varName}`);
      }
    });

    let statusCode;
    let healthStatus;
    let responseBody;
    res.setHeader('Content-Type', 'application/json');
    
    if (errors.length > 0) {
      statusCode = 503;
      healthStatus = 'FAIL';
      responseBody = { 
        status: 'NOT_READY',
        dependencies: {
          paymentProcessor: charge.isHealthy() ? 'UP' : 'DOWN'
        }
      };
    } else {
      statusCode = 200;
      healthStatus = 'PASS';
      responseBody = { 
        status: 'READY',
        dependencies: {
          paymentProcessor: 'UP'
        }
      };
    }
    
    res.status(statusCode).json(responseBody);
    
    span.setAttributes({
      'http.method': 'GET',
      'http.route': '/health/readiness',
      'http.status_code': statusCode,
      'health.check.type': 'readiness',
      'health.check.status': healthStatus
    });
    
    logger.info({
      method: 'GET',
      path: '/health/readiness',
      status: statusCode,
      duration: Date.now() - start,
      errors: errors.length > 0 ? errors : undefined
    });
    
    span.end();
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
    process.exit(1);
  }
}

module.exports = {
  getServerCredentials,
  app: app,
  rateLimitInterceptor,
  configuredRateLimit,
  rateLimiter
}
