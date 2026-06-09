'use strict';

const grpc = require('@grpc/grpc-js');
const { expect } = require('chai');
const { PaymentServiceClient } = require('../pb/demo.proto');
const { loadSupportedCurrencies } = require('../index');

const client = new PaymentServiceClient(
  'localhost:50051',
  grpc.credentials.createInsecure()
);

const validTestCard = {
  number: '4111111111111111', // Valid Luhn, 16 digits
  expiry_month: 12,
  expiry_year: new Date().getFullYear() + 1,
  cvv: '123'
};

const validAmount = {
  units: 100,
  nanos: 0
};

const validCurrency = 'USD';

describe('PaymentService Refund Input Validation AC Tests', () => {
  let supportedCurrencies;

  before(async () => {
    supportedCurrencies = await loadSupportedCurrencies();
  });

  // AC-1: Missing required fields returns INVALID_ARGUMENT
  it('test_ac1_missing_required_fields_returns_invalid_argument', (done) => {
    // Test missing amount field
    client.refund({
      credit_card: validTestCard,
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test missing credit_card field
    client.refund({
      amount: validAmount,
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test missing currency_code field
    client.refund({
      amount: validAmount,
      credit_card: validTestCard
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
      done();
    });
  });

  // AC-2: Invalid amount values return INVALID_ARGUMENT
  it('test_ac2_invalid_amount_values_returns_invalid_argument', (done) => {
    // Test negative units
    client.refund({
      amount: { ...validAmount, units: -100 },
      credit_card: validTestCard,
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test zero units
    client.refund({
      amount: { ...validAmount, units: 0 },
      credit_card: validTestCard,
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test negative nanos
    client.refund({
      amount: { ...validAmount, nanos: -1 },
      credit_card: validTestCard,
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test nanos >= 1e9
    client.refund({
      amount: { ...validAmount, nanos: 1000000000 },
      credit_card: validTestCard,
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
      done();
    });
  });

  // AC-3: Invalid credit card number returns INVALID_ARGUMENT
  it('test_ac3_invalid_credit_card_number_returns_invalid_argument', (done) => {
    // Test invalid Luhn
    client.refund({
      amount: validAmount,
      credit_card: { ...validTestCard, number: '4111111111111112' },
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test too short (12 digits)
    client.refund({
      amount: validAmount,
      credit_card: { ...validTestCard, number: '411111111111' },
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test too long (20 digits)
    client.refund({
      amount: validAmount,
      credit_card: { ...validTestCard, number: '41111111111111111111' },
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test non-numeric characters
    client.refund({
      amount: validAmount,
      credit_card: { ...validTestCard, number: '4111-1111-1111-1111' },
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
      done();
    });
  });

  // AC-4: Invalid expiry date returns INVALID_ARGUMENT
  it('test_ac4_invalid_expiry_date_returns_invalid_argument', (done) => {
    const currentYear = new Date().getFullYear();
    const currentMonth = new Date().getMonth() + 1; // JS months are 0-based

    // Test invalid month (0)
    client.refund({
      amount: validAmount,
      credit_card: { ...validTestCard, expiry_month: 0 },
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test invalid month (13)
    client.refund({
      amount: validAmount,
      credit_card: { ...validTestCard, expiry_month: 13 },
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test expired year (last year)
    client.refund({
      amount: validAmount,
      credit_card: { ...validTestCard, expiry_year: currentYear - 1 },
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test expired same year, previous month
    if (currentMonth > 1) {
      client.refund({
        amount: validAmount,
        credit_card: { ...validTestCard, expiry_month: currentMonth - 1, expiry_year: currentYear },
        currency_code: validCurrency
      }, (err, response) => {
        expect(err).to.exist;
        expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
      });
    }

    done();
  });

  // AC-5: Invalid CVV returns INVALID_ARGUMENT
  it('test_ac5_invalid_cvv_returns_invalid_argument', (done) => {
    // Test 2 digits
    client.refund({
      amount: validAmount,
      credit_card: { ...validTestCard, cvv: '12' },
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test 5 digits
    client.refund({
      amount: validAmount,
      credit_card: { ...validTestCard, cvv: '12345' },
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test non-numeric
    client.refund({
      amount: validAmount,
      credit_card: { ...validTestCard, cvv: 'ABC' },
      currency_code: validCurrency
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
      done();
    });
  });

  // AC-6: Invalid currency code returns INVALID_ARGUMENT
  it('test_ac6_invalid_currency_code_returns_invalid_argument', (done) => {
    // Test 2 letter code
    client.refund({
      amount: validAmount,
      credit_card: validTestCard,
      currency_code: 'US'
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test unsupported currency
    client.refund({
      amount: validAmount,
      credit_card: validTestCard,
      currency_code: 'XYZ'
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
    });

    // Test numeric currency
    client.refund({
      amount: validAmount,
      credit_card: validTestCard,
      currency_code: '123'
    }, (err, response) => {
      expect(err).to.exist;
      expect(err.code).to.equal(grpc.status.INVALID_ARGUMENT);
      done();
    });
  });

  // AC-7: Valid request processes normally
  it('test_ac7_valid_request_processes_successfully', (done) => {
    client.refund({
      amount: validAmount,
      credit_card: validTestCard,
      currency_code: supportedCurrencies[0]
    }, (err, response) => {
      expect(err).to.not.exist;
      expect(response).to.exist;
      done();
    });
  });
});
