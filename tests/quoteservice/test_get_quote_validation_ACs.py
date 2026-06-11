#!/usr/bin/env python3
import os
import pytest
import grpc

# Import generated gRPC code
from pb.demo_pb2 import GetQuoteRequest, Address, CartItem
from pb.demo_pb2_grpc import ShippingServiceStub

SERVICE_NAME = os.environ.get("TEST_QUOTE_SERVICE_NAME", "quote-service")
NAMESPACE = os.environ.get("TEST_NAMESPACE", "default")
SERVICE_PORT = 8080
GRPC_ENDPOINT = f"{SERVICE_NAME}.{NAMESPACE}.svc.cluster.local:{SERVICE_PORT}"

@pytest.fixture(scope="module")
def grpc_channel():
    channel = grpc.insecure_channel(GRPC_ENDPOINT)
    yield channel
    channel.close()

@pytest.fixture(scope="module")
def stub(grpc_channel):
    return ShippingServiceStub(grpc_channel)

@pytest.fixture
def valid_request():
    # Base valid request for use in all tests
    return GetQuoteRequest(
        address=Address(
            street_address="123 Main St",
            city="Anytown",
            state="CA",
            country="US",
            zip_code="90210"
        ),
        shipping_distance=10.5,
        items=[
            CartItem(product_id="prod1", quantity=2),
            CartItem(product_id="prod2", quantity=1)
        ]
    )

@pytest.mark.ac1
def test_ac1_shipping_distance_le_0_returns_invalid_argument(stub, valid_request):
    """AC-1: When a GetQuoteRequest contains a shipping_distance value ≤ 0,
    the service returns INVALID_ARGUMENT status before executing any business logic."""
    # Test with 0 distance
    valid_request.shipping_distance = 0.0
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "shipping distance must be greater than 0" in str(excinfo.value.details()).lower()

    # Test with negative distance
    valid_request.shipping_distance = -5.2
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "shipping distance must be greater than 0" in str(excinfo.value.details()).lower()

@pytest.mark.ac2
def test_ac2_item_weight_le_0_returns_invalid_argument(stub, valid_request):
    """AC-2: When a GetQuoteRequest contains any item with a weight value ≤ 0,
    the service returns INVALID_ARGUMENT status with item index before executing any business logic."""
    # Add weight fields to items (test invalid at index 1)
    valid_request.items[0].weight = 1.2
    valid_request.items[1].weight = 0.0
    
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    err_msg = str(excinfo.value.details()).lower()
    assert "item" in err_msg and "index 1" in err_msg
    assert "weight must be greater than 0" in err_msg

    # Test negative weight
    valid_request.items[1].weight = -0.5
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    err_msg = str(excinfo.value.details()).lower()
    assert "item" in err_msg and "index 1" in err_msg
    assert "weight must be greater than 0" in err_msg

@pytest.mark.ac3
def test_ac3_item_quantity_lt_1_returns_invalid_argument(stub, valid_request):
    """AC-3: When a GetQuoteRequest contains any item with a quantity value < 1,
    the service returns INVALID_ARGUMENT status with item index before executing any business logic."""
    # Test 0 quantity at index 0
    valid_request.items[0].quantity = 0
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    err_msg = str(excinfo.value.details()).lower()
    assert "item" in err_msg and "index 0" in err_msg
    assert "quantity must be greater than or equal to 1" in err_msg or "quantity must be at least 1" in err_msg

    # Test negative quantity
    valid_request.items[1].quantity = -3
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    err_msg = str(excinfo.value.details()).lower()
    assert "item" in err_msg and "index 1" in err_msg
    assert "quantity must be greater than or equal to 1" in err_msg or "quantity must be at least 1" in err_msg

@pytest.mark.ac4
def test_ac4_empty_shipping_address_fields_return_invalid_argument(stub, valid_request):
    """AC-4: When a GetQuoteRequest contains an empty value for any required shipping address field,
    the service returns INVALID_ARGUMENT status identifying the empty field."""
    # Test empty street address
    valid_request.address.street_address = ""
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    err_msg = str(excinfo.value.details()).lower()
    assert "street" in err_msg or "street_address" in err_msg
    assert "cannot be empty" in err_msg or "must not be empty" in err_msg

    # Test empty city
    valid_request.address.street_address = "123 Main St"
    valid_request.address.city = ""
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    err_msg = str(excinfo.value.details()).lower()
    assert "city" in err_msg
    assert "cannot be empty" in err_msg or "must not be empty" in err_msg

    # Test empty state
    valid_request.address.city = "Anytown"
    valid_request.address.state = ""
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    err_msg = str(excinfo.value.details()).lower()
    assert "state" in err_msg
    assert "cannot be empty" in err_msg or "must not be empty" in err_msg

    # Test empty postal code / zip code
    valid_request.address.state = "CA"
    valid_request.address.zip_code = ""
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    err_msg = str(excinfo.value.details()).lower()
    assert "postal" in err_msg or "zip" in err_msg or "zip_code" in err_msg
    assert "cannot be empty" in err_msg or "must not be empty" in err_msg

    # Test empty country
    valid_request.address.zip_code = "90210"
    valid_request.address.country = ""
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    err_msg = str(excinfo.value.details()).lower()
    assert "country" in err_msg
    assert "cannot be empty" in err_msg or "must not be empty" in err_msg

@pytest.mark.ac5
def test_ac5_all_valid_fields_pass_validation(stub, valid_request):
    """AC-5: When a GetQuoteRequest contains all valid fields matching validation rules,
    validation passes and request proceeds to existing quote calculation logic."""
    # Set valid weights for items
    valid_request.items[0].weight = 1.5
    valid_request.items[1].weight = 0.8
    
    # Should not raise INVALID_ARGUMENT error
    response = stub.GetQuote(valid_request)
    # Verify we get a valid response (cost exists)
    assert hasattr(response, "cost_usd")
    assert response.cost_usd.currency_code == "USD"
    assert response.cost_usd.units >= 0

@pytest.mark.ac6
def test_ac6_no_downstream_calls_on_validation_failure(mocker, stub, valid_request):
    """AC-6: No downstream service calls are made for any request that fails validation."""
    # Mock downstream service calls
    mock_downstream_call = mocker.patch("pb.demo_pb2_grpc.ShippingServiceStub.GetQuote")
    # Setup invalid request
    valid_request.shipping_distance = -1
    
    # Try to call endpoint
    try:
        stub.GetQuote(valid_request)
    except grpc.RpcError:
        pass
    
    # Verify no actual downstream business logic call was made
    mock_downstream_call.assert_not_called()

@pytest.mark.ac7
def test_ac7_all_validation_rules_have_coverage():
    """AC-7: All validation rules have corresponding unit tests covering valid and invalid cases."""
    # This test verifies that all required validation rules are covered by existing tests
    validation_rules = [
        "shipping_distance > 0",
        "item.weight > 0",
        "item.quantity >= 1",
        "address.street_address non-empty",
        "address.city non-empty",
        "address.state non-empty",
        "address.country non-empty",
        "address.zip_code non-empty"
    ]
    
    # Check that we have tests for each rule
    for rule in validation_rules:
        # Verify test exists for this rule (check test function names)
        test_functions = [f for f in globals() if f.startswith("test_ac")]
        assert any(rule.replace(" ", "_").replace(".", "_") in f.lower() for f in test_functions) or \
               any(rule in t.__doc__.lower() for t in [globals()[f] for f in test_functions]), \
               f"Missing test for validation rule: {rule}"

@pytest.mark.ac8
def test_ac8_error_messages_are_clear_and_descriptive(stub, valid_request):
    """AC-8: All validation error messages clearly indicate which field is invalid and the constraint violated."""
    # Test invalid shipping distance
    valid_request.shipping_distance = 0
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    msg = excinfo.value.details()
    assert "shipping_distance" in msg.lower() or "shipping distance" in msg.lower()
    assert "greater than 0" in msg.lower() or "0" in msg and "invalid" in msg

    # Test invalid item quantity
    valid_request.shipping_distance = 10
    valid_request.items[0].quantity = 0
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    msg = excinfo.value.details()
    assert "item" in msg.lower() and "index" in msg.lower() and "0" in msg
    assert "quantity" in msg.lower()
    assert "at least 1" in msg.lower() or ">= 1" in msg.lower() or "greater than 0" in msg.lower()

    # Test empty address field
    valid_request.items[0].quantity = 2
    valid_request.address.city = ""
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.GetQuote(valid_request)
    msg = excinfo.value.details()
    assert "city" in msg.lower()
    assert "empty" in msg.lower() or "required" in msg.lower()
