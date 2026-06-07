defmodule TlsIntegrationTest do
  use ExUnit.Case
  import ExUnit.CaptureLog

  @default_http_port 4000
  @default_https_port 8443

  setup do
    # Reset all TLS related env vars before each test
    System.delete_env("FLAGD_UI_TLS_ENABLED")
    System.delete_env("FLAGD_UI_TLS_PORT")
    System.delete_env("FLAGD_UI_TLS_CERT_PATH")
    System.delete_env("FLAGD_UI_TLS_KEY_PATH")
    System.delete_env("FLAGD_UI_MTLS_ENABLED")
    System.delete_env("FLAGD_UI_MTLS_CA_CERT_PATH")

    # Clean up any running instances
    Application.stop(:flagd_ui)
    :ok
  end

  test "ac1_tls_disabled_serves_http_on_default_port" do
    # AC-1: TLS disabled, service runs on port 4000 with HTTP
    System.put_env("FLAGD_UI_TLS_ENABLED", "false")

    # Start the application
    assert capture_log(fn ->
      assert :ok = Application.start(:flagd_ui)
    end) =~ "Running FlagdUiWeb.Endpoint with cowboy 2.0 at 0.0.0.0:#{@default_http_port} (http)"

    # Verify HTTP connection works
    assert {:ok, %{status: 200}} = :httpc.request('http://localhost:#{@default_http_port}/health')

    # Verify no HTTPS listener
    assert {:error, :econnrefused} = :ssl.connect('localhost', @default_https_port, [], 1000)
  end

  test "ac2_tls_enabled_serves_https_on_configured_port" do
    # AC-2a: TLS enabled with valid cert/key, starts on HTTPS port
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", "test/fixtures/tls/valid_cert.pem")
    System.put_env("FLAGD_UI_TLS_KEY_PATH", "test/fixtures/tls/valid_key.pem")

    assert capture_log(fn ->
      assert :ok = Application.start(:flagd_ui)
    end) =~ "Running FlagdUiWeb.Endpoint with cowboy 2.0 at 0.0.0.0:#{@default_https_port} (https)"

    # AC-2c: No HTTP listener on port 4000
    assert {:error, :econnrefused} = :httpc.request('http://localhost:#{@default_http_port}/health')

    # AC-2b: HTTPS endpoints work correctly
    assert {:ok, %{status: 200}} = :httpc.request(
      :get,
      {'https://localhost:#{@default_https_port}/health', []},
      [ssl: [verify: :verify_none]],
      []
    )
  end

  test "ac3_tls_enabled_missing_cert_fails_startup" do
    # AC-3: Missing cert file when TLS enabled fails startup
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_KEY_PATH", "test/fixtures/tls/valid_key.pem")
    # No cert path set

    log = capture_log(fn ->
      assert {:error, _} = Application.start(:flagd_ui)
    end)

    assert log =~ "FATAL"
    assert log =~ "TLS certificate file not found" or log =~ "FLAGD_UI_TLS_CERT_PATH is required when TLS is enabled"
  end

  test "ac3_tls_enabled_invalid_cert_fails_startup" do
    # AC-3: Invalid cert data fails startup
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", "test/fixtures/tls/invalid_cert.pem")
    System.put_env("FLAGD_UI_TLS_KEY_PATH", "test/fixtures/tls/valid_key.pem")

    log = capture_log(fn ->
      assert {:error, _} = Application.start(:flagd_ui)
    end)

    assert log =~ "FATAL"
    assert log =~ "invalid PEM data" or log =~ "Failed to read TLS certificate file"
  end

  test "ac4_mtls_enabled_valid_client_cert_works" do
    # AC-4a: mTLS enabled, valid client cert works
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", "test/fixtures/tls/valid_cert.pem")
    System.put_env("FLAGD_UI_TLS_KEY_PATH", "test/fixtures/tls/valid_key.pem")
    System.put_env("FLAGD_UI_MTLS_ENABLED", "true")
    System.put_env("FLAGD_UI_MTLS_CA_CERT_PATH", "test/fixtures/tls/ca_cert.pem")

    assert :ok = Application.start(:flagd_ui)

    # Request with valid client cert
    {:ok, client_cert} = File.read("test/fixtures/tls/client_valid_cert.pem")
    {:ok, client_key} = File.read("test/fixtures/tls/client_valid_key.pem")

    assert {:ok, %{status: 200}} = :httpc.request(
      :get,
      {'https://localhost:#{@default_https_port}/health', []},
      [
        ssl: [
          cert: client_cert,
          key: {:PEM, client_key, ""},
          cacertfile: "test/fixtures/tls/ca_cert.pem"
        ]
      ],
      []
    )
  end

  test "ac4_mtls_enabled_invalid_client_cert_returns_403" do
    # AC-4b: mTLS enabled, invalid client cert returns 403
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", "test/fixtures/tls/valid_cert.pem")
    System.put_env("FLAGD_UI_TLS_KEY_PATH", "test/fixtures/tls/valid_key.pem")
    System.put_env("FLAGD_UI_MTLS_ENABLED", "true")
    System.put_env("FLAGD_UI_MTLS_CA_CERT_PATH", "test/fixtures/tls/ca_cert.pem")

    assert :ok = Application.start(:flagd_ui)

    # Request without client cert
    assert {:ok, %{status: 403}} = :httpc.request(
      :get,
      {'https://localhost:#{@default_https_port}/health', []},
      [ssl: [verify: :verify_none]],
      []
    )

    # Request with invalid client cert
    {:ok, invalid_cert} = File.read("test/fixtures/tls/client_invalid_cert.pem")
    {:ok, invalid_key} = File.read("test/fixtures/tls/client_invalid_key.pem")

    assert {:ok, %{status: 403}} = :httpc.request(
      :get,
      {'https://localhost:#{@default_https_port}/health', []},
      [
        ssl: [
          cert: invalid_cert,
          key: {:PEM, invalid_key, ""},
          verify: :verify_none
        ]
      ],
      []
    )
  end

  test "ac5_mtls_enabled_without_tls_fails_startup" do
    # AC-5: mTLS enabled without TLS fails startup
    System.put_env("FLAGD_UI_TLS_ENABLED", "false")
    System.put_env("FLAGD_UI_MTLS_ENABLED", "true")

    log = capture_log(fn ->
      assert {:error, _} = Application.start(:flagd_ui)
    end)

    assert log =~ "FATAL"
    assert log =~ "mTLS cannot be enabled without enabling TLS first"
  end

  test "ac6_mtls_enabled_missing_ca_cert_fails_startup" do
    # AC-6: Missing CA cert when mTLS enabled fails startup
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", "test/fixtures/tls/valid_cert.pem")
    System.put_env("FLAGD_UI_TLS_KEY_PATH", "test/fixtures/tls/valid_key.pem")
    System.put_env("FLAGD_UI_MTLS_ENABLED", "true")
    # No CA cert path set

    log = capture_log(fn ->
      assert {:error, _} = Application.start(:flagd_ui)
    end)

    assert log =~ "FATAL"
    assert log =~ "MTLS_CA_CERT_PATH is required" or log =~ "CA certificate file not found"
  end
end
