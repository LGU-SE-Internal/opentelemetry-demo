defmodule FlagdUiWeb.HealthControllerTest do
  use FlagdUiWeb.ConnCase
  import Mox

  # Setup Mox for mocking FlagdClient
  setup :verify_on_exit!

  describe "AC-1: Liveness endpoint" do
    test "ac1_liveness_returns_200_ok_with_required_payload", %{conn: conn} do
      conn = get(conn, ~p"/health/liveness")

      # Verify status code is always 200
      assert response(conn, 200)
      assert get_resp_header(conn, "content-type") == ["application/json; charset=utf-8"]

      # Verify payload structure
      payload = json_response(conn, 200)
      assert payload["status"] == "ok"
      assert payload["check"] == "liveness"
      assert is_binary(payload["timestamp"])
    end
  end

  describe "AC-2: Readiness endpoint when flagd connection fails" do
    test "ac2_readiness_returns_503_when_flagd_connection_unhealthy", %{conn: conn} do
      # Mock FlagdClient.ping/0 to return error
      FlagdUi.FlagdClientMock
      |> expect(:ping, fn -> {:error, "connection refused to flagd:8013"} end)

      conn = get(conn, ~p"/health/readiness")

      # Verify status code is 503
      assert response(conn, 503)
      assert get_resp_header(conn, "content-type") == ["application/json; charset=utf-8"]

      # Verify error payload structure
      payload = json_response(conn, 503)
      assert payload["status"] == "unavailable"
      assert payload["check"] == "readiness"
      assert payload["flagd_connection"] == "unhealthy"
      assert payload["error"] == "connection refused to flagd:8013"
      assert is_binary(payload["timestamp"])
    end
  end

  describe "AC-3: Readiness endpoint when flagd connection succeeds" do
    test "ac3_readiness_returns_200_ok_when_flagd_connection_healthy", %{conn: conn} do
      # Mock FlagdClient.ping/0 to return success
      FlagdUi.FlagdClientMock
      |> expect(:ping, fn -> :ok end)

      conn = get(conn, ~p"/health/readiness")

      # Verify status code is 200
      assert response(conn, 200)
      assert get_resp_header(conn, "content-type") == ["application/json; charset=utf-8"]

      # Verify success payload structure
      payload = json_response(conn, 200)
      assert payload["status"] == "ok"
      assert payload["check"] == "readiness"
      assert payload["flagd_connection"] == "healthy"
      assert is_binary(payload["timestamp"])
    end
  end

  describe "AC-4: All health endpoints include ISO 8601 timestamp" do
    test "ac4_liveness_response_has_valid_iso8601_timestamp", %{conn: conn} do
      conn = get(conn, ~p"/health/liveness")
      payload = json_response(conn, 200)
      assert {:ok, datetime, _offset} = DateTime.from_iso8601(payload["timestamp"])
      assert datetime.time_zone == "Etc/UTC"
    end

    test "ac4_readiness_response_has_valid_iso8601_timestamp", %{conn: conn} do
      FlagdUi.FlagdClientMock
      |> expect(:ping, fn -> :ok end)

      conn = get(conn, ~p"/health/readiness")
      payload = json_response(conn, 200)
      assert {:ok, datetime, _offset} = DateTime.from_iso8601(payload["timestamp"])
      assert datetime.time_zone == "Etc/UTC"
    end
  end

  describe "AC-5: Liveness endpoint always returns 200" do
    test "ac5_liveness_returns_200_under_all_conditions", %{conn: conn} do
      # Test without any mocks, even if other services are down
      conn = get(conn, ~p"/health/liveness")
      assert response(conn, 200)
      
      # Test with invalid headers, still returns 200
      conn = 
        conn
        |> put_req_header("x-invalid-header", "invalid-value")
        |> get(~p"/health/liveness")
        
      assert response(conn, 200)
    end
  end

  describe "AC-6: Readiness endpoint handles mock ping responses correctly" do
    test "ac6_readiness_handles_successful_ping", %{conn: conn} do
      FlagdUi.FlagdClientMock
      |> expect(:ping, 2, fn -> :ok end)

      # Multiple calls should all succeed
      conn1 = get(conn, ~p"/health/readiness")
      assert response(conn1, 200)
      
      conn2 = get(recycle(conn), ~p"/health/readiness")
      assert response(conn2, 200)
    end

    test "ac6_readiness_handles_failed_ping", %{conn: conn} do
      FlagdUi.FlagdClientMock
      |> expect(:ping, 2, fn -> {:error, "timeout"} end)

      # Multiple calls should all fail appropriately
      conn1 = get(conn, ~p"/health/readiness")
      assert response(conn1, 503)
      assert json_response(conn1, 503)["error"] == "timeout"
      
      conn2 = get(recycle(conn), ~p"/health/readiness")
      assert response(conn2, 503)
      assert json_response(conn2, 503)["error"] == "timeout"
    end
  end

  describe "AC-7: Integration test with real flagd connectivity" do
    @tag :integration
    test "ac7_readiness_correctly_detects_real_flagd_status", %{conn: conn} do
      # This test runs against a real flagd instance in the test environment
      conn = get(conn, ~p"/health/readiness")
      
      # Either status is acceptable depending on test environment flagd availability
      if response(conn) == 200 do
        assert json_response(conn, 200)["flagd_connection"] == "healthy"
      else
        assert response(conn) == 503
        assert json_response(conn, 503)["flagd_connection"] == "unhealthy"
        assert is_binary(json_response(conn, 503)["error"])
      end
    end
  end
end

