require 'minitest/autorun'
require 'rack/test'
require 'json'
require_relative 'email_server'

class EmailValidationTest < Minitest::Test
  include Rack::Test::Methods

  def app
    Sinatra::Application
  end

  def setup
    @valid_payload = {
      email: "valid.user@example.com",
      order: {
        order_id: "ORD-123456789"
      }
    }
  end

  # AC-1: Invalid email format returns 400 with "email" in invalid_fields
  def test_ac1_invalid_email_format_returns_400
    invalid_emails = [
      "invalid-email",
      "user@.com",
      "no-domain.com",
      "user@domain",
      "@domain.com",
      "user@domain..com"
    ]

    invalid_emails.each do |invalid_email|
      payload = @valid_payload.merge(email: invalid_email)
      post '/send', payload.to_json, 'CONTENT_TYPE' => 'application/json'

      assert_equal 400, last_response.status, "Expected 400 for email: #{invalid_email}"
      
      response_body = JSON.parse(last_response.body)
      assert_equal "Invalid request parameters", response_body["error"]
      assert_includes response_body["invalid_fields"], "email"
      assert response_body.key?("request_id"), "Response missing request_id"
    end
  end

  # AC-2: Missing order_id field returns 400 with "order.order_id" in invalid_fields
  def test_ac2_missing_order_id_returns_400
    payload = @valid_payload.merge(order: {})
    post '/send', payload.to_json, 'CONTENT_TYPE' => 'application/json'

    assert_equal 400, last_response.status
    
    response_body = JSON.parse(last_response.body)
    assert_equal "Invalid request parameters", response_body["error"]
    assert_includes response_body["invalid_fields"], "order.order_id"
    assert response_body.key?("request_id")
  end

  # AC-3: Empty/whitespace order_id returns 400 with "order.order_id" in invalid_fields
  def test_ac3_empty_order_id_returns_400
    empty_order_ids = ["", "   ", "\n", "\t"]

    empty_order_ids.each do |empty_id|
      payload = @valid_payload.merge(order: { order_id: empty_id })
      post '/send', payload.to_json, 'CONTENT_TYPE' => 'application/json'

      assert_equal 400, last_response.status, "Expected 400 for empty order_id: '#{empty_id}'"
      
      response_body = JSON.parse(last_response.body)
      assert_equal "Invalid request parameters", response_body["error"]
      assert_includes response_body["invalid_fields"], "order.order_id"
      assert response_body.key?("request_id")
    end
  end

  # AC-4: Valid input proceeds with normal processing (returns 200 as expected)
  def test_ac4_valid_input_returns_200
    post '/send', @valid_payload.to_json, 'CONTENT_TYPE' => 'application/json'

    assert_equal 200, last_response.status, "Expected 200 for valid input"
    # No validation errors should be present
    refute last_response.body.include?("Invalid request parameters")
  end

  # AC-5: Validation failures generate structured error logs
  def test_ac5_validation_failures_log_structured_errors
    # Test invalid email log entry
    payload = @valid_payload.merge(email: "invalid-email")
    # Capture log output
    original_stderr = $stderr
    log_output = StringIO.new
    $stderr = log_output

    post '/send', payload.to_json, 'CONTENT_TYPE' => 'application/json'

    $stderr = original_stderr
    log_lines = log_output.string.split("\n")
    validation_log = log_lines.find { |line| line.include?("Request validation failed for email send endpoint") }

    refute_nil validation_log, "Expected structured error log for validation failure"
    
    log_data = JSON.parse(validation_log)
    assert_equal "error", log_data["level"]
    assert_equal "email-service", log_data["service"]
    assert_equal "POST /send", log_data["endpoint"]
    assert_equal ["email"], log_data["invalid_fields"]
    assert log_data.key?("request_id")
    assert log_data.key?("timestamp")
    assert log_data.dig("context", "email_provided")
    assert log_data.dig("context", "order_id_provided")
  end
end
