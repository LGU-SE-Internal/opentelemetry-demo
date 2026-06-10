const { exec } = require('child_process');
const path = require('path');
const { promisify } = require('util');
const fs = require('fs');
const execAsync = promisify(exec);
const readFile = promisify(fs.readFile);
const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');

const PROTO_PATH = path.join(__dirname, '../../../pb/demo.proto');
const packageDefinition = protoLoader.loadSync(PROTO_PATH, { keepCase: true, defaults: true, oneofs: true });
const otelDemoProto = grpc.loadPackageDefinition(packageDefinition).oteldemo;

const PAYMENT_SERVICE_PORT = 50053;
const TEST_PAYMENT_SERVICE_ADDR = `localhost:${PAYMENT_SERVICE_PORT}`;

jest.setTimeout(40000);

describe('Duplicate Shutdown Function Removal Acceptance Criteria', () => {
  let paymentClient;
  let serverProcess;

  beforeEach(async () => {
    serverProcess = exec('npm start', {
      cwd: path.join(__dirname, '..'),
      env: { ...process.env, PORT: PAYMENT_SERVICE_PORT.toString() }
    });
    await new Promise(resolve => setTimeout(resolve, 3000));
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

  test('ac1_only_one_shutdown_function_definition_in_main_source', async () => {
    // AC-1: Only one shutdown function definition remains in main source file (index.js)
    const indexContent = await readFile(path.join(__dirname, '../index.js'), 'utf8');
    const shutdownFunctionMatches = indexContent.match(/^function shutdown\s*\(/gm) || [];
    expect(shutdownFunctionMatches.length).toBe(1);
    
    // Verify no duplicate shutdown definitions (check for any other shutdown function declarations)
    const allShutdownMatches = indexContent.match(/shutdown\s*=\s*.*function|shutdown\s*:\s*function|const shutdown\s*=|let shutdown\s*=|var shutdown\s*=/gm) || [];
    const functionDeclarations = allShutdownMatches.filter(match => !match.includes('.shutdown') && !match.includes('shutdown('));
    expect(functionDeclarations.length).toBeLessThanOrEqual(1);
  });

  test('ac2_sigint_signal_triggers_correct_shutdown_behavior', async () => {
    // AC-2: SIGINT signal triggers correct shutdown behavior
    let stdout = '';
    serverProcess.stdout.on('data', data => stdout += data.toString());
    let stderr = '';
    serverProcess.stderr.on('data', data => stderr += data.toString());

    // Send an in-flight request before signal
    const chargePromise = promisify(paymentClient.charge.bind(paymentClient))({
      amount: { units: 100, nanos: 0, currency_code: 'USD' },
      credit_card: {
        credit_card_number: '4111-1111-1111-1111',
        credit_card_cvv: '123',
        credit_card_expiration_year: 2030,
        credit_card_expiration_month: 12
      }
    });

    // Wait 500ms then send SIGINT
    await new Promise(resolve => setTimeout(resolve, 500));
    const signalTime = Date.now();
    serverProcess.kill('SIGINT');

    // Wait for process exit
    const exitCode = await new Promise(resolve => serverProcess.on('exit', resolve));
    const exitTime = Date.now();

    // Verify in-flight request completed successfully
    const response = await chargePromise;
    expect(response).toHaveProperty('transaction_id');
    expect(typeof response.transaction_id).toBe('string');

    // Verify exit code 0
    expect(exitCode).toBe(0);

    // Verify shutdown success log (confirms connections closed, telemetry flushed)
    const successLogLines = stdout.split('\n').filter(line => line.includes('"event":"shutdown.success"'));
    expect(successLogLines.length).toBe(1);
    const successLog = JSON.parse(successLogLines[0]);
    expect(successLog).toHaveProperty('completed_requests', 1);
    expect(successLog).toHaveProperty('signal', 'SIGINT');

    // Verify shutdown completed within 10 seconds as per interface spec
    expect(exitTime - signalTime).toBeLessThan(10000);
  });

  test('ac3_sigterm_signal_triggers_correct_shutdown_behavior', async () => {
    // AC-3: SIGTERM signal triggers correct shutdown behavior
    let stdout = '';
    serverProcess.stdout.on('data', data => stdout += data.toString());

    // Send an in-flight request before signal
    const chargePromise = promisify(paymentClient.charge.bind(paymentClient))({
      amount: { units: 200, nanos: 0, currency_code: 'EUR' },
      credit_card: {
        credit_card_number: '4111-1111-1111-1111',
        credit_card_cvv: '456',
        credit_card_expiration_year: 2028,
        credit_card_expiration_month: 6
      }
    });

    // Wait 500ms then send SIGTERM
    await new Promise(resolve => setTimeout(resolve, 500));
    const signalTime = Date.now();
    serverProcess.kill('SIGTERM');

    // Wait for process exit
    const exitCode = await new Promise(resolve => serverProcess.on('exit', resolve));
    const exitTime = Date.now();

    // Verify in-flight request completed successfully
    const response = await chargePromise;
    expect(response).toHaveProperty('transaction_id');
    expect(typeof response.transaction_id).toBe('string');

    // Verify exit code 0
    expect(exitCode).toBe(0);

    // Verify shutdown success log (confirms connections closed, telemetry flushed)
    const successLogLines = stdout.split('\n').filter(line => line.includes('"event":"shutdown.success"'));
    expect(successLogLines.length).toBe(1);
    const successLog = JSON.parse(successLogLines[0]);
    expect(successLog).toHaveProperty('completed_requests', 1);
    expect(successLog).toHaveProperty('signal', 'SIGTERM');

    // Verify shutdown completed within 10 seconds as per interface spec
    expect(exitTime - signalTime).toBeLessThan(10000);
  });

  test('ac4_no_duplicate_function_definitions_in_payment_service_source', async () => {
    // AC-4: No duplicate function definitions exist in any .js/.ts files under src/payment/
    const sourceFiles = fs.readdirSync(path.join(__dirname, '..'))
      .filter(file => file.endsWith('.js') || file.endsWith('.ts'))
      .map(file => path.join(__dirname, '..', file));

    const functionCounts = new Map();

    for (const file of sourceFiles) {
      const content = await readFile(file, 'utf8');
      // Match function declarations and assignments
      const functionMatches = content.match(/^function\s+([a-zA-Z_$][0-9a-zA-Z_$]*)\s*\(/gm) || [];
      const assignMatches = content.match(/^([a-zA-Z_$][0-9a-zA-Z_$]*)\s*=\s*.*function|^const\s+([a-zA-Z_$][0-9a-zA-Z_$]*)\s*=\s*(.*=>|function)/gm) || [];
      
      [...functionMatches, ...assignMatches].forEach(match => {
        // Extract function name
        const nameMatch = match.match(/function\s+([a-zA-Z_$][0-9a-zA-Z_$]*)|([a-zA-Z_$][0-9a-zA-Z_$]*)\s*=\s*(.*=>|function)/);
        if (nameMatch) {
          const funcName = nameMatch[1] || nameMatch[2];
          if (funcName && !funcName.startsWith('_')) { // Ignore private/anon functions
            const key = `${file}:${funcName}`;
            functionCounts.set(key, (functionCounts.get(key) || 0) + 1);
          }
        }
      });
    }

    // Check for any duplicates (count > 1)
    const duplicates = Array.from(functionCounts.entries())
      .filter(([key, count]) => count > 1)
      .map(([key]) => key);

    expect(duplicates).toEqual([]);
  });

  test('ac5_all_existing_payment_service_tests_pass', async () => {
    // AC-5: All existing payment service unit, integration, E2E tests pass with zero failures
    const { stdout, stderr } = await execAsync('npm test', {
      cwd: path.join(__dirname, '..'),
      env: { ...process.env, CI: 'true' }
    });

    // Verify no test failures
    expect(stdout).not.toContain('failed');
    expect(stderr).not.toContain('failed');
    expect(stdout).toContain('passed');
  });
});
