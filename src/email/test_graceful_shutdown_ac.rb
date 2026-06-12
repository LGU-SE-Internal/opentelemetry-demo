require 'minitest/autorun'
require 'rack/test'
require 'json'
require 'timeout'
require_relative 'email_server'

class GracefulShutdownACTest < Minitest::Test
  include Rack::Test::Methods

  def app
    Sinatra::Application
  end

  def setup
    @valid_payload = {
      email: "valid.user@example.com",
      order: { order_id: "ORD-123456789" },
      subject: "Test Order Confirmation",
      body: "Thank you for your order!"
    }
    # Reset any shutdown state before each test
    $email_service_shutdown_initiated = false if defined?($email_service_shutdown_initiated)
  end

  # AC-1: When SIGINT/SIGTERM is sent, HTTP server stops accepting new connections, new requests get 503
  def test_ac1_shutdown_returns_503_for_new_requests
    # Simulate SIGTERM signal triggering shutdown
    Process.kill('TERM', Process.pid)
    # Give signal handler time to run
    sleep 0.1

    # Attempt to send a new request
    post '/send', @valid_payload.to_json, 'CONTENT_TYPE' => 'application/json'
    assert_equal 503, last_response.status, "Expected 503 Service Unavailable after shutdown initiated"

    # Test with SIGINT as well
    $email_service_shutdown_initiated = false
    Process.kill('INT', Process.pid)
    sleep 0.1

    post '/send', @valid_payload.to_json, 'CONTENT_TYPE' => 'application/json'
    assert_equal 503, last_response.status, "Expected 503 Service Unavailable after SIGINT received"
  end

  # AC-2: When shutdown is initiated, background workers stop dequeuing new jobs
  def test_ac2_shutdown_stops_worker_dequeue
    # Pre-populate work queue with 10 jobs
    queue = EmailWorker.queue if defined?(EmailWorker)
    skip "EmailWorker not implemented yet" unless defined?(EmailWorker)

    10.times { |i| queue << @valid_payload.merge(order_id: "ORD-TEST-#{i}") }
    initial_queue_size = queue.size

    # Trigger shutdown
    Process.kill('TERM', Process.pid)
    sleep 0.5

    # Verify no new jobs were dequeued after shutdown initiation
    assert_equal initial_queue_size, queue.size, "Work queue should not be processed after shutdown initiated"
    refute EmailWorker.dequeue_enabled?, "Worker dequeue should be disabled during shutdown"
  end

  # AC-3: Service waits max 30 seconds for in-flight operations to complete
  def test_ac3_service_waits_up_to_30_seconds_for_in_flight_ops
    # Simulate long-running in-flight email delivery (20 seconds)
    long_job = -> { sleep 20 }
    EmailWorker.start_in_flight_job(long_job) if defined?(EmailWorker)
    skip "EmailWorker not implemented yet" unless defined?(EmailWorker)

    # Trigger shutdown
    start_time = Time.now
    exit_code = graceful_shutdown("SIGTERM")
    elapsed = Time.now - start_time

    assert elapsed >= 20, "Service should wait for in-flight jobs to complete"
    assert elapsed <= 31, "Service should not wait longer than 30 second timeout"
  end

  # AC-4: All in-flight operations complete before timeout → exit code 0, no interrupted operations
  def test_ac4_clean_shutdown_returns_exit_code_0
    # Simulate short-running in-flight job (5 seconds)
    short_job = -> { sleep 5 }
    EmailWorker.start_in_flight_job(short_job) if defined?(EmailWorker)
    skip "EmailWorker not implemented yet" unless defined?(EmailWorker)
    initial_in_flight_count = EmailWorker.in_flight_count

    exit_code = graceful_shutdown("SIGTERM")

    assert_equal 0, exit_code, "Clean shutdown should return exit code 0"
    assert_equal 0, EmailWorker.in_flight_count, "All in-flight operations should complete successfully"
    assert_equal initial_in_flight_count, EmailWorker.completed_operations_count, "No operations should be interrupted"
  end

  # AC-5: In-flight operations still running after 30s → force terminate, exit code 1
  def test_ac5_timeout_shutdown_returns_exit_code_1
    # Simulate very long running job that exceeds 30s timeout
    long_job = -> { sleep 40 }
    EmailWorker.start_in_flight_job(long_job) if defined?(EmailWorker)
    skip "EmailWorker not implemented yet" unless defined?(EmailWorker)

    start_time = Time.now
    exit_code = graceful_shutdown("SIGTERM")
    elapsed = Time.now - start_time

    assert elapsed.between?(29, 31), "Shutdown should occur after 30 second timeout"
    assert_equal 1, exit_code, "Forced timeout shutdown should return exit code 1"
    assert EmailWorker.all_in_flight_terminated?, "All pending operations should be force terminated after timeout"
  end

  # AC-6: All shutdown events logged with correct structured schema
  def test_ac6_shutdown_events_logged_with_correct_schema
    # Capture log output
    original_stderr = $stderr
    log_output = StringIO.new
    $stderr = log_output

    # Test with 1 in-flight job that completes in 2 seconds
    short_job = -> { sleep 2 }
    EmailWorker.start_in_flight_job(short_job) if defined?(EmailWorker)
    skip "EmailWorker not implemented yet" unless defined?(EmailWorker)

    exit_code = graceful_shutdown("SIGTERM")

    $stderr = original_stderr
    log_lines = log_output.string.split("\n").map { |l| JSON.parse(l) rescue nil }.compact

    # Check shutdown start log
    start_log = log_lines.find { |l| l["event"] == "graceful_shutdown_start" }
    refute_nil start_log, "Missing graceful_shutdown_start log"
    assert_equal "INFO", start_log["level"]
    assert_equal "email", start_log["service"]
    assert_equal "graceful_shutdown", start_log["component"]
    assert_equal "SIGTERM", start_log["signal"]
    assert_equal 30, start_log["timeout_seconds"]

    # Check in-progress logs (emitted every 5s, we expect at least 0 for 2s job)
    in_progress_logs = log_lines.select { |l| l["event"] == "graceful_shutdown_in_progress" }
    in_progress_logs.each do |log|
      assert_equal "INFO", log["level"]
      assert_equal "email", log["service"]
      assert_equal "graceful_shutdown", log["component"]
      assert log.key?("pending_operations"), "in_progress log missing pending_operations"
      assert log.key?("elapsed_seconds"), "in_progress log missing elapsed_seconds"
    end

    # Check complete log
    complete_log = log_lines.find { |l| l["event"] == "graceful_shutdown_complete" }
    refute_nil complete_log, "Missing graceful_shutdown_complete log"
    assert_equal "INFO", complete_log["level"]
    assert_equal "email", complete_log["service"]
    assert_equal "graceful_shutdown", complete_log["component"]
    assert complete_log.key?("total_elapsed_seconds")
    assert_equal 1, complete_log["completed_operations"]

    # Test timeout scenario for error log
    log_output = StringIO.new
    $stderr = log_output
    long_job = -> { sleep 40 }
    EmailWorker.start_in_flight_job(long_job)
    exit_code = graceful_shutdown("SIGINT")
    $stderr = original_stderr
    log_lines = log_output.string.split("\n").map { |l| JSON.parse(l) rescue nil }.compact

    timeout_log = log_lines.find { |l| l["event"] == "graceful_shutdown_timeout" }
    refute_nil timeout_log, "Missing graceful_shutdown_timeout log"
    assert_equal "ERROR", timeout_log["level"]
    assert_equal "email", timeout_log["service"]
    assert_equal "graceful_shutdown", timeout_log["component"]
    assert_equal 30, timeout_log["elapsed_seconds"]
    assert timeout_log.key?("pending_operations")
  end

  # AC-7: In-flight ops completed during shutdown are persisted correctly
  def test_ac7_in_flight_ops_persisted_correctly_during_shutdown
    # Test successful delivery
    success_job = -> { EmailService.mark_delivery_complete("ORD-SUCCESS-123", "delivered_123") }
    EmailWorker.start_in_flight_job(success_job) if defined?(EmailWorker)
    skip "EmailWorker not implemented yet" unless defined?(EmailWorker)

    # Test failed delivery that should go to DLQ
    failed_job = -> { raise "SMTP timeout"; EmailService.write_to_dlq(@valid_payload, "SMTP timeout") }
    EmailWorker.start_in_flight_job(failed_job)

    exit_code = graceful_shutdown("SIGTERM")

    # Verify successful delivery is logged
    assert EmailService.delivery_completed?("ORD-SUCCESS-123"), "Successful delivery should be marked complete"
    assert DeliveryLog.exists?(delivery_id: "delivered_123"), "Delivery log should be persisted"

    # Verify failed delivery is written to DLQ
    dlq_entries = DLQ.entries_for_order("ORD-123456789")
    assert_equal 1, dlq_entries.size, "Failed delivery should be written to DLQ"
    assert_equal "SMTP timeout", dlq_entries.first.error_reason
  end
end
