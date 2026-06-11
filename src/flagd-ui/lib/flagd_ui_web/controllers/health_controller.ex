defmodule FlagdUiWeb.HealthController do
  use FlagdUiWeb, :controller

  alias FlagdUi.FlagdClient

  @moduledoc """
  Health check endpoints for Kubernetes liveness and readiness probes.
  """

  @doc """
  Liveness check endpoint: returns 200 OK if Elixir runtime is active.
  """
  def liveness(conn, _params) do
    timestamp = DateTime.utc_now() |> DateTime.to_iso8601()

    conn
    |> put_status(:ok)
    |> json(%{
      status: "ok",
      check: "liveness",
      timestamp: timestamp
    })
  end

  @doc """
  Readiness check endpoint: returns 200 OK if service can connect to flagd backend.
  """
  def readiness(conn, _params) do
    timestamp = DateTime.utc_now() |> DateTime.to_iso8601()

    case FlagdClient.ping() do
      :ok ->
        conn
        |> put_status(:ok)
        |> json(%{
          status: "ok",
          check: "readiness",
          flagd_connection: "healthy",
          timestamp: timestamp
        })

      {:error, reason} ->
        conn
        |> put_status(:service_unavailable)
        |> json(%{
          status: "unavailable",
          check: "readiness",
          flagd_connection: "unhealthy",
          error: to_string(reason),
          timestamp: timestamp
        })
    end
  end
end

