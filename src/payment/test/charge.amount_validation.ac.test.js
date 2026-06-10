const grpc = require('@grpc/grpc-js')
const protoLoader = require('@grpc/proto-loader')
const logger = require('../logger')

jest.mock('../logger')

describe('Charge endpoint amount validation', () => {
  let originalEnv
  let chargeHandler
  let SUPPORTED_CURRENCIES

  beforeEach(() => {
    originalEnv = { ...process.env }
    jest.clearAllMocks()
    logger.error.mockClear()
    logger.info.mockClear()
  })

  afterEach(() => {
    process.env = originalEnv
    jest.resetModules()
  })

  function loadChargeHandler() {
    const index = require('../index')
    chargeHandler = index.chargeHandler
    SUPPORTED_CURRENCIES = index.SUPPORTED_CURRENCIES
  }

  test('test_ac1_negative_units_returns_invalid_argument', async () => {
    loadChargeHandler()

    const mockCall = {
      request: {
        amount: {
          units: -100,
          nanos: 0,
          currency_code: 'USD'
        },
        credit_card: {
          credit_card_number: '4111111111111111',
          credit_card_cvv: '123',
          credit_card_expiration_year: 2030,
          credit_card_expiration_month: 12
        }
      }
    }
    const mockCallback = jest.fn()

    await chargeHandler(mockCall, mockCallback)

    expect(mockCallback).toHaveBeenCalledWith(expect.objectContaining({
      code: grpc.status.INVALID_ARGUMENT,
      message: 'units must be a non-negative integer'
    }))
  })

  test('test_ac2_nanos_less_than_zero_returns_invalid_argument', async () => {
    loadChargeHandler()

    const mockCall = {
      request: {
        amount: {
          units: 100,
          nanos: -1,
          currency_code: 'USD'
        },
        credit_card: {
          credit_card_number: '4111111111111111',
          credit_card_cvv: '123',
          credit_card_expiration_year: 2030,
          credit_card_expiration_month: 12
        }
      }
    }
    const mockCallback = jest.fn()

    await chargeHandler(mockCall, mockCallback)

    expect(mockCallback).toHaveBeenCalledWith(expect.objectContaining({
      code: grpc.status.INVALID_ARGUMENT,
      message: 'nanos must be an integer between 0 and 999999999 inclusive'
    }))
  })

  test('test_ac3_nanos_greater_than_999999999_returns_invalid_argument', async () => {
    loadChargeHandler()

    const mockCall = {
      request: {
        amount: {
          units: 100,
          nanos: 1000000000,
          currency_code: 'USD'
        },
        credit_card: {
          credit_card_number: '4111111111111111',
          credit_card_cvv: '123',
          credit_card_expiration_year: 2030,
          credit_card_expiration_month: 12
        }
      }
    }
    const mockCallback = jest.fn()

    await chargeHandler(mockCall, mockCallback)

    expect(mockCallback).toHaveBeenCalledWith(expect.objectContaining({
      code: grpc.status.INVALID_ARGUMENT,
      message: 'nanos must be an integer between 0 and 999999999 inclusive'
    }))
  })

  test('test_ac4_unsupported_currency_returns_invalid_argument', async () => {
    loadChargeHandler()
    const unsupportedCode = 'XYZ'

    const mockCall = {
      request: {
        amount: {
          units: 100,
          nanos: 0,
          currency_code: unsupportedCode
        },
        credit_card: {
          credit_card_number: '4111111111111111',
          credit_card_cvv: '123',
          credit_card_expiration_year: 2030,
          credit_card_expiration_month: 12
        }
      }
    }
    const mockCallback = jest.fn()

    await chargeHandler(mockCall, mockCallback)

    expect(mockCallback).toHaveBeenCalledWith(expect.objectContaining({
      code: grpc.status.INVALID_ARGUMENT,
      message: `currency code ${unsupportedCode} is not supported. Supported currencies: ${SUPPORTED_CURRENCIES.join(', ')}`
    }))
  })

  test('test_ac5_valid_amount_passes_validation', async () => {
    loadChargeHandler()

    const mockCall = {
      request: {
        amount: {
          units: 100,
          nanos: 500000000,
          currency_code: 'USD'
        },
        credit_card: {
          credit_card_number: '4111111111111111',
          credit_card_cvv: '123',
          credit_card_expiration_year: 2030,
          credit_card_expiration_month: 12
        }
      }
    }
    const mockCallback = jest.fn()

    await chargeHandler(mockCall, mockCallback)

    // Validation passes: no error returned, callback called with result or no error
    expect(mockCallback).toHaveBeenCalledWith(null, expect.anything())
  })

  test('test_ac6_all_invalid_cases_covered', async () => {
    loadChargeHandler()
    const testCases = [
      {
        amount: { units: -1, nanos: 0, currency_code: 'USD' },
        expectedMessage: 'units must be a non-negative integer'
      },
      {
        amount: { units: 100, nanos: -500, currency_code: 'USD' },
        expectedMessage: 'nanos must be an integer between 0 and 999999999 inclusive'
      },
      {
        amount: { units: 100, nanos: 1000000000, currency_code: 'USD' },
        expectedMessage: 'nanos must be an integer between 0 and 999999999 inclusive'
      },
      {
        amount: { units: 100, nanos: 0, currency_code: 'XXX' },
        expectedMessage: 'currency code XXX is not supported. Supported currencies: ' + SUPPORTED_CURRENCIES.join(', ')
      }
    ]

    for (const testCase of testCases) {
      const mockCall = {
        request: {
          amount: testCase.amount,
          credit_card: {
            credit_card_number: '4111111111111111',
            credit_card_cvv: '123',
            credit_card_expiration_year: 2030,
            credit_card_expiration_month: 12
          }
        }
      }
      const mockCallback = jest.fn()

      await chargeHandler(mockCall, mockCallback)

      expect(mockCallback).toHaveBeenCalledWith(expect.objectContaining({
        code: grpc.status.INVALID_ARGUMENT,
        message: testCase.expectedMessage
      }))
    }
  })

  test('test_ac7_all_valid_cases_covered', async () => {
    loadChargeHandler()
    const testCases = [
      // Zero amount
      { units: 0, nanos: 0, currency_code: 'USD' },
      // Max nanos
      { units: 100, nanos: 999999999, currency_code: 'EUR' },
      // All supported currencies
      { units: 50, nanos: 1000000, currency_code: 'GBP' },
      { units: 2000, nanos: 0, currency_code: 'JPY' }
    ]

    for (const amount of testCases) {
      const mockCall = {
        request: {
          amount,
          credit_card: {
            credit_card_number: '4111111111111111',
            credit_card_cvv: '123',
            credit_card_expiration_year: 2030,
            credit_card_expiration_month: 12
          }
        }
      }
      const mockCallback = jest.fn()

      await chargeHandler(mockCall, mockCallback)

      expect(mockCallback).toHaveBeenCalledWith(null, expect.anything())
    }
  })
})
