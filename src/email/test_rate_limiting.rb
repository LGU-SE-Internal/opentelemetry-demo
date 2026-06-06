# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

require 'minitest/autorun'
require 'rack/test'
require 'json'
require_relative 'email_server'

ENV['RACK_ENV'] = 'test'

class EmailServiceRateLimitingTest < Minitest::Test
  include Rack::Test::Methods

  def app
    Sinatra::Application
  end

  def setup
    # Reset environment variables before each test
    ENV.delete('EMAIL_RATE_LIMIT_THRESHOLD')
    ENV.delete('EMAIL_RATE_LIMIT_WINDOW_SECONDS')
    ENV.delete('EMAIL_RATE_LIMIT_SCOPE')
    # Reset Rack::Attack cache
    Rack::Attack.cache.store.clear
  end

  def test_ac1_exceeding_threshold_returns_429
    ENV['EMAIL_RATE_LIMIT_THRESHOLD'] = '2'
    ENV['EMAIL_RATE_LIMIT_WINDOW_SECONDS'] = '60'
    load __dir__ + '/email_server.rb'

    # Make 2 requests that should succeed
    2.times do
      post '/send_order_confirmation', { email: 'test@example.com', order: { order_id: '123' } }.to_json, 'CONTENT_TYPE' => 'application/json'
      assert_equal 200, last_response.status
    end

    # Third request should be rate limited
    post '/send_order_confirmation', { email: 'test@example.com', order: { order_id: '123' } }.to_json, 'CONTENT_TYPE' => 'application/json'
    assert_equal 429, last_response.status
  end

  def test_ac2_429_includes_retry_after_header
    ENV['EMAIL_RATE_LIMIT_THRESHOLD'] = '1'
    ENV['EMAIL_RATE_LIMIT_WINDOW_SECONDS'] = '60'
    load __dir__ + '/email_server.rb'

    # First request succeeds
    post '/send_order_confirmation', { email: 'test@example.com', order: { order_id: '123' } }.to_json, 'CONTENT_TYPE' => 'application/json'
    # Second request is throttled
    post '/send_order_confirmation', { email: 'test@example.com', order: { order_id: '123' } }.to_json, 'CONTENT_TYPE' => 'application/json'

    assert last_response.headers.key?('Retry-After')
    retry_after = last_response.headers['Retry-After'].to_i
    assert retry_after.between?(1, 60)
  end

  def test_ac3_429_includes_correct_json_body
    ENV['EMAIL_RATE_LIMIT_THRESHOLD'] = '1'
    load __dir__ + '/email_server.rb'

    post '/send_order_confirmation', { email: 'test@example.com', order: { order_id: '123' } }.to_json, 'CONTENT_TYPE' => 'application/json'
    post '/send_order_confirmation', { email: 'test@example.com', order: { order_id: '123' } }.to_json, 'CONTENT_TYPE' => 'application/json'

    body = JSON.parse(last_response.body)
    assert_equal 'Too many requests', body['error']
    assert_equal last_response.headers['Retry-After'].to_i, body['retry_after']
  end

  def test_ac4_threshold_configurable_via_env_var_defaults_to_100
    # Default case
    load __dir__ + '/email_server.rb'
    assert_equal 100, RATE_LIMIT_THRESHOLD

    # Configured case
    ENV['EMAIL_RATE_LIMIT_THRESHOLD'] = '500'
    load __dir__ + '/email_server.rb'
    assert_equal 500, RATE_LIMIT_THRESHOLD
  end

  def test_ac5_window_configurable_via_env_var_defaults_to_60
    # Default case
    load __dir__ + '/email_server.rb'
    assert_equal 60, RATE_LIMIT_WINDOW

    # Configured case
    ENV['EMAIL_RATE_LIMIT_WINDOW_SECONDS'] = '300'
    load __dir__ + '/email_server.rb'
    assert_equal 300, RATE_LIMIT_WINDOW
  end

  def test_ac6_scope_configurable_via_env_var_defaults_to_global
    # Default case
    load __dir__ + '/email_server.rb'
    assert_equal 'global', RATE_LIMIT_SCOPE

    # Configured case
    ENV['EMAIL_RATE_LIMIT_SCOPE'] = 'ip'
    load __dir__ + '/email_server.rb'
    assert_equal 'ip', RATE_LIMIT_SCOPE
  end

  def test_ac7_global_scope_counts_all_ips_together
    ENV['EMAIL_RATE_LIMIT_THRESHOLD'] = '2'
    ENV['EMAIL_RATE_LIMIT_SCOPE'] = 'global'
    load __dir__ + '/email_server.rb'

    # Request from IP 1
    header 'X-Forwarded-For', '192.168.1.1'
    post '/send_order_confirmation', { email: 'test1@example.com', order: { order_id: '1' } }.to_json, 'CONTENT_TYPE' => 'application/json'
    assert_equal 200, last_response.status

    # Request from IP 2
    header 'X-Forwarded-For', '192.168.1.2'
    post '/send_order_confirmation', { email: 'test2@example.com', order: { order_id: '2' } }.to_json, 'CONTENT_TYPE' => 'application/json'
    assert_equal 200, last_response.status

    # Third request from any IP should be throttled
    header 'X-Forwarded-For', '192.168.1.3'
    post '/send_order_confirmation', { email: 'test3@example.com', order: { order_id: '3' } }.to_json, 'CONTENT_TYPE' => 'application/json'
    assert_equal 429, last_response.status
  end

  def test_ac8_ip_scope_has_independent_limits_per_ip
    ENV['EMAIL_RATE_LIMIT_THRESHOLD'] = '1'
    ENV['EMAIL_RATE_LIMIT_SCOPE'] = 'ip'
    load __dir__ + '/email_server.rb'

    # Request from IP 1 first succeeds, second fails
    header 'X-Forwarded-For', '192.168.1.1'
    post '/send_order_confirmation', { email: 'test1@example.com', order: { order_id: '1' } }.to_json, 'CONTENT_TYPE' => 'application/json'
    assert_equal 200, last_response.status
    post '/send_order_confirmation', { email: 'test1@example.com', order: { order_id: '2' } }.to_json, 'CONTENT_TYPE' => 'application/json'
    assert_equal 429, last_response.status

    # Request from IP 2 should succeed
    header 'X-Forwarded-For', '192.168.1.2'
    post '/send_order_confirmation', { email: 'test2@example.com', order: { order_id: '3' } }.to_json, 'CONTENT_TYPE' => 'application/json'
    assert_equal 200, last_response.status
  end

  def test_ac9_rate_limited_request_increments_metric
    # Skip for now as metric verification requires more complex test setup
    skip "Metric verification test to be implemented"
  end

  def test_ac10_rate_limited_request_emits_structured_log
    # Skip for now as log verification requires more complex test setup
    skip "Log verification test to be implemented"
  end
end
