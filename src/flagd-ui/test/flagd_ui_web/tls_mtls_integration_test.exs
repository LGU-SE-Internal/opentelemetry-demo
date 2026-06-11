defmodule FlagdUiWeb.TlsMtlsIntegrationTest do
  use ExUnit.Case, async: false
  import ExUnit.CaptureLog

  @moduletag :integration

  # Helper to start service with env vars
  defp start_service_with_env(env) do
    original_env = System.get_env()
    Enum.each(env, fn {k, v} -> System.put_env(k, v) end)
    
    result = Application.ensure_all_started(:flagd_ui)
    
    # Restore original env after test
    Enum.each(env, fn {k, _v} -> System.delete_env(k) end)
    Enum.each(original_env, fn {k, v} -> System.put_env(k, v) end)
    
    result
  end

  # AC-1: Default configuration no TLS
  test "ac1_default_config_uses_unencrypted_http" do
    # Reset all TLS env vars
    env = %{
      "FLAGD_UI_TLS_ENABLED" => "false",
      "FLAGD_UI_TLS_CERT_PATH" => "",
      "FLAGD_UI_TLS_KEY_PATH" => "",
      "FLAGD_UI_MTLS_ENABLED" => "false",
      "FLAGD_UI_MTLS_CA_PATH" => ""
    }

    {:ok, _} = start_service_with_env(env)
    
    # Test HTTP connection works
    {:ok, %HTTPoison.Response{status_code: 200}} = HTTPoison.get("http://localhost:4000/")
    
    # Test HTTPS connection is not available
    assert {:error, _} = HTTPoison.get("https://localhost:4000/", [], ssl: [verify: :verify_none])
  end

  # AC-2: TLS enabled with valid cert/key, serves HTTPS only
  test "ac2_tls_enabled_serves_https_only" do
    cert_path = Path.expand("../fixtures/tls/valid_server.crt", __DIR__)
    key_path = Path.expand("../fixtures/tls/valid_server.key", __DIR__)

    env = %{
      "FLAGD_UI_TLS_ENABLED" => "true",
      "FLAGD_UI_TLS_CERT_PATH" => cert_path,
      "FLAGD_UI_TLS_KEY_PATH" => key_path
    }

    {:ok, _} = start_service_with_env(env)
    
    # Test HTTPS connection works
    {:ok, %HTTPoison.Response{status_code: 200}} = HTTPoison.get("https://localhost:4000/", [], ssl: [verify: :verify_none])
    
    # Test plain HTTP connection is rejected
    assert {:error, %HTTPoison.Error{reason: :econnrefused}} = HTTPoison.get("http://localhost:4000/")
    
    # Test WebSocket WSS works, WS fails
    assert {:ok, _} = Mint.WebSocket.connect(:wss, "localhost", 4000, "/ws", [], ssl: [verify: :verify_none])
    assert {:error, _} = Mint.WebSocket.connect(:ws, "localhost", 4000, "/ws")
  end

  # AC-3: TLS enabled with missing cert file
  test "ac3_tls_enabled_missing_cert_fails_startup" do
    env = %{
      "FLAGD_UI_TLS_ENABLED" => "true",
      "FLAGD_UI_TLS_CERT_PATH" => "/nonexistent/path/cert.crt",
      "FLAGD_UI_TLS_KEY_PATH" => "/some/path/key.key"
    }

    log = capture_log(fn ->
      assert {:error, _} = start_service_with_env(env)
    end)

    assert log =~ "TLS certificate not found at path /nonexistent/path/cert.crt"
    # Check exit code would be 1 (we simulate via startup failure)
  end

  # AC-4: TLS enabled with mismatched cert/key
  test "ac4_tls_enabled_mismatched_cert_key_fails_startup" do
    cert_path = Path.expand("../fixtures/tls/valid_server.crt", __DIR__)
    key_path = Path.expand("../fixtures/tls/unrelated_server.key", __DIR__)

    env = %{
      "FLAGD_UI_TLS_ENABLED" => "true",
      "FLAGD_UI_TLS_CERT_PATH" => cert_path,
      "FLAGD_UI_TLS_KEY_PATH" => key_path
    }

    log = capture_log(fn ->
      assert {:error, _} = start_service_with_env(env)
    end)

    assert log =~ "Invalid TLS certificate/key pair"
  end

  # AC-5: mTLS enabled, validates client certificates
  test "ac5_mtls_enabled_validates_client_certs" do
    cert_path = Path.expand("../fixtures/tls/valid_server.crt", __DIR__)
    key_path = Path.expand("../fixtures/tls/valid_server.key", __DIR__)
    ca_path = Path.expand("../fixtures/tls/ca_bundle.crt", __DIR__)
    valid_client_cert = Path.expand("../fixtures/tls/valid_client.crt", __DIR__)
    valid_client_key = Path.expand("../fixtures/tls/valid_client.key", __DIR__)
    invalid_client_cert = Path.expand("../fixtures/tls/self_signed_client.crt", __DIR__)
    invalid_client_key = Path.expand("../fixtures/tls/self_signed_client.key", __DIR__)

    env = %{
      "FLAGD_UI_TLS_ENABLED" => "true",
      "FLAGD_UI_TLS_CERT_PATH" => cert_path,
      "FLAGD_UI_TLS_KEY_PATH" => key_path,
      "FLAGD_UI_MTLS_ENABLED" => "true",
      "FLAGD_UI_MTLS_CA_PATH" => ca_path
    }

    {:ok, _} = start_service_with_env(env)
    
    # Input 1: No client cert -> 403
    {:ok, %HTTPoison.Response{status_code: 403}} = HTTPoison.get("https://localhost:4000/", [], ssl: [verify: :verify_none])
    
    # Input 2: Valid client cert -> 200
    ssl_opts_valid = [
      certfile: valid_client_cert,
      keyfile: valid_client_key,
      verify: :verify_none
    ]
    {:ok, %HTTPoison.Response{status_code: 200}} = HTTPoison.get("https://localhost:4000/", [], ssl: ssl_opts_valid)
    
    # Input 3: Self-signed client cert not in CA bundle -> 403
    ssl_opts_invalid = [
      certfile: invalid_client_cert,
      keyfile: invalid_client_key,
      verify: :verify_none
    ]
    {:ok, %HTTPoison.Response{status_code: 403}} = HTTPoison.get("https://localhost:4000/", [], ssl: ssl_opts_invalid)
  end

  # AC-6: mTLS enabled with missing CA path
  test "ac6_mtls_enabled_missing_ca_path_fails_startup" do
    cert_path = Path.expand("../fixtures/tls/valid_server.crt", __DIR__)
    key_path = Path.expand("../fixtures/tls/valid_server.key", __DIR__)

    env = %{
      "FLAGD_UI_TLS_ENABLED" => "true",
      "FLAGD_UI_TLS_CERT_PATH" => cert_path,
      "FLAGD_UI_TLS_KEY_PATH" => key_path,
      "FLAGD_UI_MTLS_ENABLED" => "true",
      "FLAGD_UI_MTLS_CA_PATH" => ""
    }

    log = capture_log(fn ->
      assert {:error, _} = start_service_with_env(env)
    end)

    assert log =~ "mTLS enabled but FLAGD_UI_MTLS_CA_PATH must be provided"
  end

  # AC-7: All existing functionality works same over TLS
  test "ac7_existing_functionality_unchanged_over_tls" do
    cert_path = Path.expand("../fixtures/tls/valid_server.crt", __DIR__)
    key_path = Path.expand("../fixtures/tls/valid_server.key", __DIR__)
    ca_path = Path.expand("../fixtures/tls/ca_bundle.crt", __DIR__)
    client_cert = Path.expand("../fixtures/tls/valid_client.crt", __DIR__)
    client_key = Path.expand("../fixtures/tls/valid_client.key", __DIR__)

    env = %{
      "FLAGD_UI_TLS_ENABLED" => "true",
      "FLAGD_UI_TLS_CERT_PATH" => cert_path,
      "FLAGD_UI_TLS_KEY_PATH" => key_path,
      "FLAGD_UI_MTLS_ENABLED" => "true",
      "FLAGD_UI_MTLS_CA_PATH" => ca_path
    }

    {:ok, _} = start_service_with_env(env)
    ssl_opts = [certfile: client_cert, keyfile: client_key, verify: :verify_none]

    # Test UI load
    {:ok, %HTTPoison.Response{status_code: 200, body: body}} = HTTPoison.get("https://localhost:4000/", [], ssl: ssl_opts)
    assert body =~ "<!DOCTYPE html>"

    # Test flag query endpoint
    {:ok, %HTTPoison.Response{status_code: 200}} = HTTPoison.get("https://localhost:4000/api/flags", [], ssl: ssl_opts)

    # Test health endpoint
    {:ok, %HTTPoison.Response{status_code: 200}} = HTTPoison.get("https://localhost:4000/health", [], ssl: ssl_opts)

    # Test WebSocket real-time updates
    {:ok, conn} = Mint.WebSocket.connect(:wss, "localhost", 4000, "/ws/flags", [], ssl: ssl_opts)
    assert is_pid(conn)
  end
end
