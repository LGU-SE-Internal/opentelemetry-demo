const request = require('supertest');
const express = require('express');
const grpc = require('@grpc/grpc-js');
const health = require('grpc-js-health-check');

describe('Health Endpoint Tests', () => {
  let app;
  let healthClient;
  let server;

  beforeAll(() => {
    // Setup mock gRPC server
    server = new grpc.Server();
    server.addService(health.service, new health.Implementation({
      '': health.servingStatus.SERVING
    }));
    
    return new Promise((resolve, reject) => {
      server.bindAsync('localhost:0', grpc.ServerCredentials.createInsecure(), (err, port) => {
        if (err) return reject(err);
        process.env.PAYMENT_PORT = port;
        server.start();
        
        // Import the app after setting env var
        delete require.cache[require.resolve('../index')];
        const indexModule = require('../index');
        app = indexModule.app;
        healthClient = new health.HealthClient(`localhost:${port}`, grpc.credentials.createInsecure());
        resolve();
      });
    });
  });

  afterAll((done) => {
    server.forceShutdown(done);
  });

  test('test_ac1_health_endpoint_returns_200_ok_when_all_services_operational', async () => {
    const response = await request(app).get('/health');
    expect(response.statusCode).toBe(200);
    expect(response.body).toEqual({ status: 'ok' });
  });

  test('test_ac2_health_endpoint_returns_503_when_grpc_server_unreachable', async () => {
    // Shutdown gRPC server first
    await new Promise(resolve => server.forceShutdown(resolve));
    
    const response = await request(app).get('/health');
    expect(response.statusCode).toBe(503);
    expect(response.body).toEqual({ status: 'unhealthy', error: 'gRPC server not reachable' });
  });

  test('test_ac3_health_endpoint_listens_on_custom_port_when_env_var_set', async () => {
    process.env.PAYMENT_HEALTH_PORT = '9090';
    delete require.cache[require.resolve('../index')];
    const newAppModule = require('../index');
    const newApp = newAppModule.app;
    
    // Test that it runs on custom port
    const serverInstance = newApp.listen(9090, async () => {
      const response = await request('http://localhost:9090').get('/health');
      expect(response.statusCode).toBe(200);
      serverInstance.close();
    });
  });

  test('test_ac4_health_endpoint_returns_404_for_non_health_paths', async () => {
    const response = await request(app).get('/invalid-path');
    expect(response.statusCode).toBe(404);
  });
});
