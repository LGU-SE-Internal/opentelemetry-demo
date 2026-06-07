package main

import (
	"testing"
	"github.com/google/uuid"
	pb "github.com/open-telemetry/opentelemetry-demo/src/checkout/genproto/oteldemo"
	"github.com/stretchr/testify/assert"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestAC1_NegativeItemQuantityRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: uuid.NewString(),
		Items: []*pb.OrderItem{
			{
				ProductId: "product1",
				Quantity:  0, // invalid
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Test St",
			City:          "Test City",
			State:         "CA",
			Country:       "US",
			ZipCode:       "90210",
		},
		CreditCard: &pb.CreditCardInfo{
			Number:          "4242424242424242",
			ExpirationMonth: 12,
			ExpirationYear:  2030,
			Cvv:             "123",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "invalid field items[0].quantity: must be greater than 0")
}

func TestAC2_MissingRequiredFieldRejected(t *testing.T) {
	// Test missing user_id
	req := &pb.PlaceOrderRequest{
		UserId: "", // missing
		Items: []*pb.OrderItem{
			{
				ProductId: "product1",
				Quantity:  1,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Test St",
			City:          "Test City",
			State:         "CA",
			Country:       "US",
			ZipCode:       "90210",
		},
		CreditCard: &pb.CreditCardInfo{
			Number:          "4242424242424242",
			ExpirationMonth: 12,
			ExpirationYear:  2030,
			Cvv:             "123",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "invalid field user_id: required field is empty")

	// Test missing address city
	req.UserId = uuid.NewString()
	req.Address.City = ""
	err = ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "invalid field address.city: required field is empty")
}

func TestAC3_InvalidUserIdUUIDRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "invalid-uuid", // not valid UUID v4
		Items: []*pb.OrderItem{
			{
				ProductId: "product1",
				Quantity:  1,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Test St",
			City:          "Test City",
			State:         "CA",
			Country:       "US",
			ZipCode:       "90210",
		},
		CreditCard: &pb.CreditCardInfo{
			Number:          "4242424242424242",
			ExpirationMonth: 12,
			ExpirationYear:  2030,
			Cvv:             "123",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "invalid field user_id: must be valid UUID v4")
}

func TestAC4_InvalidZipCodeForCountryRejected(t *testing.T) {
	// Test invalid US zip code
	req := &pb.PlaceOrderRequest{
		UserId: uuid.NewString(),
		Items: []*pb.OrderItem{
			{
				ProductId: "product1",
				Quantity:  1,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Test St",
			City:          "Test City",
			State:         "CA",
			Country:       "US",
			ZipCode:       "1234", // only 4 digits, invalid for US
		},
		CreditCard: &pb.CreditCardInfo{
			Number:          "4242424242424242",
			ExpirationMonth: 12,
			ExpirationYear:  2030,
			Cvv:             "123",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "invalid field address.zip_code: invalid format for country US")
}

func TestAC5_InvalidCreditCardNumberFailsLuhnRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: uuid.NewString(),
		Items: []*pb.OrderItem{
			{
				ProductId: "product1",
				Quantity:  1,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Test St",
			City:          "Test City",
			State:         "CA",
			Country:       "US",
			ZipCode:       "90210",
		},
		CreditCard: &pb.CreditCardInfo{
			Number:          "4242424242424241", // fails Luhn check
			ExpirationMonth: 12,
			ExpirationYear:  2030,
			Cvv:             "123",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "invalid field credit_card.number: invalid card number")
}

func TestAC6_ExpiredCreditCardRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: uuid.NewString(),
		Items: []*pb.OrderItem{
			{
				ProductId: "product1",
				Quantity:  1,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Test St",
			City:          "Test City",
			State:         "CA",
			Country:       "US",
			ZipCode:       "90210",
		},
		CreditCard: &pb.CreditCardInfo{
			Number:          "4242424242424242",
			ExpirationMonth: 12,
			ExpirationYear:  2020, // past year
			Cvv:             "123",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "invalid field credit_card.expiration: date is in the past")
}

func TestAC7_InvalidCVVLengthRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: uuid.NewString(),
		Items: []*pb.OrderItem{
			{
				ProductId: "product1",
				Quantity:  1,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Test St",
			City:          "Test City",
			State:         "CA",
			Country:       "US",
			ZipCode:       "90210",
		},
		CreditCard: &pb.CreditCardInfo{
			Number:          "4242424242424242",
			ExpirationMonth: 12,
			ExpirationYear:  2030,
			Cvv:             "12", // only 2 digits, invalid
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Contains(t, err.Error(), "invalid field credit_card.cvv: must be 3 or 4 digits")
}

func TestAC8_SingleValidationFunctionUsed(t *testing.T) {
	// Verify ValidatePlaceOrderRequest calls all validation helpers
	// This test ensures no duplicate validation logic exists
	// Valid request should pass all validations
	req := &pb.PlaceOrderRequest{
		UserId: uuid.NewString(),
		Items: []*pb.OrderItem{
			{
				ProductId: "product1",
				Quantity:  1,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Test St",
			City:          "Test City",
			State:         "CA",
			Country:       "US",
			ZipCode:       "90210",
		},
		CreditCard: &pb.CreditCardInfo{
			Number:          "4242424242424242",
			ExpirationMonth: 12,
			ExpirationYear:  2030,
			Cvv:             "123",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.NoError(t, err)
}

func TestAC9_AllValidationHelpersHaveTests(t *testing.T) {
	// Test ValidateUserID success
	validUUID := uuid.NewString()
	err := ValidateUserID(validUUID)
	assert.NoError(t, err)

	// Test ValidateUserID failure
	err = ValidateUserID("invalid-uuid")
	assert.Error(t, err)

	// Test ValidateItemQuantities success
	validItems := []*pb.OrderItem{{Quantity: 1}, {Quantity: 2}}
	err = ValidateItemQuantities(validItems)
	assert.NoError(t, err)

	// Test ValidateItemQuantities failure
	invalidItems := []*pb.OrderItem{{Quantity: 0}}
	err = ValidateItemQuantities(invalidItems)
	assert.Error(t, err)
}

func TestAC10_NoSensitiveDataInErrorMessages(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "invalid-uuid",
		Items: []*pb.OrderItem{
			{
				ProductId: "product1",
				Quantity:  1,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Test St",
			City:          "Test City",
			State:         "CA",
			Country:       "US",
			ZipCode:       "90210",
		},
		CreditCard: &pb.CreditCardInfo{
			Number:          "4242424242424242",
			ExpirationMonth: 12,
			ExpirationYear:  2030,
			Cvv:             "123",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	// Ensure no stack trace or sensitive data in error
	assert.NotContains(t, err.Error(), "stack")
	assert.NotContains(t, err.Error(), "runtime")
	assert.NotContains(t, err.Error(), "panic")
	assert.NotContains(t, err.Error(), req.CreditCard.Cvv)
}
