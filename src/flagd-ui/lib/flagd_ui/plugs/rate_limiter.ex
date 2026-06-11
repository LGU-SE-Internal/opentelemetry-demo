defmodule FlagdUI.Plugs.RateLimiter do
  @moduledoc """
  Rate limiting plug for HTTP endpoints that uses Hammer library
  Supports three types: :http_default, :http_health, :websocket_connect
  """
  import Plug.Conn
  import Phoenix.Controller

  @private_ip_ranges [
    {{10, 0, 0, 0}, 8},
    {{172, 16, 0, 0}, 12},
    {{192, 168, 0, 0}, 16},
    {{127, 0, 0, 0}, 8}
  ]

  @default_limits %{
    http_default: {100, 60_000},
    http_health: {1000, 60_000},
    websocket_connect: {10, 60_000}
  }

  def init(opts) do
    type = Keyword.fetch!(opts, :type)
    {limit, scale} = get_limit_config(type)
    %{type: type, limit: limit, scale: scale}
  end

  def call(conn, %{type: type, limit: limit, scale: scale}) do
    client_ip = get_client_ip(conn)
    bucket_name = "rate_limit:#{type}:#{client_ip}"

    case Hammer.check_rate(bucket_name, scale, limit) do
      {:allow, _count} ->
        conn

      {:deny, ms_until_reset} ->
        increment_metric(client_ip, type)
        retry_after = ceil(ms_until_reset / 1000)
        conn
        |> put_status(:too_many_requests)
        |> put_resp_header("retry-after", to_string(retry_after))
        |> json(%{
          error: "Too Many Requests",
          message: "You have exceeded the allowed request rate. Please try again later.",
          retry_after: retry_after
        })
        |> halt()
    end
  end

  defp get_client_ip(conn) do
    case get_req_header(conn, "x-forwarded-for") do
      [] ->
        ip_to_string(conn.remote_ip)

      [forwarded_header | _] ->
        forwarded_header
        |> String.split(",")
        |> Enum.map(&String.trim/1)
        |> Enum.find(fn ip_str ->
          case ip_str |> String.split(".") |> Enum.map(&String.to_integer/1) |> List.to_tuple() do
            ip when tuple_size(ip) == 4 -> not private_ip?(ip)
            _ -> false
          end
        end)
        |> case do
          nil -> ip_to_string(conn.remote_ip)
          public_ip -> public_ip
        end
    end
  end

  defp private_ip?(ip) do
    Enum.any?(@private_ip_ranges, fn {range, bits} ->
      ip_prefix = ip_to_integer(ip) >>> (32 - bits)
      range_prefix = ip_to_integer(range) >>> (32 - bits)
      ip_prefix == range_prefix
    end)
  end

  defp ip_to_integer({a, b, c, d}), do: a * 256 * 256 * 256 + b * 256 * 256 + c * 256 + d

  defp ip_to_string({a, b, c, d}), do: "#{a}.#{b}.#{c}.#{d}"

  defp get_limit_config(type) do
    env_var = case type do
      :http_default -> "RATE_LIMIT_HTTP_DEFAULT"
      :http_health -> "RATE_LIMIT_HTTP_HEALTH"
      :websocket_connect -> "RATE_LIMIT_WEBSOCKET_CONNECT"
    end

    default = Map.get(@default_limits, type)

    case System.get_env(env_var) do
      nil -> default
      value ->
        case String.split(value, "/") do
          [count_str, duration_str] when byte_size(count_str) > 0 and byte_size(duration_str) > 0 ->
            count = String.to_integer(count_str)
            {duration, unit} = String.split_at(duration_str, -1)
            duration = String.to_integer(duration)
            scale = case unit do
              "s" -> duration * 1000
              "m" -> duration * 60 * 1000
              "h" -> duration * 60 * 60 * 1000
              _ -> elem(default, 1)
            end
            {count, scale}
          _ -> default
        end
    end
  end

  defp increment_metric(ip_address, endpoint_type) do
    Prometheus.Metric.Counter.inc(:flagd_ui_rate_limited_requests_total, [ip_address, to_string(endpoint_type), "429"])
  end
end
