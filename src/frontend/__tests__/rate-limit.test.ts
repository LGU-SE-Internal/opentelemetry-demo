import request from 'supertest';
import http from 'http';
import next from 'next';

const dev = process.env.NODE_ENV !== 'production';
const app = next({ dev });
const handle = app.getRequestHandler();

let server: http.Server;

describe('Frontend API Rate Limiting', () => {
  beforeAll(async () => {
    await app.prepare();
    server = http.createServer((req, res) => {
      handle(req, res);
    }).listen(3000);
  });

  afterAll((done) => {
    server.close(done);
  });

  beforeEach(() => {
    // Reset environment variables for each test
    delete process.env.FRONTEND_RATE_LIMIT_MAX_REQUESTS;
    delete process.env.FRONTEND_RATE_LIMIT_WINDOW_SECONDS;
  });

  /**
   * AC-1: When a client sends more than 100 requests to any public frontend API endpoint within a 60 second window from the same IP address,
   * the 101st request returns a 429 status code with a valid Retry-After header.
   */
  test('test_ac1_rate_limit_exceeds_100_requests_returns_429', async () => {
    const testEndpoint = '/api/products'; // Any public endpoint
    
    // Send 100 successful requests
    for (let i = 0; i < 100; i++) {
      const res = await request(server).get(testEndpoint).set('X-Forwarded-For', '192.168.1.1');
      expect(res.statusCode).not.toBe(429);
    }

    // 101st request should return 429
    const res = await request(server).get(testEndpoint).set('X-Forwarded-For', '192.168.1.1');
    expect(res.statusCode).toBe(429);
    expect(res.headers['retry-after']).toBeDefined();
    expect(Number(res.headers['retry-after'])).toBeGreaterThan(0);
    expect(Number(res.headers['retry-after'])).toBeLessThanOrEqual(60);
  });

  /**
   * AC-2: When the environment variable FRONTEND_RATE_LIMIT_MAX_REQUESTS is set to 200,
   * the rate limit is adjusted to allow 200 requests per window instead of the default 100.
   */
  test('test_ac2_max_requests_env_var_adjusts_limit', async () => {
    process.env.FRONTEND_RATE_LIMIT_MAX_REQUESTS = '200';
    const testEndpoint = '/api/products';
    
    // Send 200 successful requests
    for (let i = 0; i < 200; i++) {
      const res = await request(server).get(testEndpoint).set('X-Forwarded-For', '192.168.1.2');
      expect(res.statusCode).not.toBe(429);
    }

    // 201st request should return 429
    const res = await request(server).get(testEndpoint).set('X-Forwarded-For', '192.168.1.2');
    expect(res.statusCode).toBe(429);
  });

  /**
   * AC-3: When the environment variable FRONTEND_RATE_LIMIT_WINDOW_SECONDS is set to 300,
   * the rate limit window is adjusted to 5 minutes instead of the default 60 seconds.
   */
  test('test_ac3_window_seconds_env_var_adjusts_window_length', async () => {
    process.env.FRONTEND_RATE_LIMIT_WINDOW_SECONDS = '300';
    const testEndpoint = '/api/products';
    
    // Send 100 successful requests
    for (let i = 0; i < 100; i++) {
      const res = await request(server).get(testEndpoint).set('X-Forwarded-For', '192.168.1.3');
      expect(res.statusCode).not.toBe(429);
    }

    // 101st request returns 429 with retry-after up to 300s
    const res = await request(server).get(testEndpoint).set('X-Forwarded-For', '192.168.1.3');
    expect(res.statusCode).toBe(429);
    expect(Number(res.headers['retry-after'])).toBeLessThanOrEqual(300);
  });

  /**
   * AC-4: All 429 responses include the standard RateLimit-* headers (Limit, Remaining, Reset) in the response.
   */
  test('test_ac4_429_responses_include_standard_ratelimit_headers', async () => {
    const testEndpoint = '/api/products';
    
    // Exhaust rate limit
    for (let i = 0; i < 100; i++) {
      await request(server).get(testEndpoint).set('X-Forwarded-For', '192.168.1.4');
    }

    const res = await request(server).get(testEndpoint).set('X-Forwarded-For', '192.168.1.4');
    expect(res.statusCode).toBe(429);
    expect(res.headers['ratelimit-limit']).toBeDefined();
    expect(res.headers['ratelimit-remaining']).toBeDefined();
    expect(res.headers['ratelimit-reset']).toBeDefined();
    expect(Number(res.headers['ratelimit-limit'])).toBe(100);
    expect(Number(res.headers['ratelimit-remaining'])).toBe(0);
    expect(Number(res.headers['ratelimit-reset'])).toBeGreaterThan(Math.floor(Date.now() / 1000));
  });

  /**
   * AC-5: All rate limit violation events are logged with structured JSON output containing at minimum:
   * timestamp, client IP address, requested endpoint, rate limit threshold, window length, retry after value.
   */
  test('test_ac5_rate_limit_violations_log_structured_data', async () => {
    const testEndpoint = '/api/products';
    const testIp = '192.168.1.5';
    
    // Mock console.log to capture logs
    const consoleLogSpy = jest.spyOn(console, 'log').mockImplementation();

    // Exhaust rate limit
    for (let i = 0; i < 100; i++) {
      await request(server).get(testEndpoint).set('X-Forwarded-For', testIp);
    }
    const res = await request(server).get(testEndpoint).set('X-Forwarded-For', testIp);
    expect(res.statusCode).toBe(429);

    // Check for structured log entry
    const rateLimitLogs = consoleLogSpy.mock.calls
      .map(call => {
        try { return JSON.parse(call[0]); }
        catch { return null; }
      })
      .filter(log => log?.event === 'rate_limit_violation');

    expect(rateLimitLogs.length).toBeGreaterThan(0);
    const violationLog = rateLimitLogs[0];
    expect(violationLog.timestamp).toBeDefined();
    expect(violationLog.client_ip).toBe(testIp);
    expect(violationLog.endpoint).toBe(testEndpoint);
    expect(violationLog.rate_limit_threshold).toBe(100);
    expect(violationLog.window_length_seconds).toBe(60);
    expect(violationLog.retry_after_seconds).toBe(Number(res.headers['retry-after']));

    consoleLogSpy.mockRestore();
  });

  /**
   * AC-6: Internal frontend API endpoints (prefixed with /api/internal/) are not subject to rate limiting.
   */
  test('test_ac6_internal_api_endpoints_not_rate_limited', async () => {
    const internalEndpoint = '/api/internal/config';
    
    // Send 200 requests, all should succeed
    for (let i = 0; i < 200; i++) {
      const res = await request(server).get(internalEndpoint).set('X-Forwarded-For', '192.168.1.6');
      expect(res.statusCode).not.toBe(429);
    }
  });

  /**
   * AC-7: Requests from different IP addresses are counted independently and do not affect each other's rate limits.
   */
  test('test_ac7_rate_limits_are_per_ip', async () => {
    const testEndpoint = '/api/products';
    const ip1 = '192.168.1.7';
    const ip2 = '192.168.1.8';

    // Exhaust limit for ip1
    for (let i = 0; i < 100; i++) {
      await request(server).get(testEndpoint).set('X-Forwarded-For', ip1);
    }
    const resIp1 = await request(server).get(testEndpoint).set('X-Forwarded-For', ip1);
    expect(resIp1.statusCode).toBe(429);

    // First request from ip2 should succeed
    const resIp2 = await request(server).get(testEndpoint).set('X-Forwarded-For', ip2);
    expect(resIp2.statusCode).not.toBe(429);
  });
});
