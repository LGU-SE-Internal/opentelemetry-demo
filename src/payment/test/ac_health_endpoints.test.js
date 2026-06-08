const request = require('supertest');
const { expect } = require('chai');
const net = require('net');

const PAYMENT_SERVICE_HTTP_PORT = process.env.PAYMENT_SERVICE_HTTP_PORT || 8080;
const httpClient = request(`http://localhost:${PAYMENT_SERVICE_HTTP_PORT}`);

describe('Payment Service Health Endpoints AC Tests', () => {
  // AC-1: When the payment service process is running, a GET request to `/health` on the main service port returns HTTP status code 200 OK.
  it('test_ac1_health_returns_200_when_service_running', async () => {
    const response = await httpClient.get('/health');
    expect(response.status).to.equal(200);
    // Accept either plain text OK or JSON {status: "UP"}
    if (response.headers['content-type']?.includes('application/json')) {
      expect(response.body).to.have.property('status', 'UP');
    } else {
      expect(response.text.trim()).to.equal('OK');
    }
  });

  // AC-2: When the payment service process is running and all required downstream dependencies are reachable, GET /ready returns 200 OK.
  it('test_ac2_ready_returns_200_when_all_dependencies_available', async () => {
    const response = await httpClient.get('/ready');
    expect(response.status).to.equal(200);
    if (response.headers['content-type']?.includes('application/json')) {
      expect(response.body).to.have.property('status', 'READY');
      expect(response.body).to.have.property('dependencies').that.is.an('array');
      expect(response.body.dependencies.some(dep => dep.includes('currency-service: connected'))).to.be.true;
    }
  });

  // AC-3: When any required downstream dependency is unreachable, GET /ready returns 503 Service Unavailable.
  it('test_ac3_ready_returns_503_when_dependency_unavailable', (done) => {
    // Test assumes dependencies are down for this case
    httpClient.get('/ready')
      .end((err, response) => {
        expect(response.status).to.equal(503);
        if (response.headers['content-type']?.includes('application/json')) {
          expect(response.body).to.have.property('status', 'NOT_READY');
          expect(response.body).to.have.property('errors').that.is.an('array');
          expect(response.body.errors.some(err => err.includes('currency-service: unreachable'))).to.be.true;
        }
        done();
      });
  });

  // AC-4: The TCP port used to access /health and /ready endpoints is identical to the port used for all existing payment service API endpoints.
  it('test_ac4_health_and_ready_same_port_as_main_api', async () => {
    // Verify health endpoint on main port works
    const healthRes = await httpClient.get('/health');
    expect(healthRes.status).to.equal(200);

    // Verify main API endpoint (charge) works on same port
    const chargeRes = await httpClient.post('/charge')
      .send({
        amount: { currency_code: 'USD', units: 10, nanos: 0 },
        credit_card: {
          credit_card_number: '4111111111111111',
          credit_card_expiration_month: 12,
          credit_card_expiration_year: 2030,
          credit_card_cvv: '123'
        }
      });
    expect(chargeRes.status).to.be.oneOf([200, 400, 429]); // Accept valid responses from existing endpoint
  });

  // AC-5: When the payment service process is not running, requests to both /health and /ready endpoints result in a connection refused error.
  it('test_ac5_connection_refused_when_service_not_running', async () => {
    // First stop the service temporarily for this test, or simulate closed port
    const socket = new net.Socket();
    socket.setTimeout(1000);

    const connectionRefused = await new Promise((resolve) => {
      socket.on('error', (err) => {
        if (err.code === 'ECONNREFUSED') resolve(true);
        else resolve(false);
      });
      socket.on('connect', () => {
        socket.destroy();
        resolve(false);
      });
      socket.connect(PAYMENT_SERVICE_HTTP_PORT, 'localhost');
    });

    expect(connectionRefused).to.be.true;
  });
});
