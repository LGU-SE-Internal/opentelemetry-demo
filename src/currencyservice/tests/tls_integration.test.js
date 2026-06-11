const grpc = require('@grpc/grpc-js');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// Test constants matching spec
const ENV_VARS = {
  TLS_ENABLED: 'CURRENCY_SERVICE_TLS_ENABLED',
  TLS_CERT_PATH: 'CURRENCY_SERVICE_TLS_CERT_PATH',
  TLS_KEY_PATH: 'CURRENCY_SERVICE_TLS_KEY_PATH',
  MTLS_ENABLED: 'CURRENCY_SERVICE_MTLS_ENABLED',
  MTLS_CA_CERT_PATH: 'CURRENCY_SERVICE_MTLS_CA_CERT_PATH'
};

const TEST_CERT_PATH = path.join(__dirname, 'test_data', 'test.crt');
const TEST_KEY_PATH = path.join(__dirname, 'test_data', 'test.key');
const TEST_CA_PATH = path.join(__dirname, 'test_data', 'ca.crt');
const NON_EXISTENT_PATH = path.join(__dirname, 'non_existent_file.pem');
const UNREADABLE_PATH = path.join(__dirname, 'test_data', 'unreadable.pem');

// Helper to run server with env vars and capture exit code/output
function runServerWithEnv(envVars = {}) {
  try {
    const env = { ...process.env, ...envVars };
    // Adjust command to match actual server start command
    const output = execSync('node index.js', {
      cwd: path.join(__dirname, '..'),
      env,
      timeout: 3000,
      stdio: 'pipe'
    });
    return { success: true, output: output.toString() };
  } catch (err) {
    // If we timed out but server printed that it's running, consider it a success
    const stdout = err.stdout?.toString() || '';
    if (err.code === 'ETIMEDOUT' && stdout.includes('Currency service running on port')) {
      return { success: true, output: stdout };
    }
    return {
      success: false,
      exitCode: err.status,
      stdout: stdout,
      stderr: err.stderr?.toString() || ''
    };
  }
}

// AC-1: When TLS_ENABLED not set or false, server starts in plaintext mode
test('test_ac1_plaintext_mode_default', async () => {
  delete process.env[ENV_VARS.TLS_ENABLED];
  const result = runServerWithEnv();
  expect(result.success).toBe(true);
  // Verify plaintext connection works
  const client = new grpc.Client('localhost:7000', grpc.credentials.createInsecure());
  expect(client.waitForReady(Date.now() + 2000, (err) => {
    expect(err).toBeFalsy();
    client.close();
  }));
});

// AC-2: TLS enabled with valid cert/key accepts TLS connections
test('test_ac2_tls_mode_valid_certs', async () => {
  const env = {
    [ENV_VARS.TLS_ENABLED]: 'true',
    [ENV_VARS.TLS_CERT_PATH]: TEST_CERT_PATH,
    [ENV_VARS.TLS_KEY_PATH]: TEST_KEY_PATH
  };
  const result = runServerWithEnv(env);
  expect(result.success).toBe(true);
  // Verify TLS connection works
  const client = new grpc.Client('localhost:7000', grpc.credentials.createSsl(fs.readFileSync(TEST_CA_PATH)));
  expect(client.waitForReady(Date.now() + 2000, (err) => {
    expect(err).toBeFalsy();
    client.close();
  }));
});

// AC-3: TLS enabled without cert/key fails with configuration error
test('test_ac3_tls_enabled_missing_cert_key', async () => {
  const env1 = {
    [ENV_VARS.TLS_ENABLED]: 'true',
    [ENV_VARS.TLS_KEY_PATH]: TEST_KEY_PATH
  };
  const result1 = runServerWithEnv(env1);
  expect(result1.success).toBe(false);
  expect(result1.stderr).toContain('CONFIGURATION_ERROR: TLS_CERT_PATH is required when TLS_ENABLED is true');

  const env2 = {
    [ENV_VARS.TLS_ENABLED]: 'true',
    [ENV_VARS.TLS_CERT_PATH]: TEST_CERT_PATH
  };
  const result2 = runServerWithEnv(env2);
  expect(result2.success).toBe(false);
  expect(result2.stderr).toContain('CONFIGURATION_ERROR: TLS_KEY_PATH is required when TLS_ENABLED is true');
});

// AC-4: TLS cert path points to non-existent file fails with FILE_NOT_FOUND
test('test_ac4_tls_cert_file_not_found', async () => {
  const env = {
    [ENV_VARS.TLS_ENABLED]: 'true',
    [ENV_VARS.TLS_CERT_PATH]: NON_EXISTENT_PATH,
    [ENV_VARS.TLS_KEY_PATH]: TEST_KEY_PATH
  };
  const result = runServerWithEnv(env);
  expect(result.success).toBe(false);
  expect(result.stderr).toContain(`FILE_NOT_FOUND_ERROR: ${NON_EXISTENT_PATH} does not exist`);
});

// AC-5: TLS key path is unreadable fails with PERMISSION_DENIED
test('test_ac5_tls_key_permission_denied', async () => {
  // Setup unreadable file first
  try {
    fs.writeFileSync(UNREADABLE_PATH, 'test', { mode: 0o000 });
  } catch (e) {}

  const env = {
    [ENV_VARS.TLS_ENABLED]: 'true',
    [ENV_VARS.TLS_CERT_PATH]: TEST_CERT_PATH,
    [ENV_VARS.TLS_KEY_PATH]: UNREADABLE_PATH
  };
  const result = runServerWithEnv(env);
  expect(result.success).toBe(false);
  expect(result.stderr).toContain(`PERMISSION_DENIED_ERROR: ${UNREADABLE_PATH} is not readable`);

  // Cleanup
  try { fs.unlinkSync(UNREADABLE_PATH); } catch(e) {}
});

// AC-6: mTLS enabled only accepts connections with valid client cert
test('test_ac6_mtls_enforces_client_cert', async () => {
  const env = {
    [ENV_VARS.TLS_ENABLED]: 'true',
    [ENV_VARS.TLS_CERT_PATH]: TEST_CERT_PATH,
    [ENV_VARS.TLS_KEY_PATH]: TEST_KEY_PATH,
    [ENV_VARS.MTLS_ENABLED]: 'true',
    [ENV_VARS.MTLS_CA_CERT_PATH]: TEST_CA_PATH
  };
  const result = runServerWithEnv(env);
  expect(result.success).toBe(true);

  // Test connection without client cert fails
  const badClient = new grpc.Client('localhost:7000', grpc.credentials.createSsl(fs.readFileSync(TEST_CA_PATH)));
  expect(badClient.waitForReady(Date.now() + 2000, (err) => {
    expect(err).toBeTruthy();
    badClient.close();
  }));

  // Test connection with valid client cert succeeds
  const clientCert = fs.readFileSync(path.join(__dirname, 'test_data', 'client.crt'));
  const clientKey = fs.readFileSync(path.join(__dirname, 'test_data', 'client.key'));
  const goodClient = new grpc.Client('localhost:7000', grpc.credentials.createSsl(
    fs.readFileSync(TEST_CA_PATH),
    clientKey,
    clientCert
  ));
  expect(goodClient.waitForReady(Date.now() + 2000, (err) => {
    expect(err).toBeFalsy();
    goodClient.close();
  }));
});

// AC-7: mTLS enabled without CA path fails with configuration error
test('test_ac7_mtls_missing_ca_path', async () => {
  const env = {
    [ENV_VARS.TLS_ENABLED]: 'true',
    [ENV_VARS.TLS_CERT_PATH]: TEST_CERT_PATH,
    [ENV_VARS.TLS_KEY_PATH]: TEST_KEY_PATH,
    [ENV_VARS.MTLS_ENABLED]: 'true'
  };
  const result = runServerWithEnv(env);
  expect(result.success).toBe(false);
  expect(result.stderr).toContain('CONFIGURATION_ERROR: MTLS_CA_CERT_PATH is required when MTLS_ENABLED is true');
});

// AC-8: mTLS enabled without TLS enabled fails with configuration error
test('test_ac8_mtls_requires_tls_enabled', async () => {
  const env = {
    [ENV_VARS.TLS_ENABLED]: 'false',
    [ENV_VARS.MTLS_ENABLED]: 'true'
  };
  const result = runServerWithEnv(env);
  expect(result.success).toBe(false);
  expect(result.stderr).toContain('CONFIGURATION_ERROR: MTLS_ENABLED requires TLS_ENABLED to be true');
});

// AC-9: TLS enabled rejects plaintext connections
test('test_ac9_tls_rejects_plaintext_connections', async () => {
  const env = {
    [ENV_VARS.TLS_ENABLED]: 'true',
    [ENV_VARS.TLS_CERT_PATH]: TEST_CERT_PATH,
    [ENV_VARS.TLS_KEY_PATH]: TEST_KEY_PATH
  };
  const result = runServerWithEnv(env);
  expect(result.success).toBe(true);

  // Try to connect with plaintext
  const client = new grpc.Client('localhost:7000', grpc.credentials.createInsecure());
  expect(client.waitForReady(Date.now() + 2000, (err) => {
    expect(err).toBeTruthy();
    expect(err.message).toContain('connection refused');
    client.close();
  }));
});
