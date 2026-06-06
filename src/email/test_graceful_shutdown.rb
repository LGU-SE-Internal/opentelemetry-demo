#!/usr/bin/env ruby

require "minitest/autorun"
require "net/http"
require "json"
require "timeout"

# Integration tests for email service graceful shutdown functionality
class TestEmailGracefulShutdown < Minitest::Test
  EMAIL_SERVICE_PORT = 8080
  HEALTH_ENDPOINT = "/health"
  SEND_EMAIL_ENDPOINT = "/send_order_confirmation"

  def setup
    # Clean up any running processes from previous tests
    system("pkill -f email_server.rb > /dev/null 2>&1")
    @log_file = Tempfile.new("email_service_log")
    @server_pid = nil
  end

  def teardown
    if @server_pid
      Process.kill("KILL", @server_pid) rescue nil
      Process.wait(@server_pid) rescue nil
    end
    @log_file.close
    @log_file.unlink
  end

  def start_email_server
    @server_pid = spawn("ruby email_server.rb", out: @log_file.path, err: @log_file.path)
    # Wait for server to start
    wait_for_server_start
  end

  def wait_for_server_start
    Timeout.timeout(10) do
      loop do
        begin
          response = Net::HTTP.get_response(URI("http://localhost:#{EMAIL_SERVICE_PORT}#{HEALTH_ENDPOINT}"))
          break if response.is_a?(Net::HTTPSuccess)
        rescue Errno::ECONNREFUSED
          sleep 0.1
        end
      end
    end
  end

  def send_email_request(async: false)
    uri = URI("http://localhost:#{EMAIL_SERVICE_PORT}#{SEND_EMAIL_ENDPOINT}")
    request = Net::HTTP::Post.new(uri)
    request.content_type = "application/json"
    request.body = JSON.generate({ order_id: "test-order-#{rand(10000)}", user_email: "test@example.com" })
    
    if async
      Thread.new { Net::HTTP.start(uri.host, uri.port) { |http| http.request(request) } }
    else
      Net::HTTP.start(uri.host, uri.port) { |http| http.request(request) }
    end
  end

  # AC-1: When a SIGTERM or SIGINT signal is sent to the email service process, 
  # all new incoming requests to any endpoint are immediately rejected with HTTP 503 status code.
  def test_ac1_new_requests_rejected_with_503_after_shutdown_signal
    start_email_server

    # Send SIGTERM signal
    Process.kill("TERM", @server_pid)
    sleep 0.5 # Give time for signal handler to run

    # Attempt to send a new request
    response = send_email_request
    assert_equal 503, response.code.to_i, "New request should return 503 after shutdown signal"

    # Test with SIGINT as well
    teardown
    setup
    start_email_server
    Process.kill("INT", @server_pid)
    sleep 0.5
    response = send_email_request
    assert_equal 503, response.code.to_i, "New request should return 503 after SIGINT signal"
  end

  # AC-2: The health check endpoint `/health` returns `NOT_SERVING` status 
  # with HTTP 503 code immediately after a shutdown signal is received.
  def test_ac2_health_check_returns_not_serving_after_shutdown_signal
    start_email_server

    # Check health before shutdown
    response = Net::HTTP.get_response(URI("http://localhost:#{EMAIL_SERVICE_PORT}#{HEALTH_ENDPOINT}"))
    assert_equal 200, response.code.to_i
    health_data = JSON.parse(response.body)
    assert_equal "SERVING", health_data["status"]

    # Send SIGTERM
    Process.kill("TERM", @server_pid)
    sleep 0.2

    # Check health after shutdown signal
    response = Net::HTTP.get_response(URI("http://localhost:#{EMAIL_SERVICE_PORT}#{HEALTH_ENDPOINT}"))
    assert_equal 503, response.code.to_i
    health_data = JSON.parse(response.body)
    assert_equal "NOT_SERVING", health_data["status"]
  end

  # AC-3: If there are in-flight email send operations when a shutdown signal is received,
  # the service waits for up to 30 seconds for all operations to complete before exiting.
  def test_ac3_service_waits_up_to_30s_for_in_flight_operations
    start_email_server

    # Start a long-running email send operation (simulated to take 5 seconds)
    slow_request_thread = send_email_request(async: true)

    # Wait 1 second to ensure request is in flight
    sleep 1

    # Send shutdown signal
    Process.kill("TERM", @server_pid)

    # Check that process is still running after 3 seconds (should still be waiting for in-flight)
    process_running = Process.kill(0, @server_pid) rescue false
    assert process_running, "Service should not exit while in-flight operations are running"

    # Wait for the slow request to complete
    slow_request_thread.join

    # Process should exit within a few seconds after in-flight completes
    Timeout.timeout(5) do
      Process.wait(@server_pid)
    end
  end

  # AC-4: If all in-flight operations complete before the 30 second timeout,
  # the service exits immediately after the last operation finishes.
  def test_ac4_service_exits_immediately_when_all_in_flight_complete
    start_email_server

    # Start an email operation that takes 2 seconds
    fast_request_thread = send_email_request(async: true)
    sleep 0.5
    Process.kill("TERM", @server_pid)

    # Measure time to exit
    start_time = Time.now
    Timeout.timeout(4) do # Should exit in ~1.5 seconds, not 30
      Process.wait(@server_pid)
    end
    exit_duration = Time.now - start_time

    assert exit_duration < 5, "Service should exit immediately after in-flight operations complete, not wait full 30s"
  end

  # AC-5: Any in-flight operations that are still running after the 30 second timeout are aborted,
  # and an error log entry is written for each incomplete operation including the operation ID and order ID where applicable.
  def test_ac5_in_flight_operations_aborted_after_30s_timeout
    start_email_server

    # Start an email operation simulated to take 40 seconds (longer than 30s timeout)
    very_slow_thread = send_email_request(async: true)
    sleep 1
    Process.kill("TERM", @server_pid)

    # Wait up to 35 seconds for process to exit (should exit after 30s timeout)
    start_time = Time.now
    Timeout.timeout(35) do
      Process.wait(@server_pid)
    end
    exit_duration = Time.now - start_time

    assert exit_duration >= 30, "Service should wait full 30s timeout for long running operations"
    assert exit_duration < 35, "Service should exit after 30s timeout"

    # Check log for aborted operation error
    log_content = File.read(@log_file.path)
    assert_includes log_content, "ERROR", "Error should be logged for aborted operations"
    assert_includes log_content, "aborted", "Log should mention aborted operation"
    assert_includes log_content, "order_id", "Log should include order ID for aborted operation"
  end

  # AC-6: All shutdown events (signal received, start of grace period, graceful exit, timeout exit, aborted operations)
  # are logged with timestamp and severity level (INFO for normal events, ERROR for timeouts/aborted operations).
  def test_ac6_shutdown_events_logged_correctly
    start_email_server
    Process.kill("TERM", @server_pid)
    Timeout.timeout(5) { Process.wait(@server_pid) }

    log_content = File.read(@log_file.path)
    # Check info level events
    assert_includes log_content, "INFO", "Info logs should be present for shutdown events"
    assert_includes log_content, "SIGTERM received", "Log should mention signal received"
    assert_includes log_content, "grace period started", "Log should mention grace period start"
    assert_includes log_content, "graceful exit", "Log should mention graceful exit"
    # Check timestamps exist (simple check for date format)
    assert_match(/\d{4}-\d{2}-\d{2}/, log_content, "Logs should include timestamps")
  end

  # AC-7: No email send operations that started before the shutdown signal was received
  # are interrupted during the 30 second grace period, and all successfully completed operations are logged as normal.
  def test_ac7_in_flight_operations_not_interrupted_during_grace_period
    start_email_server

    # Start an email operation that takes 3 seconds
    request_thread = send_email_request(async: true)
    sleep 0.5
    Process.kill("TERM", @server_pid)

    # Wait for request to complete
    response = request_thread.value
    assert_equal 200, response.code.to_i, "In-flight email request should complete successfully during grace period"

    # Check log for successful completion
    log_content = File.read(@log_file.path)
    assert_includes log_content, "email sent successfully", "Log should confirm email completed successfully"
  end
end
