const grpc = require('@grpc/grpc-js');
const { exec } = require('child_process');
const path = require('path');
const protoLoader = require('@grpc/proto-loader');
const { promisify } = require('util');
const execAsync = promisify(exec);

const PROTO_PATH = path.join(__dirname, '../../../pb/demo.proto');
const packageDefinition = protoLoader.loadSync(PROTO_PATH, { keepCase: true, defaults: true, oneofs: true });
const otelDemoProto = grpc.loadPackageDefinition(packageDefinition).oteldemo;

const PAYMENT_SERVICE_PORT = 50052;
const TEST_PAYMENT_SERVICE_ADDR = `localhost:${PAYMENT_SERVICE_PORT}`;

jest.setTimeout(40000); // 40s to accommodate 30s timeout test

describe('Payment Service Graceful Shutdown Acceptance Criteria', () => {
  let paymentClient;
  let serverProcess;

  beforeEach(async () => {
    // Start fresh payment service instance for each test
    serverProcess = exec('npm start', {
      cwd: path.join(__dirname, '..'),
      env: { ...process.env, PORT: PAYMENT_SERVICE_PORT.toString() }
    });
    
    // Wait for service to be ready
    await new Promise(resolve => setTimeout(resolve, 3000));
    
    // Create gRPC client
    paymentClient = new otelDemoProto.PaymentService(
      TEST_PAYMENT_SERVICE_ADDR,
      grpc.credentials.createInsecure()
    );
  });

  afterEach(() => {
    if (serverProcess && !serverProcess.killed) {
      serverProcess.kill('SIGKILL');
    }
  });

  test('ac1_new_requests_receive_unavailable_after_signal', async () => {
    // AC-1: When SIGINT/SIGTERM received, new Charge requests get UNAVAILABLE status
    // Send SIGTERM to service
    serverProcess.kill('SIGTERM');

    // Wait for shutdown to initiate
    await new Promise(resolve => setTimeout(resolve, 500));

    // Send new Charge request
    const chargeRequest = {
      amount: { units: 100, nanos: 0, currency_code: 'USD' },
      credit_card: {
        credit_card_number: '4111-1111-1111-1111',
        credit_card_cvv: '123',
        credit_card_expiration_year: 2030,
        credit_card_expiration_month: 12
      }
    };

    await expect(promisify(paymentClient.charge.bind(paymentClient))(chargeRequest))
      .rejects.toMatchObject({ code: grpc.status.UNAVAILABLE });
  });

  test('ac2_service_waits_up_to_30s_for_in_flight_requests', async () => {
    // AC-2: Service waits maximum 30 seconds for in-flight requests
    // Send a Charge request that will take 25s to complete (slow mock)
    const slowChargePromise = promisify(paymentClient.charge.bind(paymentClient))({
      amount: { units: 100, nanos: 0, currency_code: 'USD' },
      credit_card: {
        credit_card_number: '4111-1111-1111-1111',
        credit_card_cvv: '123',
        credit_card_expiration_year: 2030,
        credit_card_expiration_month: 12
      },
      // Mock delay field for testing (will be ignored in real implementation but our test harness can use it)
      __test_delay_ms: 25000
    });

    // Wait 1s then send SIGTERM
    await new Promise(resolve => setTimeout(resolve, 1000));
    const signalTime = Date.now();
    serverProcess.kill('SIGTERM');

    // Wait for process to exit
    const exitCode = await new Promise(resolve => serverProcess.on('exit', resolve));
    const exitTime = Date.now();
    const timeToExit = exitTime - signalTime;

    // Should exit after ~25s, well before 30s limit, not immediately
    expect(timeToExit).toBeGreaterThan(24000);
    expect(timeToExit).toBeLessThan(30000);
    expect(exitCode).toBe(0);
  });

  test('ac3_in_flight_requests_complete_normally_within_window', async () => {
    // AC-3: In-flight Charge requests complete normally if within 30s window
    const chargePromise = promisify(paymentClient.charge.bind(paymentClient))({
      amount: { units: 200, nanos: 0, currency_code: 'EUR' },
      credit_card: {
        credit_card_number: '4111-1111-1111-1111',
        credit_card_cvv: '456',
        credit_card_expiration_year: 2028,
        credit_card_expiration_month: 6
      }
    });

    // Send SIGTERM immediately after sending request
    await new Promise(resolve => setTimeout(resolve, 500));
    serverProcess.kill('SIGTERM');

    // Request should complete successfully with normal response
    const response = await chargePromise;
    expect(response).toHaveProperty('transaction_id');
    expect(typeof response.transaction_id).toBe('string');
  });

  test('ac4_cleanup_and_success_log_when_all_requests_complete_before_timeout', async () => {
    // AC-4: All resources cleaned up, success log, exit 0 when requests complete before timeout
    let stdout = '';
    serverProcess.stdout.on('data', data => stdout += data.toString());

    // Send a fast Charge request
    await promisify(paymentClient.charge.bind(paymentClient))({
      amount: { units: 50, nanos: 0, currency_code: 'GBP' },
      credit_card: {
        credit_card_number: '4111-1111-1111-1111',
        credit_card_cvv: '789',
        credit_card_expiration_year: 2029,
        credit_card_expiration_month: 11
      }
    });

    // Send SIGTERM
    serverProcess.kill('SIGTERM');
    
    const exitCode = await new Promise(resolve => serverProcess.on('exit', resolve));
    
    // Verify exit code 0
    expect(exitCode).toBe(0);
    
    // Verify shutdown.success log present with expected fields
    const successLogLines = stdout.split('\n').filter(line => line.includes('"event":"shutdown.success"'));
    expect(successLogLines.length).toBe(1);
    const successLog = JSON.parse(successLogLines[0]);
    expect(successLog).toHaveProperty('service', 'paymentservice');
    expect(successLog).toHaveProperty('component', 'graceful-shutdown');
    expect(successLog).toHaveProperty('completed_requests', 1);
    expect(successLog).toHaveProperty('duration_ms');
    expect(successLog).toHaveProperty('timestamp');
  });

  test('ac5_timeout_log_and_exit_code_1_when_requests_exceed_30s_window', async () => {
    // AC-5: Timeout log, cleanup, exit 1 when requests exceed 30s window
    let stdout = '';
    serverProcess.stdout.on('data', data => stdout += data.toString());

    // Send a Charge request that will take 35s to complete
    const longChargePromise = promisify(paymentClient.charge.bind(paymentClient))({
      amount: { units: 150, nanos: 0, currency_code: 'CAD' },
      credit_card: {
        credit_card_number: '4111-1111-1111-1111',
        credit_card_cvv: '321',
        credit_card_expiration_year: 2031,
        credit_card_expiration_month: 1
      },
      __test_delay_ms: 35000
    });

    // Wait 1s then send SIGTERM
    await new Promise(resolve => setTimeout(resolve, 1000));
    serverProcess.kill('SIGTERM');

    const exitCode = await new Promise(resolve => serverProcess.on('exit', resolve));
    const exitTime = Date.now();

    // Should exit after ~30s, with exit code 1
    expect(exitCode).toBe(1);

    // Verify shutdown.timeout log present
    const timeoutLogLines = stdout.split('\n').filter(line => line.includes('"event":"shutdown.timeout"'));
    expect(timeoutLogLines.length).toBe(1);
    const timeoutLog = JSON.parse(timeoutLogLines[0]);
    expect(timeoutLog).toHaveProperty('service', 'paymentservice');
    expect(timeoutLog).toHaveProperty('component', 'graceful-shutdown');
    expect(timeoutLog).toHaveProperty('incomplete_requests', 1);
    expect(timeoutLog).toHaveProperty('duration_ms');
    expect(timeoutLog).toHaveProperty('timestamp');

    // The long request should have been terminated
    await expect(longChargePromise).rejects.toBeDefined();
  });

  test('ac6_shutdown_start_log_emitted_immediately_on_signal', async () => {
    // AC-6: shutdown.start log emitted immediately after signal receipt
    let stdout = '';
    serverProcess.stdout.on('data', data => stdout += data.toString());

    // Send SIGTERM
    serverProcess.kill('SIGTERM');
    const signalTime = Date.now();

    // Wait for log to be emitted
    await new Promise(resolve => setTimeout(resolve, 500));

    // Verify shutdown.start log present
    const startLogLines = stdout.split('\n').filter(line => line.includes('"event":"shutdown.start"'));
    expect(startLogLines.length).toBe(1);
    const startLog = JSON.parse(startLogLines[0]);
    expect(startLog).toHaveProperty('service', 'paymentservice');
    expect(startLog).toHaveProperty('component', 'graceful-shutdown');
    expect(startLog).toHaveProperty('signal', 'SIGTERM');
    expect(startLog).toHaveProperty('timestamp');
  });

  test('ac7_no_new_requests_accepted_after_shutdown_initiated', async () => {
    // AC-7: No new Charge requests are accepted after shutdown starts
    // Send SIGTERM
    serverProcess.kill('SIGTERM');
    await new Promise(resolve => setTimeout(resolve, 500));

    // Attempt to send 5 consecutive requests
    const requestPromises = [];
    for (let i = 0; i < 5; i++) {
      requestPromises.push(
        promisify(paymentClient.charge.bind(paymentClient))({
          amount: { units: 10 * i, nanos: 0, currency_code: 'USD' },
          credit_card: {
            credit_card_number: '4111-1111-1111-1111',
            credit_card_cvv: '123',
            credit_card_expiration_year: 2030,
            credit_card_expiration_month: 12
          }
        })
      );
    }

    // All requests should fail with UNAVAILABLE
    const results = await Promise.allSettled(requestPromises);
    results.forEach(result => {
      expect(result.status).toBe('rejected');
      expect(result.reason.code).toBe(grpc.status.UNAVAILABLE);
    });
  });
});
