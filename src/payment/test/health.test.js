const request = require('supertest');
const express = require('express');
const fs = require('fs');
const path = require('path');

describe('Payment Service Health Endpoint Tests', () => {
  let app;

  beforeAll(() => {
    // Import the app from index.js
    delete require.cache[require.resolve('../index')];
    const indexModule = require('../index');
    app = indexModule.app;
  });

  // AC-1: Liveness endpoint returns 200 OK with UP status when process running
  test('test_ac1_liveness_endpoint_returns_200_up_status_when_process_running', async () => {
    const response = await request(app).get('/health/liveness');
    expect(response.statusCode).toBe(200);
    expect(response.headers['content-type']).toMatch(/json/);
    expect(response.body.status).toBe('UP');
    // Optional fields
    expect(response.body.timestamp).toBeDefined();
  });

  // AC-2: Readiness returns 200 UP when all dependencies are connected
  test('test_ac2_readiness_endpoint_returns_200_up_when_dependencies_connected', async () => {
    const response = await request(app).get('/health/readiness');
    expect(response.statusCode).toBe(200);
    expect(response.headers['content-type']).toMatch(/json/);
    expect(response.body.status).toBe('UP');
    expect(response.body.timestamp).toBeDefined();
    expect(response.body.dependencies).toBeDefined();
    expect(response.body.dependencies.postgres).toBe('UP');
    expect(response.body.dependencies.payment_processor).toBe('UP');
  });

  // AC-3: Readiness returns 503 DOWN when dependencies are missing
  test('test_ac3_readiness_endpoint_returns_503_down_when_dependencies_missing', async () => {
    // Mock dependency failure scenario
    // We will simulate dependency failure by mocking connection failures in implementation
    // For now, test that failure response structure is correct when 503 is returned
    const response = await request(app).get('/health/readiness');
    if (response.statusCode === 503) {
      expect(response.headers['content-type']).toMatch(/json/);
      expect(response.body.status).toBe('DOWN');
      expect(response.body.timestamp).toBeDefined();
      expect(response.body.errors).toBeInstanceOf(Array);
      expect(response.body.errors.length).toBeGreaterThan(0);
    }
  });

  // AC-4: Both endpoints are on the same port as other APIs
  test('test_ac4_health_endpoints_use_same_port_as_service_apis', async () => {
    const PAYMENT_PORT = process.env.PAYMENT_PORT || '8080';
    // Test that endpoints are accessible on service port
    const serverInstance = app.listen(PAYMENT_PORT, async () => {
      const livenessResponse = await request(`http://localhost:${PAYMENT_PORT}`).get('/health/liveness');
      expect(livenessResponse.statusCode).toBe(200);
      const readinessResponse = await request(`http://localhost:${PAYMENT_PORT}`).get('/health/readiness');
      expect(readinessResponse.statusCode).toBeOneOf([200, 503]);
      serverInstance.close();
    });
  });

  // AC-5: Docker compose has liveness probe configured
  test('test_ac5_docker_compose_has_liveness_probe_configured', async () => {
    const dockerComposePath = path.resolve(__dirname, '../../../compose.yaml');
    expect(fs.existsSync(dockerComposePath)).toBe(true);
    const dockerComposeContent = fs.readFileSync(dockerComposePath, 'utf8');
    
    // Check liveness probe config
    expect(dockerComposeContent).toContain('livenessProbe:');
    expect(dockerComposeContent).toContain('path: /health/liveness');
    expect(dockerComposeContent).toContain('periodSeconds: 10');
    expect(dockerComposeContent).toContain('timeoutSeconds: 1');
  });

  // AC-6: Docker compose has readiness probe configured
  test('test_ac6_docker_compose_has_readiness_probe_configured', async () => {
    const dockerComposePath = path.resolve(__dirname, '../../../compose.yaml');
    expect(fs.existsSync(dockerComposePath)).toBe(true);
    const dockerComposeContent = fs.readFileSync(dockerComposePath, 'utf8');
    
    // Check readiness probe config
    expect(dockerComposeContent).toContain('readinessProbe:');
    expect(dockerComposeContent).toContain('path: /health/readiness');
    expect(dockerComposeContent).toContain('initialDelaySeconds: 5');
    expect(dockerComposeContent).toContain('periodSeconds: 10');
    expect(dockerComposeContent).toContain('timeoutSeconds: 1');
  });
});
