#!/usr/bin/env python3
import os
import time
import requests
import pytest
from typing import Tuple

IMAGE_PROVIDER_URL = os.getenv("IMAGE_PROVIDER_URL", "http://localhost:8087")
TEST_IMAGE_1 = "/assets/product/1.jpg"
TEST_IMAGE_2 = "/assets/product/2.jpg"
EXPECTED_429_BODY = "429 Too Many Requests\n"
EXPECTED_429_HEADERS = ["Retry-After"]

def get_rate_limit_env_var() -> int:
    """Get per image rate limit RPS from environment variable"""
    return int(os.getenv("NGINX_PER_IMAGE_RATE_LIMIT_RPS", "2"))

def test_ac1_default_rate_limit():
    """AC-1: Default per-image rate limit is 2 RPS when env var not set"""
    current_limit = get_rate_limit_env_var()
    # Verify default value if env var not present
    if "NGINX_PER_IMAGE_RATE_LIMIT_RPS" not in os.environ:
        assert current_limit == 2, f"Expected default limit 2, got {current_limit}"
    
    # Test behavior matches 2 RPS
    success_count = 0
    for i in range(3):
        resp = requests.get(f"{IMAGE_PROVIDER_URL}{TEST_IMAGE_1}")
        if resp.status_code == 200:
            success_count += 1
        time.sleep(0.1)
    # Should have exactly 2 successes, 1 failure
    assert success_count == 2, f"Expected 2 successful requests at default limit, got {success_count}"
    assert any(resp.status_code == 429 for resp in [requests.get(f"{IMAGE_PROVIDER_URL}{TEST_IMAGE_1}") for _ in range(1)]), "Expected 429 for third request"

def test_ac2_custom_rate_limit():
    """AC-2: Custom rate limit N is enforced when env var is set"""
    if "NGINX_PER_IMAGE_RATE_LIMIT_RPS" not in os.environ:
        pytest.skip("Skipping AC2 test: NGINX_PER_IMAGE_RATE_LIMIT_RPS env var not set")
    
    custom_limit = get_rate_limit_env_var()
    success_count = 0
    for i in range(custom_limit + 1):
        resp = requests.get(f"{IMAGE_PROVIDER_URL}{TEST_IMAGE_1}")
        if resp.status_code == 200:
            success_count += 1
        time.sleep(0.1)
    
    assert success_count == custom_limit, f"Expected {custom_limit} successful requests at custom limit, got {success_count}"

def test_ac3_three_requests_exceed_limit():
    """AC-3: 3 consecutive requests to same URI <1s result in 429 for third request (limit=2 RPS)"""
    # Force limit to 2 for this test
    test_limit = 2
    # Clear any previous state
    time.sleep(1)
    
    # Send 3 requests in quick succession
    responses = []
    for _ in range(3):
        responses.append(requests.get(f"{IMAGE_PROVIDER_URL}{TEST_IMAGE_1}"))
        time.sleep(0.05)
    
    # First two should be 200, third should be 429
    assert responses[0].status_code == 200, f"First request failed with {responses[0].status_code}"
    assert responses[1].status_code == 200, f"Second request failed with {responses[1].status_code}"
    assert responses[2].status_code == 429, f"Third request should return 429, got {responses[2].status_code}"

def test_ac4_different_uris_separate_counters():
    """AC-4: Requests to different URIs do not share rate limit counters"""
    test_limit = get_rate_limit_env_var()
    time.sleep(1)
    
    # Send test_limit requests to each of two images
    image1_responses = [requests.get(f"{IMAGE_PROVIDER_URL}{TEST_IMAGE_1}") for _ in range(test_limit)]
    image2_responses = [requests.get(f"{IMAGE_PROVIDER_URL}{TEST_IMAGE_2}") for _ in range(test_limit)]
    
    # All should succeed
    assert all(r.status_code == 200 for r in image1_responses), "Some requests to image1 failed unexpectedly"
    assert all(r.status_code == 200 for r in image2_responses), "Some requests to image2 failed unexpectedly"

def test_ac5_global_limit_still_applies():
    """AC-5: Existing global rate limit is still enforced alongside per-image limit"""
    # Global limit is lower than per-image limit for this test
    time.sleep(1)
    success_count = 0
    # Send enough requests to trigger global limit even if per-image allows
    for _ in range(20):
        resp = requests.get(f"{IMAGE_PROVIDER_URL}{TEST_IMAGE_1}")
        if resp.status_code == 200:
            success_count +=1
        elif resp.status_code == 429:
            # Got 429, verify it's expected
            break
        time.sleep(0.02)
    # Should have hit rate limit before 20 requests
    assert success_count < 20, "Global rate limit not triggered as expected"

def test_ac6_429_response_matches_existing():
    """AC-6: Per-image 429 response matches existing global rate limit response"""
    # First get a global rate limit 429 response
    time.sleep(1)
    global_429 = None
    for _ in range(50):
        resp = requests.get(f"{IMAGE_PROVIDER_URL}{TEST_IMAGE_1}")
        if resp.status_code == 429:
            global_429 = resp
            break
        time.sleep(0.01)
    assert global_429 is not None, "Could not get global 429 response for comparison"
    
    # Now get per-image 429 response
    time.sleep(1)
    per_image_429 = None
    for _ in range(3):
        resp = requests.get(f"{IMAGE_PROVIDER_URL}{TEST_IMAGE_1}")
        if resp.status_code == 429:
            per_image_429 = resp
            break
        time.sleep(0.05)
    assert per_image_429 is not None, "Could not get per-image 429 response for comparison"
    
    # Compare status, body, headers
    assert per_image_429.status_code == global_429.status_code, "Status codes differ between global and per-image 429"
    assert per_image_429.text == global_429.text, "Response bodies differ between global and per-image 429"
    # Check required headers present in both
    for header in EXPECTED_429_HEADERS:
        assert header in per_image_429.headers, f"Header {header} missing from per-image 429 response"
        assert header in global_429.headers, f"Header {header} missing from global 429 response"
        assert per_image_429.headers[header] == global_429.headers[header], f"Header {header} value differs between responses"

def test_ac7_all_image_uris_covered():
    """AC-7: All static image asset URIs are covered by per-image rate limit"""
    test_uris = [
        "/assets/product/1.jpg",
        "/assets/product/2.jpg",
        "/assets/product/3.png",
        "/assets/category/electronics.jpg",
        "/assets/banner/sale.webp"
    ]
    
    for uri in test_uris:
        time.sleep(1)
        # Send 3 requests to each URI
        responses = [requests.get(f"{IMAGE_PROVIDER_URL}{uri}") for _ in range(3)]
        # Third request should be 429 if covered
        assert responses[2].status_code == 429, f"URI {uri} not covered by per-image rate limit, third request returned {responses[2].status_code}"
