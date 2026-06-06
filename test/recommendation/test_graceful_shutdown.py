import os
import signal
import time
import grpc
import pytest
from typing import Optional, Any
from concurrent import futures
from src.recommendation.recommendation_server import serve
from src.recommendation.demo_pb2 import ListRecommendationsRequest, ListRecommendationsResponse
from src.recommendation.demo_pb2_grpc import RecommendationServiceStub

# Import functions as per spec
from src.recommendation.recommendation_server import handle_shutdown_signal, graceful_shutdown

# Test constants
GRPC_PORT = 8080
TEST_PRODUCT_ID = "OLJCESPC7Z"  # Test product ID from demo data

def test_ac1_sigint_returns_unavailable_for_new_connections():
    """AC-1: SIGINT signal causes new gRPC connections to get UNAVAILABLE immediately"""
    # Start server in separate process
    server_pid = os.fork()
    if server_pid == 0:
        # Child process: run server
        serve()
        return
    
    # Wait for server to start
    time.sleep(2)
    
    # Send SIGINT to server
    os.kill(server_pid, signal.SIGINT)
    
    # Wait 0.5s for signal handler to process
    time.sleep(0.5)
    
    # Try to connect and make request
    channel = grpc.insecure_channel(f"localhost:{GRPC_PORT}")
    stub = RecommendationServiceStub(channel)
    
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.ListRecommendations(ListRecommendationsRequest(
            user_id="test_user",
            product_ids=[TEST_PRODUCT_ID]
        ), timeout=1)
    
    assert excinfo.value.code() == grpc.StatusCode.UNAVAILABLE
    
    # Cleanup
    try:
        os.kill(server_pid, signal.SIGKILL)
        os.waitpid(server_pid, 0)
    except:
        pass

def test_ac2_sigterm_returns_unavailable_for_new_connections():
    """AC-2: SIGTERM signal causes new gRPC connections to get UNAVAILABLE immediately"""
    # Start server in separate process
    server_pid = os.fork()
    if server_pid == 0:
        # Child process: run server
        serve()
        return
    
    # Wait for server to start
    time.sleep(2)
    
    # Send SIGTERM to server
    os.kill(server_pid, signal.SIGTERM)
    
    # Wait 0.5s for signal handler to process
    time.sleep(0.5)
    
    # Try to connect and make request
    channel = grpc.insecure_channel(f"localhost:{GRPC_PORT}")
    stub = RecommendationServiceStub(channel)
    
    with pytest.raises(grpc.RpcError) as excinfo:
        stub.ListRecommendations(ListRecommendationsRequest(
            user_id="test_user",
            product_ids=[TEST_PRODUCT_ID]
        ), timeout=1)
    
    assert excinfo.value.code() == grpc.StatusCode.UNAVAILABLE
    
    # Cleanup
    try:
        os.kill(server_pid, signal.SIGKILL)
        os.waitpid(server_pid, 0)
    except:
        pass

def test_ac3_in_flight_requests_complete_before_exit():
    """AC-3: In-flight requests that complete <30s finish before service exits with 0"""
    # Start server in separate process
    server_pid = os.fork()
    if server_pid == 0:
        # Monkey patch recommendation handler to take 2s to complete
        import src.recommendation.recommendation_server as rs
        original_list = rs.ListRecommendations
        
        def slow_list_recommendations(*args, **kwargs):
            time.sleep(2)
            return original_list(*args, **kwargs)
        
        rs.ListRecommendations = slow_list_recommendations
        serve()
        return
    
    # Wait for server to start
    time.sleep(2)
    
    # Make async request that will take 2s to complete
    channel = grpc.insecure_channel(f"localhost:{GRPC_PORT}")
    stub = RecommendationServiceStub(channel)
    future = stub.ListRecommendations.future(ListRecommendationsRequest(
        user_id="test_user",
        product_ids=[TEST_PRODUCT_ID]
    ), timeout=5)
    
    # Wait 0.5s so request is in flight
    time.sleep(0.5)
    
    # Send SIGTERM signal
    os.kill(server_pid, signal.SIGTERM)
    
    # Measure time until server exits
    start_time = time.time()
    _, exit_code = os.waitpid(server_pid, 0)
    elapsed = time.time() - start_time
    
    # Verify request completed successfully
    assert future.done()
    assert isinstance(future.result(), ListRecommendationsResponse)
    assert len(future.result().product_ids) > 0
    
    # Verify exit code is 0 and server waited for request to complete (>2s)
    assert exit_code == 0
    assert elapsed >= 2
    assert elapsed < 30

def test_ac4_timeout_force_termination_after_30s():
    """AC-4: In-flight requests taking >30s get terminated after 30s with non-zero exit code"""
    # Start server in separate process
    server_pid = os.fork()
    if server_pid == 0:
        # Monkey patch recommendation handler to take 40s to complete
        import src.recommendation.recommendation_server as rs
        original_list = rs.ListRecommendations
        
        def very_slow_list_recommendations(*args, **kwargs):
            time.sleep(40)
            return original_list(*args, **kwargs)
        
        rs.ListRecommendations = very_slow_list_recommendations
        serve()
        return
    
    # Wait for server to start
    time.sleep(2)
    
    # Make async request that will take 40s to complete
    channel = grpc.insecure_channel(f"localhost:{GRPC_PORT}")
    stub = RecommendationServiceStub(channel)
    future = stub.ListRecommendations.future(ListRecommendationsRequest(
        user_id="test_user",
        product_ids=[TEST_PRODUCT_ID]
    ), timeout=45)
    
    # Wait 0.5s so request is in flight
    time.sleep(0.5)
    
    # Send SIGTERM signal
    os.kill(server_pid, signal.SIGTERM)
    
    # Measure time until server exits
    start_time = time.time()
    _, exit_code = os.waitpid(server_pid, 0)
    elapsed = time.time() - start_time
    
    # Verify server terminated after ~30s
    assert elapsed >= 29.5
    assert elapsed <= 30.5
    
    # Verify exit code is non-zero
    assert exit_code != 0
    
    # Verify request failed
    assert future.done()
    with pytest.raises(grpc.RpcError):
        future.result()

def test_ac5_all_connections_closed_after_shutdown():
    """AC-5: All open network connections are closed after shutdown completes"""
    # Start server in separate process
    server_pid = os.fork()
    if server_pid == 0:
        serve()
        return
    
    # Wait for server to start
    time.sleep(2)
    
    # Open a connection
    channel = grpc.insecure_channel(f"localhost:{GRPC_PORT}")
    stub = RecommendationServiceStub(channel)
    response = stub.ListRecommendations(ListRecommendationsRequest(
        user_id="test_user",
        product_ids=[TEST_PRODUCT_ID]
    ), timeout=1)
    assert response is not None
    
    # Send SIGTERM signal
    os.kill(server_pid, signal.SIGTERM)
    
    # Wait for server to exit
    os.waitpid(server_pid, 0)
    time.sleep(1)  # Allow time for connections to be cleaned up
    
    # Try to use existing channel and make new connection, both should fail
    with pytest.raises(grpc.RpcError) as excinfo1:
        stub.ListRecommendations(ListRecommendationsRequest(
            user_id="test_user",
            product_ids=[TEST_PRODUCT_ID]
        ), timeout=1)
    
    assert excinfo1.value.code() in [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.CANCELLED]
    
    new_channel = grpc.insecure_channel(f"localhost:{GRPC_PORT}")
    new_stub = RecommendationServiceStub(new_channel)
    with pytest.raises(grpc.RpcError) as excinfo2:
        new_stub.ListRecommendations(ListRecommendationsRequest(
            user_id="test_user",
            product_ids=[TEST_PRODUCT_ID]
        ), timeout=1)
    
    assert excinfo2.value.code() == grpc.StatusCode.UNAVAILABLE

def test_ac6_shutdown_logs_present():
    """AC-6: All required shutdown log events are present in logs for corresponding scenarios"""
    import tempfile
    import re
    
    # Create temp log file
    log_fd, log_path = tempfile.mkstemp(suffix=".log")
    
    # Start server in separate process, redirect output to log file
    server_pid = os.fork()
    if server_pid == 0:
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
        os.close(log_fd)
        serve()
        return
    
    os.close(log_fd)
    
    # Wait for server to start
    time.sleep(2)
    
    # Send SIGINT signal
    os.kill(server_pid, signal.SIGINT)
    
    # Wait for server to exit
    os.waitpid(server_pid, 0)
    
    # Read log contents
    with open(log_path, "r") as f:
        logs = f.read()
    
    # Check required logs are present
    assert re.search(r"Shutdown signal SIGINT received", logs) is not None
    assert re.search(r"Waiting up to 30s for in-flight requests to complete", logs) is not None
    assert re.search(r"All in-flight requests completed, shutting down server", logs) is not None
    
    # Cleanup
    os.unlink(log_path)
    
    # Test timeout log scenario
    log_fd, log_path = tempfile.mkstemp(suffix=".log")
    server_pid = os.fork()
    if server_pid == 0:
        import src.recommendation.recommendation_server as rs
        original_list = rs.ListRecommendations
        
        def slow_list(*args, **kwargs):
            time.sleep(35)
            return original_list(*args, **kwargs)
        
        rs.ListRecommendations = slow_list
        os.dup2(log_fd, 1)
        os.dup2(log_fd, 2)
        os.close(log_fd)
        serve()
        return
    
    os.close(log_fd)
    time.sleep(2)
    
    # Make in-flight request
    channel = grpc.insecure_channel(f"localhost:{GRPC_PORT}")
    stub = RecommendationServiceStub(channel)
    future = stub.ListRecommendations.future(ListRecommendationsRequest(
        user_id="test_user",
        product_ids=[TEST_PRODUCT_ID]
    ), timeout=40)
    
    time.sleep(0.5)
    os.kill(server_pid, signal.SIGTERM)
    os.waitpid(server_pid, 0)
    
    with open(log_path, "r") as f:
        logs = f.read()
    
    assert re.search(r"Shutdown signal SIGTERM received", logs) is not None
    assert re.search(r"Waiting up to 30s for in-flight requests to complete", logs) is not None
    assert re.search(r"Shutdown timed out after 30s, forcing server termination", logs) is not None
    
    # Cleanup
    os.unlink(log_path)
