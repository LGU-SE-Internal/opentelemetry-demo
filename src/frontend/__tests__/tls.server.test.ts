import { createServer } from 'http';
import { createServer as createHttpsServer, RequestListener } from 'https';
import next, { NextServer } from 'next';
import { readFileSync } from 'fs';
import { env } from 'process';
import { startServerWithTls, TlsConfigError, FileReadError, CertificateValidationError, TlsConfig, ServerStartupResult } from '../utils/tls';

// Mock fs operations for testing
jest.mock('fs', () => ({
  ...jest.requireActual('fs'),
  existsSync: jest.fn(),
  readFileSync: jest.fn(),
  accessSync: jest.fn(),
  constants: {
    R_OK: 4
  }
}));

// Mock next server
jest.mock('next', () => {
  const mockNextServer = {
    prepare: jest.fn().mockResolvedValue(undefined),
    getRequestHandler: jest.fn().mockReturnValue(jest.fn()),
    server: null
  };
  return jest.fn(() => mockNextServer);
});

// Mock http and https server listen
jest.mock('http', () => ({
  ...jest.requireActual('http'),
  createServer: jest.fn(() => ({
    listen: jest.fn((port, cb) => cb()),
    constructor: jest.requireActual('http').Server
  }))
}));

jest.mock('https', () => ({
  ...jest.requireActual('https'),
  createServer: jest.fn((options, handler) => ({
    listen: jest.fn((port, cb) => cb()),
    options,
    constructor: jest.requireActual('https').Server
  }))
}));


describe('Frontend TLS Server Configuration', () => {
  const originalEnv = { ...env };

  beforeEach(() => {
    jest.resetModules();
    process.env = { ...originalEnv };
  });

  afterEach(() => {
    process.env = originalEnv;
  });

  // AC-1: TLS disabled, starts HTTP server, no TLS config required
  test('test_ac1_tls_disabled_starts_http_server', async () => {
    const config: TlsConfig = { enabled: false, mtlsEnabled: false };
    const result = await startServerWithTls(config);
    
    expect(result.success).toBe(true);
    expect(result.server).not.toBeNull();
    expect(result.error).toBeUndefined();
    // Verify server is HTTP not HTTPS
    expect(result.server?.server instanceof createServer().constructor).toBe(true);
  });

  // AC-2: TLS enabled with valid cert/key, starts HTTPS server
  test('test_ac2_tls_enabled_valid_cert_starts_https_server', async () => {
    // Use test cert paths that would exist in test environment
    const config: TlsConfig = {
      enabled: true,
      certPath: 'test/certs/server.crt',
      keyPath: 'test/certs/server.key',
      mtlsEnabled: false
    };
    const result = await startServerWithTls(config);
    
    expect(result.success).toBe(true);
    expect(result.server).not.toBeNull();
    expect(result.error).toBeUndefined();
    // Verify server is HTTPS
    expect(result.server?.server instanceof createHttpsServer(() => {}).constructor).toBe(true);
  });

  // AC-3: TLS enabled but missing cert or key path, fails with MISSING_CERT_KEY
  test('test_ac3_tls_enabled_missing_cert_key_fails', async () => {
    const config: TlsConfig = { enabled: true, mtlsEnabled: false };
    const result = await startServerWithTls(config);
    
    expect(result.success).toBe(false);
    expect(result.error).toBe(TlsConfigError.MISSING_CERT_KEY);
  });

  // AC-4: TLS enabled with non-existent cert file, fails with FILE_NOT_FOUND
  test('test_ac4_tls_enabled_nonexistent_cert_fails', async () => {
    const config: TlsConfig = {
      enabled: true,
      certPath: 'nonexistent/path/cert.crt',
      keyPath: 'test/certs/server.key',
      mtlsEnabled: false
    };
    const result = await startServerWithTls(config);
    
    expect(result.success).toBe(false);
    expect((result.error as FileReadError).code).toBe("FILE_NOT_FOUND");
    expect((result.error as FileReadError).path).toBe("nonexistent/path/cert.crt");
  });

  // AC-5: TLS enabled with malformed cert/key, fails with validation error
  test('test_ac5_tls_enabled_malformed_cert_fails', async () => {
    const config: TlsConfig = {
      enabled: true,
      certPath: 'test/certs/malformed.crt',
      keyPath: 'test/certs/server.key',
      mtlsEnabled: false
    };
    const result = await startServerWithTls(config);
    
    expect(result.success).toBe(false);
    expect((result.error as CertificateValidationError).code).toBe("INVALID_CERTIFICATE");
  });

  // AC-6: mTLS enabled with valid config, only accepts requests with valid client cert
  test('test_ac6_mtls_enabled_enforces_client_cert', async () => {
    const config: TlsConfig = {
      enabled: true,
      certPath: 'test/certs/server.crt',
      keyPath: 'test/certs/server.key',
      mtlsEnabled: true,
      caCertPath: 'test/certs/ca.crt'
    };
    const result = await startServerWithTls(config);
    
    expect(result.success).toBe(true);
    // Verify mTLS is configured on server
    const httpsServer = result.server?.server as ReturnType<typeof createHttpsServer>;
    expect(httpsServer.options.requestCert).toBe(true);
    expect(httpsServer.options.rejectUnauthorized).toBe(true);
  });

  // AC-7: mTLS enabled but missing CA cert path, fails with MISSING_CA_CERT
  test('test_ac7_mtls_enabled_missing_ca_cert_fails', async () => {
    const config: TlsConfig = {
      enabled: true,
      certPath: 'test/certs/server.crt',
      keyPath: 'test/certs/server.key',
      mtlsEnabled: true
    };
    const result = await startServerWithTls(config);
    
    expect(result.success).toBe(false);
    expect(result.error).toBe(TlsConfigError.MISSING_CA_CERT);
  });

  // AC-8: mTLS enabled but TLS disabled, fails with TLS_DISABLED_WITH_MTLS
  test('test_ac8_mtls_enabled_tls_disabled_fails', async () => {
    const config: TlsConfig = {
      enabled: false,
      mtlsEnabled: true,
      caCertPath: 'test/certs/ca.crt'
    };
    const result = await startServerWithTls(config);
    
    expect(result.success).toBe(false);
    expect(result.error).toBe(TlsConfigError.TLS_DISABLED_WITH_MTLS);
  });
});
