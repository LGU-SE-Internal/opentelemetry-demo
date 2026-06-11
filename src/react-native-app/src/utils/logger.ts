import { LogsAPI, SeverityNumber } from '@opentelemetry/api-logs';
import {
  LoggerProvider,
  SimpleLogRecordProcessor,
  BatchLogRecordProcessor,
} from '@opentelemetry/sdk-logs';
import { OTLPLogExporter } from '@opentelemetry/exporter-logs-otlp-http';
import { Resource } from '@opentelemetry/resources';
import { SEMRESATTRS_SERVICE_NAME } from '@opentelemetry/semantic-conventions';
import { context, trace } from '@opentelemetry/api';

let loggerProvider: LoggerProvider | undefined;

/**
 * Initialize OpenTelemetry logger SDK at app startup
 * @param collectorEndpoint - OTLP collector endpoint, defaults to "http://localhost:4318/v1/logs"
 */
export function initLogger(collectorEndpoint?: string): void {
  if (loggerProvider) return;

  const resource = Resource.default().merge(
    new Resource({
      [SEMRESATTRS_SERVICE_NAME]: 'react-native-app',
    })
  );

  loggerProvider = new LoggerProvider({
    resource,
  });

  // Use batch processor for production, simple for development
  const isDev = process.env.NODE_ENV === 'development';
  const exporter = new OTLPLogExporter({
    url: collectorEndpoint || process.env.EXPO_PUBLIC_OTEL_LOGS_ENDPOINT || 'http://localhost:4318/v1/logs',
  });

  if (isDev) {
    loggerProvider.addLogRecordProcessor(new SimpleLogRecordProcessor(exporter));
  } else {
    loggerProvider.addLogRecordProcessor(new BatchLogRecordProcessor(exporter));
  }

  LogsAPI.getInstance().setGlobalLoggerProvider(loggerProvider);
}

/**
 * Creates a per-component OpenTelemetry logger instance
 * @param componentName - Name of the component/module using the logger (e.g. "CartScreen", "ApiClient")
 * @returns Logger instance with info/error methods matching console signature
 */
export function createLogger(componentName: string): {
  info: (message: string, ...optionalArgs: unknown[]) => void;
  error: (message: string, ...optionalArgs: unknown[]) => void;
} {
  const logger = LogsAPI.getInstance().getLogger('react-native-app-logger');

  const getActiveContextAttributes = (): Record<string, unknown> => {
    const activeSpan = trace.getSpan(context.active());
    if (!activeSpan) {
      return {};
    }
    const spanContext = activeSpan.spanContext();
    
    if (!spanContext || !spanContext.isValid()) {
      return {};
    }

    return {
      'trace_id': spanContext.traceId,
      'span_id': spanContext.spanId,
      'operation.name': activeSpan['name'] || 'unknown',
    };
  };

  return {
    info: (message: string, ...optionalArgs: unknown[]) => {
      logger.emit({
        severityNumber: SeverityNumber.INFO,
        severityText: 'INFO',
        body: message,
        attributes: {
          'component.name': componentName,
          ...getActiveContextAttributes(),
        },
      });
    },
    error: (message: string, ...optionalArgs: unknown[]) => {
      logger.emit({
        severityNumber: SeverityNumber.ERROR,
        severityText: 'ERROR',
        body: message,
        attributes: {
          'component.name': componentName,
          ...getActiveContextAttributes(),
        },
      });
    },
  };
}
