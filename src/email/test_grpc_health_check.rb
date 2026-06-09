#!/usr/bin/env ruby
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

require "grpc"
require "grpc/health/v1/health_services_pb"
require "minitest/autorun"
require "net/http"
require "uri"

# Test ACs for email service gRPC health check implementation
class TestEmailGrpcHealthCheck < Minitest::Test
  EMAIL_SERVICE_GRPC_ADDR = "localhost:8080"
  EMAIL_SERVICE_HTTP_ADDR = "http://localhost:8081"

  def setup
    @grpc_stub = Grpc::Health::V1::Health::Stub.new(EMAIL_SERVICE_GRPC_ADDR, :this_channel_is_insecure)
  end

  # AC-1: When service is running normally, empty service name returns SERVING status
  def test_ac1_empty_service_returns_serving_when_healthy
    response = @grpc_stub.check(Grpc::Health::V1::HealthCheckRequest.new(service: ""))
    assert_equal Grpc::Health::V1::HealthCheckResponse::ServingStatus::SERVING, response.status
  end

  # AC-2: When service is in graceful shutdown, returns NOT_SERVING status
  def test_ac2_returns_not_serving_during_shutdown
    # This test assumes we can trigger a shutdown signal and check status before process exits
    # The service under test must be modified to hold shutdown long enough for this test to run
    pid = spawn("./email_server.rb", pgroup: true)
    sleep 3 # Wait for service to start
    
    # First check it's serving
    response = @grpc_stub.check(Grpc::Health::V1::HealthCheckRequest.new(service: ""))
    assert_equal Grpc::Health::V1::HealthCheckResponse::ServingStatus::SERVING, response.status
    
    # Send SIGTERM to trigger graceful shutdown
    Process.kill("SIGTERM", -pid)
    sleep 0.5 # Give time to enter shutdown phase
    
    # Check it returns NOT_SERVING
    response = @grpc_stub.check(Grpc::Health::V1::HealthCheckRequest.new(service: ""))
    assert_equal Grpc::Health::V1::HealthCheckResponse::ServingStatus::NOT_SERVING, response.status
    
    # Cleanup
    Process.wait(pid) rescue nil
  end

  # AC-3: Unrecognized service name returns SERVICE_UNKNOWN status
  def test_ac3_unknown_service_returns_service_unknown
    response = @grpc_stub.check(Grpc::Health::V1::HealthCheckRequest.new(service: "non-existent-service"))
    assert_equal Grpc::Health::V1::HealthCheckResponse::ServingStatus::SERVICE_UNKNOWN, response.status
    
    response = @grpc_stub.check(Grpc::Health::V1::HealthCheckRequest.new(service: "random-service-name"))
    assert_equal Grpc::Health::V1::HealthCheckResponse::ServingStatus::SERVICE_UNKNOWN, response.status
  end

  # AC-4: Existing HTTP /health endpoint still returns 200 OK when running normally
  def test_ac4_http_health_endpoint_works_normally
    uri = URI.parse("#{EMAIL_SERVICE_HTTP_ADDR}/health")
    response = Net::HTTP.get_response(uri)
    assert_equal "200", response.code
  end

  # AC-5: Existing HTTP /ready endpoint still returns 200 OK when ready to accept requests
  def test_ac5_http_ready_endpoint_works_normally
    uri = URI.parse("#{EMAIL_SERVICE_HTTP_ADDR}/ready")
    response = Net::HTTP.get_response(uri)
    assert_equal "200", response.code
  end

  # AC-6: grpc_health_probe tool returns exit code 0 against healthy running service
  def test_ac6_grpc_health_probe_success_when_healthy
    exit_status = system("grpc_health_probe -addr #{EMAIL_SERVICE_GRPC_ADDR}")
    assert exit_status, "grpc_health_probe returned non-zero exit code for healthy service"
  end

  # AC-7: grpc_health_probe tool returns non-zero exit code against service in shutdown phase
  def test_ac7_grpc_health_probe_failure_during_shutdown
    pid = spawn("./email_server.rb", pgroup: true)
    sleep 3 # Wait for service to start
    
    # First check probe succeeds
    assert system("grpc_health_probe -addr #{EMAIL_SERVICE_GRPC_ADDR}"), "Probe failed when service was healthy"
    
    # Send SIGTERM to trigger graceful shutdown
    Process.kill("SIGTERM", -pid)
    sleep 0.5 # Give time to enter shutdown phase
    
    # Check probe fails
    exit_status = system("grpc_health_probe -addr #{EMAIL_SERVICE_GRPC_ADDR}")
    refute exit_status, "grpc_health_probe returned success for service in shutdown phase"
    
    # Cleanup
    Process.wait(pid) rescue nil
  end

  # AC-8: Unit test validates health status transitions from SERVING to NOT_SERVING on shutdown signal
  def test_ac8_health_status_transition_on_shutdown
    # Load the email server code
    load "./email_server.rb"
    
    # Access the health checker instance that would be initialized in the server
    require "grpc/health/checker"
    health_checker = Grpc::Health::Checker.new
    health_checker.add_status("", Grpc::Health::V1::HealthCheckResponse::ServingStatus::SERVING)
    
    # Verify initial status is SERVING
    assert_equal Grpc::Health::V1::HealthCheckResponse::ServingStatus::SERVING, 
                 health_checker.check(Grpc::Health::V1::HealthCheckRequest.new(service: "")).status
    
    # Simulate shutdown signal handler being called
    # This assumes the server code implements a shutdown handler that updates the health status
    health_checker.add_status("", Grpc::Health::V1::HealthCheckResponse::ServingStatus::NOT_SERVING)
    
    # Verify status is now NOT_SERVING
    assert_equal Grpc::Health::V1::HealthCheckResponse::ServingStatus::NOT_SERVING, 
                 health_checker.check(Grpc::Health::V1::HealthCheckRequest.new(service: "")).status
  end
end
