const request = require('supertest');
const express = require('express');
const { createServer } = require('http');
const sinon = require('sinon');

// Import the app (assume it's exported from index.js)
let app;
let server;
let originalChargeImpl;

describe('Health & Readiness Endpoint Tests (AC Compliance)', () => {
  beforeAll(async () => {
    // Import app before tests start
    delete require.cache[require.resolve('../index')];
    const indexModule = require('../index');
    app = indexModule.app;
    
    // Save original implementation to restore later for AC-6
    originalChargeImpl = indexModule.charge;
  });

  afterEach(() => {
    sinon.restore();
  });

  afterAll((done) => {
    if (server) server.close(done);
    else done();
  });

  // AC-1: GET /health returns 200 OK with empty body when service running not in shutdown
  test('test_ac1_health_returns_200_ok_empty_body_when_running', async () => {
    const response = await request(app).get('/health');
    expect(response.statusCode).toBe(200);
    expect(response.text).toBe(''); // Empty body per spec
    expect(Object.keys(response.body)).toHaveLength(0);
  });

  // AC-2: GET /health returns 503 when service is in graceful shutdown
  test('test_ac2_health_returns_503_when_in_shutdown_state', async () => {
    // Simulate shutdown state by mocking the app shutdown flag
    if (app.locals && typeof app.locals.setShutdown === 'function') {
      app.locals.setShutdown(true);
    } else {
      // For the purpose of failing before implementation, we expect this to fail
      expect(true).toBe(false, 'Shutdown state handling not implemented');
    }

    const response = await request(app).get('/health');
    expect(response.statusCode).toBe(503);
    expect(response.text).toBe('');
  });

  // AC-3: GET /ready returns 200 OK empty body when all dependencies are reachable
  test('test_ac3_ready_returns_200_ok_empty_body_when_all_dependencies_up', async () => {
    // Assume all dependencies are healthy in normal startup state
    const response = await request(app).get('/ready');
    expect(response.statusCode).toBe(200);
    expect(response.text).toBe('');
    expect(Object.keys(response.body)).toHaveLength(0);
  });

  // AC-4: GET /ready returns 503 when any required dependency is unreachable
  test('test_ac4_ready_returns_503_when_any_dependency_down', async () => {
    // Mock a broken database connection or upstream API failure
    // First, get the dependency check function if exposed
    if (app.locals && typeof app.locals.simulateDependencyFailure === 'function') {
      app.locals.simulateDependencyFailure(true);
    } else {
      // Expected to fail before implementation
      expect(true).toBe(false, 'Dependency health checking not implemented');
    }

    const response = await request(app).get('/ready');
    expect(response.statusCode).toBe(503);
    expect(response.text).toBe('');
  });

  // AC-5: Both endpoints run on same port as main service API
  test('test_ac5_endpoints_run_on_same_port_as_main_service', async () => {
    // Start server on the main payment port
    const testPort = process.env.PAYMENT_PORT || 3000;
    server = createServer(app);
    
    await new Promise(resolve => server.listen(testPort, resolve));
    
    // Test health on same port
    const healthResp = await request(`http://localhost:${testPort}`).get('/health');
    expect(healthResp.statusCode).toBe(200);
    
    // Test ready on same port
    const readyResp = await request(`http://localhost:${testPort}`).get('/ready');
    expect([200, 503]).toContain(readyResp.statusCode); // Either is acceptable, just that it responds
    
    // Test main API endpoint (e.g. /charge) is also on same port
    const chargeResp = await request(`http://localhost:${testPort}`).post('/charge').send({ amount: 100 });
    expect(chargeResp.statusCode).not.toBe(404); // Should exist on same port
  });

  // AC-6: All existing payment service endpoints behave as before
  test('test_ac6_existing_charge_endpoint_behavior_unchanged', async () => {
    // Test a valid charge request works exactly as before
    const validRequest = {
      amount: 100.00,
      currency: 'USD',
      card: {
        number: '4111-1111-1111-1111',
        expiry: '12/28',
        cvv: '123'
      }
    };

    const response = await request(app).post('/charge').send(validRequest);
    // Expect valid response structure (no breaking changes)
    expect(response.statusCode).toBeOneOf([200, 201]);
    expect(response.body).toHaveProperty('transactionId');
    expect(response.body).toHaveProperty('success', true);
  });

  // AC-7: Health/ready endpoints have same CORS headers as existing endpoints
  test('test_ac7_endpoints_have_same_cors_headers_as_main_api', async () => {
    // Request with Origin header to trigger CORS response
    const testOrigin = 'https://example.com';

    // Get CORS headers from main API endpoint
    const mainResp = await request(app)
      .post('/charge')
      .set('Origin', testOrigin)
      .send({ amount: 100 });

    // Get CORS headers from health endpoint
    const healthResp = await request(app)
      .get('/health')
      .set('Origin', testOrigin);

    // Get CORS headers from ready endpoint
    const readyResp = await request(app)
      .get('/ready')
      .set('Origin', testOrigin);

    // Compare Access-Control-Allow-Origin header
    expect(healthResp.headers['access-control-allow-origin'])
      .toBe(mainResp.headers['access-control-allow-origin']);
    expect(readyResp.headers['access-control-allow-origin'])
      .toBe(mainResp.headers['access-control-allow-origin']);

    // Compare other relevant CORS headers if present
    const corsHeaders = [
      'access-control-allow-methods',
      'access-control-allow-headers',
      'access-control-allow-credentials'
    ];

    corsHeaders.forEach(header => {
      if (mainResp.headers[header]) {
        expect(healthResp.headers[header]).toBe(mainResp.headers[header]);
        expect(readyResp.headers[header]).toBe(mainResp.headers[header]);
      }
    });
  });
});
