import { status } from '@grpc/grpc-js';
import { logger } from './logger';

export interface GrpcRetryConfig {
  maxAttempts: number;
  initialBackoffMs: number;
  maxBackoffMs: number;
  backoffMultiplier: number;
}

export const IDEMPOTENT_GRPC_METHODS: ReadonlyArray<string> = [
  'oteldemo.CartService/GetCart',
  'oteldemo.ProductCatalogService/GetProduct',
  'oteldemo.ProductCatalogService/ListProducts',
  'oteldemo.ShippingService/GetQuote',
];

const RETRYABLE_STATUS_CODES: ReadonlyArray<status> = [
  status.UNAVAILABLE,
  status.DEADLINE_EXCEEDED,
  status.RESOURCE_EXHAUSTED,
  status.ABORTED,
  status.INTERNAL,
];

export const getRetryConfigFromEnv = (): GrpcRetryConfig => {
  return {
    maxAttempts: parseInt(process.env.GRPC_RETRY_MAX_ATTEMPTS || '3', 10),
    initialBackoffMs: parseInt(process.env.GRPC_RETRY_INITIAL_BACKOFF_MS || '100', 10),
    maxBackoffMs: parseInt(process.env.GRPC_RETRY_MAX_BACKOFF_MS || '2000', 10),
    backoffMultiplier: parseFloat(process.env.GRPC_RETRY_BACKOFF_MULTIPLIER || '2.0'),
  };
};

const defaultConfig = getRetryConfigFromEnv();

const sleep = (ms: number): Promise<void> => new Promise(resolve => setTimeout(resolve, ms));

export const isRetryableError = (error: any): boolean => {
  return error?.code !== undefined && RETRYABLE_STATUS_CODES.includes(error.code);
};

export const isIdempotentMethod = (methodName: string): boolean => {
  return IDEMPOTENT_GRPC_METHODS.includes(methodName);
};

export async function withGrpcRetry<T>(
  grpcCall: () => Promise<T>,
  methodName: string,
  timeoutMs?: number,
  config: GrpcRetryConfig = defaultConfig
): Promise<T> {
  const startTime = Date.now();
  let attempt = 0;

  while (true) {
    try {
      return await grpcCall();
    } catch (error: any) {
      attempt++;
      
      if (!isIdempotentMethod(methodName)) {
        logger.debug(`Non-idempotent method ${methodName} failed, not retrying`, {
          methodName,
          errorCode: error?.code,
          errorMessage: error?.message,
        });
        throw error;
      }

      if (attempt > config.maxAttempts) {
        logger.warn(`Exhausted all retry attempts for gRPC method ${methodName}`, {
          methodName,
          totalAttempts: attempt,
          finalErrorCode: error?.code,
          finalErrorMessage: error?.message,
        });
        throw error;
      }

      if (!isRetryableError(error)) {
        logger.debug(`Non-retryable error for gRPC method ${methodName}, not retrying`, {
          methodName,
          attempt,
          errorCode: error?.code,
          errorMessage: error?.message,
        });
        throw error;
      }

      if (timeoutMs && Date.now() - startTime > timeoutMs) {
        logger.warn(`Timeout exceeded for gRPC method ${methodName} after ${attempt} attempts`, {
          methodName,
          totalAttempts: attempt,
          elapsedTimeMs: Date.now() - startTime,
          timeoutMs,
          errorCode: error?.code,
        });
        throw error;
      }

      const delayMs = Math.min(
        config.initialBackoffMs * Math.pow(config.backoffMultiplier, attempt - 1),
        config.maxBackoffMs
      );

      logger.info(`Retrying gRPC method ${methodName} after failed attempt`, {
        methodName,
        attemptNumber: attempt,
        delayBeforeNextAttemptMs: delayMs,
        errorCode: error?.code,
        errorMessage: error?.message,
      });

      await sleep(delayMs);
    }
  }
}
