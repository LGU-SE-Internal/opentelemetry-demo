package main

import (
	"testing"
	pb "github.com/open-telemetry/opentelemetry-demo/src/checkout/genproto/oteldemo"
	"github.com/stretchr/testify/assert"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// AC-1: Empty or missing user_id returns InvalidArgument with message containing "user_id is a required field"
func TestAC1_EmptyUserIdRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "", // empty
		Email: "test@example.com",
		Items: []*pb.OrderItem{
			{
				ProductId: "prod123",
				Quantity: 1,
				UnitPrice: 19.99,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Main St",
			City: "Anytown",
			ZipCode: "12345",
			Country: "US",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "user_id is a required field")
}

// AC-2: Empty/invalid email returns InvalidArgument with message "email is invalid or missing"
func TestAC2_EmptyEmailRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "user123",
		Email: "", // empty
		Items: []*pb.OrderItem{
			{
				ProductId: "prod123",
				Quantity: 1,
				UnitPrice: 19.99,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Main St",
			City: "Anytown",
			ZipCode: "12345",
			Country: "US",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "email is invalid or missing")
}

func TestAC2_InvalidEmailFormatRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "user123",
		Email: "not-an-email", // invalid format
		Items: []*pb.OrderItem{
			{
				ProductId: "prod123",
				Quantity: 1,
				UnitPrice: 19.99,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Main St",
			City: "Anytown",
			ZipCode: "12345",
			Country: "US",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "email is invalid or missing")
}

// AC-3: Empty order_items array returns "order must contain at least one item"
func TestAC3_EmptyOrderItemsRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "user123",
		Email: "test@example.com",
		Items: []*pb.OrderItem{}, // empty
		Address: &pb.Address{
			StreetAddress: "123 Main St",
			City: "Anytown",
			ZipCode: "12345",
			Country: "US",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "order must contain at least one item")
}

// AC-4: Order item with quantity <= 0 returns error with product_id
func TestAC4_ZeroItemQuantityRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "user123",
		Email: "test@example.com",
		Items: []*pb.OrderItem{
			{
				ProductId: "prod123",
				Quantity: 0, // invalid
				UnitPrice: 19.99,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Main St",
			City: "Anytown",
			ZipCode: "12345",
			Country: "US",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "item prod123 has invalid quantity: must be positive integer")
}

func TestAC4_NegativeItemQuantityRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "user123",
		Email: "test@example.com",
		Items: []*pb.OrderItem{
			{
				ProductId: "prod456",
				Quantity: -2, // invalid
				UnitPrice: 29.99,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Main St",
			City: "Anytown",
			ZipCode: "12345",
			Country: "US",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "item prod456 has invalid quantity: must be positive integer")
}

// AC-5: Order item with unit_price <= 0 returns error with product_id
func TestAC5_ZeroUnitPriceRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "user123",
		Email: "test@example.com",
		Items: []*pb.OrderItem{
			{
				ProductId: "prod123",
				Quantity: 2,
				UnitPrice: 0.0, // invalid
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Main St",
			City: "Anytown",
			ZipCode: "12345",
			Country: "US",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "item prod123 has invalid price: must be positive value")
}

func TestAC5_NegativeUnitPriceRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "user123",
		Email: "test@example.com",
		Items: []*pb.OrderItem{
			{
				ProductId: "prod789",
				Quantity: 1,
				UnitPrice: -5.99, // invalid
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Main St",
			City: "Anytown",
			ZipCode: "12345",
			Country: "US",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "item prod789 has invalid price: must be positive value")
}

// AC-6: Missing shipping address returns error "shipping address is required"
func TestAC6_MissingShippingAddressRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "user123",
		Email: "test@example.com",
		Items: []*pb.OrderItem{
			{
				ProductId: "prod123",
				Quantity: 1,
				UnitPrice: 19.99,
			},
		},
		Address: nil, // missing address
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "shipping address is required")
}

// AC-7: Missing address fields return appropriate error
func TestAC7_MissingStreetAddressRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "user123",
		Email: "test@example.com",
		Items: []*pb.OrderItem{
			{
				ProductId: "prod123",
				Quantity: 1,
				UnitPrice: 19.99,
			},
		},
		Address: &pb.Address{
			StreetAddress: "", // empty
			City: "Anytown",
			ZipCode: "12345",
			Country: "US",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "shipping address: street_address is required")
}

func TestAC7_MissingCityRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "user123",
		Email: "test@example.com",
		Items: []*pb.OrderItem{
			{
				ProductId: "prod123",
				Quantity: 1,
				UnitPrice: 19.99,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Main St",
			City: "", // empty
			ZipCode: "12345",
			Country: "US",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "shipping address: city is required")
}

func TestAC7_MissingZipCodeRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "user123",
		Email: "test@example.com",
		Items: []*pb.OrderItem{
			{
				ProductId: "prod123",
				Quantity: 1,
				UnitPrice: 19.99,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Main St",
			City: "Anytown",
			ZipCode: "", // empty
			Country: "US",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "shipping address: zip_code is required")
}

func TestAC7_MissingCountryRejected(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "user123",
		Email: "test@example.com",
		Items: []*pb.OrderItem{
			{
				ProductId: "prod123",
				Quantity: 1,
				UnitPrice: 19.99,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Main St",
			City: "Anytown",
			ZipCode: "12345",
			Country: "", // empty
		},
	}

	err := ValidatePlaceOrderRequest(req)
	assert.Error(t, err)
	assert.Equal(t, codes.InvalidArgument, status.Code(err))
	assert.Contains(t, err.Error(), "shipping address: country is required")
}

// AC-8: Valid request passes validation
func TestAC8_ValidRequestPassesValidation(t *testing.T) {
	req := &pb.PlaceOrderRequest{
		UserId: "user123",
		Email: "valid@example.com",
		Items: []*pb.OrderItem{
			{
				ProductId: "prod123",
				Quantity: 2,
				UnitPrice: 19.99,
			},
			{
				ProductId: "prod456",
				Quantity: 1,
				UnitPrice: 29.99,
			},
		},
		Address: &pb.Address{
			StreetAddress: "123 Main St",
			City: "Anytown",
			ZipCode: "12345",
			Country: "US",
		},
	}

	err := ValidatePlaceOrderRequest(req)
	// Should not return InvalidArgument error if all validations pass
	if err != nil {
		assert.NotEqual(t, codes.InvalidArgument, status.Code(err))
	}
}
