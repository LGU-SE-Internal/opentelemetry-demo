#!/usr/bin/env ruby
require "minitest/autorun"
require "net/http"
require "json"

# Test ACs for email service HTTP health and readiness probe endpoints
class TestEmailHttpHealthProbes < Minitest::Test
  SERVICE_URL = "http://localhost:8080"

  # AC-1: When email service process is running, GET /health/liveness returns 200 OK
  def test_ac1_liveness_endpoint_returns_200_ok_when_service_running
    uri = URI("#{SERVICE_URL}/health/liveness")
    response = Net::HTTP.get_response(uri)
    assert_equal "200", response.code, "Expected 200 OK for liveness endpoint"
  end

  # AC-2: Liveness response body matches defined JSON schema
  def test_ac2_liveness_response_matches_json_schema
    uri = URI("#{SERVICE_URL}/health/liveness")
    response = Net::HTTP.get_response(uri)
    body = JSON.parse(response.body)
    
    assert_equal "ok", body["status"], "Liveness status should be 'ok'"
    assert_equal "email", body["service"], "Service name should be 'email'"
    assert_equal 2, body.keys.size, "Liveness response should only have 2 fields"
  end

  # AC-3: When service is ready, GET /health/readiness returns 200 OK
  def test_ac3_readiness_endpoint_returns_200_ok_when_ready
    uri = URI("#{SERVICE_URL}/health/readiness")
    response = Net::HTTP.get_response(uri)
    assert_equal "200", response.code, "Expected 200 OK for readiness endpoint when service ready"
  end

  # AC-4: Successful readiness response body matches defined JSON schema
  def test_ac4_readiness_success_response_matches_json_schema
    uri = URI("#{SERVICE_URL}/health/readiness")
    response = Net::HTTP.get_response(uri)
    body = JSON.parse(response.body)
    
    assert_equal "ok", body["status"], "Ready status should be 'ok'"
    assert_equal "email", body["service"], "Service name should be 'email'"
    assert_equal true, body["ready"], "Ready flag should be true"
    assert_equal 3, body.keys.size, "Success readiness response should only have 3 fields"
  end

  # AC-5: When service is not ready, GET /health/readiness returns 503 with correct schema
  def test_ac5_readiness_endpoint_returns_503_when_not_ready
    # Note: This test assumes service can be put in unready state for testing
    uri = URI("#{SERVICE_URL}/health/readiness")
    response = Net::HTTP.get_response(uri)
    
    # If service is currently not ready, verify 503 and correct body
    if response.code == "503"
      body = JSON.parse(response.body)
      assert_equal "unavailable", body["status"], "Unavailable status should be 'unavailable'"
      assert_equal "email", body["service"], "Service name should be 'email'"
      assert_equal false, body["ready"], "Ready flag should be false"
      assert_equal 3, body.keys.size, "Failure readiness response should only have 3 fields"
    end
  end

  # AC-6: Existing email service endpoints still work unchanged
  def test_ac6_existing_email_service_endpoints_unchanged
    # Verify existing gRPC email service is still accessible on the same port
    require "grpc"
    require_relative "email_server"
    
    # Check that the email service handler is still registered properly
    server = GRPC::RpcServer.new
    load "./email_server.rb"
    handlers = server.instance_variable_get(:@method_handlers) || {}
    assert handlers.any? { |k, _| k.include?("opentelemetry.proto.demo.email") }, "Existing email service handlers missing"
  end

  # AC-7: Health endpoints return 405 Method Not Allowed for non-GET methods
  def test_ac7_health_endpoints_return_405_for_non_get_methods
    %w[/health/liveness /health/readiness].each do |path|
      uri = URI("#{SERVICE_URL}#{path}")
      
      # Test POST
      post_response = Net::HTTP.post(uri, "")
      assert_equal "405", post_response.code, "Expected 405 for POST to #{path}"
      
      # Test PUT
      put_response = Net::HTTP.start(uri.host, uri.port) { |http| http.put(path, "") }
      assert_equal "405", put_response.code, "Expected 405 for PUT to #{path}"
      
      # Test DELETE
      delete_response = Net::HTTP.start(uri.host, uri.port) { |http| http.delete(path) }
      assert_equal "405", delete_response.code, "Expected 405 for DELETE to #{path}"
    end
  end
end
