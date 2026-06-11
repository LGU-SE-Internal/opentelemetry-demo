# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

defmodule FlagdUi.FlagdClient do
  @moduledoc """
  Client for interacting with the flagd backend service.
  """

  @doc """
  Pings the flagd backend to check connectivity.
  Returns :ok on successful connection, {:error, reason} on failure.
  """
  def ping do
    # Get flagd configuration from application env
    config = Application.get_env(:flagd_ui, :flagd, [])
    host = Keyword.get(config, :host, "localhost")
    port = Keyword.get(config, :port, 8013)
    timeout = Keyword.get(config, :ping_timeout, 1000)

    # Try to establish a TCP connection to flagd
    case :gen_tcp.connect(String.to_charlist(host), port, [], timeout) do
      {:ok, socket} ->
        :gen_tcp.close(socket)
        :ok
      {:error, reason} ->
        {:error, to_string(reason)}
    end
  end
end
