import os
import time
from unittest.mock import Mock, patch
import pytest
import grpc

# Env var names from spec
ENV_FAILURE_THRESHOLD = "PRODUCT_CATALOG_CIRCUIT_BREAKER_FAILURE_THRESHOLD"
ENV_RESET_TIMEOUT = "PRODUCT_CATALOG_CIRCUIT_BREAKER_RESET_TIMEOUT"
ENV_HALF_OPEN_MAX_CALLS = "PRODUCT_CATALOG_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS"

# Metric names from spec
METRIC_STATE_NAME = "recommendation_service_product_catalog_circuit_breaker_state"
METRIC_TRIPS_NAME = "recommendation_service_product_catalog_circuit_breaker_trips_total"

# Expected error message
CIRCUIT_OPEN_ERROR_MSG = "Product Catalog Service is temporarily unavailable: circuit breaker is open"


@pytest.fixture
def mock_product_catalog_client():
    client = Mock()
    client.ListProducts.side_effect = grpc.RpcError()
    client.GetProduct.side_effect = grpc.RpcError()
    return client


@pytest.fixture
def reset_env_vars():
    for var in [ENV_FAILURE_THRESHOLD, ENV_RESET_TIMEOUT, ENV_HALF_OPEN_MAX_CALLS]:
        if var in os.environ:
            del os.environ[var]
    yield
    for var in [ENV_FAILURE_THRESHOLD, ENV_RESET_TIMEOUT, ENV_HALF_OPEN_MAX_CALLS]:
        if var in os.environ:
            del os.environ[var]


def test_ac1_circuit_trips_after_consecutive_failures(mock_product_catalog_client, reset_env_vars):
    # AC-1: After N consecutive failed calls, circuit transitions to open state
    os.environ[ENV_FAILURE_THRESHOLD] = "3"
    
    from recommendation_server import product_catalog_circuit_breaker, product_catalog_client
    
    with patch.object(product_catalog_client, 'ListProducts', side_effect=grpc.RpcError()):
        # Make N-1 failed calls
        for _ in range(2):
            try:
                product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
            except grpc.RpcError:
                pass
        
        # Circuit should still be closed (0)
        assert get_metric_value(METRIC_STATE_NAME) == 0
        initial_trips = get_metric_value(METRIC_TRIPS_NAME)
        
        # Make Nth failed call
        try:
            product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
        except grpc.RpcError:
            pass
        
        # Circuit should now be open (1), trips increased by 1
        assert get_metric_value(METRIC_STATE_NAME) == 1
        assert get_metric_value(METRIC_TRIPS_NAME) == initial_trips + 1


def test_ac2_open_circuit_blocks_all_calls(mock_product_catalog_client, reset_env_vars):
    # AC-2: Open circuit returns UNAVAILABLE immediately without downstream calls
    os.environ[ENV_FAILURE_THRESHOLD] = "1"
    
    from recommendation_server import product_catalog_circuit_breaker, product_catalog_client
    
    with patch.object(product_catalog_client, 'ListProducts', side_effect=grpc.RpcError()) as mock_call:
        # Trip the circuit
        try:
            product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
        except grpc.RpcError:
            pass
        assert get_metric_value(METRIC_STATE_NAME) == 1
        mock_call.reset_mock()
        
        # Make 10 calls, none should hit downstream, all return UNAVAILABLE
        for _ in range(10):
            with pytest.raises(grpc.RpcError) as excinfo:
                product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
            assert excinfo.value.code() == grpc.StatusCode.UNAVAILABLE
            assert CIRCUIT_OPEN_ERROR_MSG in str(excinfo.value)
        
        assert mock_call.call_count == 0


def test_ac3_circuit_transitions_to_half_open_after_timeout(mock_product_catalog_client, reset_env_vars):
    # AC-3: After reset timeout, circuit transitions to half-open
    os.environ[ENV_FAILURE_THRESHOLD] = "1"
    os.environ[ENV_RESET_TIMEOUT] = "1"  # 1 second timeout
    
    from recommendation_server import product_catalog_circuit_breaker, product_catalog_client
    
    with patch.object(product_catalog_client, 'ListProducts', side_effect=grpc.RpcError()):
        # Trip the circuit
        try:
            product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
        except grpc.RpcError:
            pass
        assert get_metric_value(METRIC_STATE_NAME) == 1
        
        # Wait for reset timeout
        time.sleep(1.5)
        
        # Circuit should be half-open (2)
        assert get_metric_value(METRIC_STATE_NAME) == 2


def test_ac4_half_open_limits_max_calls(mock_product_catalog_client, reset_env_vars):
    # AC-4: Half-open only allows configured max calls
    os.environ[ENV_FAILURE_THRESHOLD] = "1"
    os.environ[ENV_RESET_TIMEOUT] = "1"
    os.environ[ENV_HALF_OPEN_MAX_CALLS] = "3"
    
    from recommendation_server import product_catalog_circuit_breaker, product_catalog_client
    
    with patch.object(product_catalog_client, 'ListProducts', side_effect=grpc.RpcError()) as mock_call:
        # Trip the circuit
        try:
            product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
        except grpc.RpcError:
            pass
        assert get_metric_value(METRIC_STATE_NAME) == 1
        
        # Wait for reset timeout
        time.sleep(1.5)
        assert get_metric_value(METRIC_STATE_NAME) == 2
        mock_call.reset_mock()
        
        # Make 5 calls: only 3 should hit downstream
        for _ in range(5):
            try:
                product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
            except grpc.RpcError:
                pass
        
        assert mock_call.call_count == 3
        # Circuit should have transitioned back to open after failures
        assert get_metric_value(METRIC_STATE_NAME) == 1


def test_ac5_half_open_failure_reopens_circuit(mock_product_catalog_client, reset_env_vars):
    # AC-5: Any failure in half-open transitions back to open
    os.environ[ENV_FAILURE_THRESHOLD] = "1"
    os.environ[ENV_RESET_TIMEOUT] = "1"
    os.environ[ENV_HALF_OPEN_MAX_CALLS] = "3"
    
    from recommendation_server import product_catalog_circuit_breaker, product_catalog_client
    
    with patch.object(product_catalog_client, 'ListProducts') as mock_call:
        # Trip circuit
        mock_call.side_effect = grpc.RpcError()
        try:
            product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
        except grpc.RpcError:
            pass
        assert get_metric_value(METRIC_STATE_NAME) == 1
        initial_trips = get_metric_value(METRIC_TRIPS_NAME)
        
        # Wait for half-open
        time.sleep(1.5)
        assert get_metric_value(METRIC_STATE_NAME) == 2
        
        # First call fails, rest succeed
        mock_call.side_effect = [grpc.RpcError(), Mock(), Mock()]
        
        for _ in range(3):
            try:
                product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
            except grpc.RpcError:
                pass
        
        # Circuit should be open, trips increased by 1
        assert get_metric_value(METRIC_STATE_NAME) == 1
        assert get_metric_value(METRIC_TRIPS_NAME) == initial_trips + 1


def test_ac6_half_open_success_closes_circuit(mock_product_catalog_client, reset_env_vars):
    # AC-6: All half-open calls succeed, circuit transitions to closed
    os.environ[ENV_FAILURE_THRESHOLD] = "1"
    os.environ[ENV_RESET_TIMEOUT] = "1"
    os.environ[ENV_HALF_OPEN_MAX_CALLS] = "3"
    
    from recommendation_server import product_catalog_circuit_breaker, product_catalog_client
    
    with patch.object(product_catalog_client, 'ListProducts') as mock_call:
        # Trip circuit
        mock_call.side_effect = grpc.RpcError()
        try:
            product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
        except grpc.RpcError:
            pass
        assert get_metric_value(METRIC_STATE_NAME) == 1
        
        # Wait for half-open
        time.sleep(1.5)
        assert get_metric_value(METRIC_STATE_NAME) == 2
        
        # All calls succeed
        mock_call.side_effect = [Mock(), Mock(), Mock()]
        
        for _ in range(3):
            product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
        
        # Circuit should be closed
        assert get_metric_value(METRIC_STATE_NAME) == 0


def test_ac7_configuration_reads_env_vars_with_defaults(reset_env_vars):
    # AC-7: Config parameters read from env vars, defaults applied when missing
    from recommendation_server import (
        PRODUCT_CATALOG_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        PRODUCT_CATALOG_CIRCUIT_BREAKER_RESET_TIMEOUT,
        PRODUCT_CATALOG_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS
    )
    
    # Test defaults
    assert PRODUCT_CATALOG_CIRCUIT_BREAKER_FAILURE_THRESHOLD == 5
    assert PRODUCT_CATALOG_CIRCUIT_BREAKER_RESET_TIMEOUT == 30
    assert PRODUCT_CATALOG_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS == 3
    
    # Test custom values
    os.environ[ENV_FAILURE_THRESHOLD] = "10"
    os.environ[ENV_RESET_TIMEOUT] = "60"
    os.environ[ENV_HALF_OPEN_MAX_CALLS] = "5"
    
    # Reload module to pick up new env vars
    import importlib
    import recommendation_server
    importlib.reload(recommendation_server)
    
    from recommendation_server import (
        PRODUCT_CATALOG_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        PRODUCT_CATALOG_CIRCUIT_BREAKER_RESET_TIMEOUT,
        PRODUCT_CATALOG_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS
    )
    
    assert PRODUCT_CATALOG_CIRCUIT_BREAKER_FAILURE_THRESHOLD == 10
    assert PRODUCT_CATALOG_CIRCUIT_BREAKER_RESET_TIMEOUT == 60
    assert PRODUCT_CATALOG_CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS == 5


def test_ac8_state_gauge_updates_immediately(reset_env_vars, mock_product_catalog_client):
    # AC-8: State gauge reflects current state immediately on changes
    os.environ[ENV_FAILURE_THRESHOLD] = "1"
    os.environ[ENV_RESET_TIMEOUT] = "1"
    
    from recommendation_server import product_catalog_circuit_breaker, product_catalog_client
    
    # Initial state: closed
    assert get_metric_value(METRIC_STATE_NAME) == 0
    
    with patch.object(product_catalog_client, 'ListProducts', side_effect=grpc.RpcError()):
        # Trip to open
        try:
            product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
        except grpc.RpcError:
            pass
        assert get_metric_value(METRIC_STATE_NAME) == 1
        
        # Wait for half-open
        time.sleep(1.5)
        assert get_metric_value(METRIC_STATE_NAME) == 2
    
    with patch.object(product_catalog_client, 'ListProducts', return_value=Mock()):
        # Close circuit
        product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
        assert get_metric_value(METRIC_STATE_NAME) == 0


def test_ac9_trip_counter_increments_exactly_once_per_trip(mock_product_catalog_client, reset_env_vars):
    # AC-9: Trip counter increases by exactly 1 each time circuit trips closed->open
    os.environ[ENV_FAILURE_THRESHOLD] = "2"
    os.environ[ENV_RESET_TIMEOUT] = "1"
    
    from recommendation_server import product_catalog_circuit_breaker, product_catalog_client
    
    initial_trips = get_metric_value(METRIC_TRIPS_NAME)
    assert initial_trips == 0
    
    with patch.object(product_catalog_client, 'ListProducts', side_effect=grpc.RpcError()):
        # First trip
        for _ in range(2):
            try:
                product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
            except grpc.RpcError:
                pass
        assert get_metric_value(METRIC_TRIPS_NAME) == 1
        assert get_metric_value(METRIC_STATE_NAME) == 1
        
        # Wait for half-open then fail again to trip second time
        time.sleep(1.5)
        for _ in range(2):
            try:
                product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
            except grpc.RpcError:
                pass
        assert get_metric_value(METRIC_TRIPS_NAME) == 2
        assert get_metric_value(METRIC_STATE_NAME) == 1


def test_ac10_retry_logic_runs_after_circuit_allows_call(mock_product_catalog_client, reset_env_vars):
    # AC-10: Existing retry logic executes for calls allowed through circuit breaker
    os.environ[ENV_FAILURE_THRESHOLD] = "5"
    
    from recommendation_server import product_catalog_circuit_breaker, product_catalog_client
    from recommendation_server import RETRY_ATTEMPTS  # Assume this exists per existing retry logic
    
    with patch.object(product_catalog_client, 'ListProducts', side_effect=grpc.RpcError()) as mock_call:
        try:
            # Call wrapped with both circuit breaker and retry
            wrapped = product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)
            # Apply existing retry decorator
            from tenacity import retry, stop_after_attempt
            wrapped = retry(stop=stop_after_attempt(RETRY_ATTEMPTS))(wrapped)
            wrapped()
        except grpc.RpcError:
            pass
        
        # Should have made RETRY_ATTEMPTS calls (circuit allowed the call, retries ran)
        assert mock_call.call_count == RETRY_ATTEMPTS


def test_ac11_open_circuit_returns_explicit_error_not_downstream_error(mock_product_catalog_client, reset_env_vars):
    # AC-11: Open circuit errors are explicit, not raw downstream errors
    os.environ[ENV_FAILURE_THRESHOLD] = "1"
    
    from recommendation_server import product_catalog_circuit_breaker, product_catalog_client
    
    with patch.object(product_catalog_client, 'ListProducts', side_effect=grpc.RpcError("raw downstream error")):
        # Trip the circuit
        try:
            product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
        except grpc.RpcError as e:
            # First failure returns raw error
            assert "raw downstream error" in str(e)
        
        # Now circuit is open, next call returns explicit error
        with pytest.raises(grpc.RpcError) as excinfo:
            product_catalog_circuit_breaker.wrap(product_catalog_client.ListProducts)()
        assert CIRCUIT_OPEN_ERROR_MSG in str(excinfo.value)
        assert "raw downstream error" not in str(excinfo.value)
        assert excinfo.value.code() == grpc.StatusCode.UNAVAILABLE


def get_metric_value(metric_name):
    """Helper to get current value of a metric from the OpenTelemetry meter provider"""
    from metrics import meter
    for metric in meter.get_metrics():
        if metric.name == metric_name:
            for point in metric.data.data_points:
                return point.value
    return 0
