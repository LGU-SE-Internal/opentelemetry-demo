"""
Integration tests for telemetry-docs service HTTP health and readiness endpoints
as defined in spec for issue #1652
"""
import pytest
import requests

# Test constants from spec
SERVICE_NAME = "telemetry-docs"
SERVICE_PORT = 8000
BASE_URL = f"http://localhost:{SERVICE_PORT}"
HEALTH_ENDPOINT = "/health"
READY_ENDPOINT = "/ready"
EXPECTED_CONTENT_TYPE = "application/json"
EXPECTED_STATIC_FILE = "/index.html"


def test_ac1_health_endpoint_returns_200_ok_with_correct_schema():
    """AC-1: GET /health returns 200 OK, correct Content-Type and matches health success schema"""
    response = requests.get(f"{BASE_URL}{HEALTH_ENDPOINT}", timeout=5)
    
    # Validate status code
    assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}"
    
    # Validate Content-Type header
    assert EXPECTED_CONTENT_TYPE in response.headers.get("Content-Type", ""), \
        f"Expected Content-Type: {EXPECTED_CONTENT_TYPE}, got {response.headers.get('Content-Type')}"
    
    # Validate response body schema
    response_json = response.json()
    assert response_json.get("status") == "healthy", f"Expected status: healthy, got {response_json.get('status')}"
    assert response_json.get("service") == SERVICE_NAME, f"Expected service: {SERVICE_NAME}, got {response_json.get('service')}"
    assert len(response_json.keys()) == 2, "Response contains extra fields not in schema"


def test_ac2_ready_endpoint_returns_200_ok_when_content_available():
    """AC-2: GET /ready returns 200 OK with success schema when static content is present"""
    # First verify static content is actually accessible (to confirm test precondition)
    static_response = requests.get(f"{BASE_URL}{EXPECTED_STATIC_FILE}", timeout=5)
    assert static_response.status_code == 200, "Precondition failed: static content not available"
    
    # Test readiness endpoint
    response = requests.get(f"{BASE_URL}{READY_ENDPOINT}", timeout=5)
    
    # Validate status code
    assert response.status_code == 200, f"Expected 200 OK, got {response.status_code}"
    
    # Validate Content-Type header
    assert EXPECTED_CONTENT_TYPE in response.headers.get("Content-Type", ""), \
        f"Expected Content-Type: {EXPECTED_CONTENT_TYPE}, got {response.headers.get('Content-Type')}"
    
    # Validate response body schema
    response_json = response.json()
    assert response_json.get("status") == "ready", f"Expected status: ready, got {response_json.get('status')}"
    assert response_json.get("service") == SERVICE_NAME, f"Expected service: {SERVICE_NAME}, got {response_json.get('service')}"
    assert response_json.get("content_available") == True, f"Expected content_available: true, got {response_json.get('content_available')}"
    assert len(response_json.keys()) == 3, "Response contains extra fields not in schema"


def test_ac3_ready_endpoint_returns_503_when_content_missing(remove_telemetry_docs_content):
    """AC-3: GET /ready returns 503 Service Unavailable with failure schema when static content is missing"""
    response = requests.get(f"{BASE_URL}{READY_ENDPOINT}", timeout=5)
    
    # Validate status code
    assert response.status_code == 503, f"Expected 503 Service Unavailable, got {response.status_code}"
    
    # Validate Content-Type header
    assert EXPECTED_CONTENT_TYPE in response.headers.get("Content-Type", ""), \
        f"Expected Content-Type: {EXPECTED_CONTENT_TYPE}, got {response.headers.get('Content-Type')}"
    
    # Validate response body schema
    response_json = response.json()
    assert response_json.get("status") == "not_ready", f"Expected status: not_ready, got {response_json.get('status')}"
    assert response_json.get("service") == SERVICE_NAME, f"Expected service: {SERVICE_NAME}, got {response_json.get('service')}"
    assert response_json.get("content_available") == False, f"Expected content_available: false, got {response_json.get('content_available')}"
    assert len(response_json.keys()) == 3, "Response contains extra fields not in schema"


def test_ac4_health_endpoint_accessible_without_authentication():
    """AC-4: /health endpoint is accessible without authentication credentials"""
    # Send request without any Authorization header
    response = requests.get(f"{BASE_URL}{HEALTH_ENDPOINT}", headers={}, timeout=5)
    
    # Verify no authentication errors
    assert response.status_code not in (401, 403), f"Expected no authentication error, got {response.status_code}"


def test_ac4_ready_endpoint_accessible_without_authentication():
    """AC-4: /ready endpoint is accessible without authentication credentials"""
    # Send request without any Authorization header
    response = requests.get(f"{BASE_URL}{READY_ENDPOINT}", headers={}, timeout=5)
    
    # Verify no authentication errors
    assert response.status_code not in (401, 403), f"Expected no authentication error, got {response.status_code}"


@pytest.fixture(scope="function")
def remove_telemetry_docs_content():
    """Fixture to temporarily remove static content from telemetry-docs service to test failure case"""
    import docker
    client = docker.from_env()
    container = client.containers.get("telemetry-docs")
    
    # Backup and remove index.html
    container.exec_run("cp /usr/share/nginx/html/index.html /tmp/index.html.bak")
    container.exec_run("rm /usr/share/nginx/html/index.html")
    
    yield
    
    # Restore content
    container.exec_run("cp /tmp/index.html.bak /usr/share/nginx/html/index.html")
    container.exec_run("rm /tmp/index.html.bak")
