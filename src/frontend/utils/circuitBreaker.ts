import CircuitBreaker from 'opossum';

export enum CircuitState {
  CLOSED = 'CLOSED',
  OPEN = 'OPEN',
  HALF_OPEN = 'HALF_OPEN'
}

export interface CircuitBreakerConfig {
  serviceName: string;
  failureThreshold?: number;
  failureThresholdWindowMs?: number;
  timeoutMs?: number;
  recoveryDelayMs?: number;
}

export class CircuitOpenError extends Error {
  constructor(serviceName: string) {
    super(`Circuit is OPEN for service ${serviceName}, requests are temporarily blocked`);
    this.name = 'CircuitOpenError';
  }
}

export interface CircuitBreaker {
  execute<T>(request: () => Promise<T>): Promise<T>;
  getState(): CircuitState;
}

const circuitBreakers: Map<string, CircuitBreaker> = new Map();

class CircuitBreakerImpl implements CircuitBreaker {
  private breaker: CircuitBreaker;
  private config: Required<CircuitBreakerConfig>;

  constructor(config: CircuitBreakerConfig) {
    this.config = {
      failureThreshold: 5,
      failureThresholdWindowMs: 10000,
      timeoutMs: 5000,
      recoveryDelayMs: 30000,
      ...config
    };

    const opossumOptions = {
      timeout: this.config.timeoutMs,
      errorThresholdPercentage: 100,
      resetTimeout: this.config.recoveryDelayMs,
      rollingCountTimeout: this.config.failureThresholdWindowMs,
      rollingCountBuckets: 10,
      volumeThreshold: this.config.failureThreshold,
      name: this.config.serviceName
    };

    this.breaker = new CircuitBreaker(async (request: () => Promise<any>) => {
      return request();
    }, opossumOptions);

    this.breaker.on('open', () => {
      console.warn(JSON.stringify({
        level: 'warn',
        event: 'circuit_state_change',
        service: this.config.serviceName,
        old_state: CircuitState.CLOSED,
        new_state: CircuitState.OPEN,
        timestamp: new Date().toISOString()
      }));
    });

    this.breaker.on('halfOpen', () => {
      console.warn(JSON.stringify({
        level: 'warn',
        event: 'circuit_state_change',
        service: this.config.serviceName,
        old_state: CircuitState.OPEN,
        new_state: CircuitState.HALF_OPEN,
        timestamp: new Date().toISOString()
      }));
    });

    this.breaker.on('close', () => {
      console.warn(JSON.stringify({
        level: 'warn',
        event: 'circuit_state_change',
        service: this.config.serviceName,
        old_state: CircuitState.HALF_OPEN,
        new_state: CircuitState.CLOSED,
        timestamp: new Date().toISOString()
      }));
    });

    this.breaker.on('reject', () => {
      console.info(JSON.stringify({
        level: 'info',
        event: 'circuit_request_rejected',
        service: this.config.serviceName,
        timestamp: new Date().toISOString()
      }));
      throw new CircuitOpenError(this.config.serviceName);
    });
  }

  async execute<T>(request: () => Promise<T>): Promise<T> {
    try {
      return await this.breaker.fire(request);
    } catch (error: any) {
      if (error instanceof CircuitOpenError) {
        throw error;
      }
      if (error?.type === 'TimeoutError') {
        throw new Error(`Timed out after ${this.config.timeoutMs}ms`);
      }
      throw error;
    }
  }

  getState(): CircuitState {
    const opossumState = this.breaker.status.state;
    switch (opossumState) {
      case CircuitBreaker.STATES.CLOSED:
        return CircuitState.CLOSED;
      case CircuitBreaker.STATES.OPEN:
        return CircuitState.OPEN;
      case CircuitBreaker.STATES.HALF_OPEN:
        return CircuitState.HALF_OPEN;
      default:
        throw new Error(`Unknown circuit state: ${opossumState}`);
    }
  }
}

export function getCircuitBreaker(config: CircuitBreakerConfig): CircuitBreaker {
  if (!circuitBreakers.has(config.serviceName)) {
    circuitBreakers.set(config.serviceName, new CircuitBreakerImpl(config));
  }
  return circuitBreakers.get(config.serviceName)!;
}
