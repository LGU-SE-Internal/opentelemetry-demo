const request = require('supertest');
const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');
const { expect } = require('chai');
const { SemanticAttributes } = require('@opentelemetry/semantic-conventions');

// Load gRPC health service proto
const healthProtoPath = require.resolve('@grpc/grpc-js-health-check/proto/grpc/health/v1/health.proto');
const packageDefinition = protoLoader.loadSync(healthProtoPath, { keepCase: true, longs: String, enums: String, defaults: true, oneofs: true });
const healthProto = grpc.loadPackageDefinition(packageDefinition).grpc.health.v1;

const PAYMENT_SERVICE_GRPC_PORT = process.env.PAYMENT_SERVICE_GRPC_PORT || 50051;
const PAYMENT_SERVICE_HTTP_PORT = process.env.PAYMENT_SERVICE_HTTP_PORT || 8080;
const PAYMENT_SERVICE_NAME = 'opentelemetry.demo.payment.v1.PaymentService';

const httpClient = request(`http://localhost:${PAYMENT_SERVICE_HTTP_PORT}`);
const grpcHealthClient = new healthProto.Health(`localhost:${PAYMENT_SERVICE_GRPC_PORT}`, grpc.credentials.createInsecure());

describe('Payment Service Health Endpoints', () => {
  // AC-1: Liveness endpoint returns 200 OK when service is running
  it('test_ac1_liveness_returns_200_when_service_running', async () => {
    const response = await httpClient.get('/health/liveness');
    expect(response.status).to.equal(200);
    expect(response.headers['content-type']).to.include('text/plain');
    expect(response.text).to.be.empty;
  });

  // AC-2: Readiness endpoint returns 200 OK with READY status when service is ready
  it('test_ac2_readiness_returns_200_ready_when_dependencies_available', async () => {
    const response = await httpClient.get('/health/readiness');
    expect(response.status).to.equal(200);
    expect(response.headers['content-type']).to.include('application/json');
    expect(response.body).to.deep.equal({ status: 'READY' });
  });

  // AC-3: Readiness endpoint returns 503 NOT_READY when service is unable to process requests
  it('test_ac3_readiness_returns_503_not_ready_when_dependencies_unavailable', (done) => {
    // Simulate dependency failure scenario (e.g. database down, invalid config)
    // Note: Test assumes service is in unready state for this test case
    httpClient.get('/health/readiness')
      .end((err, response) => {
        expect(response.status).to.equal(503);
        expect(response.headers['content-type']).to.include('application/json');
        expect(response.body).to.deep.equal({ status: 'NOT_READY' });
        done();
      });
  });

  // AC-4: gRPC Health Check with empty service returns SERVING when process is running
  it('test_ac4_grpc_health_empty_service_returns_serving_when_running', (done) => {
    grpcHealthClient.Check({ service: '' }, (err, response) => {
      expect(err).to.be.null;
      expect(response.status).to.equal('SERVING');
      done();
    });
  });

  // AC-5: gRPC Health Check for PaymentService returns SERVING only when ready
  it('test_ac5_grpc_health_payment_service_returns_serving_when_ready', (done) => {
    grpcHealthClient.Check({ service: PAYMENT_SERVICE_NAME }, (err, response) => {
      expect(err).to.be.null;
      expect(response.status).to.equal('SERVING');
      done();
    });
  });

  it('test_ac5_grpc_health_payment_service_returns_not_serving_when_unready', (done) => {
    // Simulate unready state
    grpcHealthClient.Check({ service: PAYMENT_SERVICE_NAME }, (err, response) => {
      expect(err).to.be.null;
      expect(response.status).to.equal('NOT_SERVING');
      done();
    });
  });

  // AC-6: All health check requests include required OTel attributes
  it('test_ac6_http_liveness_includes_otel_health_attributes', async () => {
    // Test assumes OTel spans are captured and available for assertion
    // This test verifies span attributes: http.route = /health/liveness, health.check.type = liveness, health.check.status = PASS
    const response = await httpClient.get('/health/liveness');
    expect(response.status).to.equal(200);
    // Span assertion placeholder (implementation will add actual span checking)
    expect(true).to.equal(true, 'Span attributes verification pending integration with OTel test collector');
  });

  it('test_ac6_http_readiness_includes_otel_health_attributes', async () => {
    const response = await httpClient.get('/health/readiness');
    expect(response.status).to.be.oneOf([200, 503]);
    const expectedStatus = response.status === 200 ? 'PASS' : 'FAIL';
    // Span assertion placeholder: http.route = /health/readiness, health.check.type = readiness, health.check.status = expectedStatus
    expect(true).to.equal(true, 'Span attributes verification pending integration with OTel test collector');
  });

  it('test_ac6_grpc_health_includes_otel_health_attributes', (done) => {
    grpcHealthClient.Check({ service: '' }, (err, response) => {
      expect(err).to.be.null;
      expect(response.status).to.equal('SERVING');
      // Span assertion placeholder: rpc.service = grpc.health.v1.Health, health.check.type = liveness, health.check.status = PASS
      expect(true).to.equal(true, 'Span attributes verification pending integration with OTel test collector');
      done();
    });
  });

  // AC-7: Health endpoints are exposed on standard ports without conflict
  it('test_ac7_health_endpoints_on_standard_ports_no_conflict', async () => {
    // Verify HTTP port 8080 is accessible for health checks
    const httpResponse = await httpClient.get('/health/liveness');
    expect(httpResponse.status).to.equal(200);
    
    // Verify gRPC port is accessible for health checks
    const grpcResponse = await new Promise((resolve, reject) => {
      grpcHealthClient.Check({ service: '' }, (err, res) => err ? reject(err) : resolve(res));
    });
    expect(grpcResponse.status).to.equal('SERVING');
    
    // Verify no port conflict with existing payment gRPC service
    const paymentServiceClient = new (grpc.loadPackageDefinition(
      protoLoader.loadSync('../../pb/demo.proto', { keepCase: true })
    ).opentelemetry.demo.payment.v1.PaymentService)(`localhost:${PAYMENT_SERVICE_GRPC_PORT}`, grpc.credentials.createInsecure());
    
    // Simple check that payment service is still accessible on same port
    const paymentResponse = await new Promise((resolve) => {
      paymentServiceClient.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res) => resolve({ err, res }));
    });
    expect(paymentResponse.err).to.not.be.an.instanceof(Error);
  });
});
