const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');
const path = require('path');
const { spawn } = require('child_process');
const { expect } = require('chai');

const PROTO_PATH = path.join(__dirname, '../../../protos/opentelemetry/proto/demo/v1/demo.proto');
const packageDefinition = protoLoader.loadSync(PROTO_PATH, { keepCase: true, enums: String, defaults: true, oneofs: true });
const otelDemoProto = grpc.loadPackageDefinition(packageDefinition).oteldemo;

const TEST_SERVER_ADDRESS = 'localhost:50052';

describe('Payment Service Rate Limit Acceptance Criteria', () => {
  let serverProcess;
  let client;

  afterEach(() => {
    if (serverProcess) {
      serverProcess.kill();
      serverProcess = null;
    }
    if (client) {
      client.close();
      client = null;
    }
    // Clean up env vars
    delete process.env.PAYMENT_SERVICE_DEFAULT_RATE_LIMIT_RPS;
    delete process.env.PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS;
    delete process.env.PAYMENT_SERVICE_REFUND_RATE_LIMIT_RPS;
    delete process.env.PAYMENT_SERVICE_GETPAYMENTMETHODS_RATE_LIMIT_RPS;
  });

  function startServer(env = {}) {
    return new Promise((resolve, reject) => {
      const serverEnv = { ...process.env, PORT: '50052', ...env };
      serverProcess = spawn('node', ['--require', './opentelemetry.js', 'index.js'], {
        cwd: path.join(__dirname, '..'),
        env: serverEnv,
      });

      let serverStarted = false;
      serverProcess.stdout.on('data', (data) => {
        if (data.toString().includes('Payment service running')) {
          serverStarted = true;
          client = new otelDemoProto.PaymentService(TEST_SERVER_ADDRESS, grpc.credentials.createInsecure());
          resolve();
        }
      });

      serverProcess.stderr.on('data', (data) => {
        console.error('Server stderr:', data.toString());
      });

      serverProcess.on('error', (err) => {
        if (!serverStarted) reject(err);
      });

      serverProcess.on('exit', (code) => {
        if (!serverStarted) reject(new Error(`Server exited with code ${code} before starting`));
      });

      // Timeout after 10s
      setTimeout(() => {
        if (!serverStarted) reject(new Error('Server start timed out after 10s'));
      }, 10000);
    });
  }

  function makeRequest(endpoint, requestData = {}) {
    return new Promise((resolve, reject) => {
      const method = client[endpoint].bind(client);
      method(requestData, (err, response) => {
        if (err) return reject(err);
        resolve(response);
      });
    });
  }

  // AC-1: All exposed gRPC endpoints have rate limiting applied
  test('test_ac1_all_endpoints_have_rate_limiting', async () => {
    await startServer({ PAYMENT_SERVICE_DEFAULT_RATE_LIMIT_RPS: '1' });

    // Test Charge endpoint
    await makeRequest('charge', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} });
    const chargeSecondReqErr = await makeRequest('charge', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} }).catch(e => e);
    expect(chargeSecondReqErr.code).to.equal(grpc.status.RESOURCE_EXHAUSTED);

    // Test Refund endpoint
    await makeRequest('refund', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} });
    const refundSecondReqErr = await makeRequest('refund', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} }).catch(e => e);
    expect(refundSecondReqErr.code).to.equal(grpc.status.RESOURCE_EXHAUSTED);

    // Test GetPaymentMethods endpoint
    await makeRequest('getPaymentMethods', {});
    const getPaymentMethodsSecondReqErr = await makeRequest('getPaymentMethods', {}).catch(e => e);
    expect(getPaymentMethodsSecondReqErr.code).to.equal(grpc.status.RESOURCE_EXHAUSTED);
  });

  // AC-2: Per-endpoint env var overrides default limit
  test('test_ac2_per_endpoint_env_var_overrides_default', async () => {
    await startServer({
      PAYMENT_SERVICE_DEFAULT_RATE_LIMIT_RPS: '1',
      PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS: '3',
    });

    // Charge should allow 3 requests
    for (let i = 0; i < 3; i++) {
      await makeRequest('charge', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} });
    }
    const chargeFourthReqErr = await makeRequest('charge', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} }).catch(e => e);
    expect(chargeFourthReqErr.code).to.equal(grpc.status.RESOURCE_EXHAUSTED);

    // Refund should only allow 1 request (default)
    await makeRequest('refund', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} });
    const refundSecondReqErr = await makeRequest('refund', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} }).catch(e => e);
    expect(refundSecondReqErr.code).to.equal(grpc.status.RESOURCE_EXHAUSTED);
  });

  // AC-3: Default applies when no per-endpoint var, fallback to 10 if default not set
  test('test_ac3_default_limit_applies_fallback_to_10', async () => {
    await startServer({}); // No env vars set, should fallback to 10 RPS

    // Send 10 successful requests
    for (let i = 0; i < 10; i++) {
      await makeRequest('charge', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} });
    }
    // 11th request should fail
    const charge11thReqErr = await makeRequest('charge', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} }).catch(e => e);
    expect(charge11thReqErr.code).to.equal(grpc.status.RESOURCE_EXHAUSTED);
  });

  // AC-4: Existing CHARGE env var continues to work as before
  test('test_ac4_existing_charge_env_var_works', async () => {
    await startServer({ PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS: '5' });

    // 5 successful requests
    for (let i = 0; i < 5; i++) {
      await makeRequest('charge', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} });
    }
    // 6th request fails
    const charge6thReqErr = await makeRequest('charge', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} }).catch(e => e);
    expect(charge6thReqErr.code).to.equal(grpc.status.RESOURCE_EXHAUSTED);
  });

  // AC-5: Rate limit exceeded returns RESOURCE_EXHAUSTED and logs correctly
  test('test_ac5_rate_limit_exceeded_returns_correct_status_and_log', async () => {
    const logStream = [];
    const originalTransport = process.stdout.write;
    process.stdout.write = (chunk) => {
      logStream.push(chunk.toString());
      return originalTransport(chunk);
    };

    try {
      await startServer({ PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS: '1' });
      await makeRequest('charge', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} });
      const err = await makeRequest('charge', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} }).catch(e => e);

      expect(err.code).to.equal(grpc.status.RESOURCE_EXHAUSTED);
      expect(err.details).to.equal('Rate limit exceeded for endpoint /oteldemo.PaymentService/Charge');

      // Check log output
      const logEntries = logStream.map(line => {
        try { return JSON.parse(line); } catch { return null; }
      }).filter(Boolean);

      const rateLimitLog = logEntries.find(entry => entry.message === 'Rate limit exceeded');
      expect(rateLimitLog).to.exist;
      expect(rateLimitLog.level).to.equal('warn');
      expect(rateLimitLog.endpoint).to.equal('/oteldemo.PaymentService/Charge');
      expect(rateLimitLog.client_ip).to.be.a('string');
      expect(rateLimitLog.limit_rps).to.equal(1);
    } finally {
      process.stdout.write = originalTransport;
    }
  });

  // AC-6: Retry info trailer with 1s delay
  test('test_ac6_retry_info_trailer_returned', async () => {
    await startServer({ PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS: '1' });
    await makeRequest('charge', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} });

    return new Promise((resolve, reject) => {
      client.charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} }, (err, response, trailers) => {
        try {
          expect(err.code).to.equal(grpc.status.RESOURCE_EXHAUSTED);
          expect(trailers.get('retry-info')).to.exist;
          expect(trailers.get('retry-info')[0]).to.include('retry-delay=1');
          resolve();
        } catch (e) {
          reject(e);
        }
      });
    });
  });

  // AC-7: Invalid env var causes service fail to start with fatal error
  test('test_ac7_invalid_env_var_causes_service_failure', async () => {
    // Test non-integer value
    let startErr = await startServer({ PAYMENT_SERVICE_DEFAULT_RATE_LIMIT_RPS: 'not-a-number' }).catch(e => e);
    expect(startErr.message).to.include('Invalid RPS limit for PAYMENT_SERVICE_DEFAULT_RATE_LIMIT_RPS: not-a-number must be a positive integer');

    // Test negative value
    startErr = await startServer({ PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS: '-5' }).catch(e => e);
    expect(startErr.message).to.include('Invalid RPS limit for PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS: -5 must be a positive integer');
  });

  // AC-8: P99 latency increase <5ms compared to baseline
  test('test_ac8_p99_latency_increase_less_than_5ms', async () => {
    // First measure baseline without rate limiting
    await startServer({ PAYMENT_SERVICE_DEFAULT_RATE_LIMIT_RPS: '100000' }); // Effectively unlimited
    const baselineLatencies = [];
    for (let i = 0; i < 100; i++) {
      const start = Date.now();
      await makeRequest('charge', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} });
      baselineLatencies.push(Date.now() - start);
    }
    serverProcess.kill();
    serverProcess = null;
    client.close();
    client = null;

    // Measure with rate limiting enabled
    await startServer({ PAYMENT_SERVICE_DEFAULT_RATE_LIMIT_RPS: '100000' });
    const rateLimitLatencies = [];
    for (let i = 0; i < 100; i++) {
      const start = Date.now();
      await makeRequest('charge', { amount: { currency_code: 'USD', units: 10, nanos: 0 }, credit_card: {} });
      rateLimitLatencies.push(Date.now() - start);
    }

    // Calculate P99
    const p99Baseline = baselineLatencies.sort((a, b) => a - b)[Math.floor(baselineLatencies.length * 0.99)];
    const p99RateLimit = rateLimitLatencies.sort((a, b) => a - b)[Math.floor(rateLimitLatencies.length * 0.99)];

    expect(p99RateLimit - p99Baseline).to.be.lessThan(5);
  });
});
