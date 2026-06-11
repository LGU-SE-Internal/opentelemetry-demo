import pytest
import requests
import grpc
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc

SERVICE_HTTP_BASE = "http://localhost:8080"
SERVICE_GRPC_ADDR = "localhost:50051"


@pytest.mark.integration
def test_ac1_liveness_always_returns_200_when_process_running():
    # AC-1: GET /health/live returns 200 OK with body OK regardless of dependencies
    response = requests.get(f"{SERVICE_HTTP_BASE}/health/live", timeout=2)
    assert response.status_code == 200
    assert response.text.strip() == "OK"
    assert response.headers["Content-Type"] == "text/plain"


@pytest.mark.integration
def test_ac2_readiness_returns_200_when_all_dependencies_healthy():
    # AC-2: GET /health/ready returns 200 OK when product catalog and flagd are healthy
    response = requests.get(f"{SERVICE_HTTP_BASE}/health/ready", timeout=2)
    assert response.status_code == 200
    assert response.text.strip() == "OK"
    assert response.headers["Content-Type"] == "text/plain"


@pytest.mark.integration
def test_ac3_readiness_returns_503_when_product_catalog_unreachable():
    # AC-3: GET /health/ready returns 503 when product catalog is unreachable
    # Assumption: test environment has product catalog disabled/down
    response = requests.get(f"{SERVICE_HTTP_BASE}/health/ready", timeout=2)
    assert response.status_code == 503
    assert "product catalog" in response.text.lower()
    assert response.headers["Content-Type"] == "text/plain"


@pytest.mark.integration
def test_ac4_readiness_returns_503_when_flagd_unreachable():
    # AC-4: GET /health/ready returns 503 when flagd is unreachable
    # Assumption: test environment has flagd disabled/down
    response = requests.get(f"{SERVICE_HTTP_BASE}/health/ready", timeout=2)
    assert response.status_code == 503
    assert "flagd" in response.text.lower()
    assert response.headers["Content-Type"] == "text/plain"


@pytest.mark.integration
def test_ac5_grpc_health_check_returns_serving_when_all_healthy():
    # AC-5: gRPC Check returns SERVING when service and dependencies are healthy
    with grpc.insecure_channel(SERVICE_GRPC_ADDR) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        request = health_pb2.HealthCheckRequest(service="recommendationService")
        response = stub.Check(request, timeout=2)
        assert response.status == health_pb2.HealthCheckResponse.SERVING


@pytest.mark.integration
def test_ac5_grpc_health_check_returns_serving_for_empty_service_name():
    # AC-5: gRPC Check returns SERVING for empty service name when all healthy
    with grpc.insecure_channel(SERVICE_GRPC_ADDR) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        request = health_pb2.HealthCheckRequest(service="")
        response = stub.Check(request, timeout=2)
        assert response.status == health_pb2.HealthCheckResponse.SERVING


@pytest.mark.integration
def test_ac6_grpc_health_check_returns_not_serving_when_dependency_unhealthy():
    # AC-6: gRPC Check returns NOT_SERVING when any dependency is unreachable
    # Assumption: at least one dependency is down in this test case
    with grpc.insecure_channel(SERVICE_GRPC_ADDR) as channel:
        stub = health_pb2_grpc.HealthStub(channel)
        request = health_pb2.HealthCheckRequest(service="recommendationService")
        response = stub.Check(request, timeout=2)
        assert response.status == health_pb2.HealthCheckResponse.NOT_SERVING


@pytest.mark.integration
def test_ac7_liveness_ignores_unhealthy_dependencies():
    # AC-7: Liveness still returns 200 even when dependencies are unhealthy
    # Assumption: at least one dependency is down in this test case
    response = requests.get(f"{SERVICE_HTTP_BASE}/health/live", timeout=2)
    assert response.status_code == 200
    assert response.text.strip() == "OK"


# AC-8 covered by ac2, ac3, ac4
# AC-9 covered by ac5, ac6
