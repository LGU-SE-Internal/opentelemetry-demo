# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import signal
import time
import threading
import grpc
import pytest
from unittest.mock import Mock, patch

# Add src/recommendation to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../src/recommendation'))

import recommendation_server
import demo_pb2
import demo_pb2_grpc

@pytest.fixture
def grpc_server():
    # Create a test server
    server = grpc.server(thread_pool=threading.ThreadPoolExecutor(max_workers=2))
    service = recommendation_server.RecommendationService()
    demo_pb2_grpc.add_RecommendationServiceServicer_to_server(service, server)
    port = server.add_insecure_port('[::]:0')
    server.start()
    yield server, port
    server.stop(grace=0)

def test_ac1_sigterm_allows_inflight_request_completion(grpc_server):
    server, port = grpc_server
    
    # Mock the product catalog call to take 2 seconds
    with patch.object(recommendation_server, 'product_catalog_stub') as mock_stub:
        def slow_list_products(request):
            time.sleep(2)
            resp = demo_pb2.ListProductsResponse()
            resp.products.extend([demo_pb2.Product(id=f"test_{i}") for i in range(10)])
            return resp
        
        mock_stub.ListProducts = slow_list_products
        
        # Create client
        channel = grpc.insecure_channel(f'localhost:{port}')
        stub = demo_pb2_grpc.RecommendationServiceStub(channel)
        
        # Start request in a thread
        request = demo_pb2.ListRecommendationsRequest(product_ids=["test1"])
        response = None
        error = None
        
        def send_request():
            nonlocal response, error
            try:
                response = stub.ListRecommendations(request, timeout=5)
            except grpc.RpcError as e:
                error = e
        
        req_thread = threading.Thread(target=send_request)
        req_thread.start()
        
        # Wait 0.5 seconds to ensure request is in flight
        time.sleep(0.5)
        
        # Send SIGTERM to this process (the handler will handle it)
        with patch.object(recommendation_server, 'os._exit') as mock_exit:
            # Simulate signal received
            recommendation_server.handle_shutdown_signal(signal.SIGTERM, None)
            
            # Wait for request thread to complete
            req_thread.join()
            
            # Verify no error, response is received
            assert error is None
            assert response is not None
            assert len(response.product_ids) > 0
            
            # Verify exit was called
            mock_exit.assert_called_once_with(0)

def test_ac2_sigint_allows_inflight_request_completion(grpc_server):
    server, port = grpc_server
    
    # Mock the product catalog call to take 2 seconds
    with patch.object(recommendation_server, 'product_catalog_stub') as mock_stub:
        def slow_list_products(request):
            time.sleep(2)
            resp = demo_pb2.ListProductsResponse()
            resp.products.extend([demo_pb2.Product(id=f"test_{i}") for i in range(10)])
            return resp
        
        mock_stub.ListProducts = slow_list_products
        
        # Create client
        channel = grpc.insecure_channel(f'localhost:{port}')
        stub = demo_pb2_grpc.RecommendationServiceStub(channel)
        
        # Start request in a thread
        request = demo_pb2.ListRecommendationsRequest(product_ids=["test1"])
        response = None
        error = None
        
        def send_request():
            nonlocal response, error
            try:
                response = stub.ListRecommendations(request, timeout=5)
            except grpc.RpcError as e:
                error = e
        
        req_thread = threading.Thread(target=send_request)
        req_thread.start()
        
        # Wait 0.5 seconds to ensure request is in flight
        time.sleep(0.5)
        
        # Send SIGINT to this process (the handler will handle it)
        with patch.object(recommendation_server, 'os._exit') as mock_exit:
            # Simulate signal received
            recommendation_server.handle_shutdown_signal(signal.SIGINT, None)
            
            # Wait for request thread to complete
            req_thread.join()
            
            # Verify no error, response is received
            assert error is None
            assert response is not None
            assert len(response.product_ids) > 0
            
            # Verify exit was called
            mock_exit.assert_called_once_with(0)

def test_ac3_force_termination_after_grace_period(grpc_server):
    server, port = grpc_server
    
    # Mock the product catalog call to take 40 seconds (longer than grace period)
    with patch.object(recommendation_server, 'product_catalog_stub') as mock_stub:
        def very_slow_list_products(request):
            time.sleep(40)
            resp = demo_pb2.ListProductsResponse()
            resp.products.extend([demo_pb2.Product(id=f"test_{i}") for i in range(10)])
            return resp
        
        mock_stub.ListProducts = very_slow_list_products
        
        # Create client
        channel = grpc.insecure_channel(f'localhost:{port}')
        stub = demo_pb2_grpc.RecommendationServiceStub(channel)
        
        # Start request in a thread
        request = demo_pb2.ListRecommendationsRequest(product_ids=["test1"])
        error = None
        
        def send_request():
            nonlocal error
            try:
                stub.ListRecommendations(request, timeout=35)
            except grpc.RpcError as e:
                error = e
        
        req_thread = threading.Thread(target=send_request)
        req_thread.start()
        
        # Wait 0.5 seconds to ensure request is in flight
        time.sleep(0.5)
        
        # Time how long the shutdown takes
        start_time = time.time()
        
        with patch.object(recommendation_server, 'os._exit') as mock_exit:
            # Simulate signal received
            recommendation_server.handle_shutdown_signal(signal.SIGTERM, None)
            
            shutdown_duration = time.time() - start_time
            
            # Verify shutdown took approximately 30 seconds
            assert abs(shutdown_duration - 30) < 2
            
            # Wait for request thread to complete
            req_thread.join()
            
            # Verify error is UNAVAILABLE
            assert error is not None
            assert error.code() == grpc.StatusCode.UNAVAILABLE
            
            # Verify exit was called
            mock_exit.assert_called_once_with(0)

def test_ac4_shutdown_start_log_emitted(grpc_server):
    server, port = grpc_server
    
    with patch.object(recommendation_server.logger, 'info') as mock_info:
        with patch.object(recommendation_server, 'os._exit'):
            recommendation_server.handle_shutdown_signal(signal.SIGTERM, None)
            
            # Verify log message is emitted
            mock_info.assert_any_call(
                "Graceful shutdown started: stopping new requests, waiting up to 30s for in-flight requests to complete"
            )

def test_ac5_shutdown_complete_log_emitted(grpc_server):
    server, port = grpc_server
    
    with patch.object(recommendation_server.logger, 'info') as mock_info:
        with patch.object(recommendation_server, 'os._exit'):
            # Mock shutdown event to be set immediately
            with patch.object(server, 'stop') as mock_stop:
                mock_event = Mock()
                mock_event.is_set.return_value = True
                mock_stop.return_value = mock_event
                
                recommendation_server.handle_shutdown_signal(signal.SIGTERM, None)
                
                # Verify log messages
                mock_info.assert_any_call(
                    "Graceful shutdown started: stopping new requests, waiting up to 30s for in-flight requests to complete"
                )
                mock_info.assert_any_call(
                    "Graceful shutdown completed: all in-flight requests finished, exiting"
                )

def test_ac6_shutdown_timeout_log_emitted(grpc_server):
    server, port = grpc_server
    
    with patch.object(recommendation_server.logger, 'warning') as mock_warning:
        with patch.object(recommendation_server, 'os._exit'):
            # Mock shutdown event to not be set
            with patch.object(server, 'stop') as mock_stop:
                mock_event = Mock()
                mock_event.is_set.return_value = False
                mock_stop.return_value = mock_event
                
                recommendation_server.handle_shutdown_signal(signal.SIGTERM, None)
                
                # Verify warning log
                mock_warning.assert_called_once_with(
                    "Graceful shutdown timed out after 30s: force terminating with active in-flight requests remaining"
                )

def test_ac7_product_catalog_call_completes_before_shutdown(grpc_server):
    server, port = grpc_server
    
    # Track if product catalog call completed
    catalog_call_completed = False
    
    def slow_list_products(request):
        nonlocal catalog_call_completed
        time.sleep(2)
        catalog_call_completed = True
        resp = demo_pb2.ListProductsResponse()
        resp.products.extend([demo_pb2.Product(id=f"test_{i}") for i in range(10)])
        return resp
    
    with patch.object(recommendation_server, 'product_catalog_stub') as mock_stub:
        mock_stub.ListProducts = slow_list_products
        
        # Create client
        channel = grpc.insecure_channel(f'localhost:{port}')
        stub = demo_pb2_grpc.RecommendationServiceStub(channel)
        
        # Start request in a thread
        request = demo_pb2.ListRecommendationsRequest(product_ids=["test1"])
        response = None
        
        def send_request():
            nonlocal response
            try:
                response = stub.ListRecommendations(request, timeout=5)
            except Exception:
                pass
        
        req_thread = threading.Thread(target=send_request)
        req_thread.start()
        
        # Wait 0.5 seconds to ensure request is in flight and catalog call is active
        time.sleep(0.5)
        
        with patch.object(recommendation_server, 'os._exit'):
            recommendation_server.handle_shutdown_signal(signal.SIGTERM, None)
            
            # Wait for request thread to complete
            req_thread.join()
            
            # Verify catalog call completed
            assert catalog_call_completed is True
            assert response is not None
