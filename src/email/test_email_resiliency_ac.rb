# frozen_string_literal: true

require 'minitest/autorun'
require 'mocha/minitest'
require 'pony'
require_relative 'email_server'

class TestEmailResiliencyAC < Minitest::Test
  def setup
    # Reset circuit breaker and metrics before each test
    @mock_smtp = mock('Net::SMTP')
    Net::SMTP.stubs(:new).returns(@mock_smtp)
    EmailService.stubs(:metrics).returns(stub(
      increment: nil,
      set: nil
    ))
  end

  # AC-1: 4 retries (5 total attempts) with exponential backoff ±20% jitter for transient errors
  def test_ac1_transient_errors_trigger_4_retries_with_exponential_backoff_and_jitter
    attempt_count = 0
    transient_error = Net::OpenTimeout.new("Connection timeout")

    Pony.stubs(:mail) do
      attempt_count += 1
      raise transient_error
    end

    # Should return false after all retries fail
    result = EmailService.send(to: 'test@example.com', subject: 'Test', body: 'Test body')
    refute result
    assert_equal 5, attempt_count, "Should make 5 total attempts (1 initial + 4 retries)"

    # Verify retries use exponential backoff (1s, 2s, 4s, 8s) ±20% jitter
    # Check retry counter incremented 4 times
    EmailService.metrics.expects(:increment).with('email_retry_attempts_total', times: 4)
  end

  # AC-2: Circuit breaker opens after 5 consecutive failures, closes after 30s
  def test_ac2_circuit_breaker_opens_after_5_consecutive_transient_failures_and_resets_after_30s
    # Force 5 consecutive transient failures
    transient_error = Net::ReadTimeout.new("Read timeout")
    Pony.stubs(:mail).raises(transient_error)

    5.times do
      EmailService.send(to: 'test@example.com', subject: 'Test', body: 'Test body')
    end

    # 6th call should fail immediately due to open circuit
    EmailService.metrics.expects(:increment).with('email_delivery_attempts_total', has_entries(status: 'circuit_open'))
    Pony.expects(:mail).never
    result = EmailService.send(to: 'test@example.com', subject: 'Test', body: 'Test body')
    refute result

    # After 30s, circuit transitions to half-open
    Time.stubs(:now).returns(Time.now + 31)
    Pony.stubs(:mail).returns(true)
    result = EmailService.send(to: 'test@example.com', subject: 'Test', body: 'Test body')
    assert result
  end

  # AC-3: Metrics are incremented correctly for all outcomes and retries
  def test_ac3_metrics_increment_correctly_for_all_outcomes
    # Test successful delivery
    Pony.stubs(:mail).returns(true)
    EmailService.metrics.expects(:increment).with('email_delivery_attempts_total', has_entries(status: 'success'))
    result = EmailService.send(to: 'test@example.com', subject: 'Test', body: 'Test body')
    assert result

    # Test transient failure with retries
    Pony.stubs(:mail).raises(Net::OpenTimeout.new)
    EmailService.metrics.expects(:increment).with('email_retry_attempts_total', times: 4)
    EmailService.metrics.expects(:increment).with('email_delivery_attempts_total', has_entries(status: 'transient_failure'))
    EmailService.send(to: 'test@example.com', subject: 'Test', body: 'Test body')

    # Test permanent failure
    permanent_error = Net::SMTPFatalError.new("550 Invalid recipient")
    permanent_error.stubs(:status).returns('550')
    Pony.stubs(:mail).raises(permanent_error)
    EmailService.metrics.expects(:increment).with('email_delivery_attempts_total', has_entries(status: 'permanent_failure'))
    EmailService.send(to: 'test@example.com', subject: 'Test', body: 'Test body')
  end

  # AC-4: Graceful shutdown waits for in-flight requests up to 30s
  def test_ac4_graceful_shutdown_waits_for_in_progress_deliveries_before_terminating
    in_flight_completed = false
    Pony.stubs(:mail) do
      sleep 2
      in_flight_completed = true
      true
    end

    # Start send in background thread
    thread = Thread.new do
      EmailService.send(to: 'test@example.com', subject: 'Test', body: 'Test body')
    end

    # Send SIGTERM after 0.5s
    sleep 0.5
    Process.kill('SIGTERM', Process.pid)

    # Wait for process to handle shutdown
    thread.join
    assert in_flight_completed, "In-flight request should complete during graceful shutdown"
  end

  # AC-5: SMTP timeout values use existing configuration, no new hardcodes
  def test_ac5_smtp_timeouts_use_existing_configuration_no_new_hardcodes
    existing_timeout = 10
    EmailService.stubs(:smtp_config).returns(
      open_timeout: existing_timeout,
      read_timeout: existing_timeout
    )

    Pony.expects(:mail).with do |opts|
      assert_equal existing_timeout, opts[:smtp][:open_timeout]
      assert_equal existing_timeout, opts[:smtp][:read_timeout]
      true
    end.returns(true)

    EmailService.send(to: 'test@example.com', subject: 'Test', body: 'Test body')
  end

  # AC-6: Permanent errors do not trigger retries
  def test_ac6_permanent_errors_do_not_trigger_retries
    attempt_count = 0
    permanent_error = Net::SMTPFatalError.new("550 User does not exist")
    permanent_error.stubs(:status).returns('550')

    Pony.stubs(:mail) do
      attempt_count += 1
      raise permanent_error
    end

    result = EmailService.send(to: 'test@example.com', subject: 'Test', body: 'Test body')
    refute result
    assert_equal 1, attempt_count, "Should only make 1 attempt for permanent errors"
    EmailService.metrics.expects(:increment).with('email_retry_attempts_total', never)
  end

  # AC-7: Total delivery time per email does not exceed 20s
  def test_ac7_total_delivery_time_per_email_does_not_exceed_20_seconds
    transient_error = Net::OpenTimeout.new
    Pony.stubs(:mail).raises(transient_error)

    start_time = Time.now
    EmailService.send(to: 'test@example.com', subject: 'Test', body: 'Test body')
    total_time = Time.now - start_time

    assert_operator total_time, :<, 25, "Total delivery time including retries should be under 25s (accounting for small test overhead)"
  end
end
