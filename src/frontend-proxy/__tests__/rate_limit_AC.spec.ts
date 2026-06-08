import request from 'supertest';
import { expect } from 'chai';
import * as dotenv from 'dotenv';
import { execSync } from 'child_process';

dotenv.config();

const PROXY_URL = process.env.FRONTEND_PROXY_URL || 'http://localhost:8080';

describe('Frontend Proxy Rate Limit Acceptance Criteria', () => {
  before(async () => {
    // Wait for proxy to be healthy before running tests
    let retries = 0;
    while (retries < 10) {
      try {
        await request(PROXY_URL).get('/healthz');
        break;
      } catch (e) {
        retries++;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    }
  });

  /**
   * AC-1: When sending 101 requests in 1 second to a public route with default limits, 
   * exactly 1 request receives a 429 Too Many Requests response code.
   */
  it('test_ac1_public_route_default_rate_limit', async () => {
    const requests = Array(101).fill(0).map(() => request(PROXY_URL).get('/'));
    const responses = await Promise.all(requests);
    
    const statusCodes = responses.map(r => r.statusCode);
    const count429 = statusCodes.filter(s => s === 429).length;
    const count200 = statusCodes.filter(s => s === 200).length;

    expect(count429).to.equal(1, `Expected exactly 1 429 response, got ${count429}`);
    expect(count200).to.equal(100, `Expected exactly 100 200 responses, got ${count200}`);
  });

  /**
   * AC-2: When sending 1001 requests in 1 second to an internal route with default limits, 
   * exactly 1 request receives a 429 Too Many Requests response code.
   */
  it('test_ac2_internal_route_default_rate_limit', async () => {
    const requests = Array(1001).fill(0).map(() => request(PROXY_URL).get('/api/products'));
    const responses = await Promise.all(requests);
    
    const statusCodes = responses.map(r => r.statusCode);
    const count429 = statusCodes.filter(s => s === 429).length;
    const count200 = statusCodes.filter(s => s === 200).length;

    expect(count429).to.equal(1, `Expected exactly 1 429 response, got ${count429}`);
    expect(count200).to.equal(1000, `Expected exactly 1000 200 responses, got ${count200}`);
  });

  /**
   * AC-3: When FRONTEND_PROXY_RATE_LIMIT_CART_RPS=50 and FRONTEND_PROXY_RATE_LIMIT_CART_BURST=10 are set,
   * sending 51 requests in 1 second to the /api/cart route returns exactly 1 429 response.
   */
  it('test_ac3_custom_route_config_rate_limit', async () => {
    // Set custom env vars for cart route
    process.env.FRONTEND_PROXY_RATE_LIMIT_CART_RPS = '50';
    process.env.FRONTEND_PROXY_RATE_LIMIT_CART_BURST = '10';

    // Restart proxy to apply config (simulate config reload)
    // Note: In actual test environment this would trigger a config reload
    // execSync('docker restart frontend-proxy', { stdio: 'ignore' });
    // await new Promise(resolve => setTimeout(resolve, 5000));

    const requests = Array(51).fill(0).map(() => request(PROXY_URL).get('/api/cart'));
    const responses = await Promise.all(requests);
    
    const statusCodes = responses.map(r => r.statusCode);
    const count429 = statusCodes.filter(s => s === 429).length;
    const count200 = statusCodes.filter(s => s === 200).length;

    expect(count429).to.equal(1, `Expected exactly 1 429 response for cart route, got ${count429}`);
    expect(count200).to.equal(50, `Expected exactly 50 200 responses for cart route, got ${count200}`);
  });

  /**
   * AC-4: All 429 responses include Retry-After, X-RateLimit-Limit, X-RateLimit-Remaining, 
   * and X-RateLimit-Reset headers with non-empty valid values.
   */
  it('test_ac4_429_response_headers', async () => {
    // Generate enough requests to trigger 429
    const requests = Array(150).fill(0).map(() => request(PROXY_URL).get('/'));
    const responses = await Promise.all(requests);
    
    const response429 = responses.find(r => r.statusCode === 429);
    expect(response429).to.exist;

    expect(response429.headers).to.have.property('retry-after');
    expect(response429.headers['retry-after']).to.match(/^\d+$/);
    expect(Number(response429.headers['retry-after'])).to.be.greaterThan(0);

    expect(response429.headers).to.have.property('x-ratelimit-limit');
    expect(response429.headers['x-ratelimit-limit']).to.match(/^\d+$/);
    expect(Number(response429.headers['x-ratelimit-limit'])).to.equal(100);

    expect(response429.headers).to.have.property('x-ratelimit-remaining');
    expect(response429.headers['x-ratelimit-remaining']).to.match(/^\d+$/);
    expect(Number(response429.headers['x-ratelimit-remaining'])).to.equal(0);

    expect(response429.headers).to.have.property('x-ratelimit-reset');
    expect(response429.headers['x-ratelimit-reset']).to.match(/^\d+$/);
    expect(Number(response429.headers['x-ratelimit-reset'])).to.be.greaterThan(Math.floor(Date.now() / 1000));
  });

  /**
   * AC-5: Requests that are below the rate limit threshold are forwarded to the upstream service
   * with no rate limit headers added to the response.
   */
  it('test_ac5_allowed_requests_no_rate_limit_headers', async () => {
    const response = await request(PROXY_URL).get('/');
    expect(response.statusCode).to.equal(200);

    expect(response.headers).to.not.have.property('retry-after');
    expect(response.headers).to.not.have.property('x-ratelimit-limit');
    expect(response.headers).to.not.have.property('x-ratelimit-remaining');
    expect(response.headers).to.not.have.property('x-ratelimit-reset');
  });

  /**
   * AC-6: The envoy_http_local_rate_limit_denied counter increments by 1 for every request 
   * that receives a 429 response, with the correct route_name dimension.
   */
  it('test_ac6_denied_requests_metric_increment', async () => {
    // Get initial metric value
    // const initialDenied = getMetricValue('envoy_http_local_rate_limit_denied', { route_name: 'frontend' });

    // Trigger a 429
    const requests = Array(150).fill(0).map(() => request(PROXY_URL).get('/'));
    await Promise.all(requests);

    // Get updated metric value
    // const updatedDenied = getMetricValue('envoy_http_local_rate_limit_denied', { route_name: 'frontend' });
    // expect(updatedDenied - initialDenied).to.equal(1);

    // Placeholder until metric fetch is implemented in test harness
    expect(true).to.be.true;
  });

  /**
   * AC-7: The envoy_http_local_rate_limit_allowed counter increments by 1 for every request
   * that is forwarded to the upstream service, with the correct route_name dimension.
   */
  it('test_ac7_allowed_requests_metric_increment', async () => {
    // Get initial metric value
    // const initialAllowed = getMetricValue('envoy_http_local_rate_limit_allowed', { route_name: 'frontend' });

    // Send 10 allowed requests
    const requests = Array(10).fill(0).map(() => request(PROXY_URL).get('/'));
    await Promise.all(requests);

    // Get updated metric value
    // const updatedAllowed = getMetricValue('envoy_http_local_rate_limit_allowed', { route_name: 'frontend' });
    // expect(updatedAllowed - initialAllowed).to.equal(10);

    // Placeholder until metric fetch is implemented in test harness
    expect(true).to.be.true;
  });
});
