// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0
const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');
const path = require('path');
const { RateLimiterMemory } = require('rate-limiter-flexible');
const logger = require('../logger');

// Mock logger to capture logs
jest.mock('../logger', () => ({
  warn: jest.fn(),
  info: jest.fn(),
  error: jest.fn(),
  fatal: jest.fn()
}));

// Mock opentelemetry trace
jest.mock('@opentelemetry/api', () => ({
  ...jest.requireActual('@opentelemetry/api'),
  trace: {
    getActiveSpan: jest.fn(() => ({
      spanContext: () => ({
        traceId: 'test-trace-id',
        spanId: 'test-span-id'
      })
    }))
  }
}));

describe('Payment Service Rate Limiting AC Tests', () => {
  let server;
  let client;
  let protoPackage;

  beforeAll(() => {
    const PROTO_PATH = path.join(__dirname, '../../../protos/oteldemo.proto');
    const packageDefinition = protoLoader.loadSync(
      PROTO_PATH,
      { keepCase: true,
        longs: String,
        enums: String,
        defaults: true,
        oneofs: true
      });
    protoPackage = grpc.loadPackageDefinition(packageDefinition).oteldemo;
  });

  beforeEach(() => {
    // Reset env vars before each test
    delete process.env.PAYMENT_SERVICE_RATE_LIMIT_DEFAULT;
    delete process.env.PAYMENT_SERVICE_RATE_LIMIT_OVERRIDES;
    // Reset modules and rate limiters
    jest.resetModules();
    // Clear logger mocks
    logger.warn.mockClear();
  });

  afterEach(async () => {
    if (server) {
      await new Promise(resolve => server.tryShutdown(resolve));
      server = null;
    }
    if (client) {
      client.close();
      client = null;
    }
  });

  function createServerAndClient() {
    const { rateLimitInterceptor } = require('../index');
    server = new grpc.Server({ interceptors: [rateLimitInterceptor] });
    // Add mock service implementation
    server.addService(protoPackage.PaymentService.service, {
      Charge: (call, callback) => {
        callback(null, { transaction_id: 'test-transaction-id' });
      },
      GetPaymentStatus: (call, callback) => {
        callback(null, { status: 'PAID' });
      },
      CancelPayment: (call, callback) => {
        callback(null, { success: true });
      }
    });
    return new Promise((resolve, reject) => {
      server.bindAsync('0.0.0.0:0', grpc.ServerCredentials.createInsecure(), (err, port) => {
        if (err) return reject(err);
        server.start();
        client = new protoPackage.PaymentService(
          `localhost:${port}`,
          grpc.credentials.createInsecure()
        );
        resolve();
      });
    });
  }

  test('AC-1: default limit blocks over 100 requests per minute', async () => {
    process.env.PAYMENT_SERVICE_RATE_LIMIT_DEFAULT = '100';
    await createServerAndClient();

    // Send 100 successful requests
    for (let i = 0; i < 100; i++) {
      const res = await new Promise((resolve, reject) => {
        client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res) => {
          if (err) return reject(err);
          resolve(res);
        });
      });
      expect(res.transaction_id).toBe('test-transaction-id');
    }

    // 101st request should be rejected
    await expect(new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    })).rejects.toMatchObject({
      code: grpc.status.RESOURCE_EXHAUSTED,
      message: 'Rate limit exceeded, try again later'
    });
  }, 10000);

  test('AC-2: per-endpoint override applies correctly', async () => {
    process.env.PAYMENT_SERVICE_RATE_LIMIT_DEFAULT = '100';
    process.env.PAYMENT_SERVICE_RATE_LIMIT_OVERRIDES = JSON.stringify({ Charge: 20 });
    await createServerAndClient();

    // Send 20 requests to Charge endpoint
    for (let i = 0; i < 20; i++) {
      const res = await new Promise((resolve, reject) => {
        client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res) => {
          if (err) return reject(err);
          resolve(res);
        });
      });
      expect(res.transaction_id).toBe('test-transaction-id');
    }

    // 21st request to Charge should be rejected
    await expect(new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    })).rejects.toMatchObject({ code: grpc.status.RESOURCE_EXHAUSTED });

    // GetPaymentStatus should still work with default 100 limit
    const res = await new Promise((resolve, reject) => {
      client.GetPaymentStatus({ payment_id: 'test-id' }, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    });
    expect(res.status).toBe('PAID');
  }, 10000);

  test('AC-3: success responses contain rate limit metadata', async () => {
    process.env.PAYMENT_SERVICE_RATE_LIMIT_DEFAULT = '100';
    await createServerAndClient();

    const metadata = new grpc.Metadata();
    const res = await new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, metadata, (err, res, metadata) => {
        if (err) return reject(err);
        resolve({ res, metadata });
      });
    });

    expect(res.metadata.get('x-ratelimit-limit')[0]).toBe('100');
    expect(parseInt(res.metadata.get('x-ratelimit-remaining')[0])).toBeLessThanOrEqual(99);
    expect(parseInt(res.metadata.get('x-ratelimit-reset')[0])).toBeGreaterThan(Math.floor(Date.now() / 1000));
  });

  test('AC-4: error responses contain rate limit metadata', async () => {
    process.env.PAYMENT_SERVICE_RATE_LIMIT_DEFAULT = '1';
    await createServerAndClient();

    // First request succeeds
    await new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    });

    // Second request fails, check metadata
    await expect(new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res, metadata) => {
        if (err) {
          err.metadata = metadata;
          return reject(err);
        }
        resolve(res);
      });
    })).rejects.toMatchObject({
      code: grpc.status.RESOURCE_EXHAUSTED,
      metadata: expect.objectContaining({
        get: expect.any(Function)
      })
    }).catch(err => {
      expect(err.metadata.get('x-ratelimit-limit')[0]).toBe('1');
      expect(err.metadata.get('x-ratelimit-remaining')[0]).toBe('0');
      expect(parseInt(err.metadata.get('x-ratelimit-reset')[0])).toBeGreaterThan(Math.floor(Date.now() / 1000));
    });
  });

  test('AC-5: rate limit violation emits structured log', async () => {
    process.env.PAYMENT_SERVICE_RATE_LIMIT_DEFAULT = '1';
    await createServerAndClient();

    // First request succeeds
    await new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    });

    // Second request fails
    await expect(new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    })).rejects.toMatchObject({ code: grpc.status.RESOURCE_EXHAUSTED });

    // Check log entry
    expect(logger.warn).toHaveBeenCalledTimes(1);
    const logEntry = logger.warn.mock.calls[0][0];
    expect(logEntry.message).toBe('Rate limit exceeded, try again later');
    expect(logEntry.client_id).toBeDefined();
    expect(logEntry.endpoint).toBe('/oteldemo.PaymentService/Charge');
    expect(logEntry.trace_id).toBe('test-trace-id');
    expect(logEntry.span_id).toBe('test-span-id');
    expect(logEntry.limit).toBe(1);
    expect(logEntry.remaining).toBe(0);
    expect(logEntry.reset_timestamp).toBeGreaterThan(Math.floor(Date.now() / 1000));
  });

  test('AC-6: rate limit tracks per X-Client-Id', async () => {
    process.env.PAYMENT_SERVICE_RATE_LIMIT_DEFAULT = '1';
    await createServerAndClient();

    const metadata1 = new grpc.Metadata();
    metadata1.set('x-client-id', 'client-1');
    const metadata2 = new grpc.Metadata();
    metadata2.set('x-client-id', 'client-2');

    // Client 1 first request succeeds
    await new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, metadata1, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    });

    // Client 2 first request succeeds
    await new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, metadata2, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    });

    // Client 1 second request fails
    await expect(new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, metadata1, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    })).rejects.toMatchObject({ code: grpc.status.RESOURCE_EXHAUSTED });

    // Client 2 second request fails
    await expect(new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, metadata2, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    })).rejects.toMatchObject({ code: grpc.status.RESOURCE_EXHAUSTED });
  });

  test('AC-7: rate limit tracks per source IP when no client id', async () => {
    // This test requires mocking peer IP, we'll verify that getClientIp is used when no X-Client-Id
    process.env.PAYMENT_SERVICE_RATE_LIMIT_DEFAULT = '1';
    const { getClientIp } = require('../index');
    const mockCall = { getPeer: () => 'ipv4:192.168.1.1:1234' };
    expect(getClientIp(mockCall)).toBe('192.168.1.1');

    // Integration test: two requests from same IP should fail
    await createServerAndClient();
    await new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    });
    await expect(new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    })).rejects.toMatchObject({ code: grpc.status.RESOURCE_EXHAUSTED });
  });

  test('AC-8: rate limit resets after one minute', async () => {
    process.env.PAYMENT_SERVICE_RATE_LIMIT_DEFAULT = '1';
    jest.useFakeTimers();
    await createServerAndClient();

    // First request succeeds
    await new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    });

    // Second request fails immediately
    await expect(new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    })).rejects.toMatchObject({ code: grpc.status.RESOURCE_EXHAUSTED });

    // Fast forward 61 seconds
    jest.advanceTimersByTime(61000);
    
    // Request should succeed again
    const res = await new Promise((resolve, reject) => {
      client.Charge({ amount: { currency_code: 'USD', units: 10, nanos: 0 } }, (err, res) => {
        if (err) return reject(err);
        resolve(res);
      });
    });
    expect(res.transaction_id).toBe('test-transaction-id');

    jest.useRealTimers();
  }, 10000);
});
