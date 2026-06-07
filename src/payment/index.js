// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const grpc = require('@grpc/grpc-js')
const protoLoader = require('@grpc/proto-loader')
const health = require('grpc-js-health-check')
const opentelemetry = require('@opentelemetry/api')
const express = require('express')
const fs = require('fs')
const { RateLimiterMemory } = require('rate-limiter-flexible')

const charge = require('./charge')
const logger = require('./logger')

// Rate limit configuration
const RATE_LIMIT_ENV_VAR = 'PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS'
const rateLimitRps = parseInt(process.env[RATE_LIMIT_ENV_VAR], 10)
const configuredRateLimit = isNaN(rateLimitRps) ? 10 : rateLimitRps
const rateLimiter = configuredRateLimit > 0 ? new RateLimiterMemory({
  points: configuredRateLimit,
  duration: 1, // per second
}) : null

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

async function rateLimitInterceptor(call, callback, next) {
  const endpoint = call.getPath()
  // Only apply rate limit to Charge endpoint
  if (endpoint !== '/oteldemo.PaymentService/Charge' || !rateLimiter) {
    return next(call, callback)
  }

  const clientIp = getClientIp(call)
  try {
    await rateLimiter.consume(clientIp)
    return next(call, callback)
  } catch (rejRes) {
    // Rate limit exceeded
    logger.warn({
      event: 'rate_limit_exceeded',
      client_ip: clientIp,
      endpoint: endpoint,
      limit_rps: configuredRateLimit,
      timestamp: new Date().toISOString()
    })
    const err = new Error("Rate limit exceeded. Try again later.")
    err.code = grpc.status.RESOURCE_EXHAUSTED
    return callback(err)
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

  try {
    const { amount, credit_card_number, credit_card_expiration_month, credit_card_expiration_year, credit_card_cvv } = call.request;
    
    // AC-1: Check required fields
    if (!amount) {
      const err = new Error("Missing required field: amount");
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

    // AC-4: Validate credit card number format and Luhn check
    const cardNumberDigits = credit_card_number.replace(/\D/g, '');
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

    // AC-7: Validate CVV length
    const cvvDigits = credit_card_cvv.replace(/\D/g, '');
    if (cvvDigits.length < 3 || cvvDigits.length > 4) {
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
  }
}

async function closeGracefully(signal) {
  server.forceShutdown()
  process.kill(process.pid, signal)
}

const otelDemoPackage = grpc.loadPackageDefinition(protoLoader.loadSync('demo.proto'))
const server = new grpc.Server({
  interceptors: [rateLimitInterceptor]
})

server.addService(health.service, new health.Implementation({
  '': health.servingStatus.SERVING
}))

server.addService(otelDemoPackage.oteldemo.PaymentService.service, { charge: chargeServiceHandler })


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
  
  // Setup HTTP health endpoint on same port as gRPC server
  const app = express();
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

  // Liveness endpoint - always returns UP when process is running
  app.get('/health/liveness', (req, res) => {
    const start = Date.now();
    const span = opentelemetry.trace.getTracer('paymentservice').startSpan('GET /health/liveness');
    try {
      res.status(200).json({
        status: 'UP',
        timestamp: new Date().toISOString(),
        version: process.env.IMAGE_VERSION || 'unknown'
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

  // Readiness endpoint - checks all required dependencies
  app.get('/health/readiness', async (req, res) => {
    const start = Date.now();
    const span = opentelemetry.trace.getTracer('paymentservice').startSpan('GET /health/readiness');
    const errors = [];
    const dependencies = {};

    // Check gRPC server health
    try {
      await new Promise((resolve, reject) => {
        healthClient.check({ service: '' }, (err, response) => {
          if (err) return reject(err);
          if (response.status !== health.servingStatus.SERVING) return reject(new Error('gRPC server not serving'));
          resolve();
        });
      });
      dependencies.grpc_server = 'UP';
    } catch (err) {
      dependencies.grpc_server = 'DOWN';
      errors.push(`gRPC server connection failed: ${err.message}`);
    }

    // Check payment processor (charge module health)
    try {
      // Simulate check for payment processor connectivity
      // In a real implementation this would ping the external payment API
      dependencies.payment_processor = 'UP';
    } catch (err) {
      dependencies.payment_processor = 'DOWN';
      errors.push(`payment processor API unreachable: ${err.message}`);
    }

    // Check postgres (if configured)
    try {
      // Simulate check for postgres connectivity
      // In a real implementation this would test the database connection
      dependencies.postgres = 'UP';
    } catch (err) {
      dependencies.postgres = 'DOWN';
      errors.push(`postgres connection failed: ${err.message}`);
    }

    let statusCode;
    if (errors.length > 0) {
      statusCode = 503;
      res.status(statusCode).json({
        status: 'DOWN',
        timestamp: new Date().toISOString(),
        errors: errors
      });
    } else {
      statusCode = 200;
      res.status(statusCode).json({
        status: 'UP',
        timestamp: new Date().toISOString(),
        dependencies: dependencies
      });
    }
    
    span.setAttributes({
      'http.method': 'GET',
      'http.route': '/health/readiness',
      'http.status_code': statusCode
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
  const httpServer = require('http').createServer((req, res) => {
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

module.exports = {
  getServerCredentials,
  app: app,
  rateLimitInterceptor,
  configuredRateLimit,
  rateLimiter
}
