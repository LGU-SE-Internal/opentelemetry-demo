'use strict';

const request = require('supertest');
const sinon = require('sinon');
const { expect } = require('chai');
const proxyquire = require('proxyquire');
const { trace } = require('@opentelemetry/api');

describe('Payment Service Health Probes AC Tests', () => {
  let app;
  let chargeStub;
  let loggerErrorStub;
  let tracerStub;
  let startSpanStub;
  let spanStub;

  beforeEach(() => {
    // Reset all stubs before each test
    chargeStub = { charge: sinon.stub() };
    loggerErrorStub = sinon.stub();
    spanStub = {
      setAttribute: sinon.stub(),
      end: sinon.stub(),
      spanContext: sinon.stub().returns({ traceId: 'test-trace-id-123' })
    };
    startSpanStub = sinon.stub().returns(spanStub);
    tracerStub = { startSpan: startSpanStub };
    sinon.stub(trace, 'getTracer').returns(tracerStub);

    // Proxyquire the app to stub dependencies
    app = proxyquire('../index', {
      './charge': chargeStub,
      './logger': { error: loggerErrorStub }
    });
  });

  afterEach(() => {
    sinon.restore();
  });

  /**
   * AC-1: When sending a GET /health/liveness request to a running payment service instance,
   * the service returns a 200 OK status code with a JSON response containing "status": "healthy"
   * and valid service name, timestamp, and checkType fields.
   */
  it('test_ac1_liveness_endpoint_returns_200_ok_with_correct_payload', async () => {
    const response = await request(app).get('/health/liveness');
    
    expect(response.statusCode).to.equal(200);
    expect(response.body.status).to.equal('healthy');
    expect(response.body.service).to.equal('payment-service');
    expect(response.body.checkType).to.equal('liveness');
    expect(response.body.timestamp).to.be.a('string');
    // Verify timestamp is valid ISO 8601 string
    expect(Date.parse(response.body.timestamp)).to.not.be.NaN;
  });

  /**
   * AC-2: When the payment service has all required downstream dependencies reachable,
   * a GET /health/readiness request returns 200 OK status code with a JSON response containing
   * "status": "ready" and a dependencies array showing all dependencies as reachable.
   */
  it('test_ac2_readiness_endpoint_returns_200_ok_when_all_dependencies_reachable', async () => {
    // Stub payment gateway is reachable
    chargeStub.charge.resolves({ success: true });

    const response = await request(app).get('/health/readiness');
    
    expect(response.statusCode).to.equal(200);
    expect(response.body.status).to.equal('ready');
    expect(response.body.service).to.equal('payment-service');
    expect(response.body.checkType).to.equal('readiness');
    expect(response.body.timestamp).to.be.a('string');
    expect(Date.parse(response.body.timestamp)).to.not.be.NaN;
    expect(response.body.dependencies).to.be.an('array');
    
    // Find payment-gateway dependency
    const paymentGatewayDep = response.body.dependencies.find(d => d.name === 'payment-gateway');
    expect(paymentGatewayDep).to.exist;
    expect(paymentGatewayDep.status).to.equal('reachable');
  });

  /**
   * AC-3: When any required downstream dependency of the payment service is unreachable,
   * a GET /health/readiness request returns 503 Service Unavailable status code with a JSON response
   * containing "status": "not_ready" and a dependencies array showing the failed dependency with error details.
   */
  it('test_ac3_readiness_endpoint_returns_503_when_dependency_unreachable', async () => {
    // Stub payment gateway is unreachable
    const testError = new Error('Payment gateway connection timed out');
    chargeStub.charge.rejects(testError);

    const response = await request(app).get('/health/readiness');
    
    expect(response.statusCode).to.equal(503);
    expect(response.body.status).to.equal('not_ready');
    expect(response.body.service).to.equal('payment-service');
    expect(response.body.checkType).to.equal('readiness');
    expect(response.body.timestamp).to.be.a('string');
    expect(response.body.dependencies).to.be.an('array');
    
    // Find payment-gateway dependency with error
    const paymentGatewayDep = response.body.dependencies.find(d => d.name === 'payment-gateway');
    expect(paymentGatewayDep).to.exist;
    expect(paymentGatewayDep.status).to.equal('unreachable');
    expect(paymentGatewayDep.error).to.equal(testError.message);
  });

  /**
   * AC-4: All requests to /health/liveness and /health/readiness endpoints generate OpenTelemetry spans
   * with the required attributes (http.route, health.check_type, http.status_code).
   */
  it('test_ac4_liveness_endpoint_generates_otel_spans_with_required_attributes', async () => {
    await request(app).get('/health/liveness');
    
    expect(startSpanStub.calledOnce).to.be.true;
    expect(spanStub.setAttribute.calledWith('http.route', '/health/liveness')).to.be.true;
    expect(spanStub.setAttribute.calledWith('health.check_type', 'liveness')).to.be.true;
    expect(spanStub.setAttribute.calledWith('http.status_code', 200)).to.be.true;
    expect(spanStub.end.calledOnce).to.be.true;
  });

  it('test_ac4_readiness_endpoint_generates_otel_spans_with_required_attributes', async () => {
    chargeStub.charge.resolves({ success: true });
    await request(app).get('/health/readiness');
    
    expect(startSpanStub.calledOnce).to.be.true;
    expect(spanStub.setAttribute.calledWith('http.route', '/health/readiness')).to.be.true;
    expect(spanStub.setAttribute.calledWith('health.check_type', 'readiness')).to.be.true;
    expect(spanStub.setAttribute.calledWith('http.status_code', 200)).to.be.true;
    expect(spanStub.end.calledOnce).to.be.true;
  });

  it('test_ac4_readiness_endpoint_generates_otel_spans_with_503_status_when_failed', async () => {
    chargeStub.charge.rejects(new Error('Connection failed'));
    await request(app).get('/health/readiness');
    
    expect(startSpanStub.calledOnce).to.be.true;
    expect(spanStub.setAttribute.calledWith('http.route', '/health/readiness')).to.be.true;
    expect(spanStub.setAttribute.calledWith('health.check_type', 'readiness')).to.be.true;
    expect(spanStub.setAttribute.calledWith('http.status_code', 503)).to.be.true;
    expect(spanStub.end.calledOnce).to.be.true;
  });

  /**
   * AC-5: All failed readiness probe checks produce an ERROR level log entry that includes
   * the dependency error details and the associated trace ID.
   */
  it('test_ac5_failed_readiness_logs_error_with_details_and_trace_id', async () => {
    const testError = new Error('Payment gateway unavailable');
    chargeStub.charge.rejects(testError);

    await request(app).get('/health/readiness');
    
    expect(loggerErrorStub.calledOnce).to.be.true;
    const logArgs = loggerErrorStub.firstCall.args;
    expect(logArgs[0]).to.include('Readiness probe failed');
    expect(logArgs[0]).to.include(testError.message);
    expect(logArgs[0]).to.include('test-trace-id-123');
  });

  /**
   * AC-6: Neither health endpoint requires authentication to access.
   */
  it('test_ac6_liveness_endpoint_does_not_require_authentication', async () => {
    // No auth headers sent
    const response = await request(app).get('/health/liveness');
    
    // Should not return 401/403
    expect(response.statusCode).to.not.equal(401);
    expect(response.statusCode).to.not.equal(403);
  });

  it('test_ac6_readiness_endpoint_does_not_require_authentication', async () => {
    chargeStub.charge.resolves({ success: true });
    // No auth headers sent
    const response = await request(app).get('/health/readiness');
    
    // Should not return 401/403
    expect(response.statusCode).to.not.equal(401);
    expect(response.statusCode).to.not.equal(403);
  });
});
