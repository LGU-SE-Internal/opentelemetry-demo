defmodule FlagdUiWeb.HealthController do
  use FlagdUiWeb, :controller

  @moduledoc """
  Health check endpoints for Kubernetes liveness and readiness probes
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

  def readiness(conn, _params) do
    # Check if all required components are ready
    checks = [
      phoenix_booted: true,
      static_assets_compiled: static_assets_ready?(),
      flagd_connection: flagd_connected?()
    ]

    failed_checks = Enum.filter(checks, fn {_key, value} -> not value end)

    if Enum.empty?(failed_checks) do
      conn
      |> put_status(:ok)
      |> json(%{
        status: "ok",
        check: "readiness",
        timestamp: DateTime.utc_now() |> DateTime.to_iso8601()
      })
    else
      reason = failed_checks |> Enum.map(fn {key, _} -> Atom.to_string(key) end) |> Enum.join(", ")
      conn
      |> put_status(:service_unavailable)
      |> json(%{
        status: "error",
        check: "readiness",
        reason: reason,
        timestamp: DateTime.utc_now() |> DateTime.to_iso8601()
      })
    end
  end

  defp static_assets_ready? do
    # Check if static assets are compiled
    File.exists?(Application.app_dir(:flagd_ui, "priv/static/assets/app.js"))
  end

  defp flagd_connected? do
    # Check if flagd service is reachable (adjust based on actual flagd connection implementation)
    case Application.get_env(:flagd_ui, :flagd_host) do
      nil -> true # No flagd host configured, skip check
      host ->
        port = Application.get_env(:flagd_ui, :flagd_port, 8013)
        case :gen_tcp.connect(String.to_charlist(host), port, [], 1000) do
          {:ok, socket} ->
            :gen_tcp.close(socket)
            true
          {:error, _} -> false
        end
    end
  end
end
