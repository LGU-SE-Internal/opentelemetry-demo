import * as crypto from 'crypto';
import { logs } from '@opentelemetry/api-logs';
import {
  CertificatePinningConfig,
  PinValidationMetrics,
  PinningNotInitializedError,
  InvalidPinConfigError,
  DomainNotPinnedError,
  PinMismatchError,
  PinnedPublicKey,
} from './types';

const logger = logs.getLogger('frontend', '1.0.0');

let currentConfig: CertificatePinningConfig | null = null;
const metricsStore: PinValidationMetrics[] = [];
const remoteConfigUpdateListeners: Array<(config: CertificatePinningConfig) => void> = [];

/**
 * Validate a pin configuration structure
 * @param config Configuration to validate
 * @throws InvalidPinConfigError if config is invalid
 */
function validateConfig(config: CertificatePinningConfig): void {
  if (!config) {
    throw new InvalidPinConfigError('Config cannot be null or undefined');
  }
  if (typeof config.enforcePinning !== 'boolean') {
    throw new InvalidPinConfigError('enforcePinning must be a boolean');
  }
  if (typeof config.expiredPinGracePeriodDays !== 'number' || config.expiredPinGracePeriodDays < 0) {
    throw new InvalidPinConfigError('expiredPinGracePeriodDays must be a non-negative number');
  }
  if (!Array.isArray(config.pins)) {
    throw new InvalidPinConfigError('pins must be an array');
  }
  config.pins.forEach((pin, index) => {
    if (typeof pin.domain !== 'string' || pin.domain.length === 0) {
      throw new InvalidPinConfigError(`Pin at index ${index} has invalid domain`);
    }
    if (typeof pin.hash !== 'string' || pin.hash.length === 0) {
      throw new InvalidPinConfigError(`Pin at index ${index} has invalid hash`);
    }
    if (pin.algorithm !== 'sha256' && pin.algorithm !== 'sha384') {
      throw new InvalidPinConfigError(`Pin at index ${index} has invalid algorithm`);
    }
    if (pin.expiresAt !== undefined && (typeof pin.expiresAt !== 'number' || pin.expiresAt <= 0)) {
      throw new InvalidPinConfigError(`Pin at index ${index} has invalid expiresAt timestamp`);
    }
  });
}

/**
 * Extract public key from X509 certificate string and compute its hash
 * @param certificate PEM encoded X509 certificate
 * @param algorithm Hash algorithm to use
 * @returns Base64 encoded hash of the public key
 */
function getPublicKeyHash(certificate: string, algorithm: 'sha256' | 'sha384'): string {
  // In a real React Native environment, you would use a library like react-native-ssl-pinning
  // to extract the public key from the certificate. This is a placeholder implementation.
  const pubKey = crypto.createPublicKey(certificate);
  const pubKeyDer = pubKey.export({ type: 'spki', format: 'der' });
  return crypto.createHash(algorithm).update(pubKeyDer).digest('base64');
}

/**
 * Initialize certificate pinning with default config and register remote config update listener
 * @param defaultConfig Fallback pin config to use if remote config is unavailable
 */
export async function initializeCertificatePinning(
  defaultConfig: CertificatePinningConfig
): Promise<void> {
  validateConfig(defaultConfig);
  currentConfig = { ...defaultConfig };
  metricsStore.length = 0;
  
  // Register remote config update listener (implementation depends on your remote config service)
  // Example for Firebase Remote Config:
  // remoteConfig.onUpdate(() => {
  //   const newConfig = remoteConfig.getValue('certificatePinningConfig').asString();
  //   updatePinConfig(JSON.parse(newConfig));
  // });
}

/**
 * Validate a server certificate against pinned public keys for the given domain
 * @param domain Target service domain name
 * @param certificateChain X509 certificate chain presented by the server
 * @returns True if validation passes, false otherwise
 * @throws PinningNotInitializedError if pinning is not initialized
 */
export async function validateCertificatePin(
  domain: string,
  certificateChain: string[]
): Promise<boolean> {
  if (!currentConfig) {
    throw new PinningNotInitializedError();
  }

  const startTime = Date.now();
  let success = false;
  let errorType: PinValidationMetrics['errorType'] = undefined;

  try {
    // Get all pins for the domain
    const domainPins = currentConfig.pins.filter(pin => pin.domain === domain);
    if (domainPins.length === 0) {
      errorType = 'NO_PIN_CONFIGURED';
      if (currentConfig.enforcePinning) {
        throw new DomainNotPinnedError(domain);
      }
      return true;
    }

    if (!certificateChain || certificateChain.length === 0) {
      errorType = 'CERTIFICATE_ERROR';
      throw new Error('No certificate chain provided');
    }

    const now = Math.floor(Date.now() / 1000);
    const gracePeriodSeconds = currentConfig.expiredPinGracePeriodDays * 86400;
    let foundValidPin = false;
    let foundExpiredPin = false;

    // Check each certificate in the chain
    for (const cert of certificateChain) {
      // Check each pin for the domain
      for (const pin of domainPins) {
        const certHash = getPublicKeyHash(cert, pin.algorithm);
        if (certHash === pin.hash) {
          // Check if pin is not expired, or within grace period
          if (!pin.expiresAt || pin.expiresAt > now) {
            foundValidPin = true;
            break;
          } else if (pin.expiresAt + gracePeriodSeconds > now) {
            foundExpiredPin = true;
          }
        }
      }
      if (foundValidPin) break;
    }

    if (foundValidPin) {
      success = true;
      return true;
    }

    if (foundExpiredPin) {
      errorType = 'PIN_EXPIRED';
      // Allow request if within grace period
      logger.warn(`Certificate pin for ${domain} expired, but within grace period`, { domain });
      return true;
    }

    errorType = 'PIN_MISMATCH';
    if (currentConfig.enforcePinning) {
      throw new PinMismatchError(domain);
    }
    logger.warn(`Certificate pin mismatch for ${domain}, enforcePinning is disabled`, { domain, enforcePinning: currentConfig.enforcePinning });
    return true;
  } catch (error) {
    logger.error(`Certificate pin validation failed for ${domain}:`, { domain, error });
    if (currentConfig.enforcePinning) {
      throw error;
    }
    return true;
  } finally {
    const validationDurationMs = Date.now() - startTime;
    metricsStore.push({
      domain,
      success,
      errorType,
      validationDurationMs,
    });
  }
}

/**
 * Get current pin validation metrics for monitoring
 * @returns Aggregated metrics for all pin validation attempts since app launch
 */
export async function getPinValidationMetrics(): Promise<PinValidationMetrics[]> {
  if (!currentConfig) {
    throw new PinningNotInitializedError();
  }
  return [...metricsStore];
}

/**
 * Manually update the pin config (used by remote config sync handler)
 * @param newConfig New pin configuration to apply
 */
export async function updatePinConfig(newConfig: CertificatePinningConfig): Promise<void> {
  try {
    validateConfig(newConfig);
    // Check if config is actually different
    if (JSON.stringify(newConfig) === JSON.stringify(currentConfig)) {
      return;
    }
    currentConfig = { ...newConfig };
    // Notify listeners
    remoteConfigUpdateListeners.forEach(listener => listener(currentConfig));
    logger.info('Certificate pinning config updated successfully');
  } catch (error) {
    logger.error('Failed to update certificate pinning config:', { error });
    // Keep existing config if new one is invalid
    if (error instanceof InvalidPinConfigError) {
      throw error;
    }
    throw new InvalidPinConfigError((error as Error).message);
  }
}

/**
 * Register a listener to be notified when pin config is updated
 * @param listener Callback function to receive new config
 */
export function addConfigUpdateListener(
  listener: (config: CertificatePinningConfig) => void
): void {
  remoteConfigUpdateListeners.push(listener);
}

/**
 * Remove a previously registered config update listener
 * @param listener Listener to remove
 */
export function removeConfigUpdateListener(
  listener: (config: CertificatePinningConfig) => void
): void {
  const index = remoteConfigUpdateListeners.indexOf(listener);
  if (index !== -1) {
    remoteConfigUpdateListeners.splice(index, 1);
  }
}
