// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import { createHash } from 'crypto';

/** Public key hash algorithm supported for pinning */
export type PinHashAlgorithm = 'sha256' | 'sha384';

/** Pinned public key entry for a backend service */
export interface PinnedPublicKey {
  /** Fully qualified domain name of the service */
  domain: string;
  /** Hash of the public key (base64 encoded) */
  hash: string;
  /** Algorithm used to generate the hash */
  algorithm: PinHashAlgorithm;
  /** Optional expiration timestamp (unix epoch seconds) for the pin */
  expiresAt?: number;
}

/** Pinning configuration structure (used for local default and remote config updates) */
export interface CertificatePinningConfig {
  /** List of pinned public keys for all backend services */
  pins: PinnedPublicKey[];
  /** Whether pin validation is enforced (if false, only logs failures) */
  enforcePinning: boolean;
  /** Maximum number of days to allow expired pins before enforcing new ones */
  expiredPinGracePeriodDays: number;
}

/** Pin validation result metrics */
export interface PinValidationMetrics {
  domain: string;
  success: boolean;
  errorType?: 'PIN_MISMATCH' | 'PIN_EXPIRED' | 'NO_PIN_CONFIGURED' | 'CERTIFICATE_ERROR';
  validationDurationMs: number;
}

// Custom Error Types
export class PinningNotInitializedError extends Error {
  constructor() {
    super('Certificate pinning has not been initialized');
    this.name = 'PinningNotInitializedError';
  }
}

export class InvalidPinConfigError extends Error {
  constructor(message: string) {
    super(`Invalid pin config: ${message}`);
    this.name = 'InvalidPinConfigError';
  }
}

export class DomainNotPinnedError extends Error {
  constructor(domain: string) {
    super(`No pin configured for domain: ${domain}`);
    this.name = 'DomainNotPinnedError';
  }
}

export class PinMismatchError extends Error {
  constructor(domain: string) {
    super(`Public key hash mismatch for domain: ${domain}`);
    this.name = 'PinMismatchError';
  }
}

let currentConfig: CertificatePinningConfig | null = null;
const metrics: PinValidationMetrics[] = [];
const remoteConfigListeners: Array<(config: CertificatePinningConfig) => void> = [];

/**
 * Initialize certificate pinning with default config and register remote config update listener
 * @param defaultConfig Fallback pin config to use if remote config is unavailable
 */
export async function initializeCertificatePinning(defaultConfig: CertificatePinningConfig): Promise<void> {
  validateConfig(defaultConfig);
  currentConfig = { ...defaultConfig };
  
  // Register remote config listener (this would typically hook into your remote config service)
  // For this implementation, we expose a manual update method and support listener registration
  console.log('Certificate pinning initialized successfully');
}

/**
 * Validate a server certificate against pinned public keys for the given domain
 * @param domain Target service domain name
 * @param certificateChain X509 certificate chain presented by the server (DER encoded base64 strings)
 * @returns True if validation passes, false otherwise
 * @throws Error if pinning is not initialized
 */
export async function validateCertificatePin(domain: string, certificateChain: string[]): Promise<boolean> {
  if (!currentConfig) {
    throw new PinningNotInitializedError();
  }

  const startTime = Date.now();
  let success = false;
  let errorType: PinValidationMetrics['errorType'] = undefined;

  try {
    const domainPins = currentConfig.pins.filter(pin => pin.domain === domain);
    
    if (domainPins.length === 0) {
      errorType = 'NO_PIN_CONFIGURED';
      if (currentConfig.enforcePinning) {
        throw new DomainNotPinnedError(domain);
      }
      // If not enforcing, just log and return success
      console.warn(`No pin configured for domain ${domain}, skipping validation`);
      return true;
    }

    const now = Math.floor(Date.now() / 1000);
    const gracePeriodMs = currentConfig.expiredPinGracePeriodDays * 24 * 60 * 60 * 1000;

    // Check each certificate in the chain
    for (const cert of certificateChain) {
      try {
        // Extract public key from certificate (simplified for example, actual implementation would parse X509 cert)
        // In a real React Native app, you would use a library like react-native-ssl-pinning to get the public key
        const publicKey = extractPublicKeyFromCertificate(cert);
        
        // Check against all pins for the domain
        for (const pin of domainPins) {
          // Calculate hash of public key
          const hash = calculatePublicKeyHash(publicKey, pin.algorithm);
          
          if (hash === pin.hash) {
            // Check if pin is expired
            if (pin.expiresAt && pin.expiresAt < now) {
              const expiredMs = (now - pin.expiresAt) * 1000;
              if (expiredMs > gracePeriodMs) {
                errorType = 'PIN_EXPIRED';
                continue;
              }
              // Pin is expired but within grace period
              console.warn(`Pin for domain ${domain} is expired but within grace period, allowing connection`);
            }
            // Valid pin found
            success = true;
            return true;
          }
        }
      } catch (e) {
        console.error('Error processing certificate:', e);
        errorType = 'CERTIFICATE_ERROR';
      }
    }

    // No valid pin found
    errorType = 'PIN_MISMATCH';
    if (currentConfig.enforcePinning) {
      throw new PinMismatchError(domain);
    }

    console.warn(`Pin mismatch for domain ${domain}, allowing connection because pinning is not enforced`);
    success = true;
    return true;
  } finally {
    const durationMs = Date.now() - startTime;
    metrics.push({
      domain,
      success,
      errorType,
      validationDurationMs: durationMs
    });
  }
}

/**
 * Get current pin validation metrics for monitoring
 * @returns Aggregated metrics for all pin validation attempts since app launch
 */
export async function getPinValidationMetrics(): Promise<PinValidationMetrics[]> {
  return [...metrics];
}

/**
 * Manually update the pin config (used by remote config sync handler)
 * @param newConfig New pin configuration to apply
 */
export async function updatePinConfig(newConfig: CertificatePinningConfig): Promise<void> {
  try {
    validateConfig(newConfig);
    
    // Check if config is actually different to avoid unnecessary updates
    if (JSON.stringify(currentConfig) === JSON.stringify(newConfig)) {
      console.log('New pin config is identical to current config, skipping update');
      return;
    }

    currentConfig = { ...newConfig };
    console.log('Pin config updated successfully');
    
    // Notify listeners of config change
    for (const listener of remoteConfigListeners) {
      try {
        listener(currentConfig);
      } catch (e) {
        console.error('Error in pin config update listener:', e);
      }
    }
  } catch (e) {
    console.error('Failed to update pin config:', e);
    // Keep existing valid config, don't throw to avoid outages
    if (currentConfig) {
      console.log('Retaining existing valid pin config');
    }
    throw e;
  }
}

/**
 * Register a listener to be notified when pin config is updated
 * @param listener Callback function to receive new config
 */
export function registerConfigUpdateListener(listener: (config: CertificatePinningConfig) => void): void {
  remoteConfigListeners.push(listener);
}

/**
 * Validate a pin configuration structure
 * @param config Config to validate
 * @throws InvalidPinConfigError if config is invalid
 */
function validateConfig(config: CertificatePinningConfig): void {
  if (!config) {
    throw new InvalidPinConfigError('Config cannot be null or undefined');
  }

  if (!Array.isArray(config.pins)) {
    throw new InvalidPinConfigError('pins must be an array');
  }

  if (typeof config.enforcePinning !== 'boolean') {
    throw new InvalidPinConfigError('enforcePinning must be a boolean');
  }

  if (typeof config.expiredPinGracePeriodDays !== 'number' || config.expiredPinGracePeriodDays < 0) {
    throw new InvalidPinConfigError('expiredPinGracePeriodDays must be a non-negative number');
  }

  for (let i = 0; i < config.pins.length; i++) {
    const pin = config.pins[i];
    if (typeof pin.domain !== 'string' || pin.domain.trim() === '') {
      throw new InvalidPinConfigError(`Pin at index ${i} has invalid domain`);
    }
    if (typeof pin.hash !== 'string' || pin.hash.trim() === '') {
      throw new InvalidPinConfigError(`Pin for domain ${pin.domain} has invalid hash`);
    }
    if (!['sha256', 'sha384'].includes(pin.algorithm)) {
      throw new InvalidPinConfigError(`Pin for domain ${pin.domain} has invalid algorithm: ${pin.algorithm}`);
    }
    if (pin.expiresAt !== undefined && (typeof pin.expiresAt !== 'number' || pin.expiresAt < 0)) {
      throw new InvalidPinConfigError(`Pin for domain ${pin.domain} has invalid expiresAt value`);
    }
  }
}

/**
 * Calculate the hash of a public key using the specified algorithm
 * @param publicKey DER encoded public key as base64 string
 * @param algorithm Hash algorithm to use
 * @returns Base64 encoded hash
 */
function calculatePublicKeyHash(publicKey: string, algorithm: PinHashAlgorithm): string {
  const publicKeyBuffer = Buffer.from(publicKey, 'base64');
  const hash = createHash(algorithm);
  hash.update(publicKeyBuffer);
  return hash.digest('base64');
}

/**
 * Extract public key from X509 certificate
 * @param certificate DER encoded X509 certificate as base64 string
 * @returns DER encoded public key as base64 string
 * @note This is a placeholder implementation. In a real React Native app,
 *       you would use a native module to extract the public key from the certificate.
 */
function extractPublicKeyFromCertificate(certificate: string): string {
  // In a real implementation, you would parse the X509 certificate here
  // For example, using react-native-ssl-pinning or a similar library
  // This placeholder just returns the input for demonstration purposes
  return certificate;
}
