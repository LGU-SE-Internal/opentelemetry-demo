import { validateBackendUrl } from '../src/utils/validateBackendUrl';

describe('validateBackendUrl function tests', () => {
  test('ac2_valid_url_without_trailing_slash_returns_same', () => {
    const url = 'https://demo.opentelemetry.io';
    expect(validateBackendUrl(url)).toBe(url);
  });

  test('ac2_valid_url_with_trailing_slash_returns_without_slash', () => {
    const input = 'https://demo.opentelemetry.io/';
    const expected = 'https://demo.opentelemetry.io';
    expect(validateBackendUrl(input)).toBe(expected);
  });

  test('ac3_url_with_invalid_scheme_throws_error', () => {
    const url = 'ftp://invalid-scheme.com';
    expect(() => validateBackendUrl(url)).toThrowError('Invalid backend URL: must start with http:// or https://');
  });

  test('ac4_url_without_host_throws_error', () => {
    const url = 'http://';
    expect(() => validateBackendUrl(url)).toThrowError('Invalid backend URL: host is required');
  });
});
