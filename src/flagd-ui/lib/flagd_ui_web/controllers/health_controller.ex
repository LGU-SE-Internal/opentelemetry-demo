defmodule FlagdUiWeb.HealthController do
  use FlagdUiWeb, :controller

  @moduledoc """
  Health check endpoints for Kubernetes liveness and readiness probes.
  """

  @doc """
  Liveness check endpoint: returns 200 OK if Elixir runtime is active.
  """
  def liveness(conn, _params) do
    # Liveness check just verifies the runtime is up, no external checks
    conn
    |> put_status(:ok)
    |> json(%{
      status: "ok",
      timestamp: DateTime.utc_now() |> DateTime.to_iso8601()
    })
  rescue
    _e ->
      conn
      |> put_status(:service_unavailable)
      |> json(%{
        status: "unhealthy",
        timestamp: DateTime.utc_now() |> DateTime.to_iso8601(),
        error: "Runtime failure detected"
      })
  end

  @doc """
  Readiness check endpoint: returns 200 OK if service is fully initialized and ready to serve traffic.
  """
  def readiness(conn, _params) do
    # Check that all application dependencies are started
    case Application.ensure_all_started(:flagd_ui) do
      {:ok, _} ->
        conn
        |> put_status(:ok)
        |> json(%{
          status: "ok",
          timestamp: DateTime.utc_now() |> DateTime.to_iso8601()
        })
      {:error, {app, reason}} ->
        conn
        |> put_status(:service_unavailable)
        |> json(%{
          status: "not_ready",
          timestamp: DateTime.utc_now() |> DateTime.to_iso8601(),
          reason: "Application #{app} failed to start: #{inspect(reason)}"
        })
    end
  rescue
    e ->
      conn
      |> put_status(:service_unavailable)
      |> json(%{
        status: "not_ready",
        timestamp: DateTime.utc_now() |> DateTime.to_iso8601(),
        reason: "Initialization failed: #{inspect(e)}"
      })
  end
end
