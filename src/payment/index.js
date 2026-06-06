// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const grpc = require('@grpc/grpc-js')
const protoLoader = require('@grpc/proto-loader')
const health = require('grpc-js-health-check')
const opentelemetry = require('@opentelemetry/api')
const express = require('express')
const fs = require('fs')

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

async function chargeServiceHandler(call, callback) {
  const span = opentelemetry.trace.getActiveSpan();

  try {
    const amount = call.request.amount
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
const server = new grpc.Server()

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
  
  // Setup HTTP health endpoint
  const HEALTH_PORT = process.env.PAYMENT_HEALTH_PORT || 8080;
  const app = express();
  module.exports.app = app;

  // Create gRPC health client to check local server
  const healthClientCreds = serverCredentials._isSecure ? grpc.credentials.createSsl() : grpc.credentials.createInsecure();
  const healthClient = new health.HealthClient(`localhost:${process.env['PAYMENT_PORT']}`, healthClientCreds);

  app.get('/health', async (req, res) => {
    try {
      await new Promise((resolve, reject) => {
        healthClient.check({ service: '' }, (err, response) => {
          if (err) {
            return reject(err);
          }
          if (response.status !== health.servingStatus.SERVING) {
            return reject(new Error('gRPC server not serving'));
          }
          resolve();
        });
      });
      res.status(200).json({ status: 'ok' });
    } catch (err) {
      res.status(503).json({ status: 'unhealthy', error: 'gRPC server not reachable' });
    }
  });

  // Catch all other routes return 404
  app.all('*', (req, res) => {
    res.status(404).send();
  });

  // Start health server
  app.listen(HEALTH_PORT, () => {
    logger.info(`Payment service health endpoint listening on port ${HEALTH_PORT}`);
  });
})

process.once('SIGINT', closeGracefully)
process.once('SIGTERM', closeGracefully)
