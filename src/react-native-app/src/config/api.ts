import { validateBackendUrl } from '../utils/validateBackendUrl';

const DEFAULT_BASE_URL = "http://localhost:8080";

/**
 * Normalized, validated base URL for backend API calls
 * Defaults to "http://localhost:8080" if no environment variable is set
 * @throws {Error} Propagates validation errors from validateBackendUrl if custom URL is provided but invalid
 */
export const BACKEND_BASE_URL: string = (() => {
  const envUrl = process.env.EXPO_PUBLIC_OTEL_DEMO_BACKEND_BASE_URL;
  
  if (!envUrl) {
    return DEFAULT_BASE_URL;
  }

  return validateBackendUrl(envUrl);
})();
