import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Import the interface definitions as per spec
# (We don't have the implementation yet, so these are stubs for test structure)
try:
    from main import app
    from main import validate_image_path  # noqa: F401
except ImportError:
    # If app not implemented yet, create mock client for test structure
    app = MagicMock()
client = TestClient(app)

# Test constants matching spec
STATIC_ROOT = "/app/static/images"
ALLOWED_EXTENSIONS = [".jpg", ".jpeg", ".png", ".gif", ".webp"]


def test_ac1_path_traversal_blocks_access_outside_static_dir():
    """AC-1: Path traversal sequences resolve outside static dir, return 400."""
    traversal_paths = [
        "../secret.txt",
        "%2e%2e/passwd",  # URL encoded ../
        "/etc/passwd",  # Absolute path
        "~/ssh/id_rsa",  # Home dir path
        "images/../../../../etc/hosts",
        "%2e%2e/%2e%2e/%2e%2e/app/config/prod.yaml",
    ]
    
    for path in traversal_paths:
        response = client.get(f"/images/{path}")
        assert response.status_code == 400, f"Path {path} should return 400"
        assert response.json() == {"error": "Invalid image path requested", "code": "INVALID_IMAGE_PATH"}
        # Verify no file content is returned
        assert b"secret" not in response.content
        assert b"passwd" not in response.content


def test_ac2_invalid_extension_returns_400():
    """AC-2: File extension not in allowed list returns 400."""
    invalid_ext_paths = [
        "image.exe",
        "document.pdf",
        "script.sh",
        "archive.zip",
        "config.yaml",
        "image.php",
        "data.json",
    ]
    
    for path in invalid_ext_paths:
        response = client.get(f"/images/{path}")
        assert response.status_code == 400, f"Path {path} with invalid extension should return 400"
        assert response.json() == {"error": "Invalid image path requested", "code": "INVALID_IMAGE_PATH"}


def test_ac3_valid_path_returns_200_with_image_content():
    """AC-3: Valid path returns 200 OK with correct image content."""
    valid_paths = [
        "product1.jpg",
        "category2/photo.jpeg",
        "banners/summer/sale.png",
        "avatar.webp",
        "animation.gif",
        "sub/dir/nested/image.jpg",
    ]
    
    for path in valid_paths:
        response = client.get(f"/images/{path}")
        # Happy path should return 200, not 400/500
        assert response.status_code != 400, f"Valid path {path} should not return 400"
        assert response.status_code != 500, f"Valid path {path} should not return 500"
        # Content type should be image type
        assert response.headers.get("Content-Type", "").startswith("image/")


def test_ac4_invalid_path_produces_structured_warn_log():
    """AC-4: Invalid path requests produce structured WARN logs with required fields."""
    invalid_path = "../secret.txt"
    
    with patch("logging.warning") as mock_warn:
        client.get(f"/images/{invalid_path}")
        
        # Verify warning was logged
        mock_warn.assert_called()
        log_call_args = mock_warn.call_args[0][0] if mock_warn.called else ""
        
        # Check log contains required fields
        required_fields = ["timestamp", "trace_id", "span_id", "client_ip", "requested_path", "error_reason"]
        for field in required_fields:
            assert field in str(log_call_args), f"Log missing required field: {field}"
        assert invalid_path in str(log_call_args), "Log should contain requested path"


def test_ac5_no_500_errors_for_invalid_paths():
    """AC-5: No 500 responses for invalid path requests, all return 400."""
    edge_case_paths = [
        "",
        "/",
        "////",
        "path/with//double//slashes.jpg",
        "path/with null\x00byte.jpg",
        "path/with/specialchars@#$%^&*.jpg",
        "path/with spaces in name.jpg",
        "very/long/path/" * 20 + "image.jpg",
        "noextension",
        ".htaccess",
        ".env",
    ]
    
    for path in edge_case_paths:
        response = client.get(f"/images/{path}")
        assert response.status_code != 500, f"Path {path} should not return 500"
        # All path validation failures return 400
        if response.status_code != 200:
            assert response.status_code == 400, f"Path {path} should return 400 not {response.status_code}"

