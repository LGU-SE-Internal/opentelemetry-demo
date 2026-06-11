import { context, trace, Span } from '@opentelemetry/api';
import { createLogger } from '../utils/logger'; // Expected implementation location per spec

// Mock OpenTelemetry collector receiver for testing
const mockLogExporter = {
  export: jest.fn((logs, resultCallback) => {
    resultCallback({ code: 0 });
  }),
  shutdown: jest.fn(),
};

// Mock the SDK logger to capture exported logs
jest.mock('@opentelemetry/sdk-logs', () => ({
  ...jest.requireActual('@opentelemetry/sdk-logs'),
  LoggerProvider: jest.fn(() => ({
    addLogRecordProcessor: jest.fn(),
    getLogger: jest.fn(() => ({
      emit: jest.fn((logRecord) => {
        mockLogExporter.export([logRecord], () => {});
      }),
    })),
  })),
  SimpleLogRecordProcessor: jest.fn(() => ({})),
}));

describe('OpenTelemetry Logger Acceptance Criteria Tests', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  /**
   * AC-1: 100% of existing console.log calls replaced with logger.info(),
   * original message and all optional args preserved exactly
   */
  test('ac1_console_log_replaced_with_info_preserves_all_args', () => {
    const testMessage = 'Test info log message';
    const testArgs = ['additional', 123, { key: 'value' }, new Error('test err')];
    const logger = createLogger('TestComponent');

    logger.info(testMessage, ...testArgs);

    // Verify log was emitted with exact message and args
    expect(mockLogExporter.export).toHaveBeenCalledTimes(1);
    const emittedLog = mockLogExporter.export.mock.calls[0][0][0];
    expect(emittedLog.body).toBe(testMessage);
    expect(emittedLog.attributes['log.arguments']).toEqual(testArgs);
  });

  /**
   * AC-2: 100% of existing console.error calls replaced with logger.error(),
   * original message and all optional args preserved exactly
   */
  test('ac2_console_error_replaced_with_error_preserves_all_args', () => {
    const testMessage = 'Test error log message';
    const testArgs = ['error detail', 500, { stack: 'trace' }, new Error('critical err')];
    const logger = createLogger('TestComponent');

    logger.error(testMessage, ...testArgs);

    // Verify log was emitted with exact message and args
    expect(mockLogExporter.export).toHaveBeenCalledTimes(1);
    const emittedLog = mockLogExporter.export.mock.calls[0][0][0];
    expect(emittedLog.body).toBe(testMessage);
    expect(emittedLog.attributes['log.arguments']).toEqual(testArgs);
  });

  /**
   * AC-3: All emitted logs include component.name attribute matching emitting component
   */
  test('ac3_all_logs_include_component_name_attribute', () => {
    const componentNames = ['CartScreen', 'ApiClient', 'UserProfile', 'CheckoutService'];
    
    componentNames.forEach(componentName => {
      const logger = createLogger(componentName);
      logger.info(`Test log from ${componentName}`);

      const emittedLog = mockLogExporter.export.mock.calls.at(-1)[0][0];
      expect(emittedLog.attributes['component.name']).toBe(componentName);
    });
  });

  /**
   * AC-4: When trace active, logs automatically include trace_id, span_id, operation.name
   */
  test('ac4_active_trace_injects_trace_span_operation_attributes', () => {
    const traceId = 'd4cda95b652f4a1592b449d5929fda1b';
    const spanId = '6e0c6327ec516348';
    const operationName = 'checkout-process';

    // Create mock active span
    const mockSpan = {
      spanContext: () => ({
        traceId,
        spanId,
        traceFlags: 1,
      }),
      name: operationName,
    } as unknown as Span;

    // Run in active trace context
    context.with(trace.setSpan(context.active(), mockSpan), () => {
      const logger = createLogger('CheckoutService');
      logger.info('Processing checkout');

      const emittedLog = mockLogExporter.export.mock.calls[0][0][0];
      expect(emittedLog.attributes['trace_id']).toBe(traceId);
      expect(emittedLog.attributes['span_id']).toBe(spanId);
      expect(emittedLog.attributes['operation.name']).toBe(operationName);
    });
  });

  /**
   * AC-4: When no trace active, no trace/span/operation attributes are added (optional)
   */
  test('ac4_no_active_trace_no_tracing_attributes_added', () => {
    const logger = createLogger('TestComponent');
    logger.info('Log without active trace');

    const emittedLog = mockLogExporter.export.mock.calls[0][0][0];
    expect(emittedLog.attributes['trace_id']).toBeUndefined();
    expect(emittedLog.attributes['span_id']).toBeUndefined();
    expect(emittedLog.attributes['operation.name']).toBeUndefined();
  });

  /**
   * AC-5: Log level mapping strictly followed: log -> INFO, error -> ERROR
   */
  test('ac5_log_level_mapping_correct', () => {
    const logger = createLogger('TestComponent');
    
    // Test info level
    logger.info('Info level log');
    let emittedLog = mockLogExporter.export.mock.calls[0][0][0];
    expect(emittedLog.severityText).toBe('INFO');
    expect(emittedLog.severityNumber).toBe(9); // INFO severity number per OTel spec

    // Test error level
    logger.error('Error level log');
    emittedLog = mockLogExporter.export.mock.calls[1][0][0];
    expect(emittedLog.severityText).toBe('ERROR');
    expect(emittedLog.severityNumber).toBe(17); // ERROR severity number per OTel spec
  });

  /**
   * AC-6: Logs exported to collector in dev mode with all mandatory fields present
   */
  test('ac6_logs_exported_to_collector_with_all_mandatory_fields', () => {
    const beforeTimestamp = Date.now();
    const logger = createLogger('TestComponent');
    logger.info('Test log for collector export');
    const afterTimestamp = Date.now();

    expect(mockLogExporter.export).toHaveBeenCalledTimes(1);
    const emittedLog = mockLogExporter.export.mock.calls[0][0][0];

    // Verify all mandatory fields exist
    expect(emittedLog.timestamp).toBeGreaterThanOrEqual(beforeTimestamp);
    expect(emittedLog.timestamp).toBeLessThanOrEqual(afterTimestamp);
    expect(emittedLog.severityText).toBeDefined();
    expect(emittedLog.body).toBeDefined();
    expect(emittedLog.attributes['component.name']).toBeDefined();
  });
});
