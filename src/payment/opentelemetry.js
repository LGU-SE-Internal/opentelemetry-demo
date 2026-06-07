// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

const opentelemetry = require("@opentelemetry/sdk-node")
const {getNodeAutoInstrumentations} = require("@opentelemetry/auto-instrumentations-node")
const {OTLPTraceExporter} = require('@opentelemetry/exporter-trace-otlp-grpc')
const {OTLPMetricExporter} = require('@opentelemetry/exporter-metrics-otlp-grpc')
const {PeriodicExportingMetricReader} = require('@opentelemetry/sdk-metrics')
const {alibabaCloudEcsDetector} = require('@opentelemetry/resource-detector-alibaba-cloud')
const {awsEc2Detector, awsEksDetector} = require('@opentelemetry/resource-detector-aws')
const {containerDetector} = require('@opentelemetry/resource-detector-container')
const {gcpDetector} = require('@opentelemetry/resource-detector-gcp')
const {envDetector, hostDetector, osDetector, processDetector} = require('@opentelemetry/resources')
const {RuntimeNodeInstrumentation} = require('@opentelemetry/instrumentation-runtime-node')

const sdk = new opentelemetry.NodeSDK({
  traceExporter: new OTLPTraceExporter(),
  instrumentations: [
    getNodeAutoInstrumentations({
      // only instrument fs if it is part of another trace
      '@opentelemetry/instrumentation-fs': {
        requireParentSpan: true,
      },
    }),
    new RuntimeNodeInstrumentation({
      monitoringPrecision: 5000,
    })
  ],
  metricReader: new PeriodicExportingMetricReader({
    exporter: new OTLPMetricExporter()
  }),
  resourceDetectors: [
    containerDetector,
    envDetector,
    hostDetector,
    osDetector,
    processDetector,
    alibabaCloudEcsDetector,
    awsEksDetector,
    awsEc2Detector,
    gcpDetector
  ],
})

// Initialize retry metrics
const meter = require('@opentelemetry/api').metrics.getMeter('payment.retry');
const metrics = {
  retryAttempts: meter.createCounter('external_call.retry_attempts', {
    description: 'Total number of retry attempts triggered for external service calls'
  }),
  retrySuccesses: meter.createCounter('external_call.retry_successes', {
    description: 'Total number of external service calls that succeeded after retries'
  }),
  retryFailures: meter.createCounter('external_call.retry_failures', {
    description: 'Total number of external service calls that failed after all retries'
  }),
  getCounter(name) {
    switch (name) {
      case 'external_call.retry_attempts': return this.retryAttempts;
      case 'external_call.retry_successes': return this.retrySuccesses;
      case 'external_call.retry_failures': return this.retryFailures;
      default: throw new Error(`Unknown metric: ${name}`);
    }
  }
};

module.exports = {
  sdk,
  metrics
};
