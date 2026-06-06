const grpc = require('@grpc/grpc-js');
const fs = require('fs');
const path = require('path');
const { getServerCredentials } = require('../index'); // Import the helper from spec

describe('Payment Service TLS/mTLS Configuration', () => {
  // Backup original env vars before each test
  let originalEnv;
  beforeEach(() => {
    originalEnv = { ...process.env };
    jest.clearAllMocks();
  });

  afterEach(() => {
    process.env = originalEnv;
  });

  // AC-1: No TLS env vars set, uses insecure credentials
  test('test_ac1_no_tls_vars_uses_insecure_credentials', () => {
    // Clear all TLS related env vars
    delete process.env.PAYMENT_SERVICE_TLS_CERT_PATH;
    delete process.env.PAYMENT_SERVICE_TLS_KEY_PATH;
    delete process.env.PAYMENT_SERVICE_TLS_CLIENT_CA_PATH;

    const credentials = getServerCredentials();
    expect(credentials).toEqual(grpc.ServerCredentials.createInsecure());
  });

  // AC-2: Valid cert and key paths set, returns TLS server credentials
  test('test_ac2_valid_cert_key_returns_tls_credentials', () => {
    const testCertPath = path.join(__dirname, 'fixtures', 'test-cert.pem');
    const testKeyPath = path.join(__dirname, 'fixtures', 'test-key.pem');
    
    process.env.PAYMENT_SERVICE_TLS_CERT_PATH = testCertPath;
    process.env.PAYMENT_SERVICE_TLS_KEY_PATH = testKeyPath;

    // Mock fs readFileSync to return dummy valid PEM data
    jest.spyOn(fs, 'readFileSync').mockImplementation((filePath) => {
      if (filePath === testCertPath) return Buffer.from('test cert data');
      if (filePath === testKeyPath) return Buffer.from('test key data');
      throw new Error(`Unexpected file read: ${filePath}`);
    });

    const credentials = getServerCredentials();
    expect(fs.readFileSync).toHaveBeenCalledTimes(2);
    expect(fs.readFileSync).toHaveBeenCalledWith(testCertPath);
    expect(fs.readFileSync).toHaveBeenCalledWith(testKeyPath);
    expect(credentials).toBeInstanceOf(grpc.ServerCredentials);
    // Should not be insecure credentials
    expect(credentials).not.toEqual(grpc.ServerCredentials.createInsecure());
  });

  // AC-3: Only one of cert/key set, throws required error
  test('test_ac3_only_one_tls_var_set_throws_error', () => {
    // Case 1: Only cert path set
    process.env.PAYMENT_SERVICE_TLS_CERT_PATH = '/tmp/cert.pem';
    delete process.env.PAYMENT_SERVICE_TLS_KEY_PATH;

    expect(() => getServerCredentials()).toThrowError(
      'Both PAYMENT_SERVICE_TLS_CERT_PATH and PAYMENT_SERVICE_TLS_KEY_PATH must be set when configuring TLS'
    );

    // Case 2: Only key path set
    delete process.env.PAYMENT_SERVICE_TLS_CERT_PATH;
    process.env.PAYMENT_SERVICE_TLS_KEY_PATH = '/tmp/key.pem';

    expect(() => getServerCredentials()).toThrowError(
      'Both PAYMENT_SERVICE_TLS_CERT_PATH and PAYMENT_SERVICE_TLS_KEY_PATH must be set when configuring TLS'
    );
  });

  // AC-4: Invalid/unreadable cert/key paths, throws read error
  test('test_ac4_invalid_cert_key_path_throws_read_error', () => {
    process.env.PAYMENT_SERVICE_TLS_CERT_PATH = '/nonexistent/path/cert.pem';
    process.env.PAYMENT_SERVICE_TLS_KEY_PATH = '/nonexistent/path/key.pem';

    // Mock fs to throw ENOENT error
    jest.spyOn(fs, 'readFileSync').mockImplementation(() => {
      throw new Error('ENOENT: no such file or directory');
    });

    expect(() => getServerCredentials()).toThrowError(
      'Failed to read TLS certificate/key file: ENOENT: no such file or directory'
    );
  });

  // AC-5: Client CA path set, returns mTLS credentials
  test('test_ac5_client_ca_set_enables_mtls', () => {
    const testCertPath = path.join(__dirname, 'fixtures', 'test-cert.pem');
    const testKeyPath = path.join(__dirname, 'fixtures', 'test-key.pem');
    const testCaPath = path.join(__dirname, 'fixtures', 'test-ca.pem');
    
    process.env.PAYMENT_SERVICE_TLS_CERT_PATH = testCertPath;
    process.env.PAYMENT_SERVICE_TLS_KEY_PATH = testKeyPath;
    process.env.PAYMENT_SERVICE_TLS_CLIENT_CA_PATH = testCaPath;

    jest.spyOn(fs, 'readFileSync').mockImplementation((filePath) => {
      if (filePath === testCertPath) return Buffer.from('test cert data');
      if (filePath === testKeyPath) return Buffer.from('test key data');
      if (filePath === testCaPath) return Buffer.from('test ca data');
      throw new Error(`Unexpected file read: ${filePath}`);
    });

    const credentials = getServerCredentials();
    expect(fs.readFileSync).toHaveBeenCalledTimes(3);
    expect(fs.readFileSync).toHaveBeenCalledWith(testCaPath);
    expect(credentials).toBeInstanceOf(grpc.ServerCredentials);
  });

  // AC-6: Invalid client CA path, throws read error
  test('test_ac6_invalid_client_ca_path_throws_error', () => {
    const testCertPath = path.join(__dirname, 'fixtures', 'test-cert.pem');
    const testKeyPath = path.join(__dirname, 'fixtures', 'test-key.pem');
    const testCaPath = '/nonexistent/path/ca.pem';
    
    process.env.PAYMENT_SERVICE_TLS_CERT_PATH = testCertPath;
    process.env.PAYMENT_SERVICE_TLS_KEY_PATH = testKeyPath;
    process.env.PAYMENT_SERVICE_TLS_CLIENT_CA_PATH = testCaPath;

    jest.spyOn(fs, 'readFileSync').mockImplementation((filePath) => {
      if (filePath === testCertPath) return Buffer.from('test cert data');
      if (filePath === testKeyPath) return Buffer.from('test key data');
      if (filePath === testCaPath) throw new Error('ENOENT: no such file or directory');
      throw new Error(`Unexpected file read: ${filePath}`);
    });

    expect(() => getServerCredentials()).toThrowError(
      'Failed to read client CA certificate file: ENOENT: no such file or directory'
    );
  });

  // AC-7: Default behavior unchanged, no TLS vars works exactly as before
  test('test_ac7_default_behavior_unchanged', () => {
    // This is a redundant check but explicitly tests AC-7 invariant
    delete process.env.PAYMENT_SERVICE_TLS_CERT_PATH;
    delete process.env.PAYMENT_SERVICE_TLS_KEY_PATH;
    delete process.env.PAYMENT_SERVICE_TLS_CLIENT_CA_PATH;

    // Should not throw any errors
    const credentials = getServerCredentials();
    expect(credentials).toEqual(grpc.ServerCredentials.createInsecure());
  });
});
