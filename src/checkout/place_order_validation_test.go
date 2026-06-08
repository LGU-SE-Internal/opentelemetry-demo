package main

import (
	"testing"
	"time"

	pb "github.com/open-telemetry/opentelemetry-demo/src/checkout/genproto/oteldemo"
	"github.com/stretchr/testify/assert"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// AC-1: If user_id is empty, request is rejected with error message "user_id is required"
func TestAC1_EmptyUserIdRejected(t *testing.T) {
	req := validTestRequest()
	req.UserId = ""

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Equal(t, "user_id is required", status.Convert(err).Message())
}

// AC-2: If user_currency is not a 3-letter uppercase ISO 4217 currency code, request is rejected
func TestAC2_InvalidCurrencyRejected(t *testing.T) {
	t.Run("lowercase currency", func(t *testing.T) {
		req := validTestRequest()
		req.UserCurrency = "usd"

		err := ValidatePlaceOrderRequest(req)
		assert.Error(t, err)
		assert.Equal(t, codes.InvalidArgument, status.Code(err))
		assert.Equal(t, "user_currency must be a valid 3-letter ISO 4217 currency code", status.Convert(err).Message())
	})

	t.Run("too short currency", func(t *testing.T) {
		req := validTestRequest()
		req.UserCurrency = "US"

		err := ValidatePlaceOrderRequest(req)
		assert.Error(t, err)
		assert.Equal(t, codes.InvalidArgument, status.Code(err))
		assert.Equal(t, "user_currency must be a valid 3-letter ISO 4217 currency code", status.Convert(err).Message())
	})

	t.Run("too long currency", func(t *testing.T) {
		req := validTestRequest()
		req.UserCurrency = "USDE"

		err := ValidatePlaceOrderRequest(req)
		assert.Error(t, err)
		assert.Equal(t, codes.InvalidArgument, status.Code(err))
		assert.Equal(t, "user_currency must be a valid 3-letter ISO 4217 currency code", status.Convert(err).Message())
	})
}

// AC-3: If email does not match standard email format, request is rejected with "email is invalid"
func TestAC3_InvalidEmailRejected(t *testing.T) {
	t.Run("missing @", func(t *testing.T) {
		req := validTestRequest()
		req.Email = "testexample.com"

		err := ValidatePlaceOrderRequest(req)
		assert.Error(t, err)
		assert.Equal(t, codes.InvalidArgument, status.Code(err))
		assert.Equal(t, "email is invalid", status.Convert(err).Message())
	})

	t.Run("missing domain", func(t *testing.T) {
		req := validTestRequest()
		req.Email = "test@"

		err := ValidatePlaceOrderRequest(req)
		assert.Error(t, err)
		assert.Equal(t, codes.InvalidArgument, status.Code(err))
		assert.Equal(t, "email is invalid", status.Convert(err).Message())
	})
}

// AC-4: If address is nil, request is rejected with "address is required"
func TestAC4_NilAddressRejected(t *testing.T) {
	req := validTestRequest()
	req.Address = nil

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Equal(t, "address is required", status.Convert(err).Message())
}

// AC-5: If address.street_address is empty, request is rejected with "street_address is required"
func TestAC5_EmptyStreetAddressRejected(t *testing.T) {
	req := validTestRequest()
	req.Address.StreetAddress = ""

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Equal(t, "street_address is required", status.Convert(err).Message())
}

// AC-6: If address.city is empty, request is rejected with "city is required"
func TestAC6_EmptyCityRejected(t *testing.T) {
	req := validTestRequest()
	req.Address.City = ""

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Equal(t, "city is required", status.Convert(err).Message())
}

// AC-7: If address.state is empty, request is rejected with "state is required"
func TestAC7_EmptyStateRejected(t *testing.T) {
	req := validTestRequest()
	req.Address.State = ""

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Equal(t, "state is required", status.Convert(err).Message())
}

// AC-8: If address.country is empty, request is rejected with "country is required"
func TestAC8_EmptyCountryRejected(t *testing.T) {
	req := validTestRequest()
	req.Address.Country = ""

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Equal(t, "country is required", status.Convert(err).Message())
}

// AC-9: If address.zip_code is empty, request is rejected with "zip_code is required"
func TestAC9_EmptyZipCodeRejected(t *testing.T) {
	req := validTestRequest()
	req.Address.ZipCode = ""

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Equal(t, "zip_code is required", status.Convert(err).Message())
}

// AC-10: If credit_card is nil, request is rejected with "credit_card is required"
func TestAC10_NilCreditCardRejected(t *testing.T) {
	req := validTestRequest()
	req.CreditCard = nil

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Equal(t, "credit_card is required", status.Convert(err).Message())
}

// AC-11: If credit_card.credit_card_number is not 13-19 digits, rejected with appropriate message
func TestAC11_InvalidCreditCardNumberRejected(t *testing.T) {
	t.Run("too short number (12 digits)", func(t *testing.T) {
		req := validTestRequest()
		req.CreditCard.CreditCardNumber = "424242424242"

		err := ValidatePlaceOrderRequest(req)
		assert.Error(t, err)
		assert.Equal(t, codes.InvalidArgument, status.Code(err))
		assert.Equal(t, "credit_card_number must be between 13 and 19 digits", status.Convert(err).Message())
	})

	t.Run("too long number (20 digits)", func(t *testing.T) {
		req := validTestRequest()
		req.CreditCard.CreditCardNumber = "42424242424242424242"

		err := ValidatePlaceOrderRequest(req)
		assert.Error(t, err)
		assert.Equal(t, codes.InvalidArgument, status.Code(err))
		assert.Equal(t, "credit_card_number must be between 13 and 19 digits", status.Convert(err).Message())
	})

	t.Run("non-numeric characters", func(t *testing.T) {
		req := validTestRequest()
		req.CreditCard.CreditCardNumber = "4242-4242-4242-4242"

		err := ValidatePlaceOrderRequest(req)
		assert.Error(t, err)
		assert.Equal(t, codes.InvalidArgument, status.Code(err))
		assert.Equal(t, "credit_card_number must be between 13 and 19 digits", status.Convert(err).Message())
	})
}

// AC-12: If credit_card.credit_card_cvv is not 3-4 digits, rejected
func TestAC12_InvalidCVVRejected(t *testing.T) {
	t.Run("2 digits", func(t *testing.T) {
		req := validTestRequest()
		req.CreditCard.CreditCardCvv = 12

		err := ValidatePlaceOrderRequest(req)
		assert.Error(t, err)
		assert.Equal(t, codes.InvalidArgument, status.Code(err))
		assert.Equal(t, "credit_card_cvv must be 3 or 4 digits", status.Convert(err).Message())
	})

	t.Run("5 digits", func(t *testing.T) {
		req := validTestRequest()
		req.CreditCard.CreditCardCvv = 12345

		err := ValidatePlaceOrderRequest(req)
		assert.Error(t, err)
		assert.Equal(t, codes.InvalidArgument, status.Code(err))
		assert.Equal(t, "credit_card_cvv must be 3 or 4 digits", status.Convert(err).Message())
	})
}

// AC-13: If credit_card_expiration_month is not 1-12, rejected
func TestAC13_InvalidExpirationMonthRejected(t *testing.T) {
	t.Run("month 0", func(t *testing.T) {
		req := validTestRequest()
		req.CreditCard.CreditCardExpirationMonth = 0

		err := ValidatePlaceOrderRequest(req)
		assert.Error(t, err)
		assert.Equal(t, codes.InvalidArgument, status.Code(err))
		assert.Equal(t, "credit_card_expiration_month must be between 1 and 12", status.Convert(err).Message())
	})

	t.Run("month 13", func(t *testing.T) {
		req := validTestRequest()
		req.CreditCard.CreditCardExpirationMonth = 13

		err := ValidatePlaceOrderRequest(req)
		assert.Error(t, err)
		assert.Equal(t, codes.InvalidArgument, status.Code(err))
		assert.Equal(t, "credit_card_expiration_month must be between 1 and 12", status.Convert(err).Message())
	})
}

// AC-14: If credit_card_expiration_year is less than current year, rejected
func TestAC14_PastExpirationYearRejected(t *testing.T) {
	currentYear := int32(time.Now().Year())
	req := validTestRequest()
	req.CreditCard.CreditCardExpirationYear = currentYear - 1

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Equal(t, "credit_card_expiration_year must not be in the past", status.Convert(err).Message())
}

// AC-15: If credit card is expired (month/year before current date), rejected
func TestAC15_ExpiredCreditCardRejected(t *testing.T) {
	currentYear := int32(time.Now().Year())
	currentMonth := int32(time.Now().Month())

	// Same year, past month
	req := validTestRequest()
	req.CreditCard.CreditCardExpirationYear = currentYear
	req.CreditCard.CreditCardExpirationMonth = currentMonth - 1
	if currentMonth == 1 {
		req.CreditCard.CreditCardExpirationYear = currentYear - 1
		req.CreditCard.CreditCardExpirationMonth = 12
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Equal(t, "credit card is expired", status.Convert(err).Message())
}

// AC-16: All valid PlaceOrderRequest objects pass validation with nil error
func TestAC16_ValidRequestPasses(t *testing.T) {
	req := validTestRequest()
	err := ValidatePlaceOrderRequest(req)
	assert.NoError(t, err)
}

// AC-17: All validation errors have gRPC status code INVALID_ARGUMENT
func TestAC17_AllErrorsUseInvalidArgumentCode(t *testing.T) {
	// Test multiple error scenarios all return INVALID_ARGUMENT
	testCases := []struct {
		name string
		req  *pb.PlaceOrderRequest
	}{
		{"empty user id", func() *pb.PlaceOrderRequest { r := validTestRequest(); r.UserId = ""; return r }()},
		{"invalid currency", func() *pb.PlaceOrderRequest { r := validTestRequest(); r.UserCurrency = "us"; return r }()},
		{"invalid email", func() *pb.PlaceOrderRequest { r := validTestRequest(); r.Email = "invalid"; return r }()},
		{"nil address", func() *pb.PlaceOrderRequest { r := validTestRequest(); r.Address = nil; return r }()},
		{"nil credit card", func() *pb.PlaceOrderRequest { r := validTestRequest(); r.CreditCard = nil; return r }()},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			err := ValidatePlaceOrderRequest(tc.req)
			assert.Error(t, err)
			assert.Equal(t, codes.InvalidArgument, status.Code(err))
		})
	}
}

// validTestRequest returns a fully valid PlaceOrderRequest for use in tests
func validTestRequest() *pb.PlaceOrderRequest {
	currentYear := int32(time.Now().Year())
	currentMonth := int32(time.Now().Month())
	expiryMonth := currentMonth + 1
	expiryYear := currentYear
	if expiryMonth > 12 {
		expiryMonth = 1
		expiryYear += 1
	}

	return &pb.PlaceOrderRequest{
		UserId:       "test-user-123",
		UserCurrency: "USD",
		Email:        "test@example.com",
		Address: &pb.Address{
			StreetAddress: "123 Test St",
			City:          "Test City",
			State:         "CA",
			Country:       "US",
			ZipCode:       "90210",
		},
		CreditCard: &pb.CreditCardInfo{
			CreditCardNumber:          "4242424242424242",
			CreditCardCvv:             123,
			CreditCardExpirationMonth: expiryMonth,
			CreditCardExpirationYear:  expiryYear,
		},
	}
}
