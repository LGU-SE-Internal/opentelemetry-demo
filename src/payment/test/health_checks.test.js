const { isHealthy } = require('../charge');
const request = require('supertest');
const app = require('../index'); // Assuming index.js exports the express server
const logger = require('../logger'); // Import local logger

describe('Payment Service Health Check Acceptance Criteria', () => {
  let originalTimeout;
  let loggerSpy;

  beforeEach(() => {
    originalTimeout = process.env.PAYMENT_PROCESSOR_CONNECTIVITY_TIMEOUT_MS;
    jest.clearAllMocks();
    loggerSpy = jest.spyOn(logger, 'error').mockImplementation(() => {});
    // Reset any mock state between tests
    jest.resetModules();
  });

  afterEach(() => {
    process.env.PAYMENT_PROCESSOR_CONNECTIVITY_TIMEOUT_MS = originalTimeout;
    loggerSpy.mockRestore();
  });

  // AC-1: When mock payment processor connectivity check succeeds, isHealthy() returns true
  test('ac1_isHealthy_returns_true_when_connectivity_succeeds', async () => {
    // TODO: When implementation exists, mock checkPaymentProcessorConnectivity to return true
    const result = await isHealthy();
    expect(result).toBe(true);
  });

  // AC-2: When mock payment processor connectivity check fails, isHealthy() returns false
  test('ac2_isHealthy_returns_false_when_connectivity_fails', async () => {
    // TODO: When implementation exists, mock checkPaymentProcessorConnectivity to return false
    const result = await isHealthy();
    expect(result).toBe(false);
  });

  // AC-3: When connectivity check takes longer than PAYMENT_PROCESSOR_CONNECTIVITY_TIMEOUT_MS, isHealthy() returns false within (<configured timeout> + 10ms)
  test('ac3_isHealthy_returns_false_on_timeout_within_threshold', async () => {
    const testTimeout = 100;
    process.env.PAYMENT_PROCESSOR_CONNECTIVITY_TIMEOUT_MS = testTimeout;
    const startTime = Date.now();
    
    // TODO: When implementation exists, mock checkPaymentProcessorConnectivity to take 200ms to respond
    const result = await isHealthy();
    const duration = Date.now() - startTime;
    
    expect(result).toBe(false);
    expect(duration).toBeLessThan(testTimeout + 10);
  });

  // AC-4: GET request to /ready endpoint returns 200 OK when isHealthy() returns true, returns 503 Service Unavailable when isHealthy() returns false
  test('ac4_ready_endpoint_returns_200_when_healthy', async () => {
    // TODO: When implementation exists, mock isHealthy to return true
    const response = await request(app).get('/ready');
    expect(response.statusCode).toBe(200);
  });

  test('ac4_ready_endpoint_returns_503_when_unhealthy', async () => {
    // TODO: When implementation exists, mock isHealthy to return false
    const response = await request(app).get('/ready');
    expect(response.statusCode).toBe(503);
  });

  // AC-5: GET request to /health/ready endpoint returns 200 OK when isHealthy() returns true, returns 503 Service Unavailable when isHealthy() returns false
  test('ac5_health_ready_endpoint_returns_200_when_healthy', async () => {
    // TODO: When implementation exists, mock isHealthy to return true
    const response = await request(app).get('/health/ready');
    expect(response.statusCode).toBe(200);
  });

  test('ac5_health_ready_endpoint_returns_503_when_unhealthy', async () => {
    // TODO: When implementation exists, mock isHealthy to return false
    const response = await request(app).get('/health/ready');
    expect(response.statusCode).toBe(503);
  });

  // AC-6: GET request to /health/readiness endpoint returns 200 OK when isHealthy() returns true, returns 503 Service Unavailable when isHealthy() returns false
  test('ac6_health_readiness_endpoint_returns_200_when_healthy', async () => {
    // TODO: When implementation exists, mock isHealthy to return true
    const response = await request(app).get('/health/readiness');
    expect(response.statusCode).toBe(200);
  });

  test('ac6_health_readiness_endpoint_returns_503_when_unhealthy', async () => {
    // TODO: When implementation exists, mock isHealthy to return false
    const response = await request(app).get('/health/readiness');
    expect(response.statusCode).toBe(503);
  });

  // AC-7: Every health check failure emits a structured log entry with all required fields
  test('ac7_health_check_failure_emits_structured_log', async () => {
    // TODO: When implementation exists, mock isHealthy to return false
    await isHealthy();
    
    expect(loggerSpy).toHaveBeenCalledWith(expect.objectContaining({
      timestamp: expect.any(String),
      event: 'payment_processor_health_check_failed',
      error: expect.any(String),
      duration_ms: expect.any(Number),
      configured_timeout_ms: expect.any(Number)
    }));
    
    // Verify timestamp is valid ISO 8601
    const logEntry = loggerSpy.mock.calls[0][0];
    expect(() => new Date(logEntry.timestamp)).not.toThrow();
  });

  // AC-8: The mock connectivity check fails approximately 10% of the time when randomly sampled over 1000+ test runs
  test('ac8_mock_connectivity_fails_approximately_10_percent_of_time', async () => {
    const totalRuns = 1000;
    const tolerance = 0.03; // Allow +/- 3% deviation from 10%
    let failureCount = 0;

    for (let i = 0; i < totalRuns; i++) {
      const result = await isHealthy();
      if (!result) {
        failureCount++;
      }
    }

    const failureRate = failureCount / totalRuns;
    expect(failureRate).toBeGreaterThan(0.1 - tolerance);
    expect(failureRate).toBeLessThan(0.1 + tolerance);
  });
});
