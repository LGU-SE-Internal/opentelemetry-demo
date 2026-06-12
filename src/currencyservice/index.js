const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');
const fs = require('fs');
const path = require('path');
const { logs } = require('@opentelemetry/api-logs');
const { LoggerProvider, BatchLogRecordProcessor, ConsoleLogRecordExporter } = require('@opentelemetry/sdk-logs');
const { OTLPLogExporter } = require('@opentelemetry/exporter-logs-otlp-http');
const { Resource } = require('@opentelemetry/resources');
const { SemanticResourceAttributes } = require('@opentelemetry/semantic-conventions');
const { context, trace } = require('@opentelemetry/api');

// Initialize logger instance to be used throughout the service
let logger;

/**
 * Initializes OpenTelemetry structured logger for the currency service
 * @param {string} serviceName Name of the service to include in log attributes
 * @param {LoggerConfig} config Logger configuration including endpoint and TLS settings
 * @returns {OtelLogger} Initialized OtelLogger instance
 */
function initLogger(serviceName, config) {
  // Create resource with service name
  const resource = Resource.default().merge(
    new Resource({
      [SemanticResourceAttributes.SERVICE_NAME]: serviceName,
    })
  );

  // Initialize logger provider
  const loggerProvider = new LoggerProvider({
    resource: resource,
  });

  // Add appropriate processors
  if (config.endpoint && config.endpoint.trim() !== '') {
    // Configure OTLP exporter
    const otlpExporterOptions = {
      url: config.endpoint,
      credentials: config.useTls ? require('https').createSecureAgent(
        config.mTLS ? {
          cert: config.mTLS.cert,
          key: config.mTLS.key,
          ca: config.mTLS.ca,
        } : {}
      ) : undefined,
    };

    const otlpExporter = new OTLPLogExporter(otlpExporterOptions);
    loggerProvider.addLogRecordProcessor(new BatchLogRecordProcessor(otlpExporter));
  }

  // Always add console exporter for fallback
  const consoleExporter = new ConsoleLogRecordExporter();
  loggerProvider.addLogRecordProcessor(new BatchLogRecordProcessor(consoleExporter));

  // Get logger instance
  const otelLogger = loggerProvider.getLogger(serviceName);

  // Implement OtelLogger interface
  const otelLoggerInterface = {
    trace: (message, attributes = {}) => {
      emitLog(otelLogger, 'TRACE', 1, message, attributes, serviceName);
    },
    debug: (message, attributes = {}) => {
      emitLog(otelLogger, 'DEBUG', 5, message, attributes, serviceName);
    },
    info: (message, attributes = {}) => {
      emitLog(otelLogger, 'INFO', 9, message, attributes, serviceName);
    },
    warn: (message, attributes = {}) => {
      emitLog(otelLogger, 'WARN', 13, message, attributes, serviceName);
    },
    error: (message, attributes = {}) => {
      emitLog(otelLogger, 'ERROR', 17, message, attributes, serviceName);
    },
    fatal: (message, attributes = {}) => {
      emitLog(otelLogger, 'FATAL', 21, message, attributes, serviceName);
    }
  };

  // Set global logger for service use
  if (!logger) {
    logger = otelLoggerInterface;
  }

  return otelLoggerInterface;
}

function emitLog(otelLogger, severityText, severityNumber, message, attributes, serviceName) {
  // Get active span context if present
  const activeSpan = trace.getSpan(context.active());
  const spanContext = activeSpan ? activeSpan.spanContext() : null;

  const logAttributes = {
    'service.name': serviceName,
    ...attributes,
  };

  // Add trace context if available
  if (spanContext && spanContext.isValid()) {
    logAttributes.trace_id = spanContext.traceId;
    logAttributes.span_id = spanContext.spanId;
    logAttributes.trace_flags = `0${spanContext.traceFlags.toString(16)}`;
  }

  otelLogger.emit({
    severityText,
    severityNumber,
    body: message,
    attributes: logAttributes,
  });
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

// Initialize and start server
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
    console.error(`Unexpected error: ${err.message}`);
  }
  process.exit(1);
});

module.exports = { initLogger };
