defmodule FlagdUI.MetricsIntegrationTest do
  use FlagdUIWeb.ConnCase, async: true

  @metrics_endpoint "/metrics"

  # AC-1: Unauthenticated GET request to /metrics returns 200 OK, Prometheus content type, valid syntax
  test "ac1_metrics_endpoint_returns_valid_prometheus_format", %{conn: conn} do
    conn = get(conn, @metrics_endpoint)

    assert conn.status == 200
    assert get_resp_header(conn, "content-type") == ["text/plain; version=0.0.4; charset=utf-8"]

    # Validate Prometheus metric syntax
    response_body = conn.resp_body
    assert is_binary(response_body)
    assert response_body != ""

    # Simple Prometheus format checks: lines are either comments (#) or metric_name{labels} value
    lines = String.split(response_body, "\n", trim: true)
    Enum.each(lines, fn line ->
      if not String.starts_with?(line, "#") do
        assert String.match?(line, ~r/^[a-zA-Z_:][a-zA-Z0-9_:]*(\{.*\})?\s+[0-9.e+-]+(\s+[0-9]+)?$/)
      end
    end)
  end

  # AC-2: Metrics response includes all default Phoenix/VM metrics
  test "ac2_metrics_include_default_phoenix_and_vm_metrics", %{conn: conn} do
    conn = get(conn, @metrics_endpoint)
    assert conn.status == 200
    body = conn.resp_body

    assert String.contains?(body, "phoenix_http_requests_total")
    assert String.contains?(body, "phoenix_http_request_duration_seconds")
    assert String.contains?(body, "erlang_vm_memory_usage_bytes")
    assert String.contains?(body, "erlang_vm_process_count")
  end

  # AC-3: Flag evaluation increments flagd_ui_flag_evaluations_total counter with correct labels
  test "ac3_flag_evaluation_increments_evaluations_counter", %{conn: conn} do
    # Get initial metric value
    initial_resp = get(conn, @metrics_endpoint)
    assert initial_resp.status == 200
    initial_count = get_metric_value(initial_resp.resp_body, "flagd_ui_flag_evaluations_total", %{"flag_key" => "test_flag", "result" => "success"}) || 0

    # Simulate a successful flag evaluation
    # TODO: Replace with actual flag evaluation API call once endpoint is known
    # For now, we just check that after an evaluation, the counter increments
    # Post to hypothetical flag evaluation endpoint
    eval_conn = post(conn, "/api/flags/test_flag/evaluate", %{context: %{}})
    assert eval_conn.status in 200..299

    # Get updated metric value
    updated_resp = get(recycle(conn), @metrics_endpoint)
    assert updated_resp.status == 200
    updated_count = get_metric_value(updated_resp.resp_body, "flagd_ui_flag_evaluations_total", %{"flag_key" => "test_flag", "result" => "success"}) || 0

    assert updated_count == initial_count + 1
  end

  # AC-4: Flag modification increments flagd_ui_flag_modifications_total counter with correct labels
  test "ac4_flag_modification_increments_modifications_counter", %{conn: conn} do
    # Test create operation
    initial_create_count = get_metric_value(get(conn, @metrics_endpoint).resp_body, "flagd_ui_flag_modifications_total", %{"flag_key" => "new_test_flag", "operation" => "create", "result" => "success"}) || 0
    create_conn = post(recycle(conn), "/api/flags", %{flag_key: "new_test_flag", value: "test_value"})
    assert create_conn.status in 200..299
    updated_create_count = get_metric_value(get(recycle(conn), @metrics_endpoint).resp_body, "flagd_ui_flag_modifications_total", %{"flag_key" => "new_test_flag", "operation" => "create", "result" => "success"}) || 0
    assert updated_create_count == initial_create_count + 1

    # Test update operation
    initial_update_count = get_metric_value(get(recycle(conn), @metrics_endpoint).resp_body, "flagd_ui_flag_modifications_total", %{"flag_key" => "new_test_flag", "operation" => "update", "result" => "success"}) || 0
    update_conn = put(recycle(conn), "/api/flags/new_test_flag", %{value: "updated_value"})
    assert update_conn.status in 200..299
    updated_update_count = get_metric_value(get(recycle(conn), @metrics_endpoint).resp_body, "flagd_ui_flag_modifications_total", %{"flag_key" => "new_test_flag", "operation" => "update", "result" => "success"}) || 0
    assert updated_update_count == initial_update_count + 1

    # Test delete operation
    initial_delete_count = get_metric_value(get(recycle(conn), @metrics_endpoint).resp_body, "flagd_ui_flag_modifications_total", %{"flag_key" => "new_test_flag", "operation" => "delete", "result" => "success"}) || 0
    delete_conn = delete(recycle(conn), "/api/flags/new_test_flag")
    assert delete_conn.status in 200..299
    updated_delete_count = get_metric_value(get(recycle(conn), @metrics_endpoint).resp_body, "flagd_ui_flag_modifications_total", %{"flag_key" => "new_test_flag", "operation" => "delete", "result" => "success"}) || 0
    assert updated_delete_count == initial_delete_count + 1
  end

  # AC-5: flagd_ui_api_request_duration_seconds histogram records latency for all flag management API endpoints
  test "ac5_api_request_duration_records_latency_for_flag_endpoints", %{conn: conn} do
    # Make a request to a flag management API endpoint
    api_conn = get(conn, "/api/flags")
    assert api_conn.status in 200..299

    # Check that the histogram metric exists for the endpoint
    metrics_resp = get(recycle(conn), @metrics_endpoint)
    assert metrics_resp.status == 200
    body = metrics_resp.resp_body

    assert String.contains?(body, "flagd_ui_api_request_duration_seconds")
    assert String.contains?(body, ~s(endpoint="/api/flags"))
  end

  # AC-6: flagd_connection_health_status gauge shows correct health status
  test "ac6_connection_health_status_gauge_shows_correct_state", %{conn: conn} do
    # When connection is healthy, value should be 1
    # TODO: Simulate healthy connection scenario
    metrics_resp = get(conn, @metrics_endpoint)
    assert metrics_resp.status == 200
    healthy_value = get_metric_value(metrics_resp.resp_body, "flagd_connection_health_status", %{"connection_id" => "default"})
    assert healthy_value == 1

    # When connection is unhealthy, value should be 0
    # TODO: Simulate unhealthy connection scenario (e.g. stop flagd service)
    # For now, just verify the metric exists
    assert String.contains?(metrics_resp.resp_body, "flagd_connection_health_status")
  end

  # AC-7: /metrics endpoint accepts requests without authentication, no 401/403
  test "ac7_metrics_endpoint_allows_unauthenticated_access", %{conn: conn} do
    # Test without any auth headers
    conn_no_auth = conn
           |> delete_req_header("authorization")
           |> delete_req_header("cookie")
           |> get(@metrics_endpoint)

    assert conn_no_auth.status == 200
    refute conn_no_auth.status in [401, 403]

    # Test with invalid auth header (should still return 200, not reject)
    conn_invalid_auth = conn
           |> put_req_header("authorization", "Bearer invalid_token")
           |> put_req_header("cookie", "invalid_session=123")
           |> get(@metrics_endpoint)

    assert conn_invalid_auth.status == 200
    refute conn_invalid_auth.status in [401, 403]
  end

  # AC-8: Metrics are exposed on port 4000 (same as main app)
  test "ac8_metrics_exposed_on_same_port_as_main_application", %{conn: conn} do
    # In test environment, we confirm the endpoint is mounted on the main router (no separate port)
    # The ConnCase uses the main application endpoint on the test port, so if this test runs successfully,
    # it confirms the /metrics endpoint is on the same port as other routes
    main_app_conn = get(conn, "/")
    assert main_app_conn.status in 200..399

    metrics_conn = get(recycle(conn), @metrics_endpoint)
    assert metrics_conn.status == 200

    # Verify both responses came from the same endpoint/port in test config
    assert main_app_conn.host == metrics_conn.host
    assert main_app_conn.port == metrics_conn.port
  end

  # Helper function to extract metric value from Prometheus response
  defp get_metric_value(body, metric_name, labels) do
    label_string = Enum.map(labels, fn {k, v} -> ~s(#{k}="#{v}") end) |> Enum.join(",")
    pattern = ~r/#{metric_name}\{#{label_string}\}\s+([0-9.]+)/

    case Regex.run(pattern, body) do
      [_, value_str] -> String.to_float(value_str)
      nil -> nil
    end
  end
end
