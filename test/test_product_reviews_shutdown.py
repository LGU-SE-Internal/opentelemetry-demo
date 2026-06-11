import signal
import time
import grpc
import pytest
from unittest.mock import Mock, patch
from src.product_reviews.product_reviews_server import (
    handle_shutdown_signal,
    run_graceful_shutdown,
)
from src.product_reviews.database import ConnectionPool
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk._logs import LoggerProvider


class TestProductReviewsGracefulShutdown:
    def test_ac1_sigint_stops_new_connections(self):
        """AC-1: SIGINT signal causes gRPC server to stop accepting new connections, return UNAVAILABLE"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        
        # Register signal handler
        handle_shutdown_signal(signal.SIGINT, None)
        
        # Verify server stops accepting new connections
        server.stop.assert_called_once_with(grace=10)
        
        # Simulate new connection attempt after shutdown triggered
        with patch('grpc.insecure_channel') as mock_channel:
            mock_channel.return_value.unary_unary.side_effect = grpc.RpcError(
                code=grpc.StatusCode.UNAVAILABLE,
                details="Service shutdown in progress"
            )
            # Attempt to call a service method
            with pytest.raises(grpc.RpcError) as exc_info:
                channel = grpc.insecure_channel('localhost:50051')
                stub = Mock()
                stub.ListProducts.return_value = None
                stub.ListProducts()
            assert exc_info.value.code() == grpc.StatusCode.UNAVAILABLE

    def test_ac2_sigterm_stops_new_connections(self):
        """AC-2: SIGTERM signal causes gRPC server to stop accepting new connections, return UNAVAILABLE"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        
        # Register signal handler
        handle_shutdown_signal(signal.SIGTERM, None)
        
        # Verify server stops accepting new connections
        server.stop.assert_called_once_with(grace=10)
        
        # Simulate new connection attempt after shutdown triggered
        with patch('grpc.insecure_channel') as mock_channel:
            mock_channel.return_value.unary_unary.side_effect = grpc.RpcError(
                code=grpc.StatusCode.UNAVAILABLE,
                details="Service shutdown in progress"
            )
            # Attempt to call a service method
            with pytest.raises(grpc.RpcError) as exc_info:
                channel = grpc.insecure_channel('localhost:50051')
                stub = Mock()
                stub.ListProducts.return_value = None
                stub.ListProducts()
            assert exc_info.value.code() == grpc.StatusCode.UNAVAILABLE

    def test_ac3_shutdown_waits_up_to_10_seconds_for_in_flight_requests(self):
        """AC-3: Service waits up to 10 seconds for in-flight requests to complete"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        
        # Mock server stop to take 9 seconds (under timeout)
        def mock_stop(grace):
            time.sleep(9)
            return Mock()
        server.stop.side_effect = mock_stop
        
        start_time = time.time()
        exit_code = run_graceful_shutdown(server, db_pool, otel_providers)
        elapsed = time.time() - start_time
        
        assert elapsed >= 9
        assert elapsed < 10.5
        assert exit_code == 0

    def test_ac4_skips_wait_when_all_requests_complete_early(self):
        """AC-4: Service proceeds to cleanup immediately when all in-flight requests complete before timeout"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        
        # Mock server stop to complete immediately
        server.stop.return_value = Mock()
        
        start_time = time.time()
        exit_code = run_graceful_shutdown(server, db_pool, otel_providers)
        elapsed = time.time() - start_time
        
        assert elapsed < 1  # Should not wait full 10 seconds
        assert exit_code == 0
        # Verify cleanup steps ran
        db_pool.closeall.assert_called_once()
        otel_providers.trace.shutdown.assert_called_once()
        otel_providers.metric.shutdown.assert_called_once()
        otel_providers.log.shutdown.assert_called_once()

    def test_ac5_force_terminate_after_10_second_timeout(self):
        """AC-5: Service forcibly terminates remaining requests after 10 second timeout"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        
        # Mock server stop to not complete within timeout
        server.stop.return_value = Mock()
        server.stop.return_value.wait.side_effect = lambda: time.sleep(11)
        
        start_time = time.time()
        exit_code = run_graceful_shutdown(server, db_pool, otel_providers)
        elapsed = time.time() - start_time
        
        assert elapsed >= 10
        assert elapsed < 11.5
        assert exit_code == 1
        # Verify cleanup steps still run even after timeout
        db_pool.closeall.assert_called_once()
        otel_providers.trace.shutdown.assert_called_once()
        otel_providers.metric.shutdown.assert_called_once()
        otel_providers.log.shutdown.assert_called_once()

    def test_ac6_database_connections_closed_during_cleanup(self):
        """AC-6: All open database connections are closed during cleanup"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        
        # Simulate some open connections
        db_pool._used = [Mock(), Mock()]
        db_pool._pool = [Mock(), Mock()]
        
        exit_code = run_graceful_shutdown(server, db_pool, otel_providers)
        
        db_pool.closeall.assert_called_once()
        assert len(db_pool._used) == 0
        assert len(db_pool._pool) == 0

    def test_ac7_otel_trace_provider_flushed_and_shut_down(self):
        """AC-7: OpenTelemetry trace provider is fully flushed and shut down"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        trace_processor = Mock()
        otel_providers.trace._active_span_processor = trace_processor
        
        exit_code = run_graceful_shutdown(server, db_pool, otel_providers)
        
        trace_processor.force_flush.assert_called_once_with(timeout_millis=5000)
        otel_providers.trace.shutdown.assert_called_once()

    def test_ac8_otel_metric_provider_flushed_and_shut_down(self):
        """AC-8: OpenTelemetry metric provider is fully flushed and shut down"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        
        exit_code = run_graceful_shutdown(server, db_pool, otel_providers)
        
        otel_providers.metric.force_flush.assert_called_once_with(timeout_millis=5000)
        otel_providers.metric.shutdown.assert_called_once()

    def test_ac9_otel_log_provider_flushed_and_shut_down(self):
        """AC-9: OpenTelemetry log provider is fully flushed and shut down"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        
        exit_code = run_graceful_shutdown(server, db_pool, otel_providers)
        
        otel_providers.log.force_flush.assert_called_once_with(timeout_millis=5000)
        otel_providers.log.shutdown.assert_called_once()

    def test_ac10_shutdown_initiated_info_logged(self, caplog):
        """AC-10: INFO level message logged when shutdown is initiated with signal type"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        
        with caplog.at_level('INFO'):
            handle_shutdown_signal(signal.SIGINT, None)
            assert any("SIGINT" in record.message and record.levelname == "INFO" for record in caplog.records)
        
        caplog.clear()
        
        with caplog.at_level('INFO'):
            handle_shutdown_signal(signal.SIGTERM, None)
            assert any("SIGTERM" in record.message and record.levelname == "INFO" for record in caplog.records)

    def test_ac11_shutdown_timeout_warn_logged(self, caplog):
        """AC-11: WARN level message logged if 10 second timeout elapses before all requests complete"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        
        # Mock server stop to timeout
        server.stop.return_value = Mock()
        server.stop.return_value.wait.return_value = False  # Indicates timeout
        
        with caplog.at_level('WARNING'):
            exit_code = run_graceful_shutdown(server, db_pool, otel_providers)
            assert any("timeout" in record.message.lower() and record.levelname == "WARNING" for record in caplog.records)
        assert exit_code == 1

    def test_ac12_cleanup_steps_info_logged(self, caplog):
        """AC-12: INFO level messages logged for each successful cleanup step"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        
        with caplog.at_level('INFO'):
            exit_code = run_graceful_shutdown(server, db_pool, otel_providers)
            
            log_messages = [record.message for record in caplog.records if record.levelname == "INFO"]
            assert any("database" in msg.lower() and "closed" in msg.lower() for msg in log_messages)
            assert any("trace provider" in msg.lower() and ("flushed" in msg.lower() or "shutdown" in msg.lower()) for msg in log_messages)
            assert any("metric provider" in msg.lower() and ("flushed" in msg.lower() or "shutdown" in msg.lower()) for msg in log_messages)
            assert any("log provider" in msg.lower() and ("flushed" in msg.lower() or "shutdown" in msg.lower()) for msg in log_messages)

    def test_ac13_successful_shutdown_exit_code_0(self):
        """AC-13: Service exits with code 0 when graceful shutdown completes successfully"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        
        # Mock successful shutdown
        server.stop.return_value = Mock()
        server.stop.return_value.wait.return_value = True  # No timeout
        
        exit_code = run_graceful_shutdown(server, db_pool, otel_providers)
        assert exit_code == 0

    def test_ac14_timeout_shutdown_exit_code_1(self):
        """AC-14: Service exits with code 1 when shutdown timeout occurs"""
        server = Mock(spec=grpc.Server)
        db_pool = Mock(spec=ConnectionPool)
        otel_providers = Mock(
            trace=Mock(spec=TracerProvider),
            metric=Mock(spec=MeterProvider),
            log=Mock(spec=LoggerProvider)
        )
        
        # Mock timeout shutdown
        server.stop.return_value = Mock()
        server.stop.return_value.wait.return_value = False  # Timeout occurred
        
        exit_code = run_graceful_shutdown(server, db_pool, otel_providers)
        assert exit_code == 1
