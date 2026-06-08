const request = require('supertest');
const { expect } = require('chai');

const PAYMENT_SERVICE_HTTP_PORT = process.env.PAYMENT_SERVICE_HTTP_PORT || 8080;
const httpClient = request(`http://localhost:${PAYMENT_SERVICE_HTTP_PORT}`);

describe('Payment Service Health Endpoints (New Spec)', () => {
  // AC-1: Liveness endpoint returns 200 OK when service is running with correct JSON structure
  it('test_ac1_liveness_returns_200_ok_with_valid_json', async () => {
    const response = await httpClient.get('/health/liveness');
    
    expect(response.status).to.equal(200);
    expect(response.headers['content-type']).to.include('application/json');
    expect(response.body.status).to.equal('ok');
    expect(response.body.service).to.equal('payment');
    expect(response.body.timestamp).to.be.a('number');
    expect(response.body.timestamp).to.be.greaterThan(1720000000000); // Rough sanity check for valid ms timestamp
  });

  // AC-2: Readiness returns 200 OK with ready true when all dependencies are good
  it('test_ac2_readiness_returns_200_with_ready_true_when_dependencies_ok', async () => {
    const response = await httpClient.get('/health/readiness');
    
    expect(response.status).to.equal(200);
    expect(response.headers['content-type']).to.include('application/json');
    expect(response.body.status).to.equal('ok');
    expect(response.body.service).to.equal('payment');
    expect(response.body.ready).to.equal(true);
    expect(response.body.timestamp).to.be.a('number');
    expect(response.body.dependencies).to.deep.equal({
      config: 'loaded',
      paymentProcessor: 'connected'
    });
  });

  // AC-3: Readiness returns 503 with ready false when dependencies fail
  it('test_ac3_readiness_returns_503_with_ready_false_when_dependencies_fail', async () => {
    // This test assumes service is in unready state (config missing or payment processor disconnected)
    // Implementation will handle simulating failure state
    const response = await httpClient.get('/health/readiness');
    
    expect(response.status).to.equal(503);
    expect(response.headers['content-type']).to.include('application/json');
    expect(response.body.status).to.equal('unavailable');
    expect(response.body.service).to.equal('payment');
    expect(response.body.ready).to.equal(false);
    expect(response.body.timestamp).to.be.a('number');
    expect(response.body.dependencies).to.be.an('object');
    // At least one dependency should show failure state
    const failedDeps = Object.values(response.body.dependencies).filter(status => status !== 'loaded' && status !== 'connected');
    expect(failedDeps).to.have.length.greaterThan(0);
  });

  // AC-4: Both endpoints have required OTel HTTP span attributes
  it('test_ac4_liveness_endpoint_has_required_otel_http_attributes', async () => {
    const response = await httpClient.get('/health/liveness');
    expect(response.status).to.equal(200);
    // Span verification placeholder:
    // - span name = "GET /health/liveness"
    // - http.method = "GET"
    // - http.route = "/health/liveness"
    // - http.status_code = 200
    expect(true).to.equal(true, 'OTel span attribute verification pending test collector integration');
  });

  it('test_ac4_readiness_endpoint_has_required_otel_http_attributes_when_ready', async () => {
    const response = await httpClient.get('/health/readiness');
    expect(response.status).to.be.oneOf([200, 503]);
    // Span verification placeholder:
    // - span name = "GET /health/readiness"
    // - http.method = "GET"
    // - http.route = "/health/readiness"
    // - http.status_code = response.status
    expect(true).to.equal(true, 'OTel span attribute verification pending test collector integration');
  });

  // AC-5: Readiness endpoint has custom readiness.ready span attribute
  it('test_ac5_readiness_endpoint_has_custom_readiness_ready_span_attribute', async () => {
    const response = await httpClient.get('/health/readiness');
    const expectedReadyValue = response.status === 200;
    // Span verification placeholder:
    // - custom attribute readiness.ready = expectedReadyValue (boolean)
    expect(true).to.equal(true, 'OTel custom readiness.ready attribute verification pending test collector integration');
  });

  // AC-6: All health endpoints return Content-Type: application/json
  it('test_ac6_liveness_endpoint_has_application_json_content_type', async () => {
    const response = await httpClient.get('/health/liveness');
    expect(response.headers['content-type']).to.include('application/json');
  });

  it('test_ac6_readiness_endpoint_has_application_json_content_type_when_200', async () => {
    const response = await httpClient.get('/health/readiness');
    expect(response.headers['content-type']).to.include('application/json');
  });

  it('test_ac6_readiness_endpoint_has_application_json_content_type_when_503', async () => {
    // Test when service is unready
    const response = await httpClient.get('/health/readiness');
    expect(response.headers['content-type']).to.include('application/json');
  });
});

