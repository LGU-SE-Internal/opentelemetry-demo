"""
Integration tests for product-reviews service gRPC health check endpoints
as defined in spec for issue #1258
"""
import grpc
import pytest
from grpc_health.v1 import health_pb2, health_pb2_grpc
from src.product_reviews import demo_pb2_grpc

# Test constants from spec
LIVENESS_SERVICE_NAME = ""
READINESS_SERVICE_NAME = "product.reviews.v1.ProductReviewService"
GRPC_PORT = 8080  # Default product-reviews service port


@pytest.fixture(scope="module")
def grpc_channel():
    """Fixture for gRPC channel to product-reviews service"""
    channel = grpc.insecure_channel(f"localhost:{GRPC_PORT}")
    yield channel
    channel.close()


@pytest.fixture(scope="module")
def health_stub(grpc_channel):
    """Fixture for Health service stub"""
    return health_pb2_grpc.HealthStub(grpc_channel)


@pytest.fixture(scope="module")
def product_reviews_stub(grpc_channel):
    """Fixture for main ProductReviewService stub"""
    return demo_pb2_grpc.ProductReviewServiceStub(grpc_channel)


def test_ac1_liveness_check_returns_serving_with_healthy_db(health_stub):
    """AC-1: Empty service name liveness check returns SERVING when process running and DB connected"""
    request = health_pb2.HealthCheckRequest(service=LIVENESS_SERVICE_NAME)
    response = health_stub.Check(request)
    assert response.status == health_pb2.HealthCheckResponse.SERVING


def test_ac1_liveness_check_returns_not_serving_when_db_down(health_stub, stop_database):
    """AC-1: Empty service name liveness check returns NOT_SERVING when DB connection fails"""
    # Stop backing database to simulate failure
    stop_database()
    request = health_pb2.HealthCheckRequest(service=LIVENESS_SERVICE_NAME)
    response = health_stub.Check(request)
    assert response.status == health_pb2.HealthCheckResponse.NOT_SERVING


def test_ac2_readiness_check_returns_not_serving_during_initialization():
    """AC-2: Readiness check returns NOT_SERVING during service initialization"""
    # Connect immediately after service start before initialization completes
    with grpc.insecure_channel(f"localhost:{GRPC_PORT}") as channel:
        health_stub = health_pb2_grpc.HealthStub(channel)
        request = health_pb2.HealthCheckRequest(service=READINESS_SERVICE_NAME)
        # Retry for a short window to catch initialization state
        for _ in range(10):
            try:
                response = health_stub.Check(request, timeout=0.1)
                if response.status == health_pb2.HealthCheckResponse.NOT_SERVING:
                    return
            except grpc.RpcError:
                pass  # Service not yet listening
        pytest.fail("Readiness check did not return NOT_SERVING during initialization")


def test_ac2_readiness_check_returns_serving_when_fully_initialized(health_stub, product_reviews_stub):
    """AC-2: Readiness check returns SERVING only when fully initialized and API ready"""
    # First validate readiness status is SERVING
    request = health_pb2.HealthCheckRequest(service=READINESS_SERVICE_NAME)
    response = health_stub.Check(request)
    assert response.status == health_pb2.HealthCheckResponse.SERVING
    
    # Validate main API endpoints are actually accessible
    # (Simple list request to confirm API is responsive)
    from src.product_reviews.demo_pb2 import ListReviewsRequest
    list_response = product_reviews_stub.ListReviews(ListReviewsRequest(product_id="test-product-id"))
    assert list_response is not None


def test_ac2_readiness_check_returns_not_serving_when_dependency_unavailable(health_stub, stop_database):
    """AC-2: Readiness check returns NOT_SERVING when critical dependencies fail"""
    # Stop backing database to simulate dependency failure
    stop_database()
    request = health_pb2.HealthCheckRequest(service=READINESS_SERVICE_NAME)
    response = health_stub.Check(request)
    assert response.status == health_pb2.HealthCheckResponse.NOT_SERVING


def test_ac3_unknown_service_name_returns_not_found_status(health_stub):
    """AC-3: Requests for unknown service names return NOT_FOUND gRPC status"""
    request = health_pb2.HealthCheckRequest(service="invalid.service.name")
    with pytest.raises(grpc.RpcError) as excinfo:
        health_stub.Check(request)
    assert excinfo.value.code() == grpc.StatusCode.NOT_FOUND


def test_ac4_health_service_runs_on_same_port_as_main_api(grpc_channel, health_stub, product_reviews_stub):
    """AC-4: Health service runs on same TCP port as main product-reviews API"""
    # Verify both health service and main API are reachable on the same port
    health_request = health_pb2.HealthCheckRequest(service=LIVENESS_SERVICE_NAME)
    health_response = health_stub.Check(health_request)
    assert health_response is not None
    
    from src.product_reviews.demo_pb2 import ListReviewsRequest
    api_response = product_reviews_stub.ListReviews(ListReviewsRequest(product_id="test-product-id"))
    assert api_response is not None


def test_ac5_watch_method_streams_status_updates_for_supported_services(health_stub, restart_database):
    """AC-5: Watch method streams status updates when service status changes"""
    # Test watch for liveness service
    request = health_pb2.HealthCheckRequest(service=LIVENESS_SERVICE_NAME)
    responses = []
    
    # Start watch in separate thread
    def collect_responses():
        for response in health_stub.Watch(request):
            responses.append(response.status)
            if len(responses) >= 2:
                break
    
    import threading
    watch_thread = threading.Thread(target=collect_responses, daemon=True)
    watch_thread.start()
    
    # Trigger status change by restarting database
    restart_database()
    
    # Wait for responses
    watch_thread.join(timeout=10)
    
    # Verify we received at least two status updates (NOT_SERVING -> SERVING or vice versa)
    assert len(responses) >= 2
    assert health_pb2.HealthCheckResponse.NOT_SERVING in responses
    assert health_pb2.HealthCheckResponse.SERVING in responses
