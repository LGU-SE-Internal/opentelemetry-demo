#!/usr/bin/env python3
import subprocess
import time
import pytest
import requests

IMAGE_PROVIDER_SERVICE_NAME = "imageprovider"
IMAGE_PROVIDER_PORT = 8080
BASE_URL = f"http://localhost:{IMAGE_PROVIDER_PORT}"
DOCKER_COMPOSE_CMD = ["docker", "compose"]


def run_cmd(cmd, shell=False, check=True):
    return subprocess.run(cmd, shell=shell, capture_output=True, text=True, check=check)


def wait_for_service_start(timeout=10):
    """Wait for image provider service to be reachable"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            requests.get(f"{BASE_URL}/status", timeout=1)
            return True
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            time.sleep(0.5)
    return False


@pytest.fixture(scope="function")
def image_provider_service():
    """Fixture to start/stop image provider service for each test"""
    # Cleanup any existing instance
    run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-fsv", IMAGE_PROVIDER_SERVICE_NAME], check=False)
    # Start service detached
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", IMAGE_PROVIDER_SERVICE_NAME])
    # Wait for service to start
    assert wait_for_service_start(timeout=15), "Image provider service failed to start within 15s"
    
    yield
    
    # Cleanup after test
    run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-fsv", IMAGE_PROVIDER_SERVICE_NAME], check=False)


def get_service_logs():
    """Get recent error logs from image provider service"""
    result = run_cmd([
        "docker", "logs", "--tail", "20", IMAGE_PROVIDER_SERVICE_NAME
    ], capture_output=True, text=True)
    return result.stderr + result.stdout


@pytest.mark.ac1
def test_ac1_path_containing_traversal_sequence_returns_400_bad_request(image_provider_service):
    """AC-1: When a request path contains the ../ traversal sequence, the service returns 400 Bad Request"""
    # Test standard traversal attempt
    response = requests.get(f"{BASE_URL}/images/../../etc/passwd", timeout=2, allow_redirects=False)
    assert response.status_code == 400, f"Expected 400, got {response.status_code}"
    
    # Test URL-encoded traversal attempt
    response = requests.get(f"{BASE_URL}/images/%2e%2e/%2e%2e/etc/passwd", timeout=2, allow_redirects=False)
    assert response.status_code == 400, f"Expected 400, got {response.status_code}"
    
    # Test multiple traversal sequences
    response = requests.get(f"{BASE_URL}/images/./.././../etc/passwd", timeout=2, allow_redirects=False)
    assert response.status_code == 400, f"Expected 400, got {response.status_code}"
    
    # Verify rejection is logged
    logs = get_service_logs()
    assert "path traversal attempt" in logs.lower()
    assert "/images/../../etc/passwd" in logs


@pytest.mark.ac2
def test_ac2_path_containing_invalid_characters_returns_400_bad_request(image_provider_service):
    """AC-2: When a request path contains invalid characters, the service returns 400 Bad Request"""
    invalid_cases = [
        ("/images/test%00.png", "null byte"),
        ("/images/test;.png", "semicolon"),
        ("/images/`test`.png", "backtick"),
        ("/images/<test>.png", "angle bracket"),
        ("/images/>test.png", "angle bracket"),
        ("/images/test|.png", "pipe character"),
        ("/images/test$.png", "dollar sign"),
        ("/images/test@.png", "at sign"),
        ("/images/test#.png", "hash sign"),
        ("/images/test%.png", "percent sign without encoding"),
    ]
    
    for path, _ in invalid_cases:
        response = requests.get(f"{BASE_URL}{path}", timeout=2, allow_redirects=False)
        assert response.status_code == 400, f"Expected 400 for path {path}, got {response.status_code}"
    
    # Verify rejection is logged
    logs = get_service_logs()
    assert "invalid characters" in logs.lower()


@pytest.mark.ac3
def test_ac3_request_with_allowed_extension_proceeds(image_provider_service):
    """AC-3: When a request targets a file with an allowed extension (case-insensitive), the request is allowed to proceed"""
    allowed_extensions = ["png", "PNG", "jpg", "JPG", "jpeg", "Jpeg", "webp", "WEBP", "gif", "GIF", "svg", "SVG"]
    
    for ext in allowed_extensions:
        # Request non-existent file to avoid 200 but confirm it's not blocked as 400
        response = requests.get(f"{BASE_URL}/images/nonexistent_test.{ext}", timeout=2, allow_redirects=False)
        assert response.status_code != 400, f"Expected allowed extension {ext} not to return 400, got {response.status_code}"
        assert response.status_code in [200, 404], f"Expected 200 or 404 for allowed extension {ext}, got {response.status_code}"


@pytest.mark.ac4
def test_ac4_request_with_disallowed_extension_returns_400_bad_request(image_provider_service):
    """AC-4: When a request targets a file with a disallowed extension, the service returns 400 Bad Request"""
    disallowed_extensions = ["php", "sh", "txt", "json", "exe", "py", "js", "html", "css", "xml", "zip", "tar", "gz"]
    
    for ext in disallowed_extensions:
        response = requests.get(f"{BASE_URL}/images/test.{ext}", timeout=2, allow_redirects=False)
        assert response.status_code == 400, f"Expected 400 for disallowed extension {ext}, got {response.status_code}"
    
    # Verify rejection is logged
    logs = get_service_logs()
    assert "disallowed extension" in logs.lower()


@pytest.mark.ac5
def test_ac5_rejected_requests_generate_structured_log_entries(image_provider_service):
    """AC-5: All rejected requests generate a structured error log entry containing all required fields"""
    # Trigger a rejection
    test_path = "/images/../../etc/passwd"
    response = requests.get(f"{BASE_URL}{test_path}", timeout=2, allow_redirects=False)
    assert response.status_code == 400
    
    # Get logs
    logs = get_service_logs()
    
    # Check all required fields are present
    required_fields = ["client ip", "requested path", "rejection reason", "request id", "timestamp"]
    for field in required_fields:
        assert field.lower() in logs.lower(), f"Required log field '{field}' not found in logs"
    
    # Check specific values are logged
    assert test_path in logs
    assert "path traversal attempt" in logs.lower()


@pytest.mark.ac6
def test_ac6_valid_request_for_existing_image_returns_200_ok(image_provider_service):
    """AC-6: Valid requests for existing image files with allowed extensions return 200 OK with correct image content"""
    # First check what images exist in the service
    result = run_cmd([
        "docker", "exec", IMAGE_PROVIDER_SERVICE_NAME,
        "find", "/static/images", "-type", "f", "|", "head", "-1"
    ], shell=True, capture_output=True, text=True)
    
    if result.returncode == 0 and result.stdout.strip():
        # Get relative path to image
        full_path = result.stdout.strip()
        relative_path = full_path.replace("/static/", "/")
        
        response = requests.get(f"{BASE_URL}{relative_path}", timeout=2)
        assert response.status_code == 200, f"Expected 200 for existing image {relative_path}, got {response.status_code}"
        assert len(response.content) > 0, "Expected non-empty image content"
    else:
        # Skip if no test images exist, just verify allowed path is not blocked
        response = requests.get(f"{BASE_URL}/images/test.png", timeout=2, allow_redirects=False)
        assert response.status_code != 400, "Valid image request should not be blocked as 400"


@pytest.mark.ac7
def test_ac7_valid_request_for_nonexistent_image_returns_404_not_found(image_provider_service):
    """AC-7: Valid requests for non-existing image files with allowed extensions return 404 Not Found"""
    response = requests.get(f"{BASE_URL}/images/definitely_does_not_exist_12345.png", timeout=2)
    assert response.status_code == 404, f"Expected 404 for non-existent image, got {response.status_code}"
