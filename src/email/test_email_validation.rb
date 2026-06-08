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
      sender_email: "sender@example.com",
      recipient_email: "recipient@example.com",
      subject: "Test Subject",
      body: "This is a test email body."
    }
  end

  # AC-1: Missing required fields return 400 with detail entry for each missing field
  def test_ac1_missing_required_fields_return_400
    required_fields = [:sender_email, :recipient_email, :subject, :body]
    
    required_fields.each do |field|
      payload = @valid_payload.dup
      payload.delete(field)
      
      post '/v1/send-email', payload.to_json, 'CONTENT_TYPE' => 'application/json'
      
      assert_equal 400, last_response.status, "Expected 400 when missing #{field}"
      
      response_body = JSON.parse(last_response.body)
      assert_equal "Validation failed", response_body["error"]
      assert_instance_of Array, response_body["details"]
      field_errors = response_body["details"].select { |err| err["field"] == field.to_s }
      assert_equal 1, field_errors.count, "Expected error for missing #{field}"
      assert_includes field_errors.first["message"], "required"
    end

    # Test multiple missing fields at once
    payload = {}
    post '/v1/send-email', payload.to_json, 'CONTENT_TYPE' => 'application/json'
    
    assert_equal 400, last_response.status
    response_body = JSON.parse(last_response.body)
    returned_fields = response_body["details"].map { |err| err["field"] }
    required_fields.each do |field|
      assert_includes returned_fields, field.to_s, "Expected error for missing #{field} when all fields are omitted"
    end
  end

  # AC-2: Invalid sender_email format returns 400 with sender_email detail
  def test_ac2_invalid_sender_email_format_returns_400
    invalid_emails = [
      "invalid-email",
      "user@.com",
      "no-domain.com",
      "user@domain",
      "@domain.com",
      "user@domain..com",
      "user name@domain.com",
      "user@domain,com"
    ]

    invalid_emails.each do |invalid_email|
      payload = @valid_payload.merge(sender_email: invalid_email)
      post '/v1/send-email', payload.to_json, 'CONTENT_TYPE' => 'application/json'

      assert_equal 400, last_response.status, "Expected 400 for sender email: #{invalid_email}"
      
      response_body = JSON.parse(last_response.body)
      assert_equal "Validation failed", response_body["error"]
      field_errors = response_body["details"].select { |err| err["field"] == "sender_email" }
      assert_equal 1, field_errors.count, "Expected error for invalid sender_email: #{invalid_email}"
      assert_includes field_errors.first["message"], "format"
    end
  end

  # AC-3: Invalid recipient_email format returns 400 with recipient_email detail
  def test_ac3_invalid_recipient_email_format_returns_400
    invalid_emails = [
      "invalid-email",
      "user@.com",
      "no-domain.com",
      "user@domain",
      "@domain.com",
      "user@domain..com",
      "user name@domain.com",
      "user@domain,com"
    ]

    invalid_emails.each do |invalid_email|
      payload = @valid_payload.merge(recipient_email: invalid_email)
      post '/v1/send-email', payload.to_json, 'CONTENT_TYPE' => 'application/json'

      assert_equal 400, last_response.status, "Expected 400 for recipient email: #{invalid_email}"
      
      response_body = JSON.parse(last_response.body)
      assert_equal "Validation failed", response_body["error"]
      field_errors = response_body["details"].select { |err| err["field"] == "recipient_email" }
      assert_equal 1, field_errors.count, "Expected error for invalid recipient_email: #{invalid_email}"
      assert_includes field_errors.first["message"], "format"
    end
  end

  # AC-4: Subject longer than 255 characters returns 400 with subject detail
  def test_ac4_subject_over_255_characters_returns_400
    long_subject = "a" * 256
    payload = @valid_payload.merge(subject: long_subject)
    
    post '/v1/send-email', payload.to_json, 'CONTENT_TYPE' => 'application/json'
    
    assert_equal 400, last_response.status
    response_body = JSON.parse(last_response.body)
    assert_equal "Validation failed", response_body["error"]
    field_errors = response_body["details"].select { |err| err["field"] == "subject" }
    assert_equal 1, field_errors.count
    assert_includes field_errors.first["message"], "length"
    assert_includes field_errors.first["message"], "255"

    # Test valid 255 character subject passes
    valid_subject = "a" * 255
    payload = @valid_payload.merge(subject: valid_subject)
    
    post '/v1/send-email', payload.to_json, 'CONTENT_TYPE' => 'application/json'
    
    # Should not return 400 for valid length (unless other errors exist)
    refute_equal 400, last_response.status, "Expected valid 255 character subject to pass validation"
  end

  # AC-5: Body longer than 10,000 characters returns 400 with body detail
  def test_ac5_body_over_10000_characters_returns_400
    long_body = "a" * 10001
    payload = @valid_payload.merge(body: long_body)
    
    post '/v1/send-email', payload.to_json, 'CONTENT_TYPE' => 'application/json'
    
    assert_equal 400, last_response.status
    response_body = JSON.parse(last_response.body)
    assert_equal "Validation failed", response_body["error"]
    field_errors = response_body["details"].select { |err| err["field"] == "body" }
    assert_equal 1, field_errors.count
    assert_includes field_errors.first["message"], "length"
    assert_includes field_errors.first["message"], "10000"

    # Test valid 10,000 character body passes
    valid_body = "a" * 10000
    payload = @valid_payload.merge(body: valid_body)
    
    post '/v1/send-email', payload.to_json, 'CONTENT_TYPE' => 'application/json'
    
    # Should not return 400 for valid length (unless other errors exist)
    refute_equal 400, last_response.status, "Expected valid 10,000 character body to pass validation"
  end

  # AC-6: Multiple validation failures return all errors
  def test_ac6_multiple_validation_failures_return_all_errors
    payload = {
      sender_email: "invalid-sender",
      recipient_email: "invalid-recipient",
      subject: "a" * 300,
      body: "a" * 11000
    }

    post '/v1/send-email', payload.to_json, 'CONTENT_TYPE' => 'application/json'
    
    assert_equal 400, last_response.status
    response_body = JSON.parse(last_response.body)
    assert_equal "Validation failed", response_body["error"]
    
    error_fields = response_body["details"].map { |err| err["field"] }
    expected_fields = ["sender_email", "recipient_email", "subject", "body"]
    expected_fields.each do |field|
      assert_includes error_fields, field, "Expected error for #{field} when all fields are invalid"
    end
  end

  # AC-7: Valid request passes validation and proceeds to SMTP layer
  def test_ac7_valid_request_passes_validation
    post '/v1/send-email', @valid_payload.to_json, 'CONTENT_TYPE' => 'application/json'
    
    # Should NOT return 400 Bad Request for valid payload
    refute_equal 400, last_response.status, "Expected valid request to pass validation"
    # Should not contain validation error message
    refute last_response.body.include?("Validation failed"), "Valid request should not have validation errors"
  end

  # AC-8: Validation applies to both HTTP and gRPC interfaces (gRPC tests to be added when gRPC SendEmail method is implemented)
  def test_ac8_grpc_validation_not_implemented_yet
    skip "gRPC SendEmail method not implemented yet, tests will be added once interface is available"
  end
end
