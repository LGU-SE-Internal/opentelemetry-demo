import { status as GrpcStatus } from '@grpc/grpc-js';
import { logger } from './telemetry/logging';

// Retry configuration type
export interface GrpcRetryConfig {
  maxAttempts: number;
  initialBackoffMs: number;
  maxBackoffMs: number;
  backoffMultiplier: number;
}

// Idempotent gRPC method list (whitelist)
export const IDEMPOTENT_GRPC_METHODS: ReadonlyArray<string> = [
  'oteldemo.CartService/GetCart',
  'oteldemo.ProductCatalogService/GetProduct',
  'oteldemo.ProductCatalogService/ListProducts',
  'oteldemo.ShippingService/GetQuote',
  // Read-only methods are idempotent; write methods are excluded by default
];

// Load configuration from environment variables
const loadConfig = (): GrpcRetryConfig => {
  return {
    maxAttempts: parseInt(process.env.GRPC_RETRY_MAX_ATTEMPTS || '3', 10),
    initialBackoffMs: parseInt(process.env.GRPC_RETRY_INITIAL_BACKOFF_MS || '100', 10),
    maxBackoffMs: parseInt(process.env.GRPC_RETRY_MAX_BACKOFF_MS || '2000', 10),
    backoffMultiplier: parseFloat(process.env.GRPC_RETRY_BACKOFF_MULTIPLIER || '2.0'),
  };
};

export let config: GrpcRetryConfig = loadConfig();

// Reload config (for testing)
export const reloadConfig = () => {
  config = loadConfig();
};

// Helper to check if error is transient and retryable
const isRetryableError = (error: any): boolean => {
  if (!error || !('code' in error)) return false;

  const retryableCodes = [
    GrpcStatus.UNAVAILABLE,
    GrpcStatus.DEADLINE_EXCEEDED,
    GrpcStatus.RESOURCE_EXHAUSTED,
    GrpcStatus.ABORTED,
    GrpcStatus.INTERNAL
  ];

  return retryableCodes.includes(error.code);
};

// Helper for exponential backoff delay
const calculateDelay = (attempt: number, config: GrpcRetryConfig): number => {
  return Math.min(
    config.initialBackoffMs * Math.pow(config.backoffMultiplier, attempt - 1),
    config.maxBackoffMs
  );
};

const delay = (ms: number): Promise<void> => {
  return new Promise(resolve => setTimeout(resolve, ms));
};

// Retry wrapper function signature
export async function withGrpcRetry<T>(
  grpcCall: () => Promise<T>,
  methodName: string,
  timeoutMs?: number
): Promise<T> {
  const startTime = Date.now();
  let attempt = 0;
  const maxAttempts = config.maxAttempts;
  
  // Check if method is idempotent
  const isIdempotent = IDEMPOTENT_GRPC_METHODS.includes(methodName);
  
  // If max attempts is 0 or method is non-idempotent, run once without retry
  if (maxAttempts <= 0 || !isIdempotent) {
    return grpcCall();
  }

  while (attempt <= maxAttempts) {
    try {
      attempt++;
      return await grpcCall();
    } catch (error: any) {
      // Check if we have attempts left and error is retryable
      const hasMoreAttempts = attempt < maxAttempts;
      const errorIsRetryable = isRetryableError(error);
      
      // Calculate elapsed time if timeout is provided
      const elapsed = Date.now() - startTime;
      const timeoutExceeded = timeoutMs ? elapsed >= timeoutMs : false;

      if (hasMoreAttempts && errorIsRetryable && !timeoutExceeded) {
        const retryDelay = calculateDelay(attempt, config);
        // Check if even after delay we would exceed timeout
        if (timeoutMs && (elapsed + retryDelay) >= timeoutMs) {
          logger.warn({
            methodName,
            totalAttempts: attempt,
            finalErrorCode: error.code,
            reason: 'Timeout would be exceeded by next retry'
          }, 'gRPC retry aborted due to impending timeout');
          throw error;
        }

        // Log retry attempt
        logger.info({
          methodName,
          attemptNumber: attempt,
          delayBeforeNextAttempt: retryDelay,
          errorCode: error.code
        }, 'Retrying failed gRPC call');
        
        await delay(retryDelay);
      } else {
        // Log exhausted attempts
        if (attempt > 1) {
          logger.warn({
            methodName,
            totalAttempts: attempt,
            finalErrorCode: error.code
          }, 'All gRPC retry attempts exhausted');
        }
        throw error;
      }
    }
  }

  // Should never reach here, just in case
  throw new Error('Unexpected error in retry logic');
}

