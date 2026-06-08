import grpc
import pytest
from pb.demo_pb2 import CurrencyConversionRequest
from pb.demo_pb2_grpc import CurrencyServiceStub

@pytest.fixture(scope="module")
def grpc_channel():
    # Assumes currency service is running on standard port as per demo setup
    channel = grpc.insecure_channel("currency:7000")
    yield channel
    channel.close()

@pytest.fixture(scope="module")
def currency_stub(grpc_channel):
    return CurrencyServiceStub(grpc_channel)

def test_ac1_source_currency_less_than_3_chars(currency_stub):
    """AC-1: Source currency <3 chars returns INVALID_ARGUMENT"""
    request = CurrencyConversionRequest(
        source_currency="US",
        target_currency="EUR",
        amount=100.0
    )
    with pytest.raises(grpc.RpcError) as excinfo:
        currency_stub.Convert(request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "Source currency code must be a valid 3-letter ISO 4217 format (uppercase)" in str(excinfo.value.details())

def test_ac2_source_currency_more_than_3_chars(currency_stub):
    """AC-2: Source currency >3 chars returns INVALID_ARGUMENT"""
    request = CurrencyConversionRequest(
        source_currency="USDA",
        target_currency="EUR",
        amount=100.0
    )
    with pytest.raises(grpc.RpcError) as excinfo:
        currency_stub.Convert(request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "Source currency code must be a valid 3-letter ISO 4217 format (uppercase)" in str(excinfo.value.details())

def test_ac3_source_currency_has_non_letters(currency_stub):
    """AC-3: Source currency with non-letter characters returns INVALID_ARGUMENT"""
    request = CurrencyConversionRequest(
        source_currency="U$D",
        target_currency="EUR",
        amount=100.0
    )
    with pytest.raises(grpc.RpcError) as excinfo:
        currency_stub.Convert(request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "Source currency code must be a valid 3-letter ISO 4217 format (uppercase)" in str(excinfo.value.details())

def test_ac4_target_currency_less_than_3_chars(currency_stub):
    """AC-4: Target currency <3 chars returns INVALID_ARGUMENT"""
    request = CurrencyConversionRequest(
        source_currency="USD",
        target_currency="EU",
        amount=100.0
    )
    with pytest.raises(grpc.RpcError) as excinfo:
        currency_stub.Convert(request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "Target currency code must be a valid 3-letter ISO 4217 format (uppercase)" in str(excinfo.value.details())

def test_ac5_target_currency_more_than_3_chars(currency_stub):
    """AC-5: Target currency >3 chars returns INVALID_ARGUMENT"""
    request = CurrencyConversionRequest(
        source_currency="USD",
        target_currency="EURO",
        amount=100.0
    )
    with pytest.raises(grpc.RpcError) as excinfo:
        currency_stub.Convert(request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "Target currency code must be a valid 3-letter ISO 4217 format (uppercase)" in str(excinfo.value.details())

def test_ac6_target_currency_has_non_letters(currency_stub):
    """AC-6: Target currency with non-letter characters returns INVALID_ARGUMENT"""
    request = CurrencyConversionRequest(
        source_currency="USD",
        target_currency="E@R",
        amount=100.0
    )
    with pytest.raises(grpc.RpcError) as excinfo:
        currency_stub.Convert(request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "Target currency code must be a valid 3-letter ISO 4217 format (uppercase)" in str(excinfo.value.details())

def test_ac7_amount_zero(currency_stub):
    """AC-7: Amount = 0 returns INVALID_ARGUMENT"""
    request = CurrencyConversionRequest(
        source_currency="USD",
        target_currency="EUR",
        amount=0.0
    )
    with pytest.raises(grpc.RpcError) as excinfo:
        currency_stub.Convert(request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "Conversion amount must be a positive non-zero numeric value" in str(excinfo.value.details())

def test_ac8_amount_negative(currency_stub):
    """AC-8: Negative amount returns INVALID_ARGUMENT"""
    request = CurrencyConversionRequest(
        source_currency="USD",
        target_currency="EUR",
        amount=-10.50
    )
    with pytest.raises(grpc.RpcError) as excinfo:
        currency_stub.Convert(request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "Conversion amount must be a positive non-zero numeric value" in str(excinfo.value.details())

def test_ac9_source_currency_3_chars_invalid_code(currency_stub):
    """AC-9: 3-letter source currency not in ISO 4217 returns INVALID_ARGUMENT"""
    request = CurrencyConversionRequest(
        source_currency="XYZ",
        target_currency="EUR",
        amount=100.0
    )
    with pytest.raises(grpc.RpcError) as excinfo:
        currency_stub.Convert(request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "Source currency code XYZ is not a supported/valid ISO 4217 currency code" in str(excinfo.value.details())

def test_ac10_target_currency_3_chars_invalid_code(currency_stub):
    """AC-10: 3-letter target currency not in ISO 4217 returns INVALID_ARGUMENT"""
    request = CurrencyConversionRequest(
        source_currency="USD",
        target_currency="XYZ",
        amount=100.0
    )
    with pytest.raises(grpc.RpcError) as excinfo:
        currency_stub.Convert(request)
    assert excinfo.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert "Target currency code XYZ is not a supported/valid ISO 4217 currency code" in str(excinfo.value.details())

def test_ac11_valid_request_passes_validation(currency_stub):
    """AC-11: All valid parameters pass validation and proceed to conversion"""
    request = CurrencyConversionRequest(
        source_currency="USD",
        target_currency="EUR",
        amount=100.0
    )
    response = currency_stub.Convert(request)
    assert response is not None
    assert hasattr(response, 'converted_amount')
    assert response.converted_amount > 0
