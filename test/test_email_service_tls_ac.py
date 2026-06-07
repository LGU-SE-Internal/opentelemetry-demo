import os
import time
import requests
import pytest
from subprocess import Popen, PIPE

EMAIL_SERVICE_HOST = os.getenv("EMAIL_SERVICE_HOST", "localhost")
DEFAULT_HTTP_PORT = 8080
DEFAULT_HTTPS_PORT = 8443
TEST_CERTS_DIR = os.path.join(os.path.dirname(__file__), "test_certs/email")


class TestEmailServiceTLS:
    def teardown_method(self):
        # Clean up any running email service process
        try:
            Popen(["pkill", "-f", "ruby app.rb"], stdout=PIPE, stderr=PIPE).wait()
            time.sleep(1)
        except Exception:
            pass

    def test_ac1_tls_disabled_uses_plain_http_8080(self):
        """AC-1: When TLS_ENABLED=false, service runs on plain HTTP port 8080 and responds to requests"""
        env = os.environ.copy()
        env["EMAIL_SERVICE_TLS_ENABLED"] = "false"
        
        # Start service
        proc = Popen(["ruby", "app.rb"], cwd="/workspace/src/email", env=env, stdout=PIPE, stderr=PIPE)
        time.sleep(3)
        
        # Verify it responds on port 8080 over HTTP
        try:
            resp = requests.get(f"http://{EMAIL_SERVICE_HOST}:{DEFAULT_HTTP_PORT}/health", timeout=2)
            assert resp.status_code == 200
            # Verify no HTTPS on port 8443
            with pytest.raises(requests.exceptions.ConnectionError):
                requests.get(f"https://{EMAIL_SERVICE_HOST}:{DEFAULT_HTTPS_PORT}/health", verify=False, timeout=2)
        finally:
            proc.terminate()
            proc.wait()

    def test_ac2_tls_enabled_uses_https_8443(self):
        """AC-2: When TLS_ENABLED=true with valid cert/key, service runs HTTPS on 8443, rejects HTTP on 8080"""
        env = os.environ.copy()
        env["EMAIL_SERVICE_TLS_ENABLED"] = "true"
        env["EMAIL_SERVICE_SSL_CERT_PATH"] = f"{TEST_CERTS_DIR}/server.crt"
        env["EMAIL_SERVICE_SSL_KEY_PATH"] = f"{TEST_CERTS_DIR}/server.key"
        
        proc = Popen(["ruby", "app.rb"], cwd="/workspace/src/email", env=env, stdout=PIPE, stderr=PIPE)
        time.sleep(3)
        
        try:
            # Verify HTTPS works on 8443
            resp = requests.get(f"https://{EMAIL_SERVICE_HOST}:{DEFAULT_HTTPS_PORT}/health", verify=False, timeout=2)
            assert resp.status_code == 200
            # Verify HTTP fails on 8080
            with pytest.raises(requests.exceptions.ConnectionError):
                requests.get(f"http://{EMAIL_SERVICE_HOST}:{DEFAULT_HTTP_PORT}/health", timeout=2)
        finally:
            proc.terminate()
            proc.wait()

    def test_ac3_tls_enabled_missing_cert_fails_start(self):
        """AC-3: When TLS_ENABLED=true but cert/key are missing/invalid, service fails to start with clear error"""
        env = os.environ.copy()
        env["EMAIL_SERVICE_TLS_ENABLED"] = "true"
        # Don't provide cert/key paths
        
        proc = Popen(["ruby", "app.rb"], cwd="/workspace/src/email", env=env, stdout=PIPE, stderr=PIPE, text=True)
        stdout, stderr = proc.communicate(timeout=5)
        
        # Verify process exited with non-zero code
        assert proc.returncode != 0
        # Verify error message mentions missing/ invalid SSL configuration
        assert "SSL" in stderr or "cert" in stderr or "key" in stderr

    def test_ac4_mtls_enabled_valid_client_cert_allowed(self):
        """AC-4 part 1: When mTLS enabled with valid CA, requests with valid client cert return 200"""
        env = os.environ.copy()
        env["EMAIL_SERVICE_TLS_ENABLED"] = "true"
        env["EMAIL_SERVICE_SSL_CERT_PATH"] = f"{TEST_CERTS_DIR}/server.crt"
        env["EMAIL_SERVICE_SSL_KEY_PATH"] = f"{TEST_CERTS_DIR}/server.key"
        env["EMAIL_SERVICE_MTLS_ENABLED"] = "true"
        env["EMAIL_SERVICE_SSL_CA_CERT_PATH"] = f"{TEST_CERTS_DIR}/ca.crt"
        
        proc = Popen(["ruby", "app.rb"], cwd="/workspace/src/email", env=env, stdout=PIPE, stderr=PIPE)
        time.sleep(3)
        
        try:
            # Request with valid client cert succeeds
            resp = requests.get(
                f"https://{EMAIL_SERVICE_HOST}:{DEFAULT_HTTPS_PORT}/health",
                cert=(f"{TEST_CERTS_DIR}/client.crt", f"{TEST_CERTS_DIR}/client.key"),
                verify=f"{TEST_CERTS_DIR}/ca.crt",
                timeout=2
            )
            assert resp.status_code == 200
        finally:
            proc.terminate()
            proc.wait()

    def test_ac4_mtls_enabled_invalid_client_cert_rejected(self):
        """AC-4 part 2: When mTLS enabled, requests without/with invalid client cert return 403"""
        env = os.environ.copy()
        env["EMAIL_SERVICE_TLS_ENABLED"] = "true"
        env["EMAIL_SERVICE_SSL_CERT_PATH"] = f"{TEST_CERTS_DIR}/server.crt"
        env["EMAIL_SERVICE_SSL_KEY_PATH"] = f"{TEST_CERTS_DIR}/server.key"
        env["EMAIL_SERVICE_MTLS_ENABLED"] = "true"
        env["EMAIL_SERVICE_SSL_CA_CERT_PATH"] = f"{TEST_CERTS_DIR}/ca.crt"
        
        proc = Popen(["ruby", "app.rb"], cwd="/workspace/src/email", env=env, stdout=PIPE, stderr=PIPE)
        time.sleep(3)
        
        try:
            # No client cert
            with pytest.raises(requests.exceptions.SSLError):
                requests.get(
                    f"https://{EMAIL_SERVICE_HOST}:{DEFAULT_HTTPS_PORT}/health",
                    verify=f"{TEST_CERTS_DIR}/ca.crt",
                    timeout=2
                )
            # Invalid client cert
            with pytest.raises(requests.exceptions.SSLError):
                requests.get(
                    f"https://{EMAIL_SERVICE_HOST}:{DEFAULT_HTTPS_PORT}/health",
                    cert=(f"{TEST_CERTS_DIR}/invalid_client.crt", f"{TEST_CERTS_DIR}/invalid_client.key"),
                    verify=f"{TEST_CERTS_DIR}/ca.crt",
                    timeout=2
                )
        finally:
            proc.terminate()
            proc.wait()

    def test_ac5_mtls_enabled_missing_ca_cert_fails_start(self):
        """AC-5: When mTLS enabled but CA cert is missing/invalid, service fails to start with clear error"""
        env = os.environ.copy()
        env["EMAIL_SERVICE_TLS_ENABLED"] = "true"
        env["EMAIL_SERVICE_SSL_CERT_PATH"] = f"{TEST_CERTS_DIR}/server.crt"
        env["EMAIL_SERVICE_SSL_KEY_PATH"] = f"{TEST_CERTS_DIR}/server.key"
        env["EMAIL_SERVICE_MTLS_ENABLED"] = "true"
        # Don't provide CA cert path
        
        proc = Popen(["ruby", "app.rb"], cwd="/workspace/src/email", env=env, stdout=PIPE, stderr=PIPE, text=True)
        stdout, stderr = proc.communicate(timeout=5)
        
        assert proc.returncode != 0
        assert "CA" in stderr or "ca" in stderr or "client certificate" in stderr

    def test_ac7_default_config_plain_http_no_certs_required(self):
        """AC-7: No TLS variables set, service runs as plain HTTP on 8080, works as before"""
        env = os.environ.copy()
        # Remove any TLS related environment variables
        for k in list(env.keys()):
            if k.startswith("EMAIL_SERVICE_TLS_") or k.startswith("EMAIL_SERVICE_SSL_") or k.startswith("EMAIL_SERVICE_MTLS_"):
                del env[k]
        
        proc = Popen(["ruby", "app.rb"], cwd="/workspace/src/email", env=env, stdout=PIPE, stderr=PIPE)
        time.sleep(3)
        
        try:
            resp = requests.get(f"http://{EMAIL_SERVICE_HOST}:{DEFAULT_HTTP_PORT}/health", timeout=2)
            assert resp.status_code == 200
        finally:
            proc.terminate()
            proc.wait()
