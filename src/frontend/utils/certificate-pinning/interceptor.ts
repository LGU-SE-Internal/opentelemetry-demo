import axios from 'axios';
import { validateCertificatePin, PinningNotInitializedError } from './index';
import { PinMismatchError, DomainNotPinnedError } from './types';

// In a real React Native environment, you would use the TLS socket implementation
// to get the certificate chain during the handshake. This is a placeholder implementation
// that demonstrates how to integrate with the network layer.

/**
 * Axios interceptor to validate certificate pins for all requests
 */
export function setupCertificatePinningInterceptor(): void {
  axios.interceptors.request.use(async (config) => {
    try {
      const url = new URL(config.url || '', config.baseURL);
      const domain = url.hostname;
      
      // In React Native, you would get the certificate chain from the TLS connection
      // This is a placeholder - actual implementation depends on your network library
      // const certificateChain = await getCertificateChainFromConnection(domain);
      
      // For demonstration purposes, we'll skip validation for now
      // Replace with actual certificate chain extraction
      const certificateChain: string[] = [];
      
      if (certificateChain.length > 0) {
        await validateCertificatePin(domain, certificateChain);
      }
      
      return config;
    } catch (error) {
      if (
        error instanceof PinningNotInitializedError ||
        error instanceof PinMismatchError ||
        error instanceof DomainNotPinnedError
      ) {
        return Promise.reject(error);
      }
      // Allow other errors to pass through
      return config;
    }
  });
}

/**
 * Fetch API interceptor to validate certificate pins for all requests
 */
export function setupFetchInterceptor(): void {
  const originalFetch = global.fetch;
  
  global.fetch = async (input, init) => {
    try {
      const url = new URL(typeof input === 'string' ? input : input.url);
      const domain = url.hostname;
      
      // In React Native, you would get the certificate chain from the TLS connection
      // const certificateChain = await getCertificateChainFromConnection(domain);
      const certificateChain: string[] = [];
      
      if (certificateChain.length > 0) {
        await validateCertificatePin(domain, certificateChain);
      }
      
      return originalFetch(input, init);
    } catch (error) {
      if (
        error instanceof PinningNotInitializedError ||
        error instanceof PinMismatchError ||
        error instanceof DomainNotPinnedError
      ) {
        return Promise.reject(error);
      }
      throw error;
    }
  };
}
