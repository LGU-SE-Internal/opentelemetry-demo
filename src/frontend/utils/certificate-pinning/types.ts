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

/** Custom error types for certificate pinning */
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
