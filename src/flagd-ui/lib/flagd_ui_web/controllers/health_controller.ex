defmodule FlagdUiWeb.HealthController do
  use FlagdUiWeb, :controller

  @moduledoc """
  Health check endpoints for Kubernetes liveness and readiness probes.
  """

  @doc """
  Liveness probe endpoint: Returns 200 OK when the service process is running.
  """
  def liveness(conn, _params) do
    conn
    |> put_status(:ok)
    |> json(%{
      status: "ok",
      check: "liveness",
      timestamp: DateTime.utc_now() |> DateTime.to_iso8601()
    })
  end

  @doc """
  Readiness probe endpoint: Returns 200 OK when the service is fully initialized and ready to serve traffic.
  """
  def readiness(conn, _params) do
    # Check if all required components are initialized
    checks = [
      phoenix_booted: true,
      static_assets_compiled: static_assets_compiled?(),
      flagd_connection_available: flagd_connection_available?()
    ]

    failed_checks = Enum.filter(checks, fn {_name, status} -> not status end)

    if Enum.empty?(failed_checks) do
      conn
      |> put_status(:ok)
      |> json(%{
        status: "ok",
        check: "readiness",
        timestamp: DateTime.utc_now() |> DateTime.to_iso8601()
      })
    else
      failed_names = Enum.map(failed_checks, fn {name, _} -> Atom.to_string(name) end) |> Enum.join(", ")
      
      conn
      |> put_status(:service_unavailable)
      |> json(%{
        status: "error",
        check: "readiness",
        reason: "Uninitialized components: #{failed_names}",
        timestamp: DateTime.utc_now() |> DateTime.to_iso8601()
      })
    end
  end

  defp static_assets_compiled? do
    # Check if main CSS and JS assets exist in priv/static
    File.exists?(Application.app_dir(:flagd_ui, "priv/static/assets/app.js")) and
    File.exists?(Application.app_dir(:flagd_ui, "priv/static/assets/app.css"))
  end

  defp flagd_connection_available? do
    # Check if flagd service is reachable using configured endpoint
    flagd_host = Application.get_env(:flagd_ui, :flagd_host, "localhost")
    flagd_port = Application.get_env(:flagd_ui, :flagd_port, 8013)

    case :gen_tcp.connect(String.to_charlist(flagd_host), flagd_port, [], 1000) do
      {:ok, socket} ->
        :gen_tcp.close(socket)
        true
      {:error, _} ->
        false
    end
  end
end
