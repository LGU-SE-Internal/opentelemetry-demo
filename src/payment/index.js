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

function registerShutdownHandlers(
  server,
  cleanupHooks,
  gracePeriodMs = 30000,
  logger
) {
  let isShuttingDown = false;
  let inFlightRequests = 0;
  let shutdownTimeout;
  const startTime = Date.now();

  // Track in-flight requests
  server.on('request', (req, res) => {
    if (isShuttingDown) {
      res.statusCode = 503;
      res.end('Service Unavailable');
      return;
    }

    inFlightRequests++;
    res.on('finish', () => {
      inFlightRequests--;
      if (isShuttingDown && inFlightRequests === 0) {
        runCleanup();
      }
    });
  });

  async function runCleanup() {
    if (shutdownTimeout) {
      clearTimeout(shutdownTimeout);
    }

    let exitCode = 0;
    for (let i = 0; i < cleanupHooks.length; i++) {
      try {
        await cleanupHooks[i]();
      } catch (err) {
        exitCode = 1;
        logger.error({
          event: 'service.shutdown.cleanup_failed',
          error: err.message,
          resourceType: i === 0 ? 'database' : i === 1 ? 'stripe' : 'unknown'
        });
      }
    }

    const durationMs = Date.now() - startTime;
    logger.info({
      event: 'service.shutdown.completed',
      durationMs
    });

    process.exit(exitCode);
  }

  function handleSignal(signal) {
    if (isShuttingDown) return;
    isShuttingDown = true;

    logger.info({
      event: 'service.shutdown.started',
      signal,
      gracePeriodMs
    });

    // Stop accepting new connections
    server.close((err) => {
      if (err) {
        logger.error({
          event: 'service.shutdown.server_close_error',
          error: err.message
        });
      }
    });

    // Set force shutdown timeout
    shutdownTimeout = setTimeout(() => {
      logger.info({
        event: 'service.shutdown.forced',
        reason: 'grace_period_exceeded',
        inFlightRequestsCount: inFlightRequests
      });
      runCleanup();
    }, gracePeriodMs);

    // If no in-flight requests, run cleanup immediately
    if (inFlightRequests === 0) {
      runCleanup();
    }
  }

  process.on('SIGINT', () => handleSignal('SIGINT'));
  process.on('SIGTERM', () => handleSignal('SIGTERM'));
}

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

server.addService(otelDemoPackage.oteldemo.PaymentService.service, { charge: chargeServiceHandler });

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

  // Liveness endpoint - always returns 200 OK empty text when process is running (AC-1)
  app.get('/health/liveness', (req, res) => {
    const start = Date.now();
    const span = opentelemetry.trace.getTracer('paymentservice').startSpan('GET /health/liveness');
    try {
      res.setHeader('Content-Type', 'text/plain');
      res.status(200).send('');
      
      span.setAttributes({
        'http.method': 'GET',
        'http.route': '/health/liveness',
        'http.status_code': 200,
        'health.check.type': 'liveness',
        'health.check.status': 'PASS'
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
      responseBody = { status: 'NOT_READY' };
    } else {
      statusCode = 200;
      healthStatus = 'PASS';
      responseBody = { status: 'READY' };
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

/**
 * Registers shutdown handlers for SIGINT/SIGTERM signals
 * @param server - Node.js HTTP server instance handling payment requests
 * @param cleanupHooks - Ordered array of async functions that perform resource cleanup
 * @param gracePeriodMs - Maximum time to wait for in-flight requests (default 30000 ms)
 * @param logger - Structured logger instance for observability events
 */
function registerShutdownHandlers(
  server,
  cleanupHooks,
  gracePeriodMs = 30000,
  logger
) {
  let isShuttingDown = false;
  let inFlightRequests = 0;
  let shutdownStartTime;
  let graceTimeout;

  // Track in-flight requests
  server.on('request', (req, res) => {
    if (isShuttingDown) {
      res.statusCode = 503;
      res.end('Service Unavailable');
      return;
    }

    inFlightRequests++;
    res.on('finish', () => {
      inFlightRequests--;
      if (isShuttingDown && inFlightRequests === 0) {
        if (graceTimeout) clearTimeout(graceTimeout);
        runCleanup();
      }
    });
  });

  async function runCleanup() {
    let exitCode = 0;
    try {
      for (const hook of cleanupHooks) {
        try {
          await hook();
        } catch (err) {
          exitCode = 1;
          logger.error({
            event: 'service.shutdown.cleanup_failed',
            error: err.message,
            resourceType: err.resourceType || 'unknown'
          });
        }
      }

      const durationMs = Date.now() - shutdownStartTime;
      logger.info({
        event: 'service.shutdown.completed',
        durationMs
      });
    } catch (err) {
      exitCode = 1;
      logger.error({
        event: 'shutdown.unexpected_error',
        error: err.message
      });
    } finally {
      process.exit(exitCode);
    }
  }

  function handleSignal(signal) {
    if (isShuttingDown) return;
    isShuttingDown = true;
    shutdownStartTime = Date.now();

    logger.info({
      event: 'service.shutdown.started',
      signal,
      gracePeriodMs
    });

    // Stop accepting new connections
    server.close((err) => {
      if (err) {
        logger.error({
          event: 'server.close.error',
          error: err.message
        });
      }
    });

    // Set up grace period timeout
    graceTimeout = setTimeout(() => {
      logger.info({
        event: 'service.shutdown.forced',
        reason: 'grace_period_exceeded',
        inFlightRequestsCount: inFlightRequests
      });
      runCleanup();
    }, gracePeriodMs);

    // If no in-flight requests, run cleanup immediately
    if (inFlightRequests === 0) {
      clearTimeout(graceTimeout);
      runCleanup();
    }
  }

  process.on('SIGINT', () => handleSignal('SIGINT'));
  process.on('SIGTERM', () => handleSignal('SIGTERM'));
}

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

    // Register shutdown handlers for the combined HTTP server
    const cleanupHooks = [
      // Database cleanup first (placeholder for actual DB connection close)
      async () => {
        logger.info('Closing database connections...');
        // Add actual DB close logic here when implemented
      },
      // Stripe/Payment processor cleanup next
      async () => {
        logger.info('Cleaning up payment processor connections...');
        // Add actual Stripe client destroy logic here when implemented
      }
    ];

    registerShutdownHandlers(httpServer, cleanupHooks, 30000, logger);
  });
})

// Remove old immediate shutdown handlers
// process.once('SIGINT', closeGracefully)
// process.once('SIGTERM', closeGracefully)

module.exports = {
  getServerCredentials,
  app: app,
  rateLimitInterceptor,
  configuredRateLimit,
  rateLimiter,
  registerShutdownHandlers
}
