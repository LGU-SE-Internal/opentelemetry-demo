defmodule FlagdUI.GracefulShutdownTest do
  use ExUnit.Case, async: false
  import ExUnit.CaptureLog

  @moduledoc "Integration tests for graceful shutdown functionality AC-1 through AC-8"

  setup do
    # Reset environment variables before each test
    System.delete_env("FLAGD_UI_SHUTDOWN_GRACE_PERIOD")
    System.delete_env("FLAGD_UI_GRACEFUL_SHUTDOWN_ENABLED")
    :ok
  end

  test "AC-1: SIGTERM stops accepting new connections immediately" do
    # Start service with graceful shutdown enabled
    Application.put_env(:flagd_ui, :graceful_shutdown, enabled: true, grace_period_seconds: 10)
    {:ok, pid} = start_supervised(FlagdUI.Application)

    # Verify service is accepting connections first
    {:ok, %{status: 200}} = Finch.build(:get, "http://localhost:4000/health") |> Finch.request(FlagdUI.Finch)

    # Send SIGTERM to service process
    Process.exit(pid, :sigterm)

    # Wait 500ms for shutdown initiation
    :timer.sleep(500)

    # New connections should be refused or return 503
    assert {:error, :econnrefused} = Finch.build(:get, "http://localhost:4000/health") |> Finch.request(FlagdUI.Finch)
  end

  test "AC-2: In-flight requests complete within grace period" do
    Application.put_env(:flagd_ui, :graceful_shutdown, enabled: true, grace_period_seconds: 15)
    {:ok, pid} = start_supervised(FlagdUI.Application)

    # Spawn a long-running request that takes 10s
    task = Task.async(fn ->
      Finch.build(:get, "http://localhost:4000/test/long-running?duration=10") |> Finch.request(FlagdUI.Finch)
    end)

    # Wait 1s for request to start processing
    :timer.sleep(1000)

    # Send SIGTERM
    Process.exit(pid, :sigterm)

    # Wait for request to complete
    assert {:ok, %{status: 200}} = Task.await(task, 20_000)
    # Verify process exited after request completed
    refute Process.alive?(pid)
  end

  test "AC-3: In-flight requests are terminated after grace period expires" do
    Application.put_env(:flagd_ui, :graceful_shutdown, enabled: true, grace_period_seconds: 10)
    {:ok, pid} = start_supervised(FlagdUI.Application)

    # Spawn a 20s long request
    task = Task.async(fn ->
      Finch.build(:get, "http://localhost:4000/test/long-running?duration=20") |> Finch.request(FlagdUI.Finch)
    end)

    # Wait 1s for request to start
    :timer.sleep(1000)

    # Send SIGTERM
    start_time = System.system_time(:second)
    Process.exit(pid, :sigterm)

    # Wait for process to exit
    wait_for_exit(pid, 15_000)
    exit_time = System.system_time(:second)

    # Process should exit within ~10s (grace period)
    assert exit_time - start_time <= 12
    # Request should have been terminated
    assert {:error, _} = Task.await(task, 5000)
  end

  test "AC-4: WebSocket connections receive 1001 close frame on shutdown" do
    Application.put_env(:flagd_ui, :graceful_shutdown, enabled: true, grace_period_seconds: 10)
    {:ok, pid} = start_supervised(FlagdUI.Application)

    # Establish WebSocket connection
    {:ok, ws_pid} = WebsocketClient.start_link("ws://localhost:4000/ws")
    assert WebsocketClient.connected?(ws_pid)

    # Send SIGTERM
    Process.exit(pid, :sigterm)

    # Wait for close frame
    assert_receive {:ws_close, 1001, "Service shutting down"}, 5000
    refute WebsocketClient.connected?(ws_pid)
  end

  test "AC-5: Grace period is configurable via environment variable" do
    System.put_env("FLAGD_UI_SHUTDOWN_GRACE_PERIOD", "60")
    # Config file value should be overridden
    Application.put_env(:flagd_ui, :graceful_shutdown, enabled: true, grace_period_seconds: 30)

    # Start application, verify config is overridden
    {:ok, _pid} = start_supervised(FlagdUI.Application)
    assert Application.get_env(:flagd_ui, :graceful_shutdown)[:grace_period_seconds] == 60
  end

  test "AC-6: All connections are cleaned up before exit" do
    Application.put_env(:flagd_ui, :graceful_shutdown, enabled: true, grace_period_seconds: 10)
    {:ok, pid} = start_supervised(FlagdUI.Application)

    # Establish DB connection
    {:ok, db_conn} = Ecto.Adapters.SQL.query(FlagdUI.Repo, "SELECT 1")
    assert db_conn != nil

    # Establish flagd upstream connection
    {:ok, upstream_conn} = GRPC.Stub.connect("localhost:8013")
    assert upstream_conn != nil

    # Capture logs during shutdown
    log = capture_log(fn ->
      Process.exit(pid, :sigterm)
      wait_for_exit(pid, 15_000)
    end)

    # Verify cleanup log line exists
    assert log =~ ~s(event="connection_cleanup_complete")
    # Verify connections are closed
    assert {:error, :closed} = Ecto.Adapters.SQL.query(FlagdUI.Repo, "SELECT 1")
  end

  test "AC-7: Shutdown events are logged with structured fields" do
    Application.put_env(:flagd_ui, :graceful_shutdown, enabled: true, grace_period_seconds: 30)
    {:ok, pid} = start_supervised(FlagdUI.Application)

    # Generate some in-flight requests and websockets
    Enum.each(1..3, fn _ ->
      Task.start(fn -> Finch.build(:get, "http://localhost:4000/test/long-running?duration=5") |> Finch.request(FlagdUI.Finch) end)
    end)
    {:ok, ws_pid} = WebsocketClient.start_link("ws://localhost:4000/ws")
    :timer.sleep(500)

    # Capture logs during SIGINT
    log = capture_log(fn ->
      Process.exit(pid, :sigint)
      wait_for_exit(pid, 10_000)
    end)

    # Verify required log fields are present
    assert log =~ ~s(event="shutdown_initiated")
    assert log =~ ~s(signal="SIGINT")
    assert log =~ ~s(grace_period=30)
    assert log =~ ~s(in_flight_requests=3)
    assert log =~ ~s(active_websockets=1)
    assert log =~ ~s(event="shutdown_complete")
  end

  test "AC-8: Graceful shutdown disabled leads to immediate exit" do
    System.put_env("FLAGD_UI_GRACEFUL_SHUTDOWN_ENABLED", "false")
    Application.put_env(:flagd_ui, :graceful_shutdown, enabled: false)
    {:ok, pid} = start_supervised(FlagdUI.Application)

    # Spawn long running request
    task = Task.async(fn ->
      Finch.build(:get, "http://localhost:4000/test/long-running?duration=10") |> Finch.request(FlagdUI.Finch)
    end)
    :timer.sleep(1000)

    # Send SIGTERM
    start_time = System.system_time(:millisecond)
    Process.exit(pid, :sigterm)

    # Process should exit within 1s
    wait_for_exit(pid, 1000)
    exit_time = System.system_time(:millisecond)
    assert exit_time - start_time <= 1000
    assert {:error, _} = Task.await(task, 1000)
  end

  defp wait_for_exit(pid, timeout) do
    if Process.alive?(pid) and timeout > 0 do
      :timer.sleep(100)
      wait_for_exit(pid, timeout - 100)
    end
  end
end
