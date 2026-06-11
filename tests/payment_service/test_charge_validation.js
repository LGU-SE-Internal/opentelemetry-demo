const grpc = require('@grpc/grpc-js');
const protoLoader = require('@grpc/proto-loader');
const assert = require('assert');

// Load protobuf definitions
const packageDefinition = protoLoader.loadSync(
  `${__dirname}/../../pb/demo.proto`,
  { keepCase: true, longs: String, enums: String, defaults: true, oneofs: true }
);
const oteldemo = grpc.loadPackageDefinition(packageDefinition).oteldemo;

// Initialize client - service address will be provided by test environment
const client = new oteldemo.PaymentService(
  process.env.PAYMENT_SERVICE_ADDR || 'localhost:50051',
  grpc.credentials.createInsecure()
);

describe('PaymentService Charge Validation', () => {
  // Valid baseline request for reference
  const validRequest = {
    amount: {
      currency_code: 'USD',
      units: 100,
      nanos: 0
    },
    credit_card: {
      credit_card_number: '4111111111111111',
      credit_card_cvv: 123,
      credit_card_expiration_year: new Date().getFullYear() + 1,
      credit_card_expiration_month: 12
    },
    currency_code: 'USD'
  };

  // AC-1: Missing required parameters
  it('test_ac1_missing_amount_parameter_returns_invalid_argument', (done) => {
    const request = { ...validRequest };
    delete request.amount;
    client.Charge(request, (err, response) => {
      assert(err);
      assert.strictEqual(err.code, grpc.status.INVALID_ARGUMENT);
      assert(err.details.includes('amount'));
      done();
    });
  });

  it('test_ac1_missing_credit_card_parameter_returns_invalid_argument', (done) => {
    const request = { ...validRequest };
    delete request.credit_card;
    client.Charge(request, (err, response) => {
      assert(err);
      assert.strictEqual(err.code, grpc.status.INVALID_ARGUMENT);
      assert(err.details.includes('credit_card'));
      done();
    });
  });

  it('test_ac1_missing_currency_code_parameter_returns_invalid_argument', (done) => {
    const request = { ...validRequest };
    delete request.currency_code;
    client.Charge(request, (err, response) => {
      assert(err);
      assert.strictEqual(err.code, grpc.status.INVALID_ARGUMENT);
      assert(err.details.includes('currency_code'));
      done();
    });
  });

  // AC-2: Amount units < 0
  it('test_ac2_negative_amount_units_returns_invalid_argument', (done) => {
    const request = JSON.parse(JSON.stringify(validRequest));
    request.amount.units = -10;
    client.Charge(request, (err, response) => {
      assert(err);
      assert.strictEqual(err.code, grpc.status.INVALID_ARGUMENT);
      assert.strictEqual(err.details, 'Amount units must be non-negative');
      done();
    });
  });

  // AC-3: Invalid amount nanos
  it('test_ac3_negative_amount_nanos_returns_invalid_argument', (done) => {
    const request = JSON.parse(JSON.stringify(validRequest));
    request.amount.nanos = -1;
    client.Charge(request, (err, response) => {
      assert(err);
      assert.strictEqual(err.code, grpc.status.INVALID_ARGUMENT);
      assert.strictEqual(err.details, 'Amount nanos must be between 0 and 999999999');
      done();
    });
  });

  it('test_ac3_excessively_large_amount_nanos_returns_invalid_argument', (done) => {
    const request = JSON.parse(JSON.stringify(validRequest));
    request.amount.nanos = 1000000000;
    client.Charge(request, (err, response) => {
      assert(err);
      assert.strictEqual(err.code, grpc.status.INVALID_ARGUMENT);
      assert.strictEqual(err.details, 'Amount nanos must be between 0 and 999999999');
      done();
    });
  });

  // AC-4: Unsupported currency code
  it('test_ac4_unsupported_currency_code_returns_invalid_argument', (done) => {
    const request = JSON.parse(JSON.stringify(validRequest));
    request.currency_code = 'XYZ';
    client.Charge(request, (err, response) => {
      assert(err);
      assert.strictEqual(err.code, grpc.status.INVALID_ARGUMENT);
      assert.strictEqual(err.details, 'Currency XYZ is not supported');
      done();
    });
  });

  // AC-5: Invalid credit card number length
  it('test_ac5_short_credit_card_number_returns_invalid_argument', (done) => {
    const request = JSON.parse(JSON.stringify(validRequest));
    request.credit_card.credit_card_number = '123456789012'; // 12 digits
    client.Charge(request, (err, response) => {
      assert(err);
      assert.strictEqual(err.code, grpc.status.INVALID_ARGUMENT);
      assert.strictEqual(err.details, 'Invalid credit card number length: must be 13, 15, or 16 digits');
      done();
    });
  });

  it('test_ac5_long_credit_card_number_returns_invalid_argument', (done) => {
    const request = JSON.parse(JSON.stringify(validRequest));
    request.credit_card.credit_card_number = '12345678901234567'; // 17 digits
    client.Charge(request, (err, response) => {
      assert(err);
      assert.strictEqual(err.code, grpc.status.INVALID_ARGUMENT);
      assert.strictEqual(err.details, 'Invalid credit card number length: must be 13, 15, or 16 digits');
      done();
    });
  });

  // AC-6: Invalid CVV length
  it('test_ac6_short_cvv_returns_invalid_argument', (done) => {
    const request = JSON.parse(JSON.stringify(validRequest));
    request.credit_card.credit_card_cvv = 12; // 2 digits
    client.Charge(request, (err, response) => {
      assert(err);
      assert.strictEqual(err.code, grpc.status.INVALID_ARGUMENT);
      assert.strictEqual(err.details, 'Invalid CVV length: must be 3 or 4 digits');
      done();
    });
  });

  it('test_ac6_long_cvv_returns_invalid_argument', (done) => {
    const request = JSON.parse(JSON.stringify(validRequest));
    request.credit_card.credit_card_cvv = 12345; // 5 digits
    client.Charge(request, (err, response) => {
      assert(err);
      assert.strictEqual(err.code, grpc.status.INVALID_ARGUMENT);
      assert.strictEqual(err.details, 'Invalid CVV length: must be 3 or 4 digits');
      done();
    });
  });

  // AC-7: Expired credit card
  it('test_ac7_expired_year_credit_card_returns_invalid_argument', (done) => {
    const request = JSON.parse(JSON.stringify(validRequest));
    request.credit_card.credit_card_expiration_year = new Date().getFullYear() - 1;
    client.Charge(request, (err, response) => {
      assert(err);
      assert.strictEqual(err.code, grpc.status.INVALID_ARGUMENT);
      assert.strictEqual(err.details, 'Credit card is expired');
      done();
    });
  });

  it('test_ac7_expired_month_same_year_credit_card_returns_invalid_argument', (done) => {
    const now = new Date();
    const request = JSON.parse(JSON.stringify(validRequest));
    request.credit_card.credit_card_expiration_year = now.getFullYear();
    request.credit_card.credit_card_expiration_month = now.getMonth(); // months are 0-based in JS, proto uses 1-based, so current month - 1
    client.Charge(request, (err, response) => {
      assert(err);
      assert.strictEqual(err.code, grpc.status.INVALID_ARGUMENT);
      assert.strictEqual(err.details, 'Credit card is expired');
      done();
    });
  });

  // AC-8: Valid request passes through
  it('test_ac8_valid_request_forwarded_to_processing', (done) => {
    client.Charge(validRequest, (err, response) => {
      assert.ifError(err);
      assert(response);
      // Verify we got a successful charge response
      assert(response.transaction_id);
      done();
    });
  });
});
