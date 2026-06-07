defmodule FlagdUiWeb.TLSIntegrationTest do
  use ExUnit.Case
  import ExUnit.CaptureLog

  @test_port 49999

  setup do
    # Save original environment variables
    original_env = %{
      "FLAGD_UI_TLS_ENABLED" => System.get_env("FLAGD_UI_TLS_ENABLED"),
      "FLAGD_UI_TLS_CERT_PATH" => System.get_env("FLAGD_UI_TLS_CERT_PATH"),
      "FLAGD_UI_TLS_KEY_PATH" => System.get_env("FLAGD_UI_TLS_KEY_PATH"),
      "FLAGD_UI_TLS_CLIENT_CA_PATH" => System.get_env("FLAGD_UI_TLS_CLIENT_CA_PATH"),
      "FLAGD_UI_TLS_CLIENT_REQUIRE" => System.get_env("FLAGD_UI_TLS_CLIENT_REQUIRE"),
      "PORT" => System.get_env("PORT")
    }

    # Set test port
    System.put_env("PORT", to_string(@test_port))

    on_exit(fn ->
      # Restore original environment variables
      for {key, value} <- original_env do
        if value do
          System.put_env(key, value)
        else
          System.delete_env(key)
        end
      end
    end)

    :ok
  end

  defp start_service do
    # Force reload runtime config and start the application
    Application.load(:flagd_ui)
    Config.Reader.read!("config/runtime.exs") |> Config.Reader.merge()
    Application.ensure_all_started(:flagd_ui)
  end

  defp stop_service do
    Application.stop(:flagd_ui)
  end

  test "AC-1: TLS enabled with valid cert/key accepts HTTPS connections, rejects HTTP" do
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", "test/support/certs/valid_server.crt")
    System.put_env("FLAGD_UI_TLS_KEY_PATH", "test/support/certs/valid_server.key")

    assert {:ok, _} = start_service()

    # Test HTTPS connection succeeds
    {:ok, {{'HTTP/1.1', 200, 'OK'}, _, _}} = :httpc.request(:get, {'https://localhost:#{@test_port}/', []}, [ssl: [verify: :verify_none]], [])

    # Test plain HTTP connection fails
    assert {:error, _} = :httpc.request(:get, {'http://localhost:#{@test_port}/', []}, [], [])

    stop_service()
  end

  test "AC-2: TLS disabled accepts plain HTTP connections" do
    System.delete_env("FLAGD_UI_TLS_ENABLED")
    System.delete_env("FLAGD_UI_TLS_CERT_PATH")
    System.delete_env("FLAGD_UI_TLS_KEY_PATH")

    assert {:ok, _} = start_service()

    # Test HTTP connection succeeds
    {:ok, {{'HTTP/1.1', 200, 'OK'}, _, _}} = :httpc.request(:get, {'http://localhost:#{@test_port}/', []}, [], [])

    # Test HTTPS connection fails
    assert {:error, _} = :httpc.request(:get, {'https://localhost:#{@test_port}/', []}, [ssl: [verify: :verify_none]], [])

    stop_service()
  end

  test "AC-3: TLS enabled without cert/key exits with error" do
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.delete_env("FLAGD_UI_TLS_CERT_PATH")
    System.put_env("FLAGD_UI_TLS_KEY_PATH", "test/support/certs/valid_server.key")

    log = capture_log(fn ->
      assert {:error, _} = start_service()
    end)

    assert log =~ "Missing required TLS configuration: FLAGD_UI_TLS_CERT_PATH and FLAGD_UI_TLS_KEY_PATH must be set when TLS is enabled"
  end

  test "AC-4: TLS enabled with invalid cert/key exits with error" do
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", "test/support/certs/invalid.crt")
    System.put_env("FLAGD_UI_TLS_KEY_PATH", "test/support/certs/valid_server.key")

    log = capture_log(fn ->
      assert {:error, _} = start_service()
    end)

    assert log =~ "invalid certificate/key file"
  end

  test "AC-5: mTLS enabled rejects connections without valid client cert" do
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", "test/support/certs/valid_server.crt")
    System.put_env("FLAGD_UI_TLS_KEY_PATH", "test/support/certs/valid_server.key")
    System.put_env("FLAGD_UI_TLS_CLIENT_CA_PATH", "test/support/certs/ca.crt")

    assert {:ok, _} = start_service()

    # Connection without client cert fails
    assert {:error, {:failed_connect, _}} = :httpc.request(:get, {'https://localhost:#{@test_port}/', []}, [ssl: [verify: :verify_none]], [])

    # Connection with valid client cert succeeds
    {:ok, {{'HTTP/1.1', 200, 'OK'}, _, _}} = :httpc.request(:get, {'https://localhost:#{@test_port}/', []}, [
      ssl: [
        certfile: "test/support/certs/valid_client.crt",
        keyfile: "test/support/certs/valid_client.key",
        verify: :verify_none
      ]
    ], [])

    stop_service()
  end

  test "AC-6: No client CA accepts connections without client cert" do
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", "test/support/certs/valid_server.crt")
    System.put_env("FLAGD_UI_TLS_KEY_PATH", "test/support/certs/valid_server.key")
    System.delete_env("FLAGD_UI_TLS_CLIENT_CA_PATH")

    assert {:ok, _} = start_service()

    # Connection without client cert succeeds
    {:ok, {{'HTTP/1.1', 200, 'OK'}, _, _}} = :httpc.request(:get, {'https://localhost:#{@test_port}/', []}, [ssl: [verify: :verify_none]], [])

    stop_service()
  end

  test "AC-7: TLS config changes apply after restart without rebuild" do
    # First run: TLS disabled, accept HTTP
    System.put_env("FLAGD_UI_TLS_ENABLED", "false")
    assert {:ok, _} = start_service()
    {:ok, {{'HTTP/1.1', 200, 'OK'}, _, _}} = :httpc.request(:get, {'http://localhost:#{@test_port}/', []}, [], [])
    stop_service()

    # Change config to TLS enabled, restart, accept HTTPS
    System.put_env("FLAGD_UI_TLS_ENABLED", "true")
    System.put_env("FLAGD_UI_TLS_CERT_PATH", "test/support/certs/valid_server.crt")
    System.put_env("FLAGD_UI_TLS_KEY_PATH", "test/support/certs/valid_server.key")
    assert {:ok, _} = start_service()
    {:ok, {{'HTTP/1.1', 200, 'OK'}, _, _}} = :httpc.request(:get, {'https://localhost:#{@test_port}/', []}, [ssl: [verify: :verify_none]], [])
    stop_service()
  end
end
