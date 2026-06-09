import os
import stat
import pytest
import grpc
from unittest.mock import patch, MagicMock
from src.product_reviews.product_reviews_server import main, PartialTLSConfigurationError, CertificateNotFoundError, InvalidCertificatePermissionError


def test_ac1_no_tls_env_vars_plaintext_communication():
    """AC-1: No TLS env vars -> plaintext server and client calls"""
    # Clear all TLS related env vars
    tls_vars = [k for k in os.environ if k.startswith("TLS_")]
    for var in tls_vars:
        del os.environ[var]

    with patch("src.product_reviews.product_reviews_server.grpc.server") as mock_server, \
         patch("src.product_reviews.product_reviews_server.grpc.insecure_channel") as mock_insecure_channel:
        mock_server_instance = MagicMock()
        mock_server.return_value = mock_server_instance

        main()

        # Verify server is insecure plaintext
        mock_server_instance.add_insecure_port.assert_called_once()
        mock_server_instance.add_secure_port.assert_not_called()
        # Verify client channel is insecure
        mock_insecure_channel.assert_called_once()


def test_ac2_server_tls_config_no_client_cert():
    """AC-2: Only server cert and key set -> TLS server without mTLS"""
    # Set only server TLS vars
    os.environ["TLS_SERVER_CERT_PATH"] = "/tmp/test_server.crt"
    os.environ["TLS_SERVER_KEY_PATH"] = "/tmp/test_server.key"
    # Clear other TLS vars
    for var in ["TLS_SERVER_CA_CERT_PATH", "TLS_CLIENT_CERT_PATH", "TLS_CLIENT_KEY_PATH", "TLS_CLIENT_CA_CERT_PATH"]:
        if var in os.environ:
            del os.environ[var]

    # Mock file existence and permissions
    with patch("os.path.exists") as mock_exists, \
         patch("os.stat") as mock_stat, \
         patch("builtins.open", MagicMock(read=lambda: b"test_data")), \
         patch("src.product_reviews.product_reviews_server.grpc.server") as mock_server, \
         patch("src.product_reviews.product_reviews_server.grpc.ssl_server_credentials") as mock_ssl_creds:

        mock_exists.return_value = True
        # Correct permissions: key=600, cert=644
        mock_stat.side_effect = lambda path: MagicMock(st_mode=stat.S_IRUSR | stat.S_IWUSR if "key" in path else stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

        mock_server_instance = MagicMock()
        mock_server.return_value = mock_server_instance
        mock_creds_instance = MagicMock()
        mock_ssl_creds.return_value = mock_creds_instance

        main()

        # Verify secure port is added with server credentials, no client CA (no mTLS)
        mock_ssl_creds.assert_called_once_with(root_certificates=None, certificate_key_pairs=[(b"test_data", b"test_data")], client_certificate_request=False)
        mock_server_instance.add_secure_port.assert_called_once()
        mock_server_instance.add_insecure_port.assert_not_called()


def test_ac3_server_mtls_config_enforced():
    """AC-3: Server cert, key and CA set -> mTLS enforced"""
    os.environ["TLS_SERVER_CERT_PATH"] = "/tmp/test_server.crt"
    os.environ["TLS_SERVER_KEY_PATH"] = "/tmp/test_server.key"
    os.environ["TLS_SERVER_CA_CERT_PATH"] = "/tmp/test_ca.crt"
    for var in ["TLS_CLIENT_CERT_PATH", "TLS_CLIENT_KEY_PATH", "TLS_CLIENT_CA_CERT_PATH"]:
        if var in os.environ:
            del os.environ[var]

    with patch("os.path.exists") as mock_exists, \
         patch("os.stat") as mock_stat, \
         patch("builtins.open", MagicMock(read=lambda: b"test_data")), \
         patch("src.product_reviews.product_reviews_server.grpc.server") as mock_server, \
         patch("src.product_reviews.product_reviews_server.grpc.ssl_server_credentials") as mock_ssl_creds:

        mock_exists.return_value = True
        mock_stat.side_effect = lambda path: MagicMock(st_mode=stat.S_IRUSR | stat.S_IWUSR if "key" in path else stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

        mock_server_instance = MagicMock()
        mock_server.return_value = mock_server_instance
        mock_creds_instance = MagicMock()
        mock_ssl_creds.return_value = mock_creds_instance

        main()

        # Verify mTLS is enforced: client cert required, CA provided
        mock_ssl_creds.assert_called_once_with(root_certificates=b"test_data", certificate_key_pairs=[(b"test_data", b"test_data")], client_certificate_request=True)


def test_ac4_client_tls_server_validation():
    """AC-4: Client CA set -> TLS calls to ProductCatalogService with server validation"""
    os.environ["TLS_CLIENT_CA_CERT_PATH"] = "/tmp/test_client_ca.crt"
    for var in ["TLS_SERVER_CERT_PATH", "TLS_SERVER_KEY_PATH", "TLS_SERVER_CA_CERT_PATH", "TLS_CLIENT_CERT_PATH", "TLS_CLIENT_KEY_PATH"]:
        if var in os.environ:
            del os.environ[var]

    with patch("os.path.exists") as mock_exists, \
         patch("os.stat") as mock_stat, \
         patch("builtins.open", MagicMock(read=lambda: b"test_ca_data")), \
         patch("src.product_reviews.product_reviews_server.grpc.server") as mock_server, \
         patch("src.product_reviews.product_reviews_server.grpc.ssl_channel_credentials") as mock_ssl_channel_creds, \
         patch("src.product_reviews.product_reviews_server.grpc.secure_channel") as mock_secure_channel:

        mock_exists.return_value = True
        mock_stat.return_value = MagicMock(st_mode=stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        mock_creds_instance = MagicMock()
        mock_ssl_channel_creds.return_value = mock_creds_instance
        mock_server.return_value = MagicMock()

        main()

        # Verify client uses secure channel with CA for server validation
        mock_ssl_channel_creds.assert_called_once_with(root_certificates=b"test_ca_data", certificate_key_pair=None)
        mock_secure_channel.assert_called_once()


def test_ac5_client_mtls_config():
    """AC-5: Client cert, key and CA set -> mTLS calls to upstream"""
    os.environ["TLS_CLIENT_CERT_PATH"] = "/tmp/test_client.crt"
    os.environ["TLS_CLIENT_KEY_PATH"] = "/tmp/test_client.key"
    os.environ["TLS_CLIENT_CA_CERT_PATH"] = "/tmp/test_client_ca.crt"
    for var in ["TLS_SERVER_CERT_PATH", "TLS_SERVER_KEY_PATH", "TLS_SERVER_CA_CERT_PATH"]:
        if var in os.environ:
            del os.environ[var]

    with patch("os.path.exists") as mock_exists, \
         patch("os.stat") as mock_stat, \
         patch("builtins.open", MagicMock(read=lambda: b"test_data")), \
         patch("src.product_reviews.product_reviews_server.grpc.server") as mock_server, \
         patch("src.product_reviews.product_reviews_server.grpc.ssl_channel_credentials") as mock_ssl_channel_creds, \
         patch("src.product_reviews.product_reviews_server.grpc.secure_channel") as mock_secure_channel:

        mock_exists.return_value = True
        mock_stat.side_effect = lambda path: MagicMock(st_mode=stat.S_IRUSR | stat.S_IWUSR if "key" in path else stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        mock_creds_instance = MagicMock()
        mock_ssl_channel_creds.return_value = mock_creds_instance
        mock_server.return_value = MagicMock()

        main()

        # Verify client uses mTLS credentials
        mock_ssl_channel_creds.assert_called_once_with(root_certificates=b"test_data", certificate_key_pair=(b"test_data", b"test_data"))


def test_ac6_missing_certificate_file_fails_start():
    """AC-6: Configured certificate path does not exist -> startup fails"""
    os.environ["TLS_SERVER_CERT_PATH"] = "/tmp/non_existent_cert.crt"
    os.environ["TLS_SERVER_KEY_PATH"] = "/tmp/existent_key.key"

    with patch("os.path.exists") as mock_exists:
        # Return false for cert path, true for key path
        mock_exists.side_effect = lambda path: False if "non_existent_cert" in path else True

        with pytest.raises(CertificateNotFoundError) as excinfo:
            main()
        assert "non_existent_cert.crt" in str(excinfo.value)


def test_ac7_invalid_private_key_permissions_fails_start():
    """AC-7: Private key has permissions > 0o600 -> startup fails"""
    os.environ["TLS_SERVER_CERT_PATH"] = "/tmp/server.crt"
    os.environ["TLS_SERVER_KEY_PATH"] = "/tmp/server.key"

    with patch("os.path.exists") as mock_exists, \
         patch("os.stat") as mock_stat:
        mock_exists.return_value = True
        # Key has 0o644 permissions (too open)
        mock_stat.side_effect = lambda path: MagicMock(st_mode=stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH if "key" in path else stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

        with pytest.raises(InvalidCertificatePermissionError) as excinfo:
            main()
        assert "server.key" in str(excinfo.value)
        assert "600" in str(excinfo.value)


def test_ac8_partial_tls_config_fails_start():
    """AC-8: Partial TLS config (e.g. server key without cert) -> startup fails"""
    os.environ["TLS_SERVER_KEY_PATH"] = "/tmp/server.key"
    # TLS_SERVER_CERT_PATH not set -> partial config
    if "TLS_SERVER_CERT_PATH" in os.environ:
        del os.environ["TLS_SERVER_CERT_PATH"]

    with patch("os.path.exists") as mock_exists:
        mock_exists.return_value = True
        with pytest.raises(PartialTLSConfigurationError) as excinfo:
            main()
        assert "TLS_SERVER_CERT_PATH" in str(excinfo.value)


def test_ac9_functional_behavior_unchanged_with_tls():
    """AC-9: Existing functional behavior unchanged when TLS is enabled"""
    # Set full TLS config for server and client
    os.environ["TLS_SERVER_CERT_PATH"] = "/tmp/server.crt"
    os.environ["TLS_SERVER_KEY_PATH"] = "/tmp/server.key"
    os.environ["TLS_CLIENT_CERT_PATH"] = "/tmp/client.crt"
    os.environ["TLS_CLIENT_KEY_PATH"] = "/tmp/client.key"
    os.environ["TLS_CLIENT_CA_CERT_PATH"] = "/tmp/ca.crt"

    with patch("os.path.exists") as mock_exists, \
         patch("os.stat") as mock_stat, \
         patch("builtins.open", MagicMock(read=lambda: b"test_data")), \
         patch("src.product_reviews.product_reviews_server.ProductCatalogServiceStub") as mock_stub, \
         patch("src.product_reviews.product_reviews_server.add_ProductReviewsServiceServicer_to_server") as mock_add_servicer:

        mock_exists.return_value = True
        mock_stat.side_effect = lambda path: MagicMock(st_mode=stat.S_IRUSR | stat.S_IWUSR if "key" in path else stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        mock_server_instance = MagicMock()
        with patch("src.product_reviews.product_reviews_server.grpc.server", return_value=mock_server_instance):
            main()

        # Verify existing servicer is added unchanged
        mock_add_servicer.assert_called_once()
        # Verify ProductCatalogStub is created same as before (only channel changes)
        mock_stub.assert_called_once()
