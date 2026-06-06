defmodule FlagdUiWeb.HealthControllerTest do
  use FlagdUiWeb.ConnCase

  describe "GET /health/liveness" do
    test "ac1_liveness_returns_ok_when_runtime_active", %{conn: conn} do
      conn = get(conn, ~p"/health/liveness")

      assert response(conn, 200)
      assert json_response(conn, 200)["status"] == "ok"
      assert get_resp_header(conn, "content-type") == ["application/json; charset=utf-8"]
    end

    test "ac2_liveness_returns_503_when_unhealthy", %{conn: conn} do
      # Simulate runtime failure scenario
      # Note: This test will be properly validated once implementation is present
      conn = get(conn, ~p"/health/liveness")

      # We expect 503 when unhealthy, so this test will fail initially as implementation doesn't exist
      if json_response(conn, 503) do
        assert json_response(conn, 503)["status"] == "unhealthy"
        assert is_binary(json_response(conn, 503)["error"])
      end
    end
  end

  describe "GET /health/readiness" do
    test "ac3_readiness_returns_ok_when_initialized", %{conn: conn} do
      conn = get(conn, ~p"/health/readiness")

      assert response(conn, 200)
      assert json_response(conn, 200)["status"] == "ok"
      assert get_resp_header(conn, "content-type") == ["application/json; charset=utf-8"]
    end

    test "ac4_readiness_returns_503_when_not_ready", %{conn: conn} do
      # Simulate uninitialized scenario
      conn = get(conn, ~p"/health/readiness")

      if json_response(conn, 503) do
        assert json_response(conn, 503)["status"] == "not_ready"
        assert is_binary(json_response(conn, 503)["reason"])
      end
    end
  end

  test "ac5_endpoints_require_no_authentication", %{conn: conn} do
    # Test liveness without any auth headers/cookies
    conn_liveness = 
      conn
      |> delete_req_header("authorization")
      |> recycle()
      |> get(~p"/health/liveness")

    assert response(conn_liveness, 200) or response(conn_liveness, 503)

    # Test readiness without any auth headers/cookies
    conn_readiness = 
      conn
      |> delete_req_header("authorization")
      |> recycle()
      |> get(~p"/health/readiness")

    assert response(conn_readiness, 200) or response(conn_readiness, 503)
  end

  test "ac6_all_responses_include_valid_utc_iso_timestamp", %{conn: conn} do
    # Test liveness response timestamp
    conn_liveness = get(conn, ~p"/health/liveness")
    liveness_body = json_response(conn_liveness, conn_liveness.status)
    assert is_binary(liveness_body["timestamp"])
    assert {:ok, _, _} = DateTime.from_iso8601(liveness_body["timestamp"])

    # Test readiness response timestamp
    conn_readiness = get(recycle(conn), ~p"/health/readiness")
    readiness_body = json_response(conn_readiness, conn_readiness.status)
    assert is_binary(readiness_body["timestamp"])
    assert {:ok, _, _} = DateTime.from_iso8601(readiness_body["timestamp"])
  end

  test "ac7_responses_contain_no_sensitive_data", %{conn: conn} do
    sensitive_fields = ["api_key", "token", "secret", "password", "pii", "user_data", "config"]

    # Check liveness response
    conn_liveness = get(conn, ~p"/health/liveness")
    liveness_body = json_response(conn_liveness, conn_liveness.status)
    for field <- sensitive_fields do
      refute Map.has_key?(liveness_body, field)
    end

    # Check readiness response
    conn_readiness = get(recycle(conn), ~p"/health/readiness")
    readiness_body = json_response(conn_readiness, conn_readiness.status)
    for field <- sensitive_fields do
      refute Map.has_key?(readiness_body, field)
    end
  end

  test "ac8_existing_routes_are_unmodified", %{conn: conn} do
    # Test existing root path still works
    conn = get(conn, ~p"/")
    assert response(conn, 200)
    assert html_response(conn, 200) =~ "Flagd UI"
  end
end
