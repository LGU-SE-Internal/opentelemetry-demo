require 'minitest/autorun'
require 'json'
require_relative 'email_server'

class TestStructuredLogging < Minitest::Test
  def setup
    @original_stdout = $stdout
    @original_stderr = $stderr
    @log_output = StringIO.new
    $stdout = @log_output
    $stderr = @log_output
  end

  def teardown
    $stdout = @original_stdout
    $stderr = @original_stderr
  end

  # AC-1: All instances of unstructured puts, p, warn, and unstructured default logger calls replaced
  def test_ac1_no_unstructured_log_calls
    # Check email_server.rb for unstructured calls
    file_content = File.read(File.expand_path('../email_server.rb', __FILE__))
    
    unstructured_calls = [
      /\bputs\s*\(/, /\bputs\s+/,
      /\bp\s*\(/, /\bp\s+/,
      /\bwarn\s*\(/, /\bwarn\s+/,
      /\bLogger\.new\(/, /\blogger\.(debug|info|warn|error|fatal)\s*['"]/, # unstructured default logger calls without fields
    ]
    
    unstructured_calls.each do |pattern|
      matches = file_content.scan(pattern)
      assert_empty matches, "Found unstructured log call matching pattern #{pattern}: #{matches.join(', ')}"
    end
  end

  # AC-2: Every log entry is valid JSON format
  def test_ac2_all_log_entries_are_valid_json
    # Trigger service startup
    server_thread = Thread.new { EmailServer.run(testing: true) }
    sleep 0.5

    # Trigger successful email send
    EmailServer.send_test_email(success: true)
    sleep 0.2

    # Trigger failed email send
    EmailServer.send_test_email(success: false)
    sleep 0.2

    server_thread.kill
    server_thread.join

    log_lines = @log_output.string.split("\n").reject(&:empty?)
    assert log_lines.size > 0, "No log lines produced"

    log_lines.each do |line|
      assert_nothing_raised JSON::ParserError do
        JSON.parse(line)
      end
    end
  end

  # AC-3: All log entries include required fields: message, service, event_id
  def test_ac3_log_entries_have_required_fields
    # Trigger service events
    server_thread = Thread.new { EmailServer.run(testing: true) }
    sleep 0.5
    EmailServer.send_test_email(success: true)
    sleep 0.2
    EmailServer.send_test_email(success: false)
    sleep 0.2
    server_thread.kill
    server_thread.join

    log_lines = @log_output.string.split("\n").reject(&:empty?)

    log_lines.each do |line|
      log_entry = JSON.parse(line)
      assert log_entry.key?('message'), "Log entry missing 'message' field: #{log_entry}"
      assert log_entry.key?('service'), "Log entry missing 'service' field: #{log_entry}"
      assert_equal 'email-service', log_entry['service'], "Service field value incorrect: #{log_entry['service']}"
      assert log_entry.key?('event_id'), "Log entry missing 'event_id' field: #{log_entry}"
      assert log_entry['event_id'].is_a?(String), "event_id must be string: #{log_entry['event_id']}"
    end
  end

  # AC-4: Log severity levels correctly mapped
  def test_ac4_correct_severity_levels
    # Trigger service startup (info level)
    server_thread = Thread.new { EmailServer.run(testing: true) }
    sleep 0.5
    # Trigger successful send (info level)
    EmailServer.send_test_email(success: true)
    sleep 0.2
    # Trigger failed send (error level)
    EmailServer.send_test_email(success: false)
    sleep 0.2
    # Trigger health check (info level)
    EmailServer.health_check
    sleep 0.2
    server_thread.kill
    server_thread.join

    log_lines = @log_output.string.split("\n").reject(&:empty?)

    info_events = ['email_service_started', 'email_send_succeeded', 'health_check_passed']
    error_events = ['email_send_failed', 'invalid_input', 'smtp_connection_error']

    log_lines.each do |line|
      log_entry = JSON.parse(line)
      event_id = log_entry['event_id']
      severity = log_entry['severity'] || log_entry['level']

      if info_events.include?(event_id)
        assert_equal 'info', severity.downcase, "Event #{event_id} should have info severity, got #{severity}"
      elsif error_events.include?(event_id)
        assert_equal 'error', severity.downcase, "Event #{event_id} should have error severity, got #{severity}"
      end
    end
  end

  # AC-5: All existing log context is preserved
  def test_ac5_log_context_preserved
    # Send email with known context values
    test_recipient = 'test@example.com'
    test_subject = 'Test Subject'
    test_timestamp = Time.now.iso8601

    server_thread = Thread.new { EmailServer.run(testing: true) }
    sleep 0.5
    EmailServer.send_email(to: test_recipient, subject: test_subject, body: 'test', timestamp: test_timestamp)
    sleep 0.2
    server_thread.kill
    server_thread.join

    log_lines = @log_output.string.split("\n").reject(&:empty?)
    send_success_log = log_lines.find { |line| JSON.parse(line)['event_id'] == 'email_send_succeeded' }

    assert send_success_log, "No email_send_succeeded log found"
    log_entry = JSON.parse(send_success_log)

    # Verify expected context fields are present
    assert log_entry.key?('recipient'), "Missing recipient context field"
    assert_equal test_recipient, log_entry['recipient'], "Recipient context does not match"
    assert log_entry.key?('subject'), "Missing subject context field"
    assert_equal test_subject, log_entry['subject'], "Subject context does not match"
    assert log_entry.key?('timestamp'), "Missing timestamp context field"
    assert_equal test_timestamp, log_entry['timestamp'], "Timestamp context does not match"
  end

  # AC-6: No new log fields break existing observability pipeline
  def test_ac6_no_unexpected_log_fields
    allowed_fields = %w[
      message service event_id severity level timestamp
      error_class error_message error_backtrace
      recipient subject smtp_provider response_code duration_ms
    ]

    # Trigger all event types
    server_thread = Thread.new { EmailServer.run(testing: true) }
    sleep 0.5
    EmailServer.send_test_email(success: true)
    sleep 0.2
    EmailServer.send_test_email(success: false)
    sleep 0.2
    server_thread.kill
    server_thread.join

    log_lines = @log_output.string.split("\n").reject(&:empty?)

    log_lines.each do |line|
      log_entry = JSON.parse(line)
      extra_fields = log_entry.keys - allowed_fields
      assert_empty extra_fields, "Log entry has unexpected fields #{extra_fields} which break observability pipeline: #{log_entry}"
    end
  end
end
