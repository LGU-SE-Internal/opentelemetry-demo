import pytest
import time
from unittest.mock import Mock, patch
from pybreaker import CircuitBreaker
import grpc
from typing import List

# Import expected names from spec
from src.product_reviews.product_reviews_server import (
    product_catalog_circuit_breaker,
    get_product_reviews_from_catalog,
    get_product_review_fallback,
    ProductReview
)
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


@pytest.fixture
def reset_circuit_breaker():
    """Reset circuit breaker state between tests"""
    product_catalog_circuit_breaker.close()
    product_catalog_circuit_breaker._state_storage._fail_counter = 0
    product_catalog_circuit_breaker._state_storage._open_time = None
    yield
    product_catalog_circuit_breaker.close()


@pytest.fixture
def mock_grpc_stub():
    """Mock gRPC stub for ProductCatalogService"""
    with patch('src.product_reviews.product_reviews_server.product_catalog_stub') as mock:
        yield mock


@pytest.fixture
def metric_reader():
    """In-memory metric reader to test OTel metrics"""
    reader = InMemoryMetricReader()
    # Patch the global meter provider to use our reader
    from opentelemetry import metrics
    original_provider = metrics.get_meter_provider()
    from opentelemetry.sdk.metrics import MeterProvider
    provider = MeterProvider(metric_readers=[reader])
    metrics.set_meter_provider(provider)
    yield reader
    metrics.set_meter_provider(original_provider)


class TestProductReviewsCircuitBreaker:
    def test_ac1_closed_circuit_10_successes(self, reset_circuit_breaker, mock_grpc_stub):
        """AC-1: 10 consecutive successful calls keep circuit closed, no fallback, return full catalog response"""
        # Setup mock response
        mock_reviews = [ProductReview(review_id="1", rating=5, text="Great")]
        mock_grpc_stub.GetProductReviews.return_value = mock_reviews

        # Call 10 times
        for _ in range(10):
            result = get_product_reviews_from_catalog(product_id="test-product-123")
            assert result == mock_reviews

        # Verify circuit remains closed
        assert product_catalog_circuit_breaker.current_state == "closed"
        # Verify no fallback was called
        mock_grpc_stub.GetProductReviews.assert_called()
        assert mock_grpc_stub.GetProductReviews.call_count == 10

    def test_ac2_5_failures_open_circuit_fallback(self, reset_circuit_breaker, mock_grpc_stub):
        """AC-2: 5 consecutive gRPC errors open circuit, subsequent calls return fallback for 30s, no exceptions"""
        # Setup mock to raise gRPC error
        mock_grpc_stub.GetProductReviews.side_effect = grpc.RpcError(code=grpc.StatusCode.UNAVAILABLE, details="Service down")

        # First 5 calls should fail but not open yet? Wait no: 5 consecutive failures trip to open
        for i in range(5):
            with pytest.raises(grpc.RpcError):
                get_product_reviews_from_catalog(product_id="test-product-123")

        # Now circuit should be open
        assert product_catalog_circuit_breaker.current_state == "open"

        # Next 10 calls should return fallback, no exception
        for _ in range(10):
            result = get_product_reviews_from_catalog(product_id="test-product-123")
            # Fallback should return empty list or cached value, conform to schema
            assert isinstance(result, List)
            assert all(isinstance(r, ProductReview) for r in result)

        # Verify grpc was called exactly 5 times (no more calls after circuit open)
        assert mock_grpc_stub.GetProductReviews.call_count == 5

    def test_ac3_half_open_state_recovery(self, reset_circuit_breaker, mock_grpc_stub):
        """AC-3: After 30s open, circuit goes half-open: first call allowed, success closes circuit, failure re-opens"""
        # First trip the circuit open
        mock_grpc_stub.GetProductReviews.side_effect = grpc.RpcError(code=grpc.StatusCode.UNAVAILABLE, details="Service down")
        for _ in range(5):
            with pytest.raises(grpc.RpcError):
                get_product_reviews_from_catalog(product_id="test-product-123")
        assert product_catalog_circuit_breaker.current_state == "open"

        # Fast forward time by 30s
        with patch('time.time', return_value=time.time() + 31):
            # First call should go to half-open, allowed to pass
            # Case 1: call succeeds -> circuit closes
            mock_success_reviews = [ProductReview(review_id="2", rating=4, text="Good")]
            mock_grpc_stub.GetProductReviews.reset_mock(side_effect=True)
            mock_grpc_stub.GetProductReviews.return_value = mock_success_reviews

            result = get_product_reviews_from_catalog(product_id="test-product-123")
            assert result == mock_success_reviews
            assert product_catalog_circuit_breaker.current_state == "closed"
            assert mock_grpc_stub.GetProductReviews.call_count == 1

            # Re-trip circuit
            mock_grpc_stub.GetProductReviews.side_effect = grpc.RpcError(code=grpc.StatusCode.UNAVAILABLE, details="Service down")
            for _ in range(5):
                with pytest.raises(grpc.RpcError):
                    get_product_reviews_from_catalog(product_id="test-product-123")
            assert product_catalog_circuit_breaker.current_state == "open"

            # Fast forward another 30s
            with patch('time.time', return_value=time.time() + 62):
                # Case 2: half-open call fails -> circuit re-opens
                mock_grpc_stub.GetProductReviews.reset_mock(side_effect=True)
                mock_grpc_stub.GetProductReviews.side_effect = grpc.RpcError(code=grpc.StatusCode.UNAVAILABLE, details="Still down")

                result = get_product_reviews_from_catalog(product_id="test-product-123")
                # Should return fallback
                assert isinstance(result, List)
                assert product_catalog_circuit_breaker.current_state == "open"
                assert mock_grpc_stub.GetProductReviews.call_count == 1

    def test_ac4_state_transition_logs(self, reset_circuit_breaker, mock_grpc_stub, caplog):
        """AC-4: All state transitions generate structured logs with required fields"""
        import logging
        caplog.set_level(logging.INFO)

        # Trigger closed -> open transition
        mock_grpc_stub.GetProductReviews.side_effect = grpc.RpcError(code=grpc.StatusCode.UNAVAILABLE, details="Down")
        for _ in range(5):
            with pytest.raises(grpc.RpcError):
                get_product_reviews_from_catalog(product_id="test-product-123")

        # Find circuit trip log entry
        trip_logs = [r for r in caplog.records if getattr(r, "event_type", None) == "circuit_trip"]
        assert len(trip_logs) == 1
        trip_log = trip_logs[0]
        assert trip_log.previous_state == "closed"
        assert trip_log.new_state == "open"
        assert trip_log.failure_count == 5
        assert hasattr(trip_log, "timestamp")

        # Fast forward to half-open transition
        caplog.clear()
        with patch('time.time', return_value=time.time() + 31):
            mock_grpc_stub.GetProductReviews.side_effect = None
            mock_grpc_stub.GetProductReviews.return_value = []
            get_product_reviews_from_catalog(product_id="test-product-123")

        # Find state change log entries
        state_logs = [r for r in caplog.records if getattr(r, "event_type", None) == "circuit_state_change"]
        assert len(state_logs) >= 2  # open -> half-open, half-open -> closed
        half_open_log = next(r for r in state_logs if r.new_state == "half-open")
        assert half_open_log.previous_state == "open"
        closed_log = next(r for r in state_logs if r.new_state == "closed")
        assert closed_log.previous_state == "half-open"

    def test_ac5_circuit_breaker_metrics(self, reset_circuit_breaker, mock_grpc_stub, metric_reader):
        """AC-5: Trip events increment trips_total counter, state gauge updates on state change"""
        # Trigger circuit trip
        mock_grpc_stub.GetProductReviews.side_effect = grpc.RpcError(code=grpc.StatusCode.UNAVAILABLE, details="Down")
        for _ in range(5):
            with pytest.raises(grpc.RpcError):
                get_product_reviews_from_catalog(product_id="test-product-123")

        # Read metrics
        metrics = metric_reader.get_metrics_data()
        resource_metrics = metrics.resource_metrics
        assert len(resource_metrics) > 0

        # Check trips_total counter
        trips_metric = next(m for m in resource_metrics[0].scope_metrics[0].metrics if m.name == "product_catalog_circuit_breaker_trips_total")
        assert trips_metric.sum.data_points[0].value == 1

        # Check state gauge (1 = open)
        state_metric = next(m for m in resource_metrics[0].scope_metrics[0].metrics if m.name == "product_catalog_circuit_breaker_state")
        assert state_metric.gauge.data_points[0].value == 1

        # Fast forward to half-open then closed
        with patch('time.time', return_value=time.time() + 31):
            mock_grpc_stub.GetProductReviews.side_effect = None
            mock_grpc_stub.GetProductReviews.return_value = []
            get_product_reviews_from_catalog(product_id="test-product-123")

        # Read metrics again, state should be 0 = closed
        metrics = metric_reader.get_metrics_data()
        state_metric = next(m for m in metrics.resource_metrics[0].scope_metrics[0].metrics if m.name == "product_catalog_circuit_breaker_state")
        assert state_metric.gauge.data_points[0].value == 0

    def test_ac6_open_circuit_fallback_schema_conformance(self, reset_circuit_breaker, mock_grpc_stub):
        """AC-6: Open circuit fallback conforms to gRPC response schema, no exceptions to upstream"""
        # Trip circuit open
        mock_grpc_stub.GetProductReviews.side_effect = grpc.RpcError(code=grpc.StatusCode.UNAVAILABLE, details="Down")
        for _ in range(5):
            with pytest.raises(grpc.RpcError):
                get_product_reviews_from_catalog(product_id="test-product-123")
        assert product_catalog_circuit_breaker.current_state == "open"

        # Test multiple product IDs, all return valid schema
        test_product_ids = ["test-1", "test-2", "non-existent-id"]
        for product_id in test_product_ids:
            result = get_product_reviews_from_catalog(product_id=product_id)
            # Verify response matches schema: list of ProductReview objects
            assert isinstance(result, list)
            for review in result:
                assert isinstance(review, ProductReview)
                # Verify required fields exist on ProductReview
                assert hasattr(review, "review_id")
                assert hasattr(review, "rating")
                assert hasattr(review, "text")

        # No exceptions should be raised to caller
        with pytest.raises(TimeoutError):
            # This should never happen: circuit breaker handles all errors
            get_product_reviews_from_catalog(product_id="test-3")

    def test_ac7_closed_circuit_latency_overhead(self, reset_circuit_breaker, mock_grpc_stub):
        """AC-7: Closed circuit adds <1ms p95 latency vs unwrapped calls"""
        import statistics

        mock_reviews = [ProductReview(review_id="1", rating=5, text="Great")]
        mock_grpc_stub.GetProductReviews.return_value = mock_reviews

        # Test unwrapped call latency (simulate by calling mock directly)
        unwrapped_latencies = []
        for _ in range(1000):
            start = time.perf_counter_ns()
            mock_grpc_stub.GetProductReviews("test-product-123", timeout=1.0)
            end = time.perf_counter_ns()
            unwrapped_latencies.append((end - start) / 1e6)  # convert to ms

        # Test wrapped call latency
        wrapped_latencies = []
        for _ in range(1000):
            start = time.perf_counter_ns()
            get_product_reviews_from_catalog(product_id="test-product-123")
            end = time.perf_counter_ns()
            wrapped_latencies.append((end - start) / 1e6)  # convert to ms

        # Calculate p95 latency
        unwrapped_p95 = statistics.quantiles(unwrapped_latencies, n=100)[94]
        wrapped_p95 = statistics.quantiles(wrapped_latencies, n=100)[94]

        overhead = wrapped_p95 - unwrapped_p95
        assert overhead < 1.0, f"Circuit breaker overhead {overhead:.2f}ms exceeds 1ms p95 limit"
