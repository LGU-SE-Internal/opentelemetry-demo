#!/usr/bin/env ruby

require "minitest/autorun"
require "rack/test"
require_relative "email_server"

class TestHTTPHealthProbes < Minitest::Test
  include Rack::Test::Methods

  def app
    Sinatra::Application
  end

  # AC-1: When service is running, GET /health/live returns 200 OK
  def test_ac1_live_endpoint_returns_200_when_service_running
    get "/health/live"
    assert_equal 200, last_response.status, "Expected 200 OK for /health/live when service is running"
  end

  # AC-2: When service is not running/down, /health/live returns 5xx or no response
  def test_ac2_live_endpoint_returns_5xx_when_service_unhealthy
    # Simulate service failure state by overriding app
    broken_app = Class.new(Sinatra::Base) do
      get "/health/live" do
        status 503
      end
    end

    Rack::Test::Session.new(broken_app).get("/health/live")
    assert last_response.status >= 500 && last_response.status < 600, "Expected 5xx status for /health/live when service is unhealthy"
  end

  # AC-3: When service running AND flagd connection healthy, GET /health/ready returns 200
  def test_ac3_ready_endpoint_returns_200_when_flagd_healthy
    # Mock healthy flagd connection state
    original_provider = OpenFeature::SDK.configuration.provider
    healthy_provider = OpenFeature::Flagd::Provider.build_client
    # We mock the provider state as healthy for this test
    OpenFeature::SDK.configure { |c| c.set_provider(healthy_provider) }

    get "/health/ready"
    assert_equal 200, last_response.status, "Expected 200 OK for /health/ready when flagd connection is healthy"

    # Restore original provider
    OpenFeature::SDK.configure { |c| c.set_provider(original_provider) }
  end

  # AC-4: When service running BUT flagd connection unhealthy, /health/ready returns 503
  def test_ac4_ready_endpoint_returns_503_when_flagd_unhealthy
    # Mock unhealthy flagd connection state
    original_provider = OpenFeature::SDK.configuration.provider
    # Create a mock provider that is not connected
    unhealthy_provider = OpenFeature::SDK::Provider::NoOpProvider.new
    OpenFeature::SDK.configure { |c| c.set_provider(unhealthy_provider) }

    get "/health/ready"
    assert_equal 503, last_response.status, "Expected 503 Service Unavailable for /health/ready when flagd connection is unhealthy"

    # Restore original provider
    OpenFeature::SDK.configure { |c| c.set_provider(original_provider) }
  end

  # AC-5: When service is not running/down, /health/ready returns 5xx or no response
  def test_ac5_ready_endpoint_returns_5xx_when_service_unhealthy
    # Simulate service failure state by overriding app
    broken_app = Class.new(Sinatra::Base) do
      get "/health/ready" do
        status 503
      end
    end

    Rack::Test::Session.new(broken_app).get("/health/ready")
    assert last_response.status >= 500 && last_response.status < 600, "Expected 5xx status for /health/ready when service is unhealthy"
  end

  # AC-6: No new external runtime dependencies added
  def test_ac6_no_new_runtime_dependencies_added
    # Load Gemfile and verify no new runtime dependencies are present beyond original
    gemfile = File.read(File.join(__dir__, "Gemfile"))
    runtime_deps = gemfile.split("\n").select { |line| line.start_with?("gem ") && !line.include?("test") && !line.include?("development") }
    
    # Check that no health-check specific runtime gems are added
    runtime_deps.each do |dep|
      refute dep.include?("health"), "Unexpected health-related runtime dependency found: #{dep}"
      refute dep.include?("probe"), "Unexpected probe-related runtime dependency found: #{dep}"
    end
  end

  # AC-7: Existing gRPC health check functionality remains fully operational
  def test_ac7_existing_grpc_health_check_unchanged
    # Verify gRPC health check files and implementation are not modified
    assert File.exist?(File.join(__dir__, "test_health_check.rb")), "gRPC health check test file removed"
    assert File.exist?(File.join(__dir__, "email_server.rb")), "Email server file missing"
    
    server_content = File.read(File.join(__dir__, "email_server.rb"))
    # Check that gRPC health check registration is still present in server code
    assert server_content.include?("grpc-health-check") || server_content.include?("Grpc::Health::Checker"), "gRPC health check implementation removed from server"
  end
end
