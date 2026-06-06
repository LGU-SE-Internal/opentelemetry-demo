const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');
const path = require('path');
const charge = require('../charge');

// Mock the charge processor to verify if it's called
jest.mock('../charge', () => jest.fn((amount, card) => ({ transaction_id: 'test-transaction-id' })));

// Load the payment service proto
const PROTO_PATH = path.join(__dirname, '../../../pb/demo.proto');
const packageDefinition = protoLoader.loadSync(PROTO_PATH, { keepCase: true, longs: String, enums: String, defaults: true, oneofs: true });
const otelDemoProto = grpc.loadPackageDefinition(packageDefinition).opentelemetry.proto.demo;

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

describe('Charge API Validation', () => {
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

  test('ac1_missing_required_field_returns_invalid_argument', async () => {
    // Test missing amount field
    const request = { ...validRequest };
    delete request.amount;

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

  test('ac2_negative_amount_units_returns_invalid_argument', async () => {
    const request = {
      ...validRequest,
      amount: { ...validRequest.amount, units: -100 }
    };

    const result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('amount.units');
    expect(charge).not.toHaveBeenCalled();
  });

  test('ac3_invalid_amount_nanos_returns_invalid_argument', async () => {
    // Test negative nanos
    let request = {
      ...validRequest,
      amount: { ...validRequest.amount, nanos: -1 }
    };

    let result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('amount.nanos');
    expect(charge).not.toHaveBeenCalled();

    // Test nanos over maximum
    request = {
      ...validRequest,
      amount: { ...validRequest.amount, nanos: 1000000000 }
    };

    result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('amount.nanos');
    expect(charge).not.toHaveBeenCalled();
  });

  test('ac4_invalid_credit_card_number_returns_invalid_argument', async () => {
    // Test too short (12 digits)
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

    // Test too long (20 digits)
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

    // Test invalid Luhn (valid length, bad checksum)
    request = {
      ...validRequest,
      credit_card_number: '4111111111111112'
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

  test('ac5_invalid_expiration_month_returns_invalid_argument', async () => {
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

  test('ac6_expired_credit_card_returns_invalid_argument', async () => {
    const now = new Date();
    // Test last month same year
    let request = {
      ...validRequest,
      credit_card_expiration_month: now.getMonth(), // 0-indexed, so last month is current month index
      credit_card_expiration_year: now.getFullYear()
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

    // Test current month previous year
    request = {
      ...validRequest,
      credit_card_expiration_month: now.getMonth() + 1, // months are 1-indexed in request
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

  test('ac7_invalid_cvv_length_returns_invalid_argument', async () => {
    // Test 2 digit CVV
    let request = {
      ...validRequest,
      credit_card_cvv: '12'
    };

    let result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('credit_card_cvv');
    expect(charge).not.toHaveBeenCalled();

    // Test 5 digit CVV
    request = {
      ...validRequest,
      credit_card_cvv: '12345'
    };

    result = await new Promise((resolve) => {
      client.charge(request, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).not.toBeNull();
    expect(result.err.code).toBe(grpc.status.INVALID_ARGUMENT);
    expect(result.err.details).toContain('credit_card_cvv');
    expect(charge).not.toHaveBeenCalled();
  });

  test('ac8_valid_request_calls_charge_processor_and_returns_response', async () => {
    const result = await new Promise((resolve) => {
      client.charge(validRequest, (err, response) => {
        resolve({ err, response });
      });
    });

    expect(result.err).toBeNull();
    expect(result.response).not.toBeNull();
    expect(result.response.transaction_id).toBe('test-transaction-id');
    expect(charge).toHaveBeenCalledTimes(1);
    expect(charge).toHaveBeenCalledWith(validRequest.amount, {
      number: validRequest.credit_card_number,
      expiration_month: validRequest.credit_card_expiration_month,
      expiration_year: validRequest.credit_card_expiration_year,
      cvv: validRequest.credit_card_cvv
    });
  });
});
