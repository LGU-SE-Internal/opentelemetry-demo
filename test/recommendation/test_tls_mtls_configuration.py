import grpc
import os
import pytest
from unittest.mock import Mock, patch, mock_open
from src.recommendation import recommendation_server

# Test constants from spec
VALID_TLS_MODES = ["disabled", "tls", "mtls"]
INVALID_TLS_MODE = "invalid_mode"
MOCK_CERT_CONTENT = "-----BEGIN CERTIFICATE-----\nmockcert\n-----END CERTIFICATE-----"
MOCK_KEY_CONTENT = "-----BEGIN PRIVATE KEY-----\nmockkey\n-----END PRIVATE KEY-----"
MOCK_CA_CONTENT = "-----BEGIN CERTIFICATE-----\nmockca\n-----END CERTIFICATE-----"


class TestRecommendationServiceTlsMtls:
    @pytest.fixture(autouse=True)
    def cleanup_env(self):
        """Clean up environment variables before and after each test"""
        env_vars = [
            "RECOMMENDATION_SERVICE_TLS_MODE",
            "RECOMMENDATION_SERVICE_TLS_CERT_PATH",
            "RECOMMENDATION_SERVICE_TLS_KEY_PATH",
            "RECOMMENDATION_SERVICE_TLS_CA_CERT_PATH",
            "PRODUCT_CATALOG_SERVICE_TLS_ENABLED",
            "PRODUCT_CATALOG_SERVICE_TLS_CA_CERT_PATH"
        ]
        original_env = {var: os.environ.get(var) for var in env_vars}
        for var in env_vars:
            os.environ.pop(var, None)
        yield
        for var, value in original_env.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value

    # AC-1 Tests
    def test_ac1_tls_mode_disabled_uses_plaintext_server(self):
        """AC-1: When TLS mode is disabled, gRPC server uses plaintext insecure connection"""
        os.environ["RECOMMENDATION_SERVICE_TLS_MODE"] = "disabled"
        with patch("src.recommendation.recommendation_server.grpc.server") as mock_grpc_server:
            mock_server_instance = Mock()
            mock_grpc_server.return_value = mock_server_instance
            
            # Start server (this is what runs on service startup)
            recommendation_server.create_grpc_server()
            
            # Verify insecure port was added, no TLS config used
            mock_server_instance.add_insecure_port.assert_called_once()
            mock_server_instance.add_secure_port.assert_not_called()

    def test_ac1_no_tls_mode_env_var_uses_plaintext_server(self):
        """AC-1: When TLS mode env var is not set, defaults to disabled with plaintext connection"""
        # Explicitly do not set RECOMMENDATION_SERVICE_TLS_MODE
        with patch("src.recommendation.recommendation_server.grpc.server") as mock_grpc_server:
            mock_server_instance = Mock()
            mock_grpc_server.return_value = mock_server_instance
            
            recommendation_server.create_grpc_server()
            
            mock_server_instance.add_insecure_port.assert_called_once()
            mock_server_instance.add_secure_port.assert_not_called()

    # AC-2 Tests
    def test_ac2_tls_mode_enabled_uses_tls_server(self):
        """AC-2: When TLS mode is 'tls' with valid cert/key, server accepts only TLS connections"""
        os.environ["RECOMMENDATION_SERVICE_TLS_MODE"] = "tls"
        os.environ["RECOMMENDATION_SERVICE_TLS_CERT_PATH"] = "/path/to/cert.pem"
        os.environ["RECOMMENDATION_SERVICE_TLS_KEY_PATH"] = "/path/to/key.pem"
        
        with patch("builtins.open", mock_open(read_data=MOCK_CERT_CONTENT)), \
             patch("src.recommendation.recommendation_server.grpc.server") as mock_grpc_server, \
             patch("src.recommendation.recommendation_server.grpc.ssl_server_credentials") as mock_ssl_creds:
            
            mock_server_instance = Mock()
            mock_grpc_server.return_value = mock_server_instance
            mock_creds_instance = Mock()
            mock_ssl_creds.return_value = mock_creds_instance
            
            recommendation_server.create_grpc_server()
            
            # Verify secure port added, no insecure port
            mock_server_instance.add_secure_port.assert_called_once()
            mock_server_instance.add_insecure_port.assert_not_called()
            # Verify no client certificate required for TLS mode
            mock_ssl_creds.assert_called_once_with(
                [(MOCK_CERT_CONTENT.encode(), MOCK_KEY_CONTENT.encode())],
                root_certificates=None,
                require_client_auth=False
            )

    def test_ac2_tls_mode_rejects_plaintext_connections(self):
        """AC-2: TLS mode server rejects plaintext connection attempts"""
        os.environ["RECOMMENDATION_SERVICE_TLS_MODE"] = "tls"
        os.environ["RECOMMENDATION_SERVICE_TLS_CERT_PATH"] = "/path/to/cert.pem"
        os.environ["RECOMMENDATION_SERVICE_TLS_KEY_PATH"] = "/path/to/key.pem"
        
        with patch("builtins.open", mock_open(read_data=MOCK_CERT_CONTENT)), \
             patch("src.recommendation.recommendation_server.grpc.server") as mock_grpc_server, \
             patch("src.recommendation.recommendation_server.grpc.ssl_server_credentials") as mock_ssl_creds:
            
            mock_server_instance = Mock()
            mock_grpc_server.return_value = mock_server_instance
            mock_creds_instance = Mock()
            mock_ssl_creds.return_value = mock_creds_instance
            
            recommendation_server.create_grpc_server()
            
            # Verify server is not listening on insecure port
            assert mock_server_instance.add_insecure_port.call_count == 0

    # AC-3 Tests
    def test_ac3_mtls_mode_enables_client_cert_auth(self):
        """AC-3a/3d: mTLS mode requires valid client certificate signed by CA"""
        os.environ["RECOMMENDATION_SERVICE_TLS_MODE"] = "mtls"
        os.environ["RECOMMENDATION_SERVICE_TLS_CERT_PATH"] = "/path/to/cert.pem"
        os.environ["RECOMMENDATION_SERVICE_TLS_KEY_PATH"] = "/path/to/key.pem"
        os.environ["RECOMMENDATION_SERVICE_TLS_CA_CERT_PATH"] = "/path/to/ca.pem"
        
        with patch("builtins.open", mock_open(read_data=MOCK_CERT_CONTENT)) as mock_file, \
             patch("src.recommendation.recommendation_server.grpc.server") as mock_grpc_server, \
             patch("src.recommendation.recommendation_server.grpc.ssl_server_credentials") as mock_ssl_creds:
            
            # Handle multiple file reads: cert, key, CA
            mock_file.side_effect = [
                mock_open(read_data=MOCK_CERT_CONTENT).return_value,
                mock_open(read_data=MOCK_KEY_CONTENT).return_value,
                mock_open(read_data=MOCK_CA_CONTENT).return_value
            ]
            
            mock_server_instance = Mock()
            mock_grpc_server.return_value = mock_server_instance
            mock_creds_instance = Mock()
            mock_ssl_creds.return_value = mock_creds_instance
            
            recommendation_server.create_grpc_server()
            
            # Verify secure port only, client auth required with CA
            mock_server_instance.add_secure_port.assert_called_once()
            mock_server_instance.add_insecure_port.assert_not_called()
            mock_ssl_creds.assert_called_once_with(
                [(MOCK_CERT_CONTENT.encode(), MOCK_KEY_CONTENT.encode())],
                root_certificates=MOCK_CA_CONTENT.encode(),
                require_client_auth=True
            )

    def test_ac3_mtls_rejects_plaintext_connections(self):
        """AC-3a: mTLS mode rejects plaintext connection attempts"""
        os.environ["RECOMMENDATION_SERVICE_TLS_MODE"] = "mtls"
        os.environ["RECOMMENDATION_SERVICE_TLS_CERT_PATH"] = "/path/to/cert.pem"
        os.environ["RECOMMENDATION_SERVICE_TLS_KEY_PATH"] = "/path/to/key.pem"
        os.environ["RECOMMENDATION_SERVICE_TLS_CA_CERT_PATH"] = "/path/to/ca.pem"
        
        with patch("builtins.open", mock_open(read_data=MOCK_CERT_CONTENT)) as mock_file, \
             patch("src.recommendation.recommendation_server.grpc.server") as mock_grpc_server, \
             patch("src.recommendation.recommendation_server.grpc.ssl_server_credentials") as mock_ssl_creds:
            
            mock_file.side_effect = [
                mock_open(read_data=MOCK_CERT_CONTENT).return_value,
                mock_open(read_data=MOCK_KEY_CONTENT).return_value,
                mock_open(read_data=MOCK_CA_CONTENT).return_value
            ]
            
            mock_server_instance = Mock()
            mock_grpc_server.return_value = mock_server_instance
            mock_ssl_creds.return_value = Mock()
            
            recommendation_server.create_grpc_server()
            
            assert mock_server_instance.add_insecure_port.call_count == 0

    # AC-4 Tests
    def test_ac4_product_catalog_tls_disabled_uses_plaintext(self):
        """AC-4: When product catalog TLS is disabled, uses plaintext client connection"""
        os.environ["PRODUCT_CATALOG_SERVICE_TLS_ENABLED"] = "false"
        with patch("src.recommendation.recommendation_server.grpc.insecure_channel") as mock_insecure_channel, \
             patch("src.recommendation.recommendation_server.grpc.secure_channel") as mock_secure_channel:
            
            recommendation_server.create_product_catalog_client()
            
            mock_insecure_channel.assert_called_once()
            mock_secure_channel.assert_not_called()

    # AC-5 Tests
    def test_ac5_product_catalog_tls_enabled_uses_tls_connection(self):
        """AC-5a: When product catalog TLS is enabled, uses TLS client connection"""
        os.environ["PRODUCT_CATALOG_SERVICE_TLS_ENABLED"] = "true"
        with patch("src.recommendation.recommendation_server.grpc.secure_channel") as mock_secure_channel, \
             patch("src.recommendation.recommendation_server.grpc.insecure_channel") as mock_insecure_channel, \
             patch("src.recommendation.recommendation_server.grpc.ssl_channel_credentials") as mock_ssl_creds:
            
            mock_creds_instance = Mock()
            mock_ssl_creds.return_value = mock_creds_instance
            
            recommendation_server.create_product_catalog_client()
            
            mock_secure_channel.assert_called_once()
            mock_insecure_channel.assert_not_called()
            # Default system CA used when no custom CA path provided
            mock_ssl_creds.assert_called_once_with(root_certificates=None)

    def test_ac5_product_catalog_custom_ca_verifies_server_cert(self):
        """AC-5b: Custom CA path for product catalog TLS enables certificate verification against CA"""
        os.environ["PRODUCT_CATALOG_SERVICE_TLS_ENABLED"] = "true"
        os.environ["PRODUCT_CATALOG_SERVICE_TLS_CA_CERT_PATH"] = "/path/to/product_ca.pem"
        
        with patch("builtins.open", mock_open(read_data=MOCK_CA_CONTENT)), \
             patch("src.recommendation.recommendation_server.grpc.secure_channel") as mock_secure_channel, \
             patch("src.recommendation.recommendation_server.grpc.ssl_channel_credentials") as mock_ssl_creds:
            
            mock_creds_instance = Mock()
            mock_ssl_creds.return_value = mock_creds_instance
            
            recommendation_server.create_product_catalog_client()
            
            mock_ssl_creds.assert_called_once_with(root_certificates=MOCK_CA_CONTENT.encode())

    # AC-6 Tests
    def test_ac6_tls_mode_missing_cert_fails_fast(self):
        """AC-6: TLS mode enabled with missing cert path fails on startup"""
        os.environ["RECOMMENDATION_SERVICE_TLS_MODE"] = "tls"
        # Do not set cert path
        
        with pytest.raises(Exception) as exc_info:
            recommendation_server.create_grpc_server()
        
        assert "certificate" in str(exc_info.value).lower()

    def test_ac6_tls_mode_missing_key_fails_fast(self):
        """AC-6: TLS mode enabled with missing key path fails on startup"""
        os.environ["RECOMMENDATION_SERVICE_TLS_MODE"] = "tls"
        os.environ["RECOMMENDATION_SERVICE_TLS_CERT_PATH"] = "/path/to/cert.pem"
        # Do not set key path
        
        with pytest.raises(Exception) as exc_info:
            recommendation_server.create_grpc_server()
        
        assert "key" in str(exc_info.value).lower()

    def test_ac6_mtls_mode_missing_ca_fails_fast(self):
        """AC-6: mTLS mode enabled with missing CA path fails on startup"""
        os.environ["RECOMMENDATION_SERVICE_TLS_MODE"] = "mtls"
        os.environ["RECOMMENDATION_SERVICE_TLS_CERT_PATH"] = "/path/to/cert.pem"
        os.environ["RECOMMENDATION_SERVICE_TLS_KEY_PATH"] = "/path/to/key.pem"
        # Do not set CA path
        
        with pytest.raises(Exception) as exc_info:
            recommendation_server.create_grpc_server()
        
        assert "ca" in str(exc_info.value).lower() or "certificate authority" in str(exc_info.value).lower()

    def test_ac6_invalid_tls_mode_logs_warning_defaults_to_disabled(self, caplog):
        """AC-6: Invalid TLS mode logs warning and defaults to disabled (plaintext)"""
        os.environ["RECOMMENDATION_SERVICE_TLS_MODE"] = INVALID_TLS_MODE
        
        with patch("src.recommendation.recommendation_server.grpc.server") as mock_grpc_server, \
             patch("src.recommendation.recommendation_server.logger") as mock_logger:
            
            mock_server_instance = Mock()
            mock_grpc_server.return_value = mock_server_instance
            
            recommendation_server.create_grpc_server()
            
            # Verify warning logged
            mock_logger.warning.assert_called_once()
            assert "invalid" in str(mock_logger.warning.call_args).lower()
            assert "tls_mode" in str(mock_logger.warning.call_args).lower()
            # Verify defaulted to plaintext
            mock_server_instance.add_insecure_port.assert_called_once()
