import os
import signal
import time
import pytest
import grpc
import asyncio
from typing import Optional
from types import FrameType
from unittest.mock import MagicMock, patch
from src.product_reviews.product_reviews_server import (
    handle_shutdown_signal,
    graceful_shutdown
)

# AC-1: SIGTERM signal triggers correct log message
def test_ac1_sigterm_log_message(caplog):
    import logging
    caplog.set_level(logging.INFO)
    with patch('src.product_reviews.product_reviews_server.shutdown_initiated', new=False):
        handle_shutdown_signal(signal.SIGTERM, None)
        assert "Received shutdown signal (SIGTERM), starting graceful shutdown sequence" in caplog.text
        assert any(record.levelname == 'INFO' for record in caplog.records if "Received shutdown signal" in record.message)

# AC-2: SIGINT signal triggers correct log message
def test_ac2_sigint_log_message(caplog):
    import logging
    caplog.set_level(logging.INFO)
    with patch('src.product_reviews.product_reviews_server.shutdown_initiated', new=False):
        handle_shutdown_signal(signal.SIGINT, None)
        assert "Received shutdown signal (SIGINT), starting graceful shutdown sequence" in caplog.text
        assert any(record.levelname == 'INFO' for record in caplog.records if "Received shutdown signal" in record.message)

# AC-3: After signal, server stops accepting new connections
@pytest.mark.asyncio
async def test_ac3_server_stops_accepting_new_connections():
    mock_server = MagicMock(spec=grpc.aio.Server)
    mock_db_pool = MagicMock()
    mock_channel = MagicMock(spec=grpc.aio.Channel)
    
    await graceful_shutdown(mock_server, mock_db_pool, mock_channel, timeout=1)
    mock_server.stop.assert_called_once()
    # After stop is called, server does not accept new connections
    assert mock_server.stop.called

# AC-4: In-flight requests allowed up to 30s timeout
@pytest.mark.asyncio
async def test_ac4_in_flight_requests_given_timeout():
    mock_server = MagicMock(spec=grpc.aio.Server)
    mock_db_pool = MagicMock()
    mock_channel = MagicMock(spec=grpc.aio.Channel)
    
    start_time = time.time()
    await graceful_shutdown(mock_server, mock_db_pool, mock_channel, timeout=2)
    elapsed = time.time() - start_time
    
    # Server.stop is called with the timeout value
    mock_server.stop.assert_called_once_with(2)
    # Timeout is respected (approx)
    assert elapsed >= 1.9 and elapsed <= 3.0

# AC-5: Database connections closed after server stops, correct log
@pytest.mark.asyncio
async def test_ac5_database_connections_closed_logged(caplog):
    import logging
    caplog.set_level(logging.INFO)
    mock_server = MagicMock(spec=grpc.aio.Server)
    mock_db_pool = MagicMock()
    mock_channel = MagicMock(spec=grpc.aio.Channel)
    
    await graceful_shutdown(mock_server, mock_db_pool, mock_channel, timeout=1)
    mock_db_pool.close.assert_called_once()
    assert "Successfully closed all database connections" in caplog.text

# AC-6: Product catalog channel closed after server stops, correct log
@pytest.mark.asyncio
async def test_ac6_product_catalog_channel_closed_logged(caplog):
    import logging
    caplog.set_level(logging.INFO)
    mock_server = MagicMock(spec=grpc.aio.Server)
    mock_db_pool = MagicMock()
    mock_channel = MagicMock(spec=grpc.aio.Channel)
    
    await graceful_shutdown(mock_server, mock_db_pool, mock_channel, timeout=1)
    mock_channel.close.assert_called_once()
    assert "Successfully closed product-catalog gRPC channel" in caplog.text

# AC-7: Successful shutdown logs message, exit code 0
@pytest.mark.asyncio
async def test_ac7_successful_shutdown_log_exit_0(caplog):
    import logging
    import sys
    caplog.set_level(logging.INFO)
    mock_server = MagicMock(spec=grpc.aio.Server)
    mock_db_pool = MagicMock()
    mock_channel = MagicMock(spec=grpc.aio.Channel)
    
    with pytest.raises(SystemExit) as exc_info:
        await graceful_shutdown(mock_server, mock_db_pool, mock_channel, timeout=1)
    
    assert exc_info.value.code == 0
    assert "Graceful shutdown completed successfully, exiting" in caplog.text
    assert any(record.levelname == 'INFO' for record in caplog.records if "Graceful shutdown completed successfully" in record.message)

# AC-8: Shutdown timeout logs warning, exit code 1
@pytest.mark.asyncio
async def test_ac8_shutdown_timeout_log_exit_1(caplog):
    import logging
    caplog.set_level(logging.WARNING)
    mock_server = MagicMock(spec=grpc.aio.Server)
    # Make server stop take longer than timeout to simulate in-flight requests blocking
    async def slow_stop(timeout):
        await asyncio.sleep(timeout + 0.5)
    mock_server.stop = slow_stop
    mock_db_pool = MagicMock()
    mock_channel = MagicMock(spec=grpc.aio.Channel)
    
    with pytest.raises(SystemExit) as exc_info:
        await graceful_shutdown(mock_server, mock_db_pool, mock_channel, timeout=1)
    
    assert exc_info.value.code == 1
    assert "Graceful shutdown timed out after 30s, forcing exit" in caplog.text
    assert any(record.levelname == 'WARNING' for record in caplog.records if "Graceful shutdown timed out" in record.message)
    # Cleanup still happens even on timeout
    mock_db_pool.close.assert_called_once()
    mock_channel.close.assert_called_once()

# AC-9: Connection cleanup error logs error, exit code 1
@pytest.mark.asyncio
async def test_ac9_cleanup_error_log_exit_1(caplog):
    import logging
    caplog.set_level(logging.ERROR)
    mock_server = MagicMock(spec=grpc.aio.Server)
    mock_db_pool = MagicMock()
    mock_db_pool.close.side_effect = Exception("DB connection close failed")
    mock_channel = MagicMock(spec=grpc.aio.Channel)
    
    with pytest.raises(SystemExit) as exc_info:
        await graceful_shutdown(mock_server, mock_db_pool, mock_channel, timeout=1)
    
    assert exc_info.value.code == 1
    assert "DB connection close failed" in caplog.text
    assert any(record.levelname == 'ERROR' for record in caplog.records)
    # Proceed with remaining cleanup
    mock_channel.close.assert_called_once()
