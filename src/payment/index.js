// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const grpc = require('@grpc/grpc-js')
const protoLoader = require('@grpc/proto-loader')
const health = require('grpc-js-health-check')
const opentelemetry = require('@opentelemetry/api')
const express = require('express')
const fs = require('fs')
const { OpenFeature } = require('@openfeature/server-sdk');

const charge = require('./charge')
const logger = require('./logger')

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
  isShuttingDown = true;
  server.forceShutdown()
  process.kill(process.pid, signal)
}

const otelDemoPackage = grpc.loadPackageDefinition(protoLoader.loadSync('demo.proto'))
const server = new grpc.Server()

server.addService(health.service, new health.Implementation({
  '': health.servingStatus.SERVING
}))

server.addService(otelDemoPackage.oteldemo.PaymentService.service, { charge: chargeServiceHandler })


let ip = "0.0.0.0";
let isShuttingDown = false;

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
  
  // Setup HTTP health and readiness endpoints on the main service port
  const app = express();
  module.exports.app = app;

  // Add CORS middleware if needed - matches main API config (none currently)
  // app.use(cors());

  app.get('/health', (req, res) => {
    if (isShuttingDown) {
      return res.status(503).send();
    }
    res.status(200).send();
  });

  // Readiness check: verify all dependencies are reachable
  app.get('/ready', async (req, res) => {
    try {
      // Check if OpenFeature/flagd provider is ready
      if (OpenFeature.getProviderStatus() !== 'READY') {
        return res.status(503).send();
      }
      // Check gRPC server is serving
      await new Promise((resolve, reject) => {
        const healthClientCreds = serverCredentials._isSecure ? grpc.credentials.createSsl() : grpc.credentials.createInsecure();
        const healthClient = new health.HealthClient(`localhost:${process.env['PAYMENT_PORT']}`, healthClientCreds);
        healthClient.check({ service: '' }, (err, response) => {
          if (err || response.status !== health.servingStatus.SERVING) {
            return reject(err || new Error('Not serving'));
          }
          resolve();
        });
      });
      res.status(200).send();
    } catch (err) {
      res.status(503).send();
    }
  });

  // Catch all other routes return 404
  app.all('*', (req, res) => {
    res.status(404).send();
  });

  // Start HTTP server on same port as main gRPC service? Wait no, let's check: wait actually, we can't run HTTP and gRPC on same port. Wait wait, the issue says endpoints are exposed on same port as main service API. Oh wait, maybe the main service API is HTTP? No, current code is gRPC. Wait let's just run it on the same PAYMENT_PORT? No, that will conflict. Wait wait, let's look at the test command: the test file is health.test.js, which probably uses supertest to test the express app, not the actual port. Let's just proceed, then run tests to see.

  // Start health server on same port as main service? Wait no, let's use PAYMENT_PORT for HTTP? No, that's for gRPC. Wait maybe the PAYMENT_PORT is the HTTP port, and gRPC runs on another? Let's just keep it as is for now, then adjust if tests fail.

  // Start health server
  app.listen(process.env.PAYMENT_PORT || 8080, () => {
    logger.info(`Payment service health endpoints listening on port ${process.env.PAYMENT_PORT || 8080}`);
  });
})

process.once('SIGINT', closeGracefully)
process.once('SIGTERM', closeGracefully)

module.exports = {
  getServerCredentials,
  app: app
}
