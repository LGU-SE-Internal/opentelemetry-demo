#!/usr/bin/env ruby
# frozen_string_literal: true

require 'grpc'
require 'minitest/autorun'
require_relative 'email_server'
require 'opentelemetry/sdk'

# Test rate limiting acceptance criteria for email service gRPC endpoints
class TestRateLimitAC < Minitest::Test
  def setup
    @server = GRPC::RpcServer.new
    @server.add_http2_port('0.0.0.0:8080', :this_port_is_insecure)
    @server.handle(EmailService::Server.new)
    @server_thread = Thread.new { @server.run }
    sleep 0.1 until @server.running?
    @stub = EmailService::Stub.new('localhost:8080', :this_channel_is_insecure)
  end

  def teardown
    @server.stop
    @server_thread.join
  end

  # AC-1: Rate limiting applied per client IP address
  def test_ac1_per_client_ip_rate_limiting
    # Configure rate limit to 2 requests per 10s window
    ENV['EMAIL_SERVICE_RATE_LIMIT_MAX_REQUESTS'] = '2'
    ENV['EMAIL_SERVICE_RATE_LIMIT_WINDOW_MS'] = '10000'

    # Simulate requests from IP 192.168.1.1
    client_ip1 = '192.168.1.1'
    # First two requests should succeed
    2.times do
      response = @stub.send_email(
        EmailRequest.new(to: 'test@example.com', subject: 'Test', body: 'Test'),
        metadata: { 'x-forwarded-for' => client_ip1 }
      )
      assert_equal EmailResponse::STATUS_OK, response.status
    end

    # Third request from same IP should be rejected
    assert_raises GRPC::ResourceExhausted do
      @stub.send_email(
        EmailRequest.new(to: 'test@example.com', subject: 'Test', body: 'Test'),
        metadata: { 'x-forwarded-for' => client_ip1 }
      )
    end

    # Request from different IP should succeed
    client_ip2 = '192.168.1.2'
    response = @stub.send_email(
      EmailRequest.new(to: 'test@example.com', subject: 'Test', body: 'Test'),
      metadata: { 'x-forwarded-for' => client_ip2 }
    )
    assert_equal EmailResponse::STATUS_OK, response.status

    ENV.delete('EMAIL_SERVICE_RATE_LIMIT_MAX_REQUESTS')
    ENV.delete('EMAIL_SERVICE_RATE_LIMIT_WINDOW_MS')
  end

  # AC-2: Rate limit parameters configurable via environment variables
  def test_ac2_env_var_configuration
    # Test custom config
    custom_max = 50
    custom_window = 30000
    ENV['EMAIL_SERVICE_RATE_LIMIT_MAX_REQUESTS'] = custom_max.to_s
    ENV['EMAIL_SERVICE_RATE_LIMIT_WINDOW_MS'] = custom_window.to_s

    # Verify config is loaded correctly
    config = RateLimitConfig.new
    assert_equal custom_max, config.max_requests
    assert_equal custom_window, config.window_ms

    ENV.delete('EMAIL_SERVICE_RATE_LIMIT_MAX_REQUESTS')
    ENV.delete('EMAIL_SERVICE_RATE_LIMIT_WINDOW_MS')

    # Test default config when no env vars set
    default_config = RateLimitConfig.new
    assert_equal 100, default_config.max_requests
    assert_equal 60000, default_config.window_ms
  end

  # AC-3: Rejected requests return correct gRPC status
  def test_ac3_resource_exhausted_status
    ENV['EMAIL_SERVICE_RATE_LIMIT_MAX_REQUESTS'] = '1'
    ENV['EMAIL_SERVICE_RATE_LIMIT_WINDOW_MS'] = '10000'

    client_ip = '192.168.1.10'
    # First request succeeds
    @stub.send_email(
      EmailRequest.new(to: 'test@example.com', subject: 'Test', body: 'Test'),
      metadata: { 'x-forwarded-for' => client_ip }
    )

    # Second request fails with correct status and message
    exception = assert_raises GRPC::ResourceExhausted do
      @stub.send_email(
        EmailRequest.new(to: 'test@example.com', subject: 'Test', body: 'Test'),
        metadata: { 'x-forwarded-for' => client_ip }
      )
    end

    assert_equal 8, exception.code # RESOURCE_EXHAUSTED status code
    assert_equal 'Rate limit exceeded. Try again later.', exception.message

    ENV.delete('EMAIL_SERVICE_RATE_LIMIT_MAX_REQUESTS')
    ENV.delete('EMAIL_SERVICE_RATE_LIMIT_WINDOW_MS')
  end

  # AC-4: Rate limit violations emit structured OTel logs
  def test_ac4_otel_logs_on_violation
    ENV['EMAIL_SERVICE_RATE_LIMIT_MAX_REQUESTS'] = '1'
    ENV['EMAIL_SERVICE_RATE_LIMIT_WINDOW_MS'] = '10000'

    client_ip = '192.168.1.20'
    method_name = 'opentelemetry.demo.email.v1.EmailService/SendEmail'

    # Capture OTel logs
    log_exporter = InMemoryLogExporter.new
    OpenTelemetry.sdk.logs.add_log_record_processor(
      OpenTelemetry::SDK::Logs::Export::SimpleLogRecordProcessor.new(log_exporter)
    )

    # Trigger violation
    @stub.send_email(
      EmailRequest.new(to: 'test@example.com', subject: 'Test', body: 'Test'),
      metadata: { 'x-forwarded-for' => client_ip }
    )
    assert_raises GRPC::ResourceExhausted do
      @stub.send_email(
        EmailRequest.new(to: 'test@example.com', subject: 'Test', body: 'Test'),
        metadata: { 'x-forwarded-for' => client_ip }
      )
    end

    # Verify log entry exists with all required attributes
    violation_logs = log_exporter.log_records.select do |log|
      log.attributes['event.name'] == 'rate_limit_violation'
    end
    assert_equal 1, violation_logs.count

    log_attrs = violation_logs.first.attributes
    assert_equal client_ip, log_attrs['client_ip']
    assert_equal method_name, log_attrs['grpc_method']
    assert_equal 1, log_attrs['rate_limit_max']
    assert_equal 10000, log_attrs['rate_limit_window_ms']
    assert log_attrs['violation_timestamp'].is_a? Integer
    assert log_attrs['violation_timestamp'] > 0

    ENV.delete('EMAIL_SERVICE_RATE_LIMIT_MAX_REQUESTS')
    ENV.delete('EMAIL_SERVICE_RATE_LIMIT_WINDOW_MS')
  end

  # AC-5: Rate limit violations emit correct OTel metrics
  def test_ac5_otel_metrics_on_violation
    ENV['EMAIL_SERVICE_RATE_LIMIT_MAX_REQUESTS'] = '2'
    ENV['EMAIL_SERVICE_RATE_LIMIT_WINDOW_MS'] = '10000'

    client_ip = '192.168.1.30'
    method_name = 'opentelemetry.demo.email.v1.EmailService/SendEmail'

    # Capture OTel metrics
    metric_reader = OpenTelemetry::SDK::Metrics::Export::InMemoryMetricReader.new
    OpenTelemetry.sdk.metrics.add_metric_reader(metric_reader)

    # First two requests, active count should increment to 2
    2.times do
      @stub.send_email(
        EmailRequest.new(to: 'test@example.com', subject: 'Test', body: 'Test'),
        metadata: { 'x-forwarded-for' => client_ip }
      )
    end

    # Check active request gauge is 2
    metric_reader.collect do |resource_metrics|
      gauge_metric = resource_metrics.metrics.find { |m| m.name == 'email_service_rate_limit_active_request_count' }
      assert_not_nil gauge_metric
      gauge_point = gauge_metric.data_points.find do |p|
        p.attributes['client_ip'] == client_ip && p.attributes['grpc_method'] == method_name
      end
      assert_equal 2, gauge_point.value
    end

    # Trigger violation
    assert_raises GRPC::ResourceExhausted do
      @stub.send_email(
        EmailRequest.new(to: 'test@example.com', subject: 'Test', body: 'Test'),
        metadata: { 'x-forwarded-for' => client_ip }
      )
    end

    # Check violation counter incremented by 1
    metric_reader.collect do |resource_metrics|
      counter_metric = resource_metrics.metrics.find { |m| m.name == 'email_service_rate_limit_violations' }
      assert_not_nil counter_metric
      counter_point = counter_metric.data_points.find do |p|
        p.attributes['client_ip'] == client_ip && p.attributes['grpc_method'] == method_name
      end
      assert_equal 1, counter_point.value
    end

    ENV.delete('EMAIL_SERVICE_RATE_LIMIT_MAX_REQUESTS')
    ENV.delete('EMAIL_SERVICE_RATE_LIMIT_WINDOW_MS')
  end

  # AC-6: Existing functionality preserved for requests under rate limit
  def test_ac6_existing_functionality_preserved
    # Test valid requests work normally
    valid_request = EmailRequest.new(to: 'valid@example.com', subject: 'Valid', body: 'Valid content')
    response = @stub.send_email(valid_request)
    assert_equal EmailResponse::STATUS_OK, response.status
    assert_not_nil response.message_id

    # Test invalid requests return expected errors unchanged
    invalid_request = EmailRequest.new(to: 'invalid-email', subject: 'Invalid', body: 'Content')
    exception = assert_raises GRPC::InvalidArgument do
      @stub.send_email(invalid_request)
    end
    assert_equal 'Invalid email address format', exception.message
  end

  # AC-7: Rate limiting applies to all exposed gRPC endpoints
  def test_ac7_all_grpc_endpoints_limited
    ENV['EMAIL_SERVICE_RATE_LIMIT_MAX_REQUESTS'] = '1'
    ENV['EMAIL_SERVICE_RATE_LIMIT_WINDOW_MS'] = '10000'
    client_ip = '192.168.1.40'

    # Test SendEmail endpoint
    @stub.send_email(
      EmailRequest.new(to: 'test@example.com', subject: 'Test', body: 'Test'),
      metadata: { 'x-forwarded-for' => client_ip }
    )
    assert_raises GRPC::ResourceExhausted do
      @stub.send_email(
        EmailRequest.new(to: 'test@example.com', subject: 'Test', body: 'Test'),
        metadata: { 'x-forwarded-for' => client_ip }
      )
    end

    # Reset rate limit state
    sleep 10.1

    # Test other endpoint (e.g. GetEmailStatus) is also limited
    @stub.get_email_status(
      GetStatusRequest.new(message_id: 'test-msg-id'),
      metadata: { 'x-forwarded-for' => client_ip }
    )
    assert_raises GRPC::ResourceExhausted do
      @stub.get_email_status(
        GetStatusRequest.new(message_id: 'test-msg-id'),
        metadata: { 'x-forwarded-for' => client_ip }
      )
    end

    ENV.delete('EMAIL_SERVICE_RATE_LIMIT_MAX_REQUESTS')
    ENV.delete('EMAIL_SERVICE_RATE_LIMIT_WINDOW_MS')
  end
end
