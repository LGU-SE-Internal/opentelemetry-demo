const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');
const path = require('path');
const charge = require('../charge');

// Mock the charge processor to verify if it's called
jest.mock('../charge', () => jest.fn((amount, card) => ({ transaction_id: 'test-transaction-id' })));

// Load the payment service proto
const PROTO_PATH = path.join(__dirname, '../../../pb/demo.proto');
const packageDefinition = protoLoader.loadSync(PROTO_PATH, { keepCase: true, longs: String, enums: String, defaults: true, oneofs: true });
const otelDemoProto = grpc.loadPackageDefinition(packageDefinition).oteldemo;

let server;
let client;

beforeAll((done) => {
  // Import the server setup
  const { getServer } = require('../index');
  server = getServer();
  server.bindAsync('127.0.0.1:0', grpc.ServerCredentials.createInsecure(), (err, port) => {
    if (err) {
      done(err);
      return;
    }
    server.start();
    client = new otelDemoProto.PaymentService(`127.0.0.1:${port}`, grpc.credentials.createInsecure());
    done();
  });
});

afterAll((done) => {
  server.tryShutdown(done);
  jest.clearAllMocks();
});

describe('Charge API Input Validation AC Tests', () => {
  // Valid base request to use for modifications
  const validRequest = {
    amount: {
      units: 100,
      nanos: 0,
      currency_code: 'USD'
    },
    credit_card_number: '4111111111111111', // Valid Visa passes Luhn
    credit_card_expiration_month: 12,
    credit_card_expiration_year: new Date().getFullYear() + 1,
    credit_card_cvv: '123'
  };

  beforeEach(() => {
    charge.mockClear();
  });

  // AC-1: When amount is 0 or negative, endpoint returns INVALID_ARGUMENT error, no charge is processed.
  test('ac1_zero_amount_returns_invalid_argument', async () => {
    const request = {
      ...validRequest,
      amount: { ...validRequest.amount, units: 0, nanos: 0 }
    };

    const result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('amount');
    expect(charge).not.toHaveBeenCalled();
  });

  test('ac1_negative_amount_returns_invalid_argument', async () => {
    const request = {
      ...validRequest,
      amount: { ...validRequest.amount, units: -50 }
    };

    const result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('amount');
    expect(charge).not.toHaveBeenCalled();
  });

  // AC-2: When currency_code is not a 3-character alphabetic string matching a valid ISO 4217 code, endpoint returns INVALID_ARGUMENT error.
  test('ac2_invalid_currency_code_length_returns_invalid_argument', async () => {
    // Test 2 character code
    let request = {
      ...validRequest,
      amount: { ...validRequest.amount, currency_code: 'US' }
    };

    let result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('currency_code');
    expect(charge).not.toHaveBeenCalled();

    // Test 4 character code
    request = {
      ...validRequest,
      amount: { ...validRequest.amount, currency_code: 'USDD' }
    };

    result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('currency_code');
    expect(charge).not.toHaveBeenCalled();
  });

  test('ac2_invalid_currency_code_non_alphabetic_returns_invalid_argument', async () => {
    const request = {
      ...validRequest,
      amount: { ...validRequest.amount, currency_code: 'U1D' }
    };

    const result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('currency_code');
    expect(charge).not.toHaveBeenCalled();
  });

  test('ac2_unsupported_currency_code_returns_invalid_argument', async () => {
    const request = {
      ...validRequest,
      amount: { ...validRequest.amount, currency_code: 'XYZ' } // Not a valid ISO 4217 code
    };

    const result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('currency_code');
    expect(charge).not.toHaveBeenCalled();
  });

  // AC-3: When credit_card_number fails Luhn algorithm check, or length does not match supported card networks (13-19 digits for Visa, Mastercard, Amex, Discover), endpoint returns INVALID_ARGUMENT error.
  test('ac3_credit_card_fails_luhn_check_returns_invalid_argument', async () => {
    const request = {
      ...validRequest,
      credit_card_number: '4111111111111112' // Valid length, bad Luhn checksum
    };

    const result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('credit_card_number');
    expect(charge).not.toHaveBeenCalled();
  });

  test('ac3_unsupported_card_network_length_returns_invalid_argument', async () => {
    // Test 12 digits (too short)
    let request = {
      ...validRequest,
      credit_card_number: '411111111111'
    };

    let result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('credit_card_number');
    expect(charge).not.toHaveBeenCalled();

    // Test 20 digits (too long)
    request = {
      ...validRequest,
      credit_card_number: '41111111111111111111'
    };

    result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('credit_card_number');
    expect(charge).not.toHaveBeenCalled();
  });

  // AC-4: When credit_card_expiration_month is not between 1-12, or expiration date (MM/YY or MM/YYYY) is in the past, endpoint returns INVALID_ARGUMENT error.
  test('ac4_expiration_month_out_of_range_returns_invalid_argument', async () => {
    // Test month 0
    let request = {
      ...validRequest,
      credit_card_expiration_month: 0
    };

    let result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('credit_card_expiration_month');
    expect(charge).not.toHaveBeenCalled();

    // Test month 13
    request = {
      ...validRequest,
      credit_card_expiration_month: 13
    };

    result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('credit_card_expiration_month');
    expect(charge).not.toHaveBeenCalled();
  });

  test('ac4_expiration_date_in_past_returns_invalid_argument', async () => {
    const now = new Date();
    // Test previous month
    let request = {
      ...validRequest,
      credit_card_expiration_month: now.getMonth() === 0 ? 12 : now.getMonth(),
      credit_card_expiration_year: now.getMonth() === 0 ? now.getFullYear() - 1 : now.getFullYear()
    };

    let result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('expiration date');
    expect(charge).not.toHaveBeenCalled();

    // Test previous year same month
    request = {
      ...validRequest,
      credit_card_expiration_month: now.getMonth() + 1,
      credit_card_expiration_year: now.getFullYear() - 1
    };

    result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('expiration date');
    expect(charge).not.toHaveBeenCalled();
  });

  // AC-5: When credit_card_cvv is not 3 digits (Visa, Mastercard, Discover) or 4 digits (Amex, based on card number prefix), endpoint returns INVALID_ARGUMENT error.
  test('ac5_amex_card_with_3_digit_cvv_returns_invalid_argument', async () => {
    const request = {
      ...validRequest,
      credit_card_number: '378282246310005', // Valid Amex card number (starts with 37)
      credit_card_cvv: '123' // Amex requires 4 digits
    };

    const result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('credit_card_cvv');
    expect(charge).not.toHaveBeenCalled();
  });

  test('ac5_visa_card_with_4_digit_cvv_returns_invalid_argument', async () => {
    const request = {
      ...validRequest,
      credit_card_number: '4111111111111111', // Valid Visa card number (starts with 4)
      credit_card_cvv: '1234' // Visa requires 3 digits
    };

    const result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('credit_card_cvv');
    expect(charge).not.toHaveBeenCalled();
  });

  test('ac5_mastercard_card_with_4_digit_cvv_returns_invalid_argument', async () => {
    const request = {
      ...validRequest,
      credit_card_number: '5555555555554444', // Valid Mastercard number (starts with 55)
      credit_card_cvv: '1234' // Mastercard requires 3 digits
    };

    const result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('credit_card_cvv');
    expect(charge).not.toHaveBeenCalled();
  });

  // AC-6: When all input parameters pass all validation checks, the charge proceeds normally and returns a successful ChargeResponse.
  test('ac6_all_valid_parameters_returns_successful_response', async () => {
    // Test with Visa
    let request = {
      ...validRequest,
      amount: { units: 100, nanos: 0, currency_code: 'EUR' }, // Valid currency
      credit_card_number: '4111111111111111', // Valid Visa
      credit_card_expiration_month: new Date().getMonth() + 1,
      credit_card_expiration_year: new Date().getFullYear() + 2,
      credit_card_cvv: '123'
    };

    let result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).toBeNull();
    expect(result.response).not.toBeNull();
    expect(result.response.transaction_id).toBe('test-transaction-id');
    expect(charge).toHaveBeenCalledTimes(1);

    // Test with Amex
    charge.mockClear();
    request = {
      ...validRequest,
      credit_card_number: '378282246310005', // Valid Amex
      credit_card_cvv: '1234' // 4 digits for Amex
    };

    result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).toBeNull();
    expect(result.response).not.toBeNull();
    expect(result.response.transaction_id).toBe('test-transaction-id');
    expect(charge).toHaveBeenCalledTimes(1);
  });
});
