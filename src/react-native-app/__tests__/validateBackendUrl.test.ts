import { validateBackendUrl } from '../src/utils/validateBackendUrl';

describe('validateBackendUrl', () => {
  test('ac2_valid_url_without_trailing_slash_returns_same', () => {
    expect(validateBackendUrl('https://demo.opentelemetry.io')).toBe('https://demo.opentelemetry.io');
  });

  test('ac2_valid_url_with_trailing_slash_returns_without_slash', () => {
    expect(validateBackendUrl('https://demo.opentelemetry.io/')).toBe('https://demo.opentelemetry.io');
  });

  test('ac3_url_with_invalid_scheme_throws_error', () => {
    expect(() => validateBackendUrl('ftp://invalid-scheme.com')).toThrow("Invalid backend URL: must start with http:// or https://");
  });

  test('ac4_url_without_host_throws_error', () => {
    expect(() => validateBackendUrl('http://')).toThrow("Invalid backend URL: host is required");
  });

  test('valid url with port returns correctly', () => {
    expect(validateBackendUrl('http://localhost:8080/')).toBe('http://localhost:8080');
  });

  test('valid url with path prefix returns correctly', () => {
    expect(validateBackendUrl('https://demo.example.com/api/v1/')).toBe('https://demo.example.com/api/v1');
  });
});
