const grpc = require('@grpc/grpc-js')
const protoLoader = require('@grpc/proto-loader')
const { RateLimiterMemory } = require('rate-limiter-flexible')
const logger = require('../logger')

jest.mock('rate-limiter-flexible')
jest.mock('../logger')

describe('Charge endpoint rate limiting', () => {
  let originalEnv
  let rateLimitInterceptor
  let configuredRateLimit
  let rateLimiter

  beforeEach(() => {
    originalEnv = { ...process.env }
    jest.clearAllMocks()
    RateLimiterMemory.mockClear()
    logger.warn.mockClear()
  })

  afterEach(() => {
    process.env = originalEnv
    jest.resetModules()
  })

  function loadInterceptor() {
    const index = require('../index')
    rateLimitInterceptor = index.rateLimitInterceptor
    configuredRateLimit = index.configuredRateLimit
    rateLimiter = index.rateLimiter
  }

  test('test_ac1_configured_rate_limit_enforced_per_ip', async () => {
    process.env.PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS = '5'
    loadInterceptor()

    expect(RateLimiterMemory).toHaveBeenCalledWith({ points: 5, duration: 1 })
    expect(configuredRateLimit).toBe(5)

    const mockCall = {
      getPath: () => '/oteldemo.PaymentService/Charge',
      getPeer: () => 'ipv4:192.168.1.1:12345'
    }
    const mockCallback = jest.fn()
    const mockNext = jest.fn()

    // 5 successful requests
    for (let i = 0; i < 5; i++) {
      await rateLimitInterceptor(mockCall, mockCallback, mockNext)
      expect(mockNext).toHaveBeenCalledTimes(i + 1)
      expect(mockCallback).not.toHaveBeenCalled()
    }

    // 6th request should be rate limited
    rateLimiter.consume.mockRejectedValueOnce(new Error('Rate limit exceeded'))
    await rateLimitInterceptor(mockCall, mockCallback, mockNext)
    expect(mockCallback).toHaveBeenCalledWith(expect.objectContaining({
      code: grpc.status.RESOURCE_EXHAUSTED,
      message: "Rate limit exceeded. Try again later."
    }))
    expect(logger.warn).toHaveBeenCalledWith(expect.objectContaining({
      event: 'rate_limit_exceeded',
      client_ip: '192.168.1.1',
      endpoint: '/oteldemo.PaymentService/Charge',
      limit_rps: 5
    }))
  })

  test('test_ac2_default_rate_limit_10_rps', () => {
    delete process.env.PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS
    loadInterceptor()
    expect(configuredRateLimit).toBe(10)
    expect(RateLimiterMemory).toHaveBeenCalledWith({ points: 10, duration: 1 })
  })

  test('test_ac3_rate_limit_exceeded_returns_resource_exhausted', async () => {
    process.env.PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS = '2'
    loadInterceptor()
    rateLimiter.consume.mockRejectedValueOnce(new Error('Rate limit exceeded'))

    const mockCall = {
      getPath: () => '/oteldemo.PaymentService/Charge',
      getPeer: () => 'ipv4:10.0.0.1:54321'
    }
    const mockCallback = jest.fn()
    const mockNext = jest.fn()

    await rateLimitInterceptor(mockCall, mockCallback, mockNext)
    expect(mockCallback).toHaveBeenCalledWith(expect.objectContaining({
      code: grpc.status.RESOURCE_EXHAUSTED,
      message: "Rate limit exceeded. Try again later."
    }))
    expect(mockNext).not.toHaveBeenCalled()
  })

  test('test_ac4_rate_limit_logs_contain_required_fields', async () => {
    process.env.PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS = '3'
    loadInterceptor()
    rateLimiter.consume.mockRejectedValueOnce(new Error('Rate limit exceeded'))

    const testIp = '172.16.0.5'
    const mockCall = {
      getPath: () => '/oteldemo.PaymentService/Charge',
      getPeer: () => `ipv4:${testIp}:9999`
    }
    const mockCallback = jest.fn()
    const mockNext = jest.fn()

    await rateLimitInterceptor(mockCall, mockCallback, mockNext)
    expect(logger.warn).toHaveBeenCalledWith(expect.objectContaining({
      event: 'rate_limit_exceeded',
      client_ip: testIp,
      endpoint: '/oteldemo.PaymentService/Charge',
      limit_rps: 3,
      timestamp: expect.stringMatching(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/)
    }))
  })

  test('test_ac5_requests_below_limit_processed_normally_no_logs', async () => {
    process.env.PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS = '10'
    loadInterceptor()

    const mockCall = {
      getPath: () => '/oteldemo.PaymentService/Charge',
      getPeer: () => 'ipv4:127.0.0.1:1111'
    }
    const mockCallback = jest.fn()
    const mockNext = jest.fn()

    for (let i = 0; i < 10; i++) {
      await rateLimitInterceptor(mockCall, mockCallback, mockNext)
    }
    expect(mockNext).toHaveBeenCalledTimes(10)
    expect(mockCallback).not.toHaveBeenCalled()
    expect(logger.warn).not.toHaveBeenCalled()
  })

  test('test_ac6_rate_limits_independent_per_ip', async () => {
    process.env.PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS = '2'
    loadInterceptor()

    const callIp1 = {
      getPath: () => '/oteldemo.PaymentService/Charge',
      getPeer: () => 'ipv4:192.168.1.1:1234'
    }
    const callIp2 = {
      getPath: () => '/oteldemo.PaymentService/Charge',
      getPeer: () => 'ipv4:192.168.1.2:5678'
    }
    const mockCallback = jest.fn()
    const mockNext = jest.fn()

    // IP1 uses both allowed requests
    await rateLimitInterceptor(callIp1, mockCallback, mockNext)
    await rateLimitInterceptor(callIp1, mockCallback, mockNext)
    expect(mockNext).toHaveBeenCalledTimes(2)

    // IP2 can still make requests
    await rateLimitInterceptor(callIp2, mockCallback, mockNext)
    expect(mockNext).toHaveBeenCalledTimes(3)

    // IP1 next request is blocked
    rateLimiter.consume.mockRejectedValueOnce(new Error('Rate limit exceeded'))
    await rateLimitInterceptor(callIp1, mockCallback, mockNext)
    expect(mockCallback).toHaveBeenCalledTimes(1)
    expect(logger.warn).toHaveBeenCalledTimes(1)

    // IP2 can make second request
    await rateLimitInterceptor(callIp2, mockCallback, mockNext)
    expect(mockNext).toHaveBeenCalledTimes(4)
  })

  test('test_ac7_rate_limit_disabled_when_env_var_non_positive', () => {
    // Test 0
    process.env.PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS = '0'
    loadInterceptor()
    expect(rateLimiter).toBeNull()

    // Test negative value
    process.env.PAYMENT_SERVICE_CHARGE_RATE_LIMIT_RPS = '-5'
    jest.resetModules()
    loadInterceptor()
    expect(rateLimiter).toBeNull()
  })
})
