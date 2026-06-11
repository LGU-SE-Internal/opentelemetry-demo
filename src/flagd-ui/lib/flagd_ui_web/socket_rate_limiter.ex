defmodule FlagdUI.Web.SocketRateLimiter do
  @moduledoc """
  Socket connect handler wrapper that applies rate limiting to WebSocket connection attempts
  """
  alias FlagdUI.Plugs.RateLimiter

  def connect(handler, params, socket, connect_info) do
    # Extract client IP
    client_ip = case connect_info do
      %{x_headers: headers} ->
        case List.keyfind(headers, "x-forwarded-for", 0) do
          nil -> ip_to_string(connect_info.peer_data.address)
          {_, forwarded} ->
            forwarded
            |> String.split(",")
            |> Enum.map(&String.trim/1)
            |> Enum.find(fn ip_str ->
              case ip_str |> String.split(".") |> Enum.map(&String.to_integer/1) |> List.to_tuple() do
                ip when tuple_size(ip) == 4 -> not private_ip?(ip)
                _ -> false
              end
            end)
            |> case do
              nil -> ip_to_string(connect_info.peer_data.address)
              public_ip -> public_ip
            end
        end
      _ -> ip_to_string(connect_info.peer_data.address)
    end

    {limit, scale} = RateLimiter.get_limit_config(:websocket_connect)
    bucket_name = "rate_limit:websocket_connect:#{client_ip}"

    case Hammer.check_rate(bucket_name, scale, limit) do
      {:allow, _count} ->
        handler.connect(params, socket, connect_info)

      {:deny, ms_until_reset} ->
        Prometheus.Metric.Counter.inc(:flagd_ui_rate_limited_requests_total, [client_ip, "websocket_connect", "429"])
        retry_after = ceil(ms_until_reset / 1000)
        {:error, %{status: 429, headers: [{"retry-after", to_string(retry_after)}]}}
    end
  end

  defp private_ip?(ip) do
    private_ranges = [
      {{10, 0, 0, 0}, 8},
      {{172, 16, 0, 0}, 12},
      {{192, 168, 0, 0}, 16},
      {{127, 0, 0, 0}, 8}
    ]
    Enum.any?(private_ranges, fn {range, bits} ->
      ip_prefix = ip_to_integer(ip) >>> (32 - bits)
      range_prefix = ip_to_integer(range) >>> (32 - bits)
      ip_prefix == range_prefix
    end)
  end

  defp ip_to_integer({a, b, c, d}), do: a * 256 * 256 * 256 + b * 256 * 256 + c * 256 + d
  defp ip_to_string({a, b, c, d}), do: "#{a}.#{b}.#{c}.#{d}"
end
