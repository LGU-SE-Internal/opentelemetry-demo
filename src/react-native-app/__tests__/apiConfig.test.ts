import { BACKEND_BASE_URL } from '../src/config/api';

describe('api config BACKEND_BASE_URL tests', () => {
  beforeEach(() => {
    // Clear environment variable before each test
    delete process.env.EXPO_PUBLIC_OTEL_DEMO_BACKEND_BASE_URL;
    // Reset module cache to re-import with new env
    jest.resetModules();
  });

  test('ac1_no_env_var_returns_default_localhost', () => {
    const { BACKEND_BASE_URL } = require('../src/config/api');
    expect(BACKEND_BASE_URL).toBe('http://localhost:8080');
  });

  test('ac2_env_var_set_returns_valid_normalized_url', () => {
    process.env.EXPO_PUBLIC_OTEL_DEMO_BACKEND_BASE_URL = 'https://demo.opentelemetry.io/';
    const { BACKEND_BASE_URL } = require('../src/config/api');
    expect(BACKEND_BASE_URL).toBe('https://demo.opentelemetry.io');
  });

  test('ac3_invalid_scheme_env_var_throws_error_on_import', () => {
    process.env.EXPO_PUBLIC_OTEL_DEMO_BACKEND_BASE_URL = 'ftp://invalid-scheme.com';
    expect(() => require('../src/config/api')).toThrowError('Invalid backend URL: must start with http:// or https://');
  });

  test('ac4_missing_host_env_var_throws_error_on_import', () => {
    process.env.EXPO_PUBLIC_OTEL_DEMO_BACKEND_BASE_URL = 'http://';
    expect(() => require('../src/config/api')).toThrowError('Invalid backend URL: host is required');
  });
});
