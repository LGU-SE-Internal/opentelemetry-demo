#!/usr/bin/env python3
import subprocess
import time
import pytest
import requests
import ssl
import socket
import json
from pathlib import Path

BASE_URL_HTTP = "http://localhost:8080"
BASE_URL_HTTPS = "https://localhost:8443"
DOCKER_COMPOSE_CMD = ["docker", "compose"]
TEST_CERT_DIR = Path(__file__).parent / "test_certs"
TEST_CERT = TEST_CERT_DIR / "server.crt"
TEST_KEY = TEST_CERT_DIR / "server.key"
TEST_CA = TEST_CERT_DIR / "ca.crt"
INVALID_CERT = TEST_CERT_DIR / "invalid.crt"
INVALID_KEY = TEST_CERT_DIR / "invalid.key"
CLIENT_VALID_CERT = TEST_CERT_DIR / "client.crt"
CLIENT_VALID_KEY = TEST_CERT_DIR / "client.key"
CLIENT_INVALID_CERT = TEST_CERT_DIR / "client_invalid.crt"


def run_cmd(cmd, shell=False, check=True, env=None):
    return subprocess.run(cmd, shell=shell, capture_output=True, text=True, check=check, env=env)


def wait_for_service(port, timeout=15, use_ssl=False):
    start = time.time()
    while time.time() - start < timeout:
        try:
            if use_ssl:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with socket.create_connection(("localhost", port), timeout=1):
                    with ctx.wrap_socket(socket.socket(), server_hostname="localhost"):
                        return True
            else:
                with socket.create_connection(("localhost", port), timeout=1):
                    return True
        except:
            time.sleep(0.5)
    return False


def stop_service():
    run_cmd(DOCKER_COMPOSE_CMD + ["stop", "imageprovider"], check=False)
    run_cmd(DOCKER_COMPOSE_CMD + ["rm", "-f", "imageprovider"], check=False)


@pytest.fixture(autouse=True)
def teardown():
    yield
    stop_service()


def test_ac1_tls_disabled_works_http_8080():
    """AC-1: TLS disabled, service works on 8080 HTTP"""
    env = {"TLS_ENABLED": "false"}
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", "imageprovider"], env=env)
    assert wait_for_service(8080, use_ssl=False)
    resp = requests.get(f"{BASE_URL_HTTP}/placeholder.jpg", verify=False)
    assert resp.status_code == 200
    assert len(resp.content) > 0


def test_ac2a_tls_enabled_valid_cert_starts_successfully():
    """AC-2a: TLS enabled with valid cert/key, service starts"""
    env = {
        "TLS_ENABLED": "true",
        "TLS_CERT_PATH": str(TEST_CERT),
        "TLS_KEY_PATH": str(TEST_KEY)
    }
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", "imageprovider"], env=env)
    assert wait_for_service(8443, use_ssl=True)
    res = run_cmd(DOCKER_COMPOSE_CMD + ["ps", "-q", "imageprovider"], capture_output=True)
    container_id = res.stdout.strip()
    inspect_res = run_cmd(["docker", "inspect", "-f", "{{.State.ExitCode}}", container_id])
    assert inspect_res.stdout.strip() == "0"


def test_ac2b_tls_enabled_accepts_https_8443():
    """AC-2b: TLS enabled, valid HTTPS connections accepted on 8443"""
    env = {
        "TLS_ENABLED": "true",
        "TLS_CERT_PATH": str(TEST_CERT),
        "TLS_KEY_PATH": str(TEST_KEY)
    }
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", "imageprovider"], env=env)
    assert wait_for_service(8443, use_ssl=True)
    resp = requests.get(f"{BASE_URL_HTTPS}/placeholder.jpg", verify=str(TEST_CA))
    assert resp.status_code == 200
    assert len(resp.content) > 0


def test_ac2c_tls_enabled_rejects_http_on_8443():
    """AC-2c: TLS enabled, unencrypted HTTP on 8443 returns 400"""
    env = {
        "TLS_ENABLED": "true",
        "TLS_CERT_PATH": str(TEST_CERT),
        "TLS_KEY_PATH": str(TEST_KEY)
    }
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", "imageprovider"], env=env)
    assert wait_for_service(8443, use_ssl=True)
    try:
        resp = requests.get("http://localhost:8443/placeholder.jpg", timeout=5, allow_redirects=False)
        assert resp.status_code == 400
    except requests.exceptions.ConnectionError:
        pytest.fail("Connection rejected, expected 400 Bad Request for HTTP on TLS port")


def test_ac3_tls_enabled_missing_cert_fails_start():
    """AC-3: TLS enabled with missing cert, service fails to start with non-zero exit"""
    env = {
        "TLS_ENABLED": "true",
        "TLS_KEY_PATH": str(TEST_KEY)
    }
    with pytest.raises(subprocess.CalledProcessError):
        run_cmd(DOCKER_COMPOSE_CMD + ["run", "--rm", "imageprovider"], env=env, check=True)
    logs = run_cmd(DOCKER_COMPOSE_CMD + ["logs", "imageprovider"], capture_output=True).stderr
    assert "TLS certificate load error" in logs.lower() or "certificate" in logs.lower()


def test_ac4_tls_enabled_missing_key_fails_start():
    """AC-4: TLS enabled with missing key, service fails to start with non-zero exit"""
    env = {
        "TLS_ENABLED": "true",
        "TLS_CERT_PATH": str(TEST_CERT)
    }
    with pytest.raises(subprocess.CalledProcessError):
        run_cmd(DOCKER_COMPOSE_CMD + ["run", "--rm", "imageprovider"], env=env, check=True)
    logs = run_cmd(DOCKER_COMPOSE_CMD + ["logs", "imageprovider"], capture_output=True).stderr
    assert "TLS private key load error" in logs.lower() or "private key" in logs.lower()


def test_ac5a_mtls_enabled_valid_ca_starts_successfully():
    """AC-5a: mTLS enabled with valid CA, service starts"""
    env = {
        "TLS_ENABLED": "true",
        "TLS_CERT_PATH": str(TEST_CERT),
        "TLS_KEY_PATH": str(TEST_KEY),
        "MTLS_ENABLED": "true",
        "MTLS_CA_CERT_PATH": str(TEST_CA)
    }
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", "imageprovider"], env=env)
    assert wait_for_service(8443, use_ssl=True)
    res = run_cmd(DOCKER_COMPOSE_CMD + ["ps", "-q", "imageprovider"], capture_output=True)
    container_id = res.stdout.strip()
    inspect_res = run_cmd(["docker", "inspect", "-f", "{{.State.ExitCode}}", container_id])
    assert inspect_res.stdout.strip() == "0"


def test_ac5b_mtls_valid_client_cert_accepted():
    """AC-5b: mTLS enabled, valid client cert connections accepted"""
    env = {
        "TLS_ENABLED": "true",
        "TLS_CERT_PATH": str(TEST_CERT),
        "TLS_KEY_PATH": str(TEST_KEY),
        "MTLS_ENABLED": "true",
        "MTLS_CA_CERT_PATH": str(TEST_CA)
    }
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", "imageprovider"], env=env)
    assert wait_for_service(8443, use_ssl=True)
    resp = requests.get(
        f"{BASE_URL_HTTPS}/placeholder.jpg",
        verify=str(TEST_CA),
        cert=(str(CLIENT_VALID_CERT), str(CLIENT_VALID_KEY))
    )
    assert resp.status_code == 200


def test_ac5c_mtls_invalid_client_cert_rejected_403():
    """AC-5c: mTLS enabled, invalid/missing client cert returns 403"""
    env = {
        "TLS_ENABLED": "true",
        "TLS_CERT_PATH": str(TEST_CERT),
        "TLS_KEY_PATH": str(TEST_KEY),
        "MTLS_ENABLED": "true",
        "MTLS_CA_CERT_PATH": str(TEST_CA)
    }
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", "imageprovider"], env=env)
    assert wait_for_service(8443, use_ssl=True)

    # Test missing client cert
    try:
        resp = requests.get(f"{BASE_URL_HTTPS}/placeholder.jpg", verify=str(TEST_CA))
        assert resp.status_code == 403
    except requests.exceptions.SSLError:
        pass  # Expected SSL error for missing cert

    # Test invalid client cert
    try:
        resp = requests.get(
            f"{BASE_URL_HTTPS}/placeholder.jpg",
            verify=str(TEST_CA),
            cert=(str(CLIENT_INVALID_CERT), str(CLIENT_VALID_KEY))
        )
        assert resp.status_code == 403
    except requests.exceptions.SSLError:
        pass  # Expected SSL error for invalid cert


def test_ac6_mtls_enabled_missing_ca_fails_start():
    """AC-6: mTLS enabled with missing CA, service fails to start with non-zero exit"""
    env = {
        "TLS_ENABLED": "true",
        "TLS_CERT_PATH": str(TEST_CERT),
        "TLS_KEY_PATH": str(TEST_KEY),
        "MTLS_ENABLED": "true"
    }
    with pytest.raises(subprocess.CalledProcessError):
        run_cmd(DOCKER_COMPOSE_CMD + ["run", "--rm", "imageprovider"], env=env, check=True)
    logs = run_cmd(DOCKER_COMPOSE_CMD + ["logs", "imageprovider"], capture_output=True).stderr
    assert "mTLS CA load error" in logs.lower() or "ca certificate" in logs.lower()


def test_ac7_tls_min_version_13_rejects_tls12():
    """AC-7: TLS min version set to 1.3, rejects TLS 1.2 connections"""
    env = {
        "TLS_ENABLED": "true",
        "TLS_CERT_PATH": str(TEST_CERT),
        "TLS_KEY_PATH": str(TEST_KEY),
        "TLS_MIN_VERSION": "TLSv1.3"
    }
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", "imageprovider"], env=env)
    assert wait_for_service(8443, use_ssl=True)

    # Try connect with TLS 1.2 only
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.min_version = ssl.TLSVersion.TLSv1_2
    ctx.max_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    with pytest.raises((ssl.SSLError, ConnectionResetError)):
        with socket.create_connection(("localhost", 8443), timeout=5):
            with ctx.wrap_socket(socket.socket(), server_hostname="localhost") as s:
                s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
                s.recv(1024)


def test_ac8_custom_cipher_suites_enforced():
    """AC-8: Custom cipher suites set, only allowed ciphers work for TLS 1.2"""
    allowed_cipher = "ECDHE-ECDSA-AES256-GCM-SHA384"
    disallowed_cipher = "AES256-SHA256"
    env = {
        "TLS_ENABLED": "true",
        "TLS_CERT_PATH": str(TEST_CERT),
        "TLS_KEY_PATH": str(TEST_KEY),
        "TLS_MIN_VERSION": "TLSv1.2",
        "TLS_CIPHER_SUITES": allowed_cipher
    }
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", "imageprovider"], env=env)
    assert wait_for_service(8443, use_ssl=True)

    # Test allowed cipher works
    ctx_good = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx_good.set_ciphers(allowed_cipher)
    ctx_good.check_hostname = False
    ctx_good.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("localhost", 8443), timeout=5):
        with ctx_good.wrap_socket(socket.socket(), server_hostname="localhost") as s:
            assert s.cipher()[0] == allowed_cipher

    # Test disallowed cipher fails
    ctx_bad = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx_bad.set_ciphers(disallowed_cipher)
    ctx_bad.check_hostname = False
    ctx_bad.verify_mode = ssl.CERT_NONE
    with pytest.raises((ssl.SSLError, ConnectionResetError)):
        with socket.create_connection(("localhost", 8443), timeout=5):
            with ctx_bad.wrap_socket(socket.socket(), server_hostname="localhost"):
                pass


def test_ac9_tls_events_logged_as_structured_json():
    """AC-9: TLS events logged as structured JSON with required fields"""
    env = {
        "TLS_ENABLED": "true",
        "TLS_CERT_PATH": str(TEST_CERT),
        "TLS_KEY_PATH": str(TEST_KEY),
        "MTLS_ENABLED": "true",
        "MTLS_CA_CERT_PATH": str(TEST_CA)
    }
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", "imageprovider"], env=env)
    assert wait_for_service(8443, use_ssl=True)

    # Make successful and failed requests
    try:
        requests.get(f"{BASE_URL_HTTPS}/placeholder.jpg", verify=str(TEST_CA), cert=(str(CLIENT_VALID_CERT), str(CLIENT_VALID_KEY)))
        requests.get(f"{BASE_URL_HTTPS}/placeholder.jpg", verify=str(TEST_CA))
    except:
        pass

    logs = run_cmd(DOCKER_COMPOSE_CMD + ["logs", "imageprovider"], capture_output=True).stdout + run_cmd(DOCKER_COMPOSE_CMD + ["logs", "imageprovider"], capture_output=True).stderr
    found_tls_log = False
    for line in logs.splitlines():
        try:
            log_entry = json.loads(line)
            if "tls_version" in log_entry or "client_ip" in log_entry or "cipher_suite" in log_entry:
                found_tls_log = True
                assert "client_ip" in log_entry
                assert "tls_version" in log_entry
                assert "cipher_suite" in log_entry
                break
        except json.JSONDecodeError:
            continue
    assert found_tls_log, "No structured TLS event logs found"


def test_ac10_default_config_no_change_backward_compatible():
    """AC-10: Default config (no TLS env vars) works same as before"""
    run_cmd(DOCKER_COMPOSE_CMD + ["up", "-d", "imageprovider"])
    assert wait_for_service(8080, use_ssl=False)
    resp = requests.get(f"{BASE_URL_HTTP}/placeholder.jpg")
    assert resp.status_code == 200
    # Verify TLS port is not open
    assert not wait_for_service(8443, timeout=5, use_ssl=True)
