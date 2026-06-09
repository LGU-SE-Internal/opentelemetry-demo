defmodule FlagdUi.TLSIntegrationTest do
  use ExUnit.Case, async: false
  import ExUnit.CaptureLog

  @default_port Application.compile_env(:flagd_ui, :http_port, 8080)
  @valid_cert_path "test/fixtures/tls/valid_server_cert.pem"
  @valid_key_path "test/fixtures/tls/valid_server_key.pem"
  @valid_ca_path "test/fixtures/tls/valid_ca_cert.pem"
  @invalid_cert_path "test/fixtures/tls/invalid_cert.pem"
  @invalid_key_path "test/fixtures/tls/invalid_key.pem"
  @non_existent_path "test/fixtures/tls/does_not_exist.pem"

  setup do
    # Clear all TLS related env vars before each test
    System.delete_env("FLAGD_UI_TLS_ENABLED")
    System.delete_env("FLAGD_UI_TLS_CERT_PATH")
    System.delete_env("FLAGD_UI_TLS_KEY_PATH")
    System.delete_env("FLAGD_UI_TLS_MTLS_ENABLED")
    System.delete_env("FLAGD_UI_TLS_CA_CERT_PATH")
    :ok
  end

  # AC-1: When TLS_ENABLED is not set or false, service starts HTTP
  test "ac1_tls_disabled_starts_http_server_successfully" do
    # Given: TLS is disabled (default state)
    # When: Service starts
    {:ok, pid} = start_supervised(FlagdUI.Application)

    # Then: Listens for HTTP connections
    {:ok, conn} = :gen_tcp.connect('localhost', @default_port, [:binary, active: false])
    :gen_tcp.send(conn, "GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert {:ok, data} = :gen_tcp.recv(conn, 0, 5000)
    assert data =~ "HTTP/1.1"
    refute data =~ "SSL"
    :gen_tcp.close(conn)

    stop_supervised(pid)
  end

  # AC-2: TLS enabled with valid cert/key runs HTTPS, rejects HTTP
  test "ac2_tls_enabled_with_valid_cert_key_starts_https_server" do
    # Given: TLS enabled with valid cert and key paths
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", @valid_cert_path)
    System.put_env("FLAGD_UI_TLS_KEY_PATH", @valid_key_path)

    # When: Service starts
    {:ok, pid} = start_supervised(FlagdUI.Application)

    # Then: HTTPS connection succeeds
    assert {:ok, _} = :ssl.connect('localhost', @default_port, [], 5000)

    # And: Plain HTTP connection fails
    {:ok, conn} = :gen_tcp.connect('localhost', @default_port, [:binary, active: false])
    :gen_tcp.send(conn, "GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert {:error, :closed} = :gen_tcp.recv(conn, 0, 5000)
    :gen_tcp.close(conn)

    stop_supervised(pid)
  end

  # AC-3: TLS enabled but missing cert/key paths fails startup
  test "ac3_tls_enabled_missing_cert_key_fails_startup" do
    # Given: TLS enabled but no cert or key paths provided
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")

    # When/Then: Service fails to start with correct error
    log = capture_log(fn ->
      assert {:error, reason} = start_supervised(FlagdUI.Application)
      assert reason =~ "TLS enabled but required configuration parameters (FLAGD_UI_TLS_CERT_PATH, FLAGD_UI_TLS_KEY_PATH) are missing"
    end)
    assert log =~ "exit code 1"
  end

  # AC-4: TLS enabled with invalid cert file fails startup
  test "ac4_tls_enabled_invalid_cert_file_fails_startup" do
    # Given: TLS enabled with invalid cert path
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", @non_existent_path)
    System.put_env("FLAGD_UI_TLS_KEY_PATH", @valid_key_path)

    # When/Then: Service fails to start with invalid certificate error
    log = capture_log(fn ->
      assert {:error, reason} = start_supervised(FlagdUI.Application)
      assert reason =~ "invalid certificate file"
    end)
    assert log =~ "exit code 1"

    # Test with existing but invalid cert
    System.put_env("FLAGD_UI_TLS_CERT_PATH", @invalid_cert_path)
    log = capture_log(fn ->
      assert {:error, reason} = start_supervised(FlagdUI.Application)
      assert reason =~ "invalid certificate file"
    end)
    assert log =~ "exit code 1"
  end

  # AC-5: TLS enabled with invalid key file fails startup
  test "ac5_tls_enabled_invalid_key_file_fails_startup" do
    # Given: TLS enabled with invalid key path
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", @valid_cert_path)
    System.put_env("FLAGD_UI_TLS_KEY_PATH", @non_existent_path)

    # When/Then: Service fails to start with invalid private key error
    log = capture_log(fn ->
      assert {:error, reason} = start_supervised(FlagdUI.Application)
      assert reason =~ "invalid private key file"
    end)
    assert log =~ "exit code 1"

    # Test with existing but invalid key
    System.put_env("FLAGD_UI_TLS_KEY_PATH", @invalid_key_path)
    log = capture_log(fn ->
      assert {:error, reason} = start_supervised(FlagdUI.Application)
      assert reason =~ "invalid private key file"
    end)
    assert log =~ "exit code 1"
  end

  # AC-6: mTLS enabled requires valid client certificate
  test "ac6_mtls_enabled_requires_valid_client_certificate" do
    # Given: TLS and mTLS enabled with valid CA
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", @valid_cert_path)
    System.put_env("FLAGD_UI_TLS_KEY_PATH", @valid_key_path)
    System.put_env("FLAGD_UI_TLS_MTLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CA_CERT_PATH", @valid_ca_path)

    # When: Service starts
    {:ok, pid} = start_supervised(FlagdUI.Application)

    # Then: Connection without client cert fails
    assert {:error, {:tls_alert, _}} = :ssl.connect('localhost', @default_port, [], 5000)

    # And: Connection with valid client cert succeeds
    client_opts = [certfile: "test/fixtures/tls/valid_client_cert.pem", keyfile: "test/fixtures/tls/valid_client_key.pem"]
    assert {:ok, _} = :ssl.connect('localhost', @default_port, client_opts, 5000)

    stop_supervised(pid)
  end

  # AC-7: mTLS enabled missing/invalid CA fails startup
  test "ac7_mtls_enabled_missing_invalid_ca_fails_startup" do
    # Given: mTLS enabled but no CA path provided
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", @valid_cert_path)
    System.put_env("FLAGD_UI_TLS_KEY_PATH", @valid_key_path)
    System.put_env("FLAGD_UI_TLS_MTLS_ENABLED", "true")

    # When/Then: Service fails to start with missing CA error
    log = capture_log(fn ->
      assert {:error, reason} = start_supervised(FlagdUI.Application)
      assert reason =~ "mTLS enabled but required FLAGD_UI_TLS_CA_CERT_PATH parameter is missing"
    end)
    assert log =~ "exit code 1"

    # Test with invalid CA path
    System.put_env("FLAGD_UI_TLS_CA_CERT_PATH", @non_existent_path)
    log = capture_log(fn ->
      assert {:error, reason} = start_supervised(FlagdUI.Application)
      assert reason =~ "invalid CA certificate for mTLS"
    end)
    assert log =~ "exit code 1"
  end

  # AC-8: Existing functionality behaves identical over HTTPS
  test "ac8_functionality_identical_over_https_and_http" do
    # First test over HTTP (baseline)
    {:ok, pid1} = start_supervised(FlagdUI.Application)
    {:ok, conn1} = :gen_tcp.connect('localhost', @default_port, [:binary, active: false])
    :gen_tcp.send(conn1, "GET /api/flags HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert {:ok, http_response} = :gen_tcp.recv(conn1, 0, 5000)
    :gen_tcp.close(conn1)
    stop_supervised(pid1)

    # Then test over HTTPS
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", @valid_cert_path)
    System.put_env("FLAGD_UI_TLS_KEY_PATH", @valid_key_path)
    {:ok, pid2} = start_supervised(FlagdUI.Application)
    {:ok, ssl_conn} = :ssl.connect('localhost', @default_port, [], 5000)
    :ssl.send(ssl_conn, "GET /api/flags HTTP/1.1\r\nHost: localhost\r\n\r\n")
    assert {:ok, https_response} = :ssl.recv(ssl_conn, 0, 5000)
    :ssl.close(ssl_conn)
    stop_supervised(pid2)

    # Compare responses (ignore headers related to TLS)
    http_body = String.split(http_response, "\r\n\r\n") |> List.last()
    https_body = String.split(https_response, "\r\n\r\n") |> List.last()
    assert http_body == https_body
  end
end
