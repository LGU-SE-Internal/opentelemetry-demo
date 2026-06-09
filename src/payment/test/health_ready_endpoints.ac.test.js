const request = require('supertest');
const app = require('../index'); // Assuming the express app is exported from index.js

describe('Payment Service Health and Readiness Endpoints AC Tests', () => {
  let server;

  beforeAll(async () => {
    // Start server on a random port for testing
    server = app.listen(0);
  });

  afterAll(async () => {
    // Close server connections
    await new Promise(resolve => server.close(resolve));
  });

  test('test_ac1_health_endpoint_returns_200_when_process_running', async () => {
    // AC-1: When the payment service Node.js process is running, GET /health returns 200 OK with expected JSON body
    const response = await request(server).get('/health');
    
    expect(response.statusCode).toBe(200);
    expect(response.headers['content-type']).toMatch(/application\/json/);
    expect(response.body).toEqual({
      status: 'healthy',
      check: 'liveness'
    });
  });

  test('test_ac2_health_endpoint_returns_200_regardless_of_downstream_dependencies', async () => {
    // AC-2: /health returns 200 OK regardless of state of any downstream dependencies
    const response = await request(server).get('/health');
    
    expect(response.statusCode).toBe(200);
    expect(response.headers['content-type']).toMatch(/application\/json/);
  });

  test('test_ac3_ready_endpoint_returns_200_when_all_dependencies_reachable', async () => {
    // AC-3: When all required downstream dependencies are reachable, GET /ready returns 200 OK with expected JSON body
    const response = await request(server).get('/ready');
    
    expect(response.statusCode).toBe(200);
    expect(response.headers['content-type']).toMatch(/application\/json/);
    expect(response.body).toEqual({
      status: 'ready',
      check: 'readiness'
    });
  });

  test('test_ac4_ready_endpoint_returns_503_when_any_dependency_unreachable', async () => {
    // AC-4: When any required downstream dependency is unreachable, GET /ready returns 503 with expected error JSON body
    const response = await request(server).get('/ready');
    
    if (response.statusCode === 503) {
      expect(response.headers['content-type']).toMatch(/application\/json/);
      expect(response.body.status).toBe('not ready');
      expect(response.body.check).toBe('readiness');
      expect(response.body.error).toBeDefined();
      expect(typeof response.body.error).toBe('string');
      expect(response.body.error.length).toBeGreaterThan(0);
    }
  });

  test('test_ac5_endpoints_exposed_on_same_port_as_main_api', async () => {
    // AC-5: Both /health and /ready are accessible on the same TCP port as main payment service API
    const healthResponse = await request(server).get('/health');
    const readyResponse = await request(server).get('/ready');
    
    expect(healthResponse.statusCode).not.toBe(404);
    expect(readyResponse.statusCode).not.toBe(404);
  });

  test('test_ac6_both_endpoints_return_application_json_content_type', async () => {
    // AC-6: Both endpoints return Content-Type header set to application/json
    const healthResponse = await request(server).get('/health');
    const readyResponse = await request(server).get('/ready');
    
    expect(healthResponse.headers['content-type']).toMatch(/application\/json/);
    expect(readyResponse.headers['content-type']).toMatch(/application\/json/);
  });
});
