#!/usr/bin/env python3
"""Test for recommendation service graceful shutdown functionality."""
import os
import signal
import time
import threading
import grpc
from concurrent import futures
import demo_pb2
import demo_pb2_grpc
from recommendation_server import serve, RecommendationService

# Test server address
TEST_ADDR = '[::]:0'

# Mock Product Catalog service for testing
class MockProductCatalogService(demo_pb2_grpc.ProductCatalogServiceServicer):
    def ListProducts(self, request, context):
        # Simulate long running request if requested
        if request.user_id == 'slow':
            time.sleep(15)  # Longer than 10s grace period
        response = demo_pb2.ListProductsResponse()
        product = demo_pb2.Product()
        product.id = 'test-product-1'
        response.products.append(product)
        product = demo_pb2.Product()
        product.id = 'test-product-2'
        response.products.append(product)
        return response

def test_shutdown_signal_handling():
    """Test that SIGINT/SIGTERM triggers graceful shutdown."""
    # Start mock product catalog server
    mock_pc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    demo_pb2_grpc.add_ProductCatalogServiceServicer_to_server(MockProductCatalogService(), mock_pc_server)
    mock_pc_port = mock_pc_server.add_insecure_port('[::]:0')
    mock_pc_server.start()
    mock_pc_addr = f'[::]:{mock_pc_port}'
    
    # Import here to avoid side effects
    import recommendation_server
    # Create product catalog client for test server
    recommendation_server.product_catalog_stub, product_catalog_channel = \
        recommendation_server.create_product_catalog_client(mock_pc_addr)
    
    # Start recommendation service in separate thread
    server = serve(TEST_ADDR, product_catalog_channel=product_catalog_channel, test_mode=True, logger=None)
    test_port = server._port
    client_addr = f'localhost:{test_port}'
    
    # Create client
    channel = grpc.insecure_channel(client_addr)
    stub = demo_pb2_grpc.RecommendationServiceStub(channel)
    
    # Test normal request works
    request = demo_pb2.ListRecommendationsRequest(user_id='test-user', product_ids=[], result_size=2)
    response = stub.ListRecommendations(request)
    assert len(response.product_ids) > 0, "Normal request failed"
    
    # Test case 1: Fast in-flight request completes within grace period
    fast_result = []
    def send_fast_request():
        try:
            request = demo_pb2.ListRecommendationsRequest(user_id='fast-user', product_ids=[], result_size=2)
            response = stub.ListRecommendations(request)
            fast_result.append(('success', response))
        except grpc.RpcError as e:
            fast_result.append(('error', e))
    
    # Test case 2: Slow request exceeds grace period
    slow_result = []
    def send_slow_request():
        try:
            # Send a request that will trigger 15s sleep in product catalog
            request = demo_pb2.ListRecommendationsRequest(user_id='slow', product_ids=[], result_size=2)
            response = stub.ListRecommendations(request, timeout=20)
            slow_result.append(('success', response))
        except grpc.RpcError as e:
            slow_result.append(('error', e))
    
    # Start slow request first
    slow_thread = threading.Thread(target=send_slow_request)
    slow_thread.start()
    time.sleep(0.5)  # Give time for request to be in flight
    
    # Start fast request
    fast_thread = threading.Thread(target=send_fast_request)
    fast_thread.start()
    time.sleep(0.2)  # Give time for fast request to be in flight
    
    # Send SIGINT signal to trigger shutdown
    os.kill(os.getpid(), signal.SIGINT)
    
    # Wait for threads to complete
    slow_thread.join(timeout=20)
    fast_thread.join(timeout=5)
    
    # Verify results
    assert len(fast_result) == 1
    assert fast_result[0][0] == 'success', f"Fast request failed unexpectedly: {fast_result[0][1]}"
    assert len(slow_result) == 1
    assert slow_result[0][0] == 'error', "Slow request should have been cancelled"
    assert slow_result[0][1].code() == grpc.StatusCode.CANCELLED, f"Expected CANCELLED status, got {slow_result[0][1].code()}"
    
    # Verify new connections are rejected after shutdown
    try:
        stub.ListRecommendations(request, timeout=1)
        assert False, "New request after shutdown should fail"
    except grpc.RpcError as e:
        assert e.code() == grpc.StatusCode.UNAVAILABLE, f"Expected UNAVAILABLE status, got {e.code()}"
    
    # Cleanup
    mock_pc_server.stop(0)
    
    print("All shutdown tests passed!")

if __name__ == "__main__":
    test_shutdown_signal_handling()
