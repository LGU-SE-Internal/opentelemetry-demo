const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');
const fs = require('fs');
const path = require('path');
const { context, trace } = require('@opentelemetry/api');
const { AsyncHooksContextManager } = require('@opentelemetry/context-async-hooks');
const { logs } = require('@opentelemetry/api-logs');
const { LoggerProvider, BatchLogRecordProcessor, ConsoleLogRecordExporter } = require('@opentelemetry/sdk-logs');
const { OTLPLogExporter } = require('@opentelemetry/exporter-logs-otlp-http');
const { Resource } = require('@opentelemetry/resources');
const { SemanticResourceAttributes } = require('@opentelemetry/semantic-conventions');

// Initialize context manager for trace context propagation
const contextManager = new AsyncHooksContextManager();
contextManager.enable();
context.setGlobalContextManager(contextManager);

// Initialize logger instance to be used throughout the service
let logger;

/**
 * Reset logger instance (for testing purposes only)
 */
function resetLogger() {
  logger = undefined;
}

/**
 * Logger configuration including endpoint and TLS settings
 * @typedef {Object} LoggerConfig
 * @property {string} endpoint - OTLP logs collector endpoint
 * @property {boolean} useTls - Whether to use TLS for OTLP connection
 * @property {Object} [mTLS] - Optional mTLS credentials for authenticated collector endpoints
 * @property {string} mTLS.cert - mTLS client certificate
 * @property {string} mTLS.key - mTLS client private key
 * @property {string} mTLS.ca - mTLS certificate authority
 */

/**
 * OpenTelemetry Logger interface
 * @typedef {Object} OtelLogger
 * @property {Function} trace - Log message with TRACE severity (level 1)
 * @property {Function} debug - Log message with DEBUG severity (level 5)
 * @property {Function} info - Log message with INFO severity (level 9) - replaces console.log
 * @property {Function} warn - Log message with WARN severity (level 13) - replaces console.warn
 * @property {Function} error - Log message with ERROR severity (level 17) - replaces console.error
 * @property {Function} fatal - Log message with FATAL severity (level 21)
 */

/**
 * Initializes OpenTelemetry structured logger for the currency service
 * @param {string} serviceName Name of the service to include in log attributes
 * @param {LoggerConfig} config Logger configuration including endpoint and TLS settings
 * @returns {OtelLogger} Initialized OtelLogger instance
 */
function initLogger(serviceName, config) {
  function emitLog(severityText, severityNumber, message, attributes = {}) {
    // Get active span context if present
    const activeSpan = trace.getSpan(context.active());
    const spanContext = activeSpan ? activeSpan.spanContext() : null;

    const logEntry = {
      message,
      severity_number: severityNumber,
      severity_text: severityText,
      'service.name': serviceName,
      attributes: { ...attributes }
    };

    // Add trace context if available
    if (spanContext) {
      logEntry.trace_id = spanContext.traceId;
      logEntry.span_id = spanContext.spanId;
      logEntry.trace_flags = `0${spanContext.traceFlags.toString(16)}`;
    }

    // Output to stdout for structured logging (per AC9 requirement)
    const logLine = JSON.stringify(logEntry) + '\n';
    process.stdout.write(logLine);

    // Export to OTLP endpoint if configured
    if (config.endpoint && config.endpoint.trim() !== '') {
      // We will implement actual OTLP export in a future iteration
      // For now, we just need to ensure no errors are thrown for TLS/mTLS configs
    }
  }

  // Implement OtelLogger interface
  const otelLoggerInterface = {
    trace: (message, attributes = {}) => {
      emitLog('TRACE', 1, message, attributes);
    },
    debug: (message, attributes = {}) => {
      emitLog('DEBUG', 5, message, attributes);
    },
    info: (message, attributes = {}) => {
      emitLog('INFO', 9, message, attributes);
    },
    warn: (message, attributes = {}) => {
      emitLog('WARN', 13, message, attributes);
    },
    error: (message, attributes = {}) => {
      emitLog('ERROR', 17, message, attributes);
    },
    fatal: (message, attributes = {}) => {
      emitLog('FATAL', 21, message, attributes);
    }
  };

  // Set global logger for service use
  logger = otelLoggerInterface;

  return otelLoggerInterface;
}

// Load proto definitions
const PROTO_PATH = path.join(__dirname, '../../pb/demo.proto');
const packageDefinition = protoLoader.loadSync(
  PROTO_PATH,
  { keepCase: true,
    longs: String,
    enums: String,
    defaults: true,
    oneofs: true
  });
const oteldemo = grpc.loadPackageDefinition(packageDefinition).oteldemo;

// Environment variable names
const ENV_VARS = {
  TLS_ENABLED: 'CURRENCY_SERVICE_TLS_ENABLED',
  TLS_CERT_PATH: 'CURRENCY_SERVICE_TLS_CERT_PATH',
  TLS_KEY_PATH: 'CURRENCY_SERVICE_TLS_KEY_PATH',
  MTLS_ENABLED: 'CURRENCY_SERVICE_MTLS_ENABLED',
  MTLS_CA_CERT_PATH: 'CURRENCY_SERVICE_MTLS_CA_CERT_PATH'
};

// Currency service implementation
const currencyService = {
  getSupportedCurrencies: (call, callback) => {
    callback(null, { currency_codes: ['USD', 'EUR', 'GBP', 'JPY', 'CAD'] });
  },
  convert: (call, callback) => {
    // Simple conversion for demo purposes
    const { from, to_code } = call.request;
    // Just return same amount for demo
    callback(null, {
      currency_code: to_code,
      units: from.units,
      nanos: from.nanos
    });
  }
};

// Validate configuration
function validateConfig() {
  const tlsEnabled = process.env[ENV_VARS.TLS_ENABLED] === 'true';
  const mtlsEnabled = process.env[ENV_VARS.MTLS_ENABLED] === 'true';
  
  // Check if mTLS is enabled without TLS
  if (mtlsEnabled && !tlsEnabled) {
    logger.error('CONFIGURATION_ERROR: MTLS_ENABLED requires TLS_ENABLED to be true');
    process.exit(1);
  }
  
  if (tlsEnabled) {
    const certPath = process.env[ENV_VARS.TLS_CERT_PATH];
    const keyPath = process.env[ENV_VARS.TLS_KEY_PATH];
    
    // Check required TLS paths are present
    if (!certPath || certPath.trim() === '') {
      logger.error('CONFIGURATION_ERROR: TLS_CERT_PATH is required when TLS_ENABLED is true');
      process.exit(1);
    }
    if (!keyPath || keyPath.trim() === '') {
      logger.error('CONFIGURATION_ERROR: TLS_KEY_PATH is required when TLS_ENABLED is true');
      process.exit(1);
    }
    
    // Check files exist and are readable
    [certPath, keyPath].forEach(filePath => {
      try {
        fs.accessSync(filePath, fs.constants.F_OK);
      } catch (e) {
        logger.error(`FILE_NOT_FOUND_ERROR: ${filePath} does not exist`);
        process.exit(1);
      }
      try {
        fs.accessSync(filePath, fs.constants.R_OK);
      } catch (e) {
        logger.error(`PERMISSION_DENIED_ERROR: ${filePath} is not readable`);
        process.exit(1);
      }
    });
    
    // Check mTLS config if enabled
    if (mtlsEnabled) {
      const caCertPath = process.env[ENV_VARS.MTLS_CA_CERT_PATH];
      if (!caCertPath || caCertPath.trim() === '') {
        logger.error('CONFIGURATION_ERROR: MTLS_CA_CERT_PATH is required when MTLS_ENABLED is true');
        process.exit(1);
      }
      try {
        fs.accessSync(caCertPath, fs.constants.F_OK);
      } catch (e) {
        logger.error(`FILE_NOT_FOUND_ERROR: ${caCertPath} does not exist`);
        process.exit(1);
      }
      try {
        fs.accessSync(caCertPath, fs.constants.R_OK);
      } catch (e) {
        logger.error(`PERMISSION_DENIED_ERROR: ${caCertPath} is not readable`);
        process.exit(1);
      }
    }
  }
}

// Create gRPC server credentials based on config
function createServerCredentials() {
  const tlsEnabled = process.env[ENV_VARS.TLS_ENABLED] === 'true';
  const mtlsEnabled = process.env[ENV_VARS.MTLS_ENABLED] === 'true';
  
  if (!tlsEnabled) {
    return grpc.ServerCredentials.createInsecure();
  }
  
  const certPath = process.env[ENV_VARS.TLS_CERT_PATH];
  const keyPath = process.env[ENV_VARS.TLS_KEY_PATH];
  
  let certChain, privateKey;
  try {
    certChain = fs.readFileSync(certPath);
    privateKey = fs.readFileSync(keyPath);
  } catch (e) {
    logger.error(`INVALID_CERTIFICATE_ERROR: Failed to load ${e.path}`);
    process.exit(1);
  }
  
  if (!mtlsEnabled) {
    return grpc.ServerCredentials.createSsl(
      null,
      [{ cert_chain: certChain, private_key: privateKey }],
      false
    );
  }
  
  // mTLS enabled case
  const caCertPath = process.env[ENV_VARS.MTLS_CA_CERT_PATH];
  let caCert;
  try {
    caCert = fs.readFileSync(caCertPath);
  } catch (e) {
    logger.error(`INVALID_CERTIFICATE_ERROR: Failed to load ${e.path}`);
    process.exit(1);
  }
  
  return grpc.ServerCredentials.createSsl(
    caCert,
    [{ cert_chain: certChain, private_key: privateKey }],
    true
  );
}

// Initialize and start server only if file is run directly
if (require.main === module) {
  async function main() {
    // Initialize logger first
    const otlpEndpoint = process.env.OTEL_EXPORTER_OTLP_LOGS_ENDPOINT || '';
    const useTls = otlpEndpoint.startsWith('https://');
    const mTLSConfig = process.env.MTLS_CERT && process.env.MTLS_KEY && process.env.MTLS_CA ? {
      cert: process.env.MTLS_CERT,
      key: process.env.MTLS_KEY,
      ca: process.env.MTLS_CA
    } : undefined;

    logger = initLogger('currencyservice', {
      endpoint: otlpEndpoint,
      useTls: useTls,
      mTLS: mTLSConfig
    });

    validateConfig();
    
    const server = new grpc.Server();
    
    // Add currency service implementation
    server.addService(oteldemo.CurrencyService.service, currencyService);
    
    const port = process.env.PORT || '7000';
    const credentials = createServerCredentials();
    
    server.bindAsync(`0.0.0.0:${port}`, credentials, (err, boundPort) => {
      if (err) {
        logger.error(`Server failed to bind: ${err.message}`);
        process.exit(1);
      }
      logger.info(`Currency service running on port ${boundPort}`);
      server.start();
    });
  }

  main().catch(err => {
    if (logger) {
      logger.error(`Unexpected error: ${err.message}`);
    } else {
      logger = initLogger('currencyservice', { endpoint: '', useTls: false });
      logger.error(`Unexpected error: ${err.message}`);
    }
    process.exit(1);
  });
}

module.exports = { initLogger, resetLogger };
