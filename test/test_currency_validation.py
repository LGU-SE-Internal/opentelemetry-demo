#!/usr/bin/env python3
import os
import sys
import math
import grpc
import subprocess
import time
from typing import List

# Import protobuf definitions for currency service
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'pb'))
import demo_pb2
import demo_pb2_grpc

DEFAULT_CURRENCY_SERVICE_ADDR = "localhost:7000"
SERVICE_BINARY = os.path.join(os.path.dirname(__file__), '..', 'src', 'currency', 'server')

def start_currency_service() -> subprocess.Popen:
    """Start the currency service subprocess"""
    env = os.environ.copy()
    env["PORT"] = DEFAULT_CURRENCY_SERVICE_ADDR.split(":")[1]
    proc = subprocess.Popen(
        [SERVICE_BINARY],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    # Wait for service to initialize
    time.sleep(3)
    return proc

def get_supported_currencies(stub: demo_pb2_grpc.CurrencyServiceStub) -> List[str]:
    """Get list of supported currency codes from service"""
    response = stub.ListSupportedCurrencies(demo_pb2.ListSupportedCurrenciesRequest())
    return [c.currency_code for c in response.currencies]

def test_ac1_unsupported_source_currency():
    """AC-1: Request with unsupported source currency returns INVALID_ARGUMENT with correct message"""
    proc = start_currency_service()
    try:
        with grpc.insecure_channel(DEFAULT_CURRENCY_SERVICE_ADDR) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            supported = get_supported_currencies(stub)
            invalid_source = "XXX"
            while invalid_source in supported:
                invalid_source += "X"
            
            try:
                stub.ConvertCurrency(demo_pb2.ConvertCurrencyRequest(
                    source_currency=invalid_source,
                    target_currency=supported[0],
                    amount=100.0
                ))
                assert False, "Expected gRPC exception for invalid source currency"
            except grpc.RpcError as e:
                assert e.code() == grpc.StatusCode.INVALID_ARGUMENT, f"Expected INVALID_ARGUMENT, got {e.code()}"
                expected_msg = f"Source currency code '{invalid_source}' is not supported"
                assert expected_msg in e.details(), f"Expected message '{expected_msg}', got '{e.details()}'"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac2_unsupported_target_currency():
    """AC-2: Request with unsupported target currency returns INVALID_ARGUMENT with correct message"""
    proc = start_currency_service()
    try:
        with grpc.insecure_channel(DEFAULT_CURRENCY_SERVICE_ADDR) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            supported = get_supported_currencies(stub)
            invalid_target = "XXX"
            while invalid_target in supported:
                invalid_target += "X"
            
            try:
                stub.ConvertCurrency(demo_pb2.ConvertCurrencyRequest(
                    source_currency=supported[0],
                    target_currency=invalid_target,
                    amount=100.0
                ))
                assert False, "Expected gRPC exception for invalid target currency"
            except grpc.RpcError as e:
                assert e.code() == grpc.StatusCode.INVALID_ARGUMENT, f"Expected INVALID_ARGUMENT, got {e.code()}"
                expected_msg = f"Target currency code '{invalid_target}' is not supported"
                assert expected_msg in e.details(), f"Expected message '{expected_msg}', got '{e.details()}'"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac3_negative_amount():
    """AC-3: Request with negative amount returns INVALID_ARGUMENT with correct message"""
    proc = start_currency_service()
    try:
        with grpc.insecure_channel(DEFAULT_CURRENCY_SERVICE_ADDR) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            supported = get_supported_currencies(stub)
            negative_amount = -10.5
            
            try:
                stub.ConvertCurrency(demo_pb2.ConvertCurrencyRequest(
                    source_currency=supported[0],
                    target_currency=supported[1] if len(supported) > 1 else supported[0],
                    amount=negative_amount
                ))
                assert False, "Expected gRPC exception for negative amount"
            except grpc.RpcError as e:
                assert e.code() == grpc.StatusCode.INVALID_ARGUMENT, f"Expected INVALID_ARGUMENT, got {e.code()}"
                expected_msg = f"Amount cannot be negative: {negative_amount}"
                assert expected_msg in e.details(), f"Expected message '{expected_msg}', got '{e.details()}'"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac4_nan_amount():
    """AC-4: Request with NaN amount returns INVALID_ARGUMENT with correct message"""
    proc = start_currency_service()
    try:
        with grpc.insecure_channel(DEFAULT_CURRENCY_SERVICE_ADDR) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            supported = get_supported_currencies(stub)
            
            try:
                stub.ConvertCurrency(demo_pb2.ConvertCurrencyRequest(
                    source_currency=supported[0],
                    target_currency=supported[1] if len(supported) > 1 else supported[0],
                    amount=math.nan
                ))
                assert False, "Expected gRPC exception for NaN amount"
            except grpc.RpcError as e:
                assert e.code() == grpc.StatusCode.INVALID_ARGUMENT, f"Expected INVALID_ARGUMENT, got {e.code()}"
                assert "Amount is not a valid monetary value" in e.details(), f"Expected invalid amount message, got '{e.details()}'"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac4_positive_infinity_amount():
    """AC-4: Request with +Infinity amount returns INVALID_ARGUMENT with correct message"""
    proc = start_currency_service()
    try:
        with grpc.insecure_channel(DEFAULT_CURRENCY_SERVICE_ADDR) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            supported = get_supported_currencies(stub)
            
            try:
                stub.ConvertCurrency(demo_pb2.ConvertCurrencyRequest(
                    source_currency=supported[0],
                    target_currency=supported[1] if len(supported) > 1 else supported[0],
                    amount=math.inf
                ))
                assert False, "Expected gRPC exception for +Infinity amount"
            except grpc.RpcError as e:
                assert e.code() == grpc.StatusCode.INVALID_ARGUMENT, f"Expected INVALID_ARGUMENT, got {e.code()}"
                assert "Amount is not a valid monetary value" in e.details(), f"Expected invalid amount message, got '{e.details()}'"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac4_negative_infinity_amount():
    """AC-4: Request with -Infinity amount returns INVALID_ARGUMENT with correct message"""
    proc = start_currency_service()
    try:
        with grpc.insecure_channel(DEFAULT_CURRENCY_SERVICE_ADDR) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            supported = get_supported_currencies(stub)
            
            try:
                stub.ConvertCurrency(demo_pb2.ConvertCurrencyRequest(
                    source_currency=supported[0],
                    target_currency=supported[1] if len(supported) > 1 else supported[0],
                    amount=-math.inf
                ))
                assert False, "Expected gRPC exception for -Infinity amount"
            except grpc.RpcError as e:
                assert e.code() == grpc.StatusCode.INVALID_ARGUMENT, f"Expected INVALID_ARGUMENT, got {e.code()}"
                assert "Amount is not a valid monetary value" in e.details(), f"Expected invalid amount message, got '{e.details()}'"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

def test_ac5_valid_request_passes_validation():
    """AC-5: Valid request passes validation and proceeds to conversion successfully"""
    proc = start_currency_service()
    try:
        with grpc.insecure_channel(DEFAULT_CURRENCY_SERVICE_ADDR) as channel:
            stub = demo_pb2_grpc.CurrencyServiceStub(channel)
            supported = get_supported_currencies(stub)
            # Test zero amount (allowed, non-negative)
            response = stub.ConvertCurrency(demo_pb2.ConvertCurrencyRequest(
                source_currency=supported[0],
                target_currency=supported[1] if len(supported) > 1 else supported[0],
                amount=0.0
            ))
            assert response is not None, "Expected valid conversion response for zero amount"
            
            # Test positive amount
            response = stub.ConvertCurrency(demo_pb2.ConvertCurrencyRequest(
                source_currency=supported[0],
                target_currency=supported[1] if len(supported) > 1 else supported[0],
                amount=123.45
            ))
            assert response is not None, "Expected valid conversion response for positive amount"
    finally:
        proc.terminate()
        proc.wait(timeout=5)

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
