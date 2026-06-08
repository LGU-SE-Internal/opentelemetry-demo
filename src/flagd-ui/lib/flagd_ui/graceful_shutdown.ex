defmodule FlagdUI.GracefulShutdown do
  @moduledoc "Implements graceful shutdown workflow for SIGTERM/SIGINT signals"
  use GenServer

  @max_grace_period 300
  @default_config [
    enabled: true,
    grace_period_seconds: 30,
    shutdown_log_level: :info
  ]

  def start_link(_opts) do
    GenServer.start_link(__MODULE__, [], name: __MODULE__)
  end

  @impl true
  def init(_opts) do
    config = load_config()

    if config[:enabled] do
      # Validate grace period
      grace_period = config[:grace_period_seconds]
      if grace_period > @max_grace_period do
        raise "Grace period cannot exceed #{@max_grace_period} seconds, got #{grace_period}"
      end

      # Register signal handlers
      case :gen_event.swap_handler(:erl_signal_server, {:erl_signal_handler, []}, {__MODULE__, []}, []) do
        :ok ->
          Logger.log(config[:shutdown_log_level], "Graceful shutdown enabled with #{grace_period}s grace period")
        {:error, reason} ->
          Logger.error("Failed to initialize graceful shutdown signal handlers: #{inspect(reason)}, falling back to immediate shutdown")
          config = Keyword.put(config, :enabled, false)
          Application.put_env(:flagd_ui, :graceful_shutdown, config)
      end
    else
      Logger.warning("Graceful shutdown is disabled, service will exit immediately on SIGTERM/SIGINT")
    end

    {:ok, %{config: config, shutting_down: false}}
  end

  def handle_signal(signal) when signal in [:sigterm, :sigint] do
    GenServer.call(__MODULE__, {:handle_signal, signal}, :infinity)
  end

  @impl true
  def handle_call({:handle_signal, signal}, _from, state) do
    if state.config[:enabled] and not state.shutting_down do
      state = %{state | shutting_down: true}
      config = state.config

      # Log shutdown initiation
      in_flight = Bandit.Connections.count(FlagdUiWeb.Endpoint)
      active_ws = FlagdUiWeb.Endpoint.socket_count()
      Logger.log(config[:shutdown_log_level], 
        "event=\"shutdown_initiated\" signal=\"#{String.upcase(to_string(signal))}\" " <>
        "grace_period=#{config[:grace_period_seconds]} in_flight_requests=#{in_flight} " <>
        "active_websockets=#{active_ws}"
      )

      # Stop accepting new connections first
      Bandit.Connections.drain(FlagdUiWeb.Endpoint, timeout: 0)

      # Close all WebSocket connections with 1001 code
      FlagdUiWeb.Endpoint.broadcast("ws", "shutdown", %{reason: "Service shutting down"})
      Process.sleep(100) # Give sockets time to send close frame

      # Wait for in-flight requests to complete during grace period
      wait_for_drain(config[:grace_period_seconds] * 1000)

      # Clean up connections
      # Close database connections
      Ecto.Adapters.SQL.disconnect_all(FlagdUI.Repo, 5000)
      # Close upstream flagd connections
      GRPC.Stub.disconnect_all()

      Logger.log(config[:shutdown_log_level], "event=\"connection_cleanup_complete\"")
      Logger.log(config[:shutdown_log_level], "event=\"shutdown_complete\" reason=\"signal_received\"")

      # Exit normally
      System.stop(0)
    else
      # Immediate exit if disabled
      System.stop(0)
    end

    {:reply, :ok, state}
  end

  # Signal handler callback for :gen_event
  def handle_event(signal, state) when signal in [:sigterm, :sigint] do
    handle_signal(signal)
    {:ok, state}
  end

  def handle_event(_signal, state) do
    {:ok, state}
  end

  defp load_config do
    config = Application.get_env(:flagd_ui, :graceful_shutdown, @default_config)
    # Apply environment variable overrides
    config = case System.get_env("FLAGD_UI_GRACEFUL_SHUTDOWN_ENABLED") do
      "false" -> Keyword.put(config, :enabled, false)
      "true" -> Keyword.put(config, :enabled, true)
      _ -> config
    end

    config = case System.get_env("FLAGD_UI_SHUTDOWN_GRACE_PERIOD") do
      val when is_binary(val) ->
        case Integer.parse(val) do
          {num, ""} when num in 1..@max_grace_period -> Keyword.put(config, :grace_period_seconds, num)
          _ -> config
        end
      _ -> config
    end

    # Store updated config back to application env for test verification
    Application.put_env(:flagd_ui, :graceful_shutdown, config)
    config
  end

  defp wait_for_drain(0), do: :ok
  defp wait_for_drain(time_remaining) do
    if Bandit.Connections.count(FlagdUiWeb.Endpoint) > 0 and time_remaining > 0 do
      Process.sleep(100)
      wait_for_drain(time_remaining - 100)
    end
  end
end
