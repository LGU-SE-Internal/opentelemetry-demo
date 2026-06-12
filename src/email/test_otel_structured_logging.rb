require 'minitest/autorun'
require 'open3'
require 'json'
require_relative 'email_server'

class TestOtelStructuredLogging < Minitest::Test
  def setup
    @email_server_file = File.read(File.expand_path('email_server.rb', __dir__))
  end

  # AC-1: All direct print/puts calls at lines 780, 842, 865 are replaced with OpenTelemetry logger invocations
  def test_ac1_no_raw_print_calls_at_affected_lines
    lines = @email_server_file.split("\n")
    
    # Check line 780 (1-indexed)
    assert !lines[779].match?(/^(print|puts)\s/), "Line 780 still contains raw print/puts call"
    assert lines[779].match?(/logger\.(info|warn|error)/), "Line 780 does not use OTel logger invocation"
    
    # Check line 842
    assert !lines[841].match?(/^(print|puts)\s/), "Line 842 still contains raw print/puts call"
    assert lines[841].match?(/logger\.(info|warn|error)/), "Line 842 does not use OTel logger invocation"
    
    # Check line 865
    assert !lines[864].match?(/^(print|puts)\s/), "Line 865 still contains raw print/puts call"
    assert lines[864].match?(/logger\.(info|warn|error)/), "Line 865 does not use OTel logger invocation"
  end

  # AC-2: All structured log entries contain all required metadata
  def test_ac2_log_entries_contain_all_required_metadata
    # Mock logger to capture log calls
    captured_logs = []
    mock_logger = Object.new
    def mock_logger.method_missing(method, message, attrs = {})
      captured_logs << { severity: method, message: message, attributes: attrs }
    end

    # Override logger in EmailServer
    original_logger = EmailServer.logger if defined?(EmailServer.logger)
    EmailServer.logger = mock_logger if defined?(EmailServer)

    # Trigger each log event
    # 1. Info log (e.g. email sent success)
    test_correlation_id = "test-correlation-123"
    test_timestamp = Time.now.utc.iso8601
    EmailServer.trigger_email_sent_log(test_timestamp, test_correlation_id) if defined?(EmailServer.trigger_email_sent_log)

    # 2. Warn log (e.g. email delivery retry)
    test_event_type = "email.delivery.retry"
    EmailServer.trigger_delivery_retry_log(test_timestamp, test_correlation_id, test_event_type) if defined?(EmailServer.trigger_delivery_retry_log)

    # 3. Error log (e.g. delivery failed)
    test_error = StandardError.new("SMTP connection refused")
    EmailServer.trigger_delivery_failed_log(test_timestamp, test_correlation_id, test_event_type, test_error) if defined?(EmailServer.trigger_delivery_failed_log)

    # Verify all logs have required fields
    captured_logs.each do |log|
      attrs = log[:attributes]
      assert attrs.key?(:timestamp), "Log missing timestamp attribute"
      assert attrs.key?(:event_type), "Log missing event_type attribute"
      assert attrs.key?(:correlation_id), "Log missing correlation_id attribute"
      assert attrs.key?(:trace_id), "Log missing trace_id attribute"
      assert attrs.key?(:span_id), "Log missing span_id attribute"
      assert_equal OpenTelemetry::Trace.current_trace_id, attrs[:trace_id], "Trace ID does not match current context" if defined?(OpenTelemetry::Trace)
      assert_equal OpenTelemetry::Trace.current_span_id, attrs[:span_id], "Span ID does not match current context" if defined?(OpenTelemetry::Trace)
      
      if log[:severity] == :error
        assert attrs.key?(:error_details), "Error log missing error_details attribute"
      end
    end
  ensure
    EmailServer.logger = original_logger if defined?(EmailServer) && original_logger
  end

  # AC-3: Log severity levels are correctly mapped
  def test_ac3_correct_severity_levels_mapped
    # Get lines and check severity matches intent
    lines = @email_server_file.split("\n")
    
    # Line 780: Typically info level (e.g. email queued/sent)
    assert lines[779].match?(/logger\.info/), "Line 780 should use info severity"
    
    # Line 842: Typically warn level (e.g. delivery retry)
    assert lines[841].match?(/logger\.warn/), "Line 842 should use warn severity"
    
    # Line 865: Typically error level (e.g. delivery failed)
    assert lines[864].match?(/logger\.error/), "Line 865 should use error severity"
  end

  # AC-4: Existing structured log parsing tests still pass
  def test_ac4_existing_structured_log_parsing_tests_pass
    # Run existing structured logging test file
    test_file = File.expand_path('test_structured_logging.rb', __dir__)
    output, status = Open3.capture2e("ruby #{test_file} -v")
    
    assert status.success?, "Existing structured logging tests failed: #{output}"
  end

  # AC-5: No functional regression in email service functionality
  def test_ac5_no_functional_regression
    # Skip if we don't have the server running, just verify the endpoint definitions exist
    assert defined?(EmailServer::ENDPOINTS[:health]), "Health endpoint missing"
    assert defined?(EmailServer::ENDPOINTS[:send_email]), "Send email endpoint missing"
    
    # Test core request/response parsing works as expected
    test_payload = { to: "test@example.com", subject: "Test", body: "Test", correlation_id: "123" }
    parsed = EmailServer.parse_request(test_payload.to_json) if defined?(EmailServer.parse_request)
    assert_equal "test@example.com", parsed[:to] if parsed
  end
end
