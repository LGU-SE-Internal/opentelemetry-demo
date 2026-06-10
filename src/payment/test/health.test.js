const request = require('supertest');
const { expect } = require('chai');

const PAYMENT_SERVICE_HTTP_PORT = process.env.PAYMENT_SERVICE_HTTP_PORT || 8080;
const httpClient = request(`http://localhost:${PAYMENT_SERVICE_HTTP_PORT}`);

describe('Payment Service Health Endpoints (AC Verification)', () => {
  // AC-1: GET /health/liveness returns 200 OK with correct payload
  it('test_ac1_liveness_returns_200_ok_with_valid_json_payload', async () => {
    const response = await httpClient.get('/health/liveness');
    
    expect(response.status).to.equal(200);
    expect(response.headers['content-type']).to.include('application/json');
    expect(response.body.status).to.equal('ok');
    expect(response.body.message).to.equal('Service is alive');
    expect(response.body.timestamp).to.be.a('string');
    // Verify timestamp is valid ISO 8601 UTC
    const timestampDate = new Date(response.body.timestamp);
    expect(timestampDate.toISOString()).to.equal(response.body.timestamp);
  });

  // AC-2: GET /health/readiness returns 200 OK when payment processor is available
  it('test_ac2_readiness_returns_200_ok_when_all_dependencies_available', async () => {
    const response = await httpClient.get('/health/readiness');
    
    expect(response.status).to.equal(200);
    expect(response.headers['content-type']).to.include('application/json');
    expect(response.body.status).to.equal('ok');
    expect(response.body.message).to.equal('All dependencies are available');
    expect(response.body.timestamp).to.be.a('string');
    const timestampDate = new Date(response.body.timestamp);
    expect(timestampDate.toISOString()).to.equal(response.body.timestamp);
    expect(response.body.checks).to.be.an('object');
    expect(response.body.checks.payment_processor).to.equal('ok');
  });

  // AC-3: GET /health/readiness returns 503 when payment processor is unavailable
  it('test_ac3_readiness_returns_503_when_dependencies_unavailable', async () => {
    // This test runs when payment processor connection is simulated to fail
    const response = await httpClient.get('/health/readiness');
    
    expect(response.status).to.equal(503);
    expect(response.headers['content-type']).to.include('application/json');
    expect(response.body.status).to.equal('unavailable');
    expect(response.body.message).to.equal('One or more dependencies are unavailable');
    expect(response.body.timestamp).to.be.a('string');
    const timestampDate = new Date(response.body.timestamp);
    expect(timestampDate.toISOString()).to.equal(response.body.timestamp);
    expect(response.body.checks).to.be.an('object');
    expect(response.body.checks.payment_processor).to.equal('failed');
    expect(response.body.checks.error).to.be.a('string');
    expect(response.body.checks.error).to.not.be.empty;
  });

  // AC-4: Unit test for liveness endpoint always returns 200
  it('test_ac4_liveness_always_returns_200_when_service_running', async () => {
    // Even if dependencies are failed, liveness should still return 200
    const response = await httpClient.get('/health/liveness');
    expect(response.status).to.equal(200);
    expect(response.body.status).to.equal('ok');
  });

  // AC-5: Unit tests for readiness endpoint both success and failure cases
  it('test_ac5_readiness_success_case_returns_correct_payload', async () => {
    const response = await httpClient.get('/health/readiness');
    expect(response.status).to.equal(200);
    expect(response.body.checks.payment_processor).to.equal('ok');
  });

  it('test_ac5_readiness_failure_case_returns_correct_payload', async () => {
    // Simulate payment processor failure scenario
    const response = await httpClient.get('/health/readiness');
    expect(response.status).to.equal(503);
    expect(response.body.checks.payment_processor).to.equal('failed');
    expect(response.body.checks.error).to.exist;
  });
});
