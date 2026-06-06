#!/usr/bin/env ruby

require "grpc"
require "grpc/health/v1/health_services_pb"
require "minitest/autorun"

# Test ACs for email service gRPC health check implementation
class TestEmailHealthCheck < Minitest::Test
  def setup
    @stub = Grpc::Health::V1::Health::Stub.new("localhost:8080", :this_channel_is_insecure)
  end

  # AC-1: grpc-health-check gem installs successfully
  def test_ac1_grpc_health_check_gem_installed
    assert Gem::Specification.find_all_by_name("grpc-health-check").any?, "grpc-health-check gem not installed"
  end

  # AC-2: Empty service name returns SERVING status
  def test_ac2_empty_service_returns_serving
    response = @stub.check(Grpc::Health::V1::HealthCheckRequest.new(service: ""))
    assert_equal Grpc::Health::V1::HealthCheckResponse::ServingStatus::SERVING, response.status
  end

  # AC-3: Non-empty service name returns INVALID_ARGUMENT error
  def test_ac3_non_empty_service_returns_invalid_argument
    assert_raises GRPC::InvalidArgument do
      @stub.check(Grpc::Health::V1::HealthCheckRequest.new(service: "email"))
    end

    assert_raises GRPC::InvalidArgument do
      @stub.check(Grpc::Health::V1::HealthCheckRequest.new(service: "any"))
    end
  end

  # AC-4: grpc-health-probe tool returns status 0 and SERVING output
  def test_ac4_grpc_health_probe_returns_serving
    result = system("grpc-health-probe -addr=localhost:8080")
    assert result, "grpc-health-probe failed with non-zero exit code"

    output = `grpc-health-probe -addr=localhost:8080`
    assert_includes output, "status: SERVING", "grpc-health-probe output does not contain SERVING status"
  end

  # AC-5: Existing email service functionality still works
  def test_ac5_existing_email_service_functionality_works
    # Check that existing email service methods are not broken by health check registration
    server = GRPC::RpcServer.new
    # Load existing service handlers
    load "./email_server.rb"
    original_handlers = server.instance_variable_get(:@method_handlers) || {}
    original_count = original_handlers.size

    # Register health service as would be done in implementation
    require "grpc/health/checker"
    health_checker = Grpc::Health::Checker.new
    health_checker.add_status("", Grpc::Health::V1::HealthCheckResponse::ServingStatus::SERVING)
    server.handle(health_checker)

    new_handlers = server.instance_variable_get(:@method_handlers) || {}
    # Verify original handlers still exist
    original_handlers.each do |handler_name, _|
      assert new_handlers.key?(handler_name), "Original handler #{handler_name} removed after adding health service"
    end
    assert new_handlers.size > original_count, "Health service not added correctly"
  end
end
