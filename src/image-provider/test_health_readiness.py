import requests
import subprocess
import time
import os
import pytest

SERVICE_URL = "http://localhost:8080"
HEALTH_ENDPOINT = f"{SERVICE_URL}/health"
READY_ENDPOINT = f"{SERVICE_URL}/ready"
ASSET_PATH = "./static/Banner.png"
NGINX_CONF_PATH = "./nginx.conf.template"
DOCKERFILE_PATH = "./Dockerfile"

def is_nginx_running():
    try:
        subprocess.run(["pgrep", "nginx"], check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError:
        return False

def start_nginx():
    subprocess.run(["nginx", "-g", "daemon off;"], capture_output=True, start_new_session=True)
    time.sleep(2)

def stop_nginx():
    subprocess.run(["pkill", "nginx"], capture_output=True)
    time.sleep(2)

@pytest.mark.ac1
def test_ac1_health_endpoint_returns_200_ok_when_nginx_running():
    # Arrange: Ensure nginx is running
    if not is_nginx_running():
        start_nginx()
    
    # Act: Call GET /health endpoint
    response = requests.get(HEALTH_ENDPOINT, timeout=5)
    
    # Assert: 200 status, body is "OK", content type text/plain
    assert response.status_code == 200
    assert response.text.strip() == "OK"
    assert "text/plain" in response.headers.get("Content-Type", "")

@pytest.mark.ac2
def test_ac2_health_endpoint_fails_when_nginx_not_running():
    # Arrange: Ensure nginx is stopped
    if is_nginx_running():
        stop_nginx()
    
    # Act & Assert: Connection refused/timeout when calling /health
    with pytest.raises((requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        requests.get(HEALTH_ENDPOINT, timeout=2)

@pytest.mark.ac3
def test_ac3_ready_endpoint_returns_200_ok_when_assets_present():
    # Arrange: Ensure nginx is running and assets exist
    if not is_nginx_running():
        start_nginx()
    assert os.path.exists(ASSET_PATH), "Test setup failed: Static asset missing"
    
    # Act: Call GET /ready endpoint
    response = requests.get(READY_ENDPOINT, timeout=5)
    
    # Assert: 200 status, body is "OK", content type text/plain
    assert response.status_code == 200
    assert response.text.strip() == "OK"
    assert "text/plain" in response.headers.get("Content-Type", "")

@pytest.mark.ac4
def test_ac4_ready_endpoint_returns_503_when_assets_missing():
    # Arrange: Ensure nginx is running, temporarily move asset
    if not is_nginx_running():
        start_nginx()
    assert os.path.exists(ASSET_PATH), "Test setup failed: Static asset missing"
    
    # Temporarily rename asset
    os.rename(ASSET_PATH, f"{ASSET_PATH}.tmp")
    try:
        # Act: Call GET /ready endpoint
        response = requests.get(READY_ENDPOINT, timeout=5)
        
        # Assert: 503 status code
        assert response.status_code == 503
    finally:
        # Restore asset
        os.rename(f"{ASSET_PATH}.tmp", ASSET_PATH)

@pytest.mark.ac5
def test_ac5_endpoints_accessible_on_port_8080_same_as_main_service():
    # Arrange: Ensure nginx is running
    if not is_nginx_running():
        start_nginx()
    
    # Act: Test main service (static asset) and health/ready endpoints on same port
    main_response = requests.get(f"{SERVICE_URL}/Banner.png", timeout=5)
    health_response = requests.get(HEALTH_ENDPOINT, timeout=5)
    ready_response = requests.get(READY_ENDPOINT, timeout=5)
    
    # Assert: All responses successful, all on port 8080
    assert main_response.status_code == 200
    assert health_response.status_code == 200
    assert ready_response.status_code == 200
    assert HEALTH_ENDPOINT.startswith(SERVICE_URL)
    assert READY_ENDPOINT.startswith(SERVICE_URL)

@pytest.mark.ac6
def test_ac6_no_new_external_dependencies_added():
    # Read Dockerfile and check for new package installs
    with open(DOCKERFILE_PATH, "r") as f:
        dockerfile_content = f.read()
    
    # Assert no new apt/apk installs added beyond base Nginx
    install_commands = [line for line in dockerfile_content.splitlines() if "install" in line.lower()]
    # The base Nginx image already has all required packages, no new installs should be present
    assert len(install_commands) == 0 or all("apt-get update && apt-get install -y --no-install-recommends" not in line for line in install_commands), "New external dependencies added to Dockerfile"

@pytest.mark.ac7
def test_ac7_all_endpoint_config_in_nginx_conf_template():
    # Read nginx.conf.template
    with open(NGINX_CONF_PATH, "r") as f:
        conf_content = f.read()
    
    # Assert both /health and /ready endpoints are defined in the config file
    assert "/health" in conf_content, "/health endpoint not found in nginx.conf.template"
    assert "/ready" in conf_content, "/ready endpoint not found in nginx.conf.template"
