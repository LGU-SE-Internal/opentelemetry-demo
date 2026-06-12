import os
import pytest
from unittest.mock import Mock, patch, call
import grpc
import pybreaker

# Idempotent product catalog gRPC methods per spec
PRODUCT_CATALOG_METHODS = ["GetProduct", "ListProducts", "SearchProducts"]

# Expected configuration values per spec
EXPECTED_FAILURE_THRESHOLD = 5
EXPECTED_RESET_TIMEOUT = 30
EXPECTED_HALF_OPEN_MAX_CALLS = 2
EXPECTED_METRIC_NAME = "recommendation_service_product_catalog_circuit_breaker_state"
ALLOWED_STATES = ["closed", "open", "half-open"]
EXPECTED_ERROR_MESSAGE = "Product catalog service is unavailable: circuit breaker is open"


@pytest.fixture(autouse=True)
def reset_env_vars():
    """Reset environment variables before each test"""
    original_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def mock_metrics():
    """Mock the Prometheus metrics"""
    with patch("recommendation.recommendation_service.product_catalog_circuit_breaker_state_gauge") as mock_gauge:
        yield mock_gauge


@pytest.fixture
def mock_logger():
    """Mock the structured logger"""
    with patch("recommendation.recommendation_service.logger") as mock_log:
        yield mock_log


@pytest.fixture
def circuit_breaker():
    """Import the actual circuit breaker instance once implementation exists"""
    from recommendation.recommendation_service import product_catalog_circuit_breaker
    yield product_catalog_circuit_breaker
    # Reset state after each test
    product_catalog_circuit_breaker.close()


def test_ac1_all_methods_wrapped_with_correct_config():
    """AC-1: All product catalog gRPC method calls are wrapped with pybreaker circuit breaker with correct configuration"""
    from recommendation.recommendation_service import product_catalog_circuit_breaker, circuit_breaker_wrapper

    # Verify circuit breaker configuration matches spec
    assert product_catalog_circuit_breaker.fail_max == EXPECTED_FAILURE_THRESHOLD
    assert product_catalog_circuit_breaker.reset_timeout == EXPECTED_RESET_TIMEOUT
    assert product_catalog_circuit_breaker.half_open_max_calls == EXPECTED_HALF_OPEN_MAX_CALLS

    # Verify all product catalog methods are wrapped with the circuit breaker
    for method_name in PRODUCT_CATALOG_METHODS:
        mock_method = Mock()
        mock_method.__name__ = method_name
        wrapped_method = circuit_breaker_wrapper(mock_method)
        assert hasattr(wrapped_method, '__wrapped__')
        assert wrapped_method.__wrapped__ == mock_method


def test_ac2_circuit_opens_after_5_consecutive_failures():
    """AC-2: 5 consecutive failed calls open circuit, subsequent calls return UNAVAILABLE for 30s"""
    from recommendation.recommendation_service import product_catalog_circuit_breaker, circuit_breaker_wrapper

    # Reset circuit to closed state
    product_catalog_circuit_breaker.close()
    assert product_catalog_circuit_breaker.current_state == "closed"

    # Mock method that always fails with gRPC error
    mock_method = Mock()
    mock_method.__name__ = PRODUCT_CATALOG_METHODS[0]
    mock_method.side_effect = grpc.RpcError("service down")
    mock_method.code = Mock(return_value=grpc.StatusCode.UNAVAILABLE)

    wrapped_method = circuit_breaker_wrapper(mock_method)

    # Make 5 consecutive failed calls
    for i in range(EXPECTED_FAILURE_THRESHOLD):
        with pytest.raises(grpc.RpcError):
            wrapped_method("test_arg")
        assert product_catalog_circuit_breaker.current_state == "closed"

    # 6th call should open circuit and return UNAVAILABLE
    with pytest.raises(grpc.RpcError) as exc_info:
        wrapped_method("test_arg")
    
    assert exc_info.value.code() == grpc.StatusCode.UNAVAILABLE
    assert EXPECTED_ERROR_MESSAGE in str(exc_info.value)
    assert product_catalog_circuit_breaker.current_state == "open"

    # Subsequent calls during reset timeout should also return UNAVAILABLE
    for i in range(3):
        with pytest.raises(grpc.RpcError) as exc_info:
            wrapped_method("test_arg")
        assert exc_info.value.code() == grpc.StatusCode.UNAVAILABLE
        assert product_catalog_circuit_breaker.current_state == "open"


def test_ac3_circuit_transitions_to_half_open_after_timeout():
    """AC-3: After 30s in open state, circuit transitions to half-open allowing max 2 calls"""
    from recommendation.recommendation_service import product_catalog_circuit_breaker, circuit_breaker_wrapper

    # Open circuit first
    product_catalog_circuit_breaker.open()
    assert product_catalog_circuit_breaker.current_state == "open"

    # Mock time to simulate 30s passing
    with patch("time.time", return_value=product_catalog_circuit_breaker.open_time + EXPECTED_RESET_TIMEOUT + 1):
        # First call after timeout should transition to half-open
        mock_method = Mock(return_value="success")
        mock_method.__name__ = PRODUCT_CATALOG_METHODS[0]
        wrapped_method = circuit_breaker_wrapper(mock_method)

        result = wrapped_method("test_arg")
        assert result == "success"
        assert product_catalog_circuit_breaker.current_state == "half-open"
        assert mock_method.call_count == 1

        # Second call allowed in half-open
        result = wrapped_method("test_arg")
        assert result == "success"
        assert mock_method.call_count == 2

        # Third call should be rejected while in half-open (max 2 calls)
        with pytest.raises(grpc.RpcError) as exc_info:
            wrapped_method("test_arg")
        assert exc_info.value.code() == grpc.StatusCode.UNAVAILABLE
        assert EXPECTED_ERROR_MESSAGE in str(exc_info.value)


def test_ac4_half_open_state_behavior():
    """AC-4: Half-open state behavior: success closes circuit, failure re-opens"""
    from recommendation.recommendation_service import product_catalog_circuit_breaker, circuit_breaker_wrapper

    # Test scenario 1: both half-open calls succeed → circuit closes
    product_catalog_circuit_breaker.half_open()
    assert product_catalog_circuit_breaker.current_state == "half-open"

    mock_success = Mock(return_value="ok")
    mock_success.__name__ = PRODUCT_CATALOG_METHODS[0]
    wrapped_success = circuit_breaker_wrapper(mock_success)

    # Two successful calls
    wrapped_success("test")
    wrapped_success("test")
    assert product_catalog_circuit_breaker.current_state == "closed"

    # Test scenario 2: any half-open call fails → circuit re-opens
    product_catalog_circuit_breaker.half_open()
    assert product_catalog_circuit_breaker.current_state == "half-open"

    mock_fail = Mock(side_effect=grpc.RpcError("error"))
    mock_fail.__name__ = PRODUCT_CATALOG_METHODS[0]
    mock_fail.code = Mock(return_value=grpc.StatusCode.UNAVAILABLE)
    wrapped_fail = circuit_breaker_wrapper(mock_fail)

    with pytest.raises(grpc.RpcError):
        wrapped_fail("test")
    assert product_catalog_circuit_breaker.current_state == "open"


def test_ac5_circuit_breaker_state_metric_exposed_correctly(mock_metrics):
    """AC-5: Prometheus gauge metric correctly reflects circuit state with exactly one state = 1"""
    from recommendation.recommendation_service import product_catalog_circuit_breaker

    mock_gauge = mock_metrics
    # Reset call history
    mock_gauge.set.clear()

    # Test closed state
    product_catalog_circuit_breaker.close()
    expected_calls = [
        call.labels(state="closed").set(1),
        call.labels(state="open").set(0),
        call.labels(state="half-open").set(0)
    ]
    mock_gauge.labels.assert_has_calls(expected_calls, any_order=True)
    mock_gauge.set.reset_mock()

    # Test open state
    product_catalog_circuit_breaker.open()
    expected_calls = [
        call.labels(state="closed").set(0),
        call.labels(state="open").set(1),
        call.labels(state="half-open").set(0)
    ]
    mock_gauge.labels.assert_has_calls(expected_calls, any_order=True)
    mock_gauge.set.reset_mock()

    # Test half-open state
    product_catalog_circuit_breaker.half_open()
    expected_calls = [
        call.labels(state="closed").set(0),
        call.labels(state="open").set(0),
        call.labels(state="half-open").set(1)
    ]
    mock_gauge.labels.assert_has_calls(expected_calls, any_order=True)


def test_ac6_state_transition_logs_generated(mock_logger):
    """AC-6: All state transitions generate structured INFO logs with required fields"""
    from recommendation.recommendation_service import product_catalog_circuit_breaker

    mock_log = mock_logger
    # Reset call history
    mock_log.info.reset_mock()

    # Test closed → open transition
    product_catalog_circuit_breaker.close()
    product_catalog_circuit_breaker.open()

    log_calls = [call.args[0] for call in mock_log.info.call_args_list]
    assert any("circuit_breaker_state_transition" in log for log in log_calls)
    transition_log = [call for call in mock_log.info.call_args_list if "circuit_breaker_state_transition" in str(call.args)][0]
    log_fields = transition_log.kwargs["extra"]
    assert log_fields["service"] == "recommendation"
    assert log_fields["component"] == "product_catalog_circuit_breaker"
    assert log_fields["previous_state"] == "closed"
    assert log_fields["new_state"] == "open"
    assert "timestamp" in log_fields
    mock_log.info.reset_mock()

    # Test open → half-open transition
    product_catalog_circuit_breaker.half_open()
    transition_log = [call for call in mock_log.info.call_args_list if "circuit_breaker_state_transition" in str(call.args)][0]
    log_fields = transition_log.kwargs["extra"]
    assert log_fields["previous_state"] == "open"
    assert log_fields["new_state"] == "half-open"
    mock_log.info.reset_mock()

    # Test half-open → closed transition
    product_catalog_circuit_breaker.close()
    transition_log = [call for call in mock_log.info.call_args_list if "circuit_breaker_state_transition" in str(call.args)][0]
    log_fields = transition_log.kwargs["extra"]
    assert log_fields["previous_state"] == "half-open"
    assert log_fields["new_state"] == "closed"
    mock_log.info.reset_mock()

    # Test half-open → open transition
    product_catalog_circuit_breaker.half_open()
    product_catalog_circuit_breaker.open()
    transition_log = [call for call in mock_log.info.call_args_list if "circuit_breaker_state_transition" in str(call.args)][0]
    log_fields = transition_log.kwargs["extra"]
    assert log_fields["previous_state"] == "half-open"
    assert log_fields["new_state"] == "open"


def test_ac7_retry_logic_preserved_inside_circuit_breaker():
    """AC-7: Existing exponential backoff retry logic executes inside circuit breaker, failed retries count as one failure"""
    from recommendation.recommendation_service import product_catalog_circuit_breaker, circuit_breaker_wrapper, retry_product_catalog_call

    # Reset circuit
    product_catalog_circuit_breaker.close()
    assert product_catalog_circuit_breaker.current_state == "closed"

    # Mock method that always fails
    mock_method = Mock()
    mock_method.__name__ = PRODUCT_CATALOG_METHODS[0]
    mock_method.side_effect = grpc.RpcError("error")
    mock_method.code = Mock(return_value=grpc.StatusCode.UNAVAILABLE)

    # Wrap method with both retry and circuit breaker (circuit breaker wraps retry)
    def wrapped_with_both(*args, **kwargs):
        return circuit_breaker_wrapper(lambda: retry_product_catalog_call(mock_method, *args, **kwargs))()

    # Call once: all retries fail, this should count as 1 circuit failure, not N (number of retries)
    with pytest.raises(grpc.RpcError):
        wrapped_with_both("test")
    
    # Verify retry was called multiple times
    assert mock_method.call_count > 1  # At least 1 initial + 1 retry
    # Verify circuit failure count incremented by 1, not by number of retries
    assert product_catalog_circuit_breaker.fail_counter == 1
    assert product_catalog_circuit_breaker.current_state == "closed"
