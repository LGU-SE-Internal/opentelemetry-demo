// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const grpc = require('@grpc/grpc-js')
const protoLoader = require('@grpc/proto-loader')
const health = require('grpc-js-health-check')
const opentelemetry = require('@opentelemetry/api')

const charge = require('./charge')
const logger = require('./logger')

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

const envVarSpecs = [
  { name: 'PAYMENT_PORT', type: 'TCP port number', allowedValues: [] }
];
let validatedEnvCount = 0;

envVarSpecs.forEach(spec => {
  const value = process.env[spec.name];
  if (!value) {
    let errorMsg = "Invalid environment variable configuration:\n";
    errorMsg += `  Variable name: ${spec.name}\n`;
    errorMsg += `  Invalid value: <not set>\n`;
    errorMsg += `  Expected: Required ${spec.type} value`;
    if (spec.allowedValues.length > 0) {
      errorMsg += `, allowed values: ${spec.allowedValues.join(', ')}`;
    }
    console.error(errorMsg);
    process.exit(1);
  }
  validatedEnvCount++;
});

const ipv6_enabled = process.env.IPV6_ENABLED;

if (ipv6_enabled == "true") {
  ip = "[::]";
  logger.info(`Overwriting Localhost IP: ${ip}`)
}

logger.info("All required environment variables validated successfully", {
  validated_env_vars: validatedEnvCount,
  configuration_valid: true
});

const address = ip + `:${process.env['PAYMENT_PORT']}`;

server.bindAsync(address, grpc.ServerCredentials.createInsecure(), (err, port) => {
  if (err) {
    return logger.error({ err })
  }

  logger.info(`payment gRPC server started on ${address}`)
})

process.once('SIGINT', closeGracefully)
process.once('SIGTERM', closeGracefully)
