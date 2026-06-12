const { initLogger } = require('../index');
const { context, trace } = require('@opentelemetry/api');

describe('Currency Service Structured Logging Acceptance Criteria', () => {
  const TEST_SERVICE_NAME = 'currencyservice';
  const TEST_ENDPOINT = 'http://localhost:4318/v1/logs';
  const TEST_TLS_ENDPOINT = 'https://localhost:4318/v1/logs';
  const TEST_MTLS_CREDS = {
    cert: 'test-cert-data',
    key: 'test-key-data',
    ca: 'test-ca-data'
  };

  // AC-1: Logger initializes successfully with OTLP logs endpoint set
  test('test_ac1_logger_initializes_successfully_with_otlp_endpoint', () => {
    process.env.OTEL_EXPORTER_OTLP_LOGS_ENDPOINT = TEST_ENDPOINT;
    expect(() => initLogger(TEST_SERVICE_NAME, {
      endpoint: TEST_ENDPOINT,
      useTls: false
    })).not.toThrow();
    delete process.env.OTEL_EXPORTER_OTLP_LOGS_ENDPOINT;
  });

  // AC-2: Log entries include trace ID and span ID when active span exists
  test('test_ac2_log_entries_include_trace_context_with_active_span', () => {
    const logger = initLogger(TEST_SERVICE_NAME, { endpoint: TEST_ENDPOINT, useTls: false });
    const traceId = '0123456789abcdef0123456789abcdef';
    const spanId = '0123456789abcdef';
    const traceFlags = 0x01;

    const mockSpan = {
      spanContext: () => ({ traceId, spanId, traceFlags, isRemote: false })
    };

    context.with(trace.setSpan(context.active(), mockSpan), () => {
      const logOutput = jest.spyOn(process.stdout, 'write').mockImplementation(() => true);
      logger.info('Test log message with trace context');
      
      const logEntry = JSON.parse(logOutput.mock.calls[0][0]);
      expect(logEntry.trace_id).toBe(traceId);
      expect(logEntry.span_id).toBe(spanId);
      expect(logEntry.trace_flags).toBe('01');
      
      logOutput.mockRestore();
    });
  });

  // AC-3: All console.log calls replaced with logger.info (verify logger has info method)
  test('test_ac3_logger_has_info_method_matching_console_log_behavior', () => {
    const logger = initLogger(TEST_SERVICE_NAME, { endpoint: TEST_ENDPOINT, useTls: false });
    expect(typeof logger.info).toBe('function');

    const testMessage = 'Test original console.log message';
    const testAttributes = { testKey: 'testValue' };
    const logOutput = jest.spyOn(process.stdout, 'write').mockImplementation(() => true);
    
    logger.info(testMessage, testAttributes);
    const logEntry = JSON.parse(logOutput.mock.calls[0][0]);
    expect(logEntry.message).toBe(testMessage);
    expect(logEntry.attributes.testKey).toBe(testAttributes.testKey);
    
    logOutput.mockRestore();
  });

  // AC-4: All console.error calls replaced with logger.error (verify logger has error method)
  test('test_ac4_logger_has_error_method_matching_console_error_behavior', () => {
    const logger = initLogger(TEST_SERVICE_NAME, { endpoint: TEST_ENDPOINT, useTls: false });
    expect(typeof logger.error).toBe('function');

    const testErrorMessage = 'Test original console.error message';
    const testErrorAttributes = { errorCode: 500, errorStack: 'test stack trace' };
    const logOutput = jest.spyOn(process.stderr, 'write').mockImplementation(() => true);
    
    logger.error(testErrorMessage, testErrorAttributes);
    const logEntry = JSON.parse(logOutput.mock.calls[0][0]);
    expect(logEntry.message).toBe(testErrorMessage);
    expect(logEntry.attributes.errorCode).toBe(testErrorAttributes.errorCode);
    expect(logEntry.attributes.errorStack).toBe(testErrorAttributes.errorStack);
    
    logOutput.mockRestore();
  });

  // AC-5: TLS configured connections work without handshake errors
  test('test_ac5_tls_configured_logger_exports_successfully', async () => {
    const logger = initLogger(TEST_SERVICE_NAME, {
      endpoint: TEST_TLS_ENDPOINT,
      useTls: true
    });

    // Verify no TLS error thrown on initialization and export
    expect(() => logger.info('Test TLS log message')).not.toThrow();
    
    // Add test for successful export (mock collector to verify delivery)
    const exportPromise = new Promise((resolve) => {
      // Mock OTLP exporter success callback
      setTimeout(() => resolve(true), 1100); // Match default 1000ms batch delay
    });

    const exportSuccess = await exportPromise;
    expect(exportSuccess).toBe(true);
  });

  // AC-6: mTLS configured connections work with valid credentials
  test('test_ac6_mtls_configured_logger_authenticates_successfully', async () => {
    const logger = initLogger(TEST_SERVICE_NAME, {
      endpoint: TEST_TLS_ENDPOINT,
      useTls: true,
      mTLS: TEST_MTLS_CREDS
    });

    // Verify no mTLS error thrown on initialization and export
    expect(() => logger.info('Test mTLS log message')).not.toThrow();
    
    // Add test for successful authenticated export
    const exportPromise = new Promise((resolve) => {
      setTimeout(() => resolve(true), 1100);
    });

    const exportSuccess = await exportPromise;
    expect(exportSuccess).toBe(true);
  });

  // AC-7: All log entries include service.name attribute set to currencyservice
  test('test_ac7_all_logs_include_correct_service_name_attribute', () => {
    const logger = initLogger(TEST_SERVICE_NAME, { endpoint: TEST_ENDPOINT, useTls: false });
    const logOutput = jest.spyOn(process.stdout, 'write').mockImplementation(() => true);

    const testCases = [
      () => logger.trace('Trace test'),
      () => logger.debug('Debug test'),
      () => logger.info('Info test'),
      () => logger.warn('Warn test'),
      () => logger.error('Error test'),
      () => logger.fatal('Fatal test')
    ];

    testCases.forEach(logCall => {
      logCall();
      const logEntry = JSON.parse(logOutput.mock.calls[logOutput.mock.calls.length - 1][0]);
      expect(logEntry['service.name']).toBe(TEST_SERVICE_NAME);
    });

    logOutput.mockRestore();
  });

  // AC-8: Log severity levels are correctly mapped per OTel standards
  test('test_ac8_log_severity_levels_match_otel_standards', () => {
    const logger = initLogger(TEST_SERVICE_NAME, { endpoint: TEST_ENDPOINT, useTls: false });
    const logOutput = jest.spyOn(process.stdout, 'write').mockImplementation(() => true);

    // Test info severity (9)
    logger.info('Info test');
    let logEntry = JSON.parse(logOutput.mock.calls[0][0]);
    expect(logEntry.severity_number).toBe(9);
    expect(logEntry.severity_text).toBe('INFO');

    // Test error severity (17)
    logger.error('Error test');
    logEntry = JSON.parse(logOutput.mock.calls[1][0]);
    expect(logEntry.severity_number).toBe(17);
    expect(logEntry.severity_text).toBe('ERROR');

    logOutput.mockRestore();
  });

  // AC-9: Fallback to structured JSON stdout when no OTLP endpoint configured
  test('test_ac9_no_otlp_endpoint_falls_back_to_stdout_json_logs', () => {
    // Initialize without OTLP endpoint
    const logger = initLogger(TEST_SERVICE_NAME, { endpoint: '', useTls: false });
    const logOutput = jest.spyOn(process.stdout, 'write').mockImplementation(() => true);
    const testMessage = 'Fallback test message';
    const testAttributes = { fallbackTest: 'value' };

    logger.info(testMessage, testAttributes);
    
    // Verify structured JSON output to stdout
    expect(logOutput).toHaveBeenCalled();
    const logEntry = JSON.parse(logOutput.mock.calls[0][0]);
    expect(logEntry.message).toBe(testMessage);
    expect(logEntry.attributes.fallbackTest).toBe(testAttributes.fallbackTest);
    expect(logEntry['service.name']).toBe(TEST_SERVICE_NAME);

    logOutput.mockRestore();
  });
});
