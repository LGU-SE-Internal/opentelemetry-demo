import grpc
import time
import os
from concurrent import futures
from unittest import mock
from src.currency.proto import currency_pb2
from src.currency.proto import currency_pb2_grpc

# Test constants from spec
RATE_LIMIT_ENV_VAR = "CURRENCY_SERVICE_RATE_LIMIT_RPS"
RATE_LIMIT_METRIC_NAME = "currency_service_rate_limited_requests_total"
GRPC_STATUS_RESOURCE_EXHAUSTED = grpc.StatusCode.RESOURCE_EXHAUSTED
ENDPOINT_GET_SUPPORTED_CURRENCIES = "GetSupportedCurrencies"
ENDPOINT_CONVERT = "Convert"

def test_ac1_per_ip_rate_limit_enforces_n_requests_per_second():
    """AC-1: When rate limit set to N, allow exactly N requests per second per IP, reject excess."""
    os.environ[RATE_LIMIT_ENV_VAR] = "5"
    # Setup test client with different IPs
    client_ip1 = "192.168.1.100"
    client_ip2 = "192.168.1.101"
    
    # Send 6 requests from ip1 in 1s window
    success_count_ip1 = 0
    reject_count_ip1 = 0
    for i in range(6):
        # Mock gRPC call from ip1
        with mock.patch('grpc.aio.ServerCall.peer', return_value=f"ipv4:{client_ip1}:12345"):
            try:
                # Make actual GetSupportedCurrencies call
                stub = currency_pb2_grpc.CurrencyServiceStub(grpc.insecure_channel('localhost:7000'))
                stub.GetSupportedCurrencies(currency_pb2.GetSupportedCurrenciesRequest())
                success_count_ip1 +=1
            except grpc.RpcError as e:
                if e.code() == GRPC_STATUS_RESOURCE_EXHAUSTED:
                    reject_count_ip1 +=1
    
    # Verify exactly 5 success, 1 reject for ip1
    assert success_count_ip1 == 5, f"Expected 5 successful requests, got {success_count_ip1}"
    assert reject_count_ip1 == 1, f"Expected 1 rejected request, got {reject_count_ip1}"
    
    # Send 3 requests from ip2, all should succeed
    success_count_ip2 = 0
    for i in range(3):
        with mock.patch('grpc.aio.ServerCall.peer', return_value=f"ipv4:{client_ip2}:12345"):
            try:
                stub.GetSupportedCurrencies(currency_pb2.GetSupportedCurrenciesRequest())
                success_count_ip2 +=1
            except grpc.RpcError as e:
                pass
    
    assert success_count_ip2 == 3, f"Expected 3 successful requests for second IP, got {success_count_ip2}"
    
    # Wait 1 second for rate limit window to reset
    time.sleep(1.1)
    
    # Send another 5 requests from ip1, should all succeed now
    success_count_reset = 0
    for i in range(5):
        with mock.patch('grpc.aio.ServerCall.peer', return_value=f"ipv4:{client_ip1}:12345"):
            try:
                stub.GetSupportedCurrencies(currency_pb2.GetSupportedCurrenciesRequest())
                success_count_reset +=1
            except grpc.RpcError as e:
                pass
    
    assert success_count_reset == 5, f"Expected 5 successful requests after window reset, got {success_count_reset}"
    del os.environ[RATE_LIMIT_ENV_VAR]

def test_ac2_rejected_requests_return_resource_exhausted_status():
    """AC-2: Rejected requests receive RESOURCE_EXHAUSTED status with correct error message."""
    os.environ[RATE_LIMIT_ENV_VAR] = "1"
    client_ip = "10.0.0.1"
    stub = currency_pb2_grpc.CurrencyServiceStub(grpc.insecure_channel('localhost:7000'))
    
    # First request should succeed
    with mock.patch('grpc.aio.ServerCall.peer', return_value=f"ipv4:{client_ip}:12345"):
        stub.GetSupportedCurrencies(currency_pb2.GetSupportedCurrenciesRequest())
    
    # Second request should fail with correct error
    with mock.patch('grpc.aio.ServerCall.peer', return_value=f"ipv4:{client_ip}:12345"):
        try:
            stub.GetSupportedCurrencies(currency_pb2.GetSupportedCurrenciesRequest())
            assert False, "Expected gRPC error not received"
        except grpc.RpcError as e:
            assert e.code() == GRPC_STATUS_RESOURCE_EXHAUSTED, f"Expected RESOURCE_EXHAUSTED, got {e.code()}"
            assert "Rate limit exceeded: maximum 1 requests per second per IP address" in str(e.details())
    
    del os.environ[RATE_LIMIT_ENV_VAR]

def test_ac3_rate_limit_disabled_when_env_var_not_set_or_non_positive():
    """AC-3: Rate limiting disabled when env var is unset, 0, or negative."""
    stub = currency_pb2_grpc.CurrencyServiceStub(grpc.insecure_channel('localhost:7000'))
    client_ip = "172.16.0.1"
    
    # Test 1: Env var not set, send 10 requests, all should succeed
    success_count = 0
    for i in range(10):
        with mock.patch('grpc.aio.ServerCall.peer', return_value=f"ipv4:{client_ip}:12345"):
            try:
                stub.GetSupportedCurrencies(currency_pb2.GetSupportedCurrenciesRequest())
                success_count +=1
            except grpc.RpcError as e:
                pass
    assert success_count == 10, f"Expected 10 successful requests with no rate limit set, got {success_count}"
    
    # Test 2: Env var set to 0
    os.environ[RATE_LIMIT_ENV_VAR] = "0"
    success_count = 0
    for i in range(10):
        with mock.patch('grpc.aio.ServerCall.peer', return_value=f"ipv4:{client_ip}:12345"):
            try:
                stub.GetSupportedCurrencies(currency_pb2.GetSupportedCurrenciesRequest())
                success_count +=1
            except grpc.RpcError as e:
                pass
    assert success_count == 10, f"Expected 10 successful requests with rate limit 0, got {success_count}"
    del os.environ[RATE_LIMIT_ENV_VAR]
    
    # Test 3: Env var set to negative value
    os.environ[RATE_LIMIT_ENV_VAR] = "-5"
    success_count = 0
    for i in range(10):
        with mock.patch('grpc.aio.ServerCall.peer', return_value=f"ipv4:{client_ip}:12345"):
            try:
                stub.GetSupportedCurrencies(currency_pb2.GetSupportedCurrenciesRequest())
                success_count +=1
            except grpc.RpcError as e:
                pass
    assert success_count == 10, f"Expected 10 successful requests with negative rate limit, got {success_count}"
    del os.environ[RATE_LIMIT_ENV_VAR]

def test_ac4_rate_limited_requests_increment_counter_metric():
    """AC-4: Rejected requests increment counter metric with correct labels."""
    os.environ[RATE_LIMIT_ENV_VAR] = "2"
    client_ip = "192.168.0.10"
    stub = currency_pb2_grpc.CurrencyServiceStub(grpc.insecure_channel('localhost:7000'))
    
    # Send 3 GetSupportedCurrencies requests, 1 should be rejected
    for i in range(3):
        with mock.patch('grpc.aio.ServerCall.peer', return_value=f"ipv4:{client_ip}:12345"):
            try:
                stub.GetSupportedCurrencies(currency_pb2.GetSupportedCurrenciesRequest())
            except grpc.RpcError:
                pass
    
    # Verify metric exists with correct labels and value 1
    # Mock prometheus metric scrape
    from prometheus_client.parser import text_string_to_metric_families
    import requests
    metrics = requests.get("http://localhost:9464/metrics").text
    found = False
    for family in text_string_to_metric_families(metrics):
        if family.name == RATE_LIMIT_METRIC_NAME:
            for sample in family.samples:
                if sample.labels["ip_address"] == client_ip and sample.labels["endpoint"] == ENDPOINT_GET_SUPPORTED_CURRENCIES:
                    assert sample.value == 1.0, f"Expected metric value 1, got {sample.value}"
                    found = True
    assert found, f"Metric {RATE_LIMIT_METRIC_NAME} with correct labels not found"
    
    # Send 3 Convert requests, 1 rejected, total 2 rejects
    for i in range(3):
        with mock.patch('grpc.aio.ServerCall.peer', return_value=f"ipv4:{client_ip}:12345"):
            try:
                req = currency_pb2.ConvertRequest(from_currency="USD", to_currency="EUR", units=1, nanos=0)
                stub.Convert(req)
            except grpc.RpcError:
                pass
    
    # Check Convert endpoint metric
    metrics = requests.get("http://localhost:9464/metrics").text
    found = False
    for family in text_string_to_metric_families(metrics):
        if family.name == RATE_LIMIT_METRIC_NAME:
            for sample in family.samples:
                if sample.labels["ip_address"] == client_ip and sample.labels["endpoint"] == ENDPOINT_CONVERT:
                    assert sample.value == 1.0, f"Expected Convert metric value 1, got {sample.value}"
                    found = True
    assert found, f"Metric {RATE_LIMIT_METRIC_NAME} for Convert endpoint not found"
    del os.environ[RATE_LIMIT_ENV_VAR]

def test_ac6_rate_limits_independent_per_ip():
    """AC-6: Rate limits are enforced independently per client IP."""
    os.environ[RATE_LIMIT_ENV_VAR] = "3"
    ip1 = "10.1.0.1"
    ip2 = "10.1.0.2"
    ip3 = "10.1.0.3"
    stub = currency_pb2_grpc.CurrencyServiceStub(grpc.insecure_channel('localhost:7000'))
    
    # Send 5 requests from each IP
    stats = {ip1: {"success": 0, "reject": 0}, ip2: {"success":0, "reject":0}, ip3: {"success":0, "reject":0}}
    for ip in [ip1, ip2, ip3]:
        for i in range(5):
            with mock.patch('grpc.aio.ServerCall.peer', return_value=f"ipv4:{ip}:12345"):
                try:
                    stub.GetSupportedCurrencies(currency_pb2.GetSupportedCurrenciesRequest())
                    stats[ip]["success"] +=1
                except grpc.RpcError as e:
                    if e.code() == GRPC_STATUS_RESOURCE_EXHAUSTED:
                        stats[ip]["reject"] +=1
    
    # Each IP should have exactly 3 success, 2 rejects
    for ip in [ip1, ip2, ip3]:
        assert stats[ip]["success"] == 3, f"IP {ip} expected 3 successes, got {stats[ip]['success']}"
        assert stats[ip]["reject"] == 2, f"IP {ip} expected 2 rejects, got {stats[ip]['reject']}"
    del os.environ[RATE_LIMIT_ENV_VAR]

def test_ac7_rate_limit_applied_equally_to_both_endpoints():
    """AC-7: Rate limiting applied identically to both endpoints."""
    os.environ[RATE_LIMIT_ENV_VAR] = "4"
    client_ip = "172.31.0.50"
    stub = currency_pb2_grpc.CurrencyServiceStub(grpc.insecure_channel('localhost:7000'))
    
    # Alternate between endpoints for total 6 requests
    requests = [
        (ENDPOINT_GET_SUPPORTED_CURRENCIES, currency_pb2.GetSupportedCurrenciesRequest()),
        (ENDPOINT_CONVERT, currency_pb2.ConvertRequest(from_currency="USD", to_currency="GBP", units=1, nanos=0)),
        (ENDPOINT_GET_SUPPORTED_CURRENCIES, currency_pb2.GetSupportedCurrenciesRequest()),
        (ENDPOINT_CONVERT, currency_pb2.ConvertRequest(from_currency="EUR", to_currency="CAD", units=1, nanos=0)),
        (ENDPOINT_GET_SUPPORTED_CURRENCIES, currency_pb2.GetSupportedCurrenciesRequest()),
        (ENDPOINT_CONVERT, currency_pb2.ConvertRequest(from_currency="JPY", to_currency="AUD", units=100, nanos=0)),
    ]
    
    success_count = 0
    reject_count = 0
    for endpoint, req in requests:
        with mock.patch('grpc.aio.ServerCall.peer', return_value=f"ipv4:{client_ip}:12345"):
            try:
                if endpoint == ENDPOINT_GET_SUPPORTED_CURRENCIES:
                    stub.GetSupportedCurrencies(req)
                else:
                    stub.Convert(req)
                success_count +=1
            except grpc.RpcError as e:
                if e.code() == GRPC_STATUS_RESOURCE_EXHAUSTED:
                    reject_count +=1
    
    # Total of 4 success, 2 reject regardless of endpoint
    assert success_count == 4, f"Expected 4 total successful requests, got {success_count}"
    assert reject_count == 2, f"Expected 2 total rejected requests, got {reject_count}"
    del os.environ[RATE_LIMIT_ENV_VAR]
