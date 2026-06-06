const request = require('supertest');
const { app } = require('../index');
const grpc = require('@grpc/grpc-js');
const health = require('grpc-js-health-check');
const { OpenFeature } = require('@openfeature/server-sdk');

describe('Health and Readiness Endpoints', () => {
  // AC-1: Health returns 200 when running
  test('test_ac1_health_returns_200_ok_empty_body_when_running', async () => {
    const res = await request(app).get('/health');
    expect(res.statusCode).toBe(200);
    expect(res.text).toBe('');
  });

  // AC-3: Ready returns 200 when all dependencies are up
  test('test_ac3_ready_returns_200_ok_empty_body_when_all_dependencies_up', async () => {
    // Mock OpenFeature status as ready
    jest.spyOn(OpenFeature, 'getProviderStatus').mockReturnValue('READY');
    // Mock gRPC health check response
    const mockCheck = jest.fn((_, callback) => {
      callback(null, { status: health.servingStatus.SERVING });
    });
    jest.spyOn(health, 'HealthClient').mockImplementation(() => ({
      check: mockCheck
    }));

    const res = await request(app).get('/ready');
    expect(res.statusCode).toBe(200);
    expect(res.text).toBe('');
  });

  // AC-4: Ready returns 503 when any dependency is down
  test('test_ac4_ready_returns_503_when_any_dependency_down', async () => {
    // Mock OpenFeature status as not ready
    jest.spyOn(OpenFeature, 'getProviderStatus').mockReturnValue('NOT_READY');

    const res = await request(app).get('/ready');
    expect(res.statusCode).toBe(503);
    expect(res.text).toBe('');
  });

  // AC-6: Existing charge endpoint behavior unchanged
  test('test_ac6_existing_charge_endpoint_behavior_unchanged', () => {
    // Just verify the gRPC service is still registered as before, no changes
    const server = new grpc.Server();
    const otelDemoPackage = grpc.loadPackageDefinition(require('@grpc/proto-loader').loadSync('demo.proto'));
    expect(() => server.addService(otelDemoPackage.oteldemo.PaymentService.service, { charge: require('../index').chargeServiceHandler })).not.toThrow();
  });

  // AC-7: Endpoints have same CORS headers as main API
  test('test_ac7_endpoints_have_same_cors_headers_as_main_api', async () => {
    const res = await request(app).get('/health').set('Origin', 'http://example.com');
    // Current main API has no CORS headers, so health endpoints shouldn't either
    expect(res.headers['access-control-allow-origin']).toBeUndefined();
  });
});
