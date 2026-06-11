defmodule FlagdUI.RateLimiterTest do
  use FlagdUI.ConnCase
  use Phoenix.ChannelTest

  @moduledoc """
  Integration tests for rate limiting functionality per AC requirements
  """

  @default_http_limit 100
  @default_health_limit 1000
  @default_websocket_limit 10
  @test_ip "192.168.1.100"
  @other_ip "192.168.1.200"
  @proxy_ip "10.0.0.1"

  setup do
    # Clear any existing rate limit state before each test
    Hammer.delete_all_buckets!()
    :ok
  end

  # Helper to create a connection with specified IP
  defp conn_with_ip(conn, ip) do
    conn
    |> Map.put(:remote_ip, ip |> String.split(".") |> Enum.map(&String.to_integer/1) |> List.to_tuple())
  end

  # Helper to send multiple requests to a path
  defp send_multiple_requests(conn, path, count) do
    for _ <- 1..count do
      get(conn, path)
    end
  end

  test "AC1: Exceeding default HTTP rate limit returns 429 with correct headers and body" do
    conn = build_conn() |> conn_with_ip(@test_ip)

    # Send up to limit, should all return 200
    send_multiple_requests(conn, "/", @default_http_limit)
      |> Enum.each(fn resp -> assert resp.status == 200 end)

    # Next request should be rate limited
    resp = get(conn, "/")
    assert resp.status == 429

    # Check response headers
    assert {"retry-after", retry_after_str} = List.keyfind(resp.resp_headers, "retry-after", 0)
    retry_after = String.to_integer(retry_after_str)
    assert retry_after > 0

    # Check response body
    assert resp_content_type(resp) == "application/json"
    body = json_response(resp, 429)
    assert body["error"] == "Too Many Requests"
    assert body["message"] == "You have exceeded the allowed request rate. Please try again later."
    assert body["retry_after"] == retry_after

    # Verify requests from another IP still work
    other_conn = build_conn() |> conn_with_ip(@other_ip)
    assert get(other_conn, "/").status == 200
  end

  test "AC2: Exceeding health check rate limit returns 429 with correct headers and body" do
    conn = build_conn() |> conn_with_ip(@test_ip)

    # Send up to health limit, should all return 200
    send_multiple_requests(conn, "/health", @default_health_limit)
      |> Enum.each(fn resp -> assert resp.status == 200 end)

    # Next request should be rate limited
    resp = get(conn, "/health")
    assert resp.status == 429

    # Check response headers
    assert {"retry-after", retry_after_str} = List.keyfind(resp.resp_headers, "retry-after", 0)
    retry_after = String.to_integer(retry_after_str)
    assert retry_after > 0

    # Check response body
    assert resp_content_type(resp) == "application/json"
    body = json_response(resp, 429)
    assert body["error"] == "Too Many Requests"
    assert body["message"] == "You have exceeded the allowed request rate. Please try again later."
    assert body["retry_after"] == retry_after
  end

  test "AC3: Exceeding WebSocket connection limit returns 429" do
    conn = build_conn() |> conn_with_ip(@test_ip)

    # Send up to WebSocket connection limit
    for _ <- 1..@default_websocket_limit do
      {:ok, _, _} = socket("", %{}) |> connect(%{})
    end

    # Next connection attempt should be rate limited
    assert {:error, %{status: 429, headers: headers}} = socket("", %{}) |> connect(%{})
    assert {"retry-after", retry_after_str} = List.keyfind(headers, "retry-after", 0)
    assert String.to_integer(retry_after_str) > 0
  end

  test "AC4: Custom rate limit environment variables are respected" do
    # Set custom environment variables
    System.put_env("RATE_LIMIT_HTTP_DEFAULT", "10/1m")
    System.put_env("RATE_LIMIT_HTTP_HEALTH", "20/1m")
    System.put_env("RATE_LIMIT_WEBSOCKET_CONNECT", "5/1m")

    # Restart rate limiter to pick up new config
    Application.stop(:flagd_ui)
    Application.start(:flagd_ui)

    conn = build_conn() |> conn_with_ip(@test_ip)

    # Test custom HTTP limit
    send_multiple_requests(conn, "/", 10) |> Enum.each(fn r -> assert r.status == 200 end)
    assert get(conn, "/").status == 429

    # Test custom health limit
    send_multiple_requests(conn, "/health", 20) |> Enum.each(fn r -> assert r.status == 200 end)
    assert get(conn, "/health").status == 429

    # Test custom WebSocket limit
    for _ <- 1..5 do
      {:ok, _, _} = socket("", %{}) |> connect(%{})
    end
    assert {:error, %{status: 429}} = socket("", %{}) |> connect(%{})

    # Cleanup env vars
    System.delete_env("RATE_LIMIT_HTTP_DEFAULT")
    System.delete_env("RATE_LIMIT_HTTP_HEALTH")
    System.delete_env("RATE_LIMIT_WEBSOCKET_CONNECT")
    Application.stop(:flagd_ui)
    Application.start(:flagd_ui)
  end

  test "AC5: Rate limited requests increment Prometheus counter with correct labels" do
    conn = build_conn() |> conn_with_ip(@test_ip)

    # Exceed HTTP limit
    send_multiple_requests(conn, "/", @default_http_limit)
    get(conn, "/")

    # Check counter metric
    metrics = Prometheus.Metric.Counter.value(:flagd_ui_rate_limited_requests_total, labels: [@test_ip, "http_default", "429"])
    assert metrics == 1

    # Exceed health limit
    send_multiple_requests(conn, "/health", @default_health_limit)
    get(conn, "/health")

    health_metrics = Prometheus.Metric.Counter.value(:flagd_ui_rate_limited_requests_total, labels: [@test_ip, "http_health", "429"])
    assert health_metrics == 1

    # Exceed WebSocket limit
    for _ <- 1..@default_websocket_limit do
      {:ok, _, _} = socket("", %{}) |> connect(%{})
    end
    socket("", %{}) |> connect(%{})

    ws_metrics = Prometheus.Metric.Counter.value(:flagd_ui_rate_limited_requests_total, labels: [@test_ip, "websocket_connect", "429"])
    assert ws_metrics == 1
  end

  test "AC6: Rate limits are enforced per unique IP address" do
    conn1 = build_conn() |> conn_with_ip(@test_ip)
    conn2 = build_conn() |> conn_with_ip(@other_ip)

    # Exceed limit for first IP
    send_multiple_requests(conn1, "/", @default_http_limit)
    assert get(conn1, "/").status == 429

    # Second IP still works
    assert get(conn2, "/").status == 200
    send_multiple_requests(conn2, "/", @default_http_limit)
    assert get(conn2, "/").status == 429
  end

  test "AC7: X-Forwarded-For header is used for client IP when present" do
    # Connection comes from proxy IP, but X-Forwarded-For has real client IP
    conn = build_conn()
      |> conn_with_ip(@proxy_ip)
      |> put_req_header("x-forwarded-for", "#{@test_ip}, 10.0.0.2, 10.0.0.3")

    # Exceed limit for client IP via proxy
    send_multiple_requests(conn, "/", @default_http_limit)
    assert get(conn, "/").status == 429

    # Same client IP from another proxy should also be limited
    conn2 = build_conn()
      |> conn_with_ip("10.0.0.4")
      |> put_req_header("x-forwarded-for", "#{@test_ip}, 10.0.0.5")
    assert get(conn2, "/").status == 429

    # Different client IP via same proxy should work
    conn3 = build_conn()
      |> conn_with_ip(@proxy_ip)
      |> put_req_header("x-forwarded-for", "#{@other_ip}, 10.0.0.2")
    assert get(conn3, "/").status == 200
  end
end
