require 'minitest/autorun'
require 'minitest/mock'
require 'net/smtp'
require 'prometheus/client'
require_relative 'email_server'

class SMTPRetryTest < Minitest::Test
  def setup
    ENV['EMAIL_SMTP_MAX_RETRIES'] = '3'
    @recipient = 'test.user@example.com'
    Prometheus::Client.registry.reset!
    $stderr = StringIO.new
  end

  def teardown
    ENV.delete('EMAIL_SMTP_MAX_RETRIES')
    $stderr = STDERR
  end

  # Test helper: simulate SMTP response with given code
  def simulate_smtp_response(code, message = '')
    e = Net::SMTPError.new(message)
    e.instance_variable_set(:@status, code.to_s)
    e
  end

  # AC-1: 4xx errors retry N times with exponential backoff + jitter
  def test_ac1_4xx_errors_retry_with_exponential_backoff
    retry_attempts = 0
    expected_retries = ENV['EMAIL_SMTP_MAX_RETRIES'].to_i

    assert_raises Net::SMTPError do
      retry_smtp_delivery do
        retry_attempts += 1
        raise simulate_smtp_response(421, 'Service unavailable')
      end
    end

    # N+1 total attempts: 1 initial + N retries
    assert_equal expected_retries + 1, retry_attempts
    assert_equal expected_retries, Prometheus::Client.registry.get(:email_delivery_retry_total).get(labels: { retry_attempt: 1 })
    assert_equal expected_retries >= 2 ? 1 : 0, Prometheus::Client.registry.get(:email_delivery_retry_total).get(labels: { retry_attempt: 2 })
    assert_equal expected_retries >=3 ? 1 : 0, Prometheus::Client.registry.get(:email_delivery_retry_total).get(labels: { retry_attempt: 3 })

    # Check logs for retry warnings
    log_lines = $stderr.string.split("\n").select { |l| l.include?('"level":"warn"') }
    assert_equal expected_retries, log_lines.size
    log_lines.each_with_index do |line, idx|
      log_data = JSON.parse(line)
      assert_equal @recipient, log_data['recipient']
      assert_equal '421', log_data['smtp_code']
      assert_equal idx + 1, log_data['retry_count']
      assert_equal expected_retries, log_data['max_retries']
      delay = log_data['next_retry_delay'].to_f
      expected_base = 2 ** idx
      assert delay >= expected_base * 0.8, "Delay #{delay} too small for attempt #{idx+1}"
      assert delay <= expected_base * 1.2, "Delay #{delay} too large for attempt #{idx+1}"
      assert delay <= 30, "Delay #{delay} exceeds 30s cap"
    end
  end

  # AC-2: 5xx errors are not retried
  def test_ac2_5xx_errors_no_retry
    attempt_count = 0

    assert_raises Net::SMTPError do
      retry_smtp_delivery do
        attempt_count +=1
        raise simulate_smtp_response(550, 'Invalid recipient')
      end
    end

    assert_equal 1, attempt_count
    assert_equal 0, Prometheus::Client.registry.get(:email_delivery_retry_total).values.sum
    # Permanent failure metric incremented
    assert_equal 1, Prometheus::Client.registry.get(:email_delivery_failed_total).get(labels: { failure_type: 'permanent' })

    # Check error log
    log_lines = $stderr.string.split("\n").select { |l| l.include?('"level":"error"') }
    assert_equal 1, log_lines.size
    log_data = JSON.parse(log_lines.first)
    assert_equal @recipient, log_data['recipient']
    assert_equal '550', log_data['smtp_code']
    assert_equal 0, log_data['total_retries']
    assert_equal 'permanent', log_data['failure_type']
  end

  # AC-3: Network failures are retried
  def test_ac3_network_failures_retried
    retry_attempts = 0
    expected_retries = ENV['EMAIL_SMTP_MAX_RETRIES'].to_i
    network_errors = [
      Errno::ETIMEDOUT.new,
      Errno::ECONNRESET.new,
      SocketError.new('DNS resolution failed')
    ]

    network_errors.each do |error|
      attempt_count = 0
      assert_raises error.class do
        retry_smtp_delivery do
          attempt_count +=1
          raise error
        end
      end
      assert_equal expected_retries +1, attempt_count
    end
  end

  # AC-4: MAX_RETRIES=0 disables retries
  def test_ac4_max_retries_zero_no_retries
    ENV['EMAIL_SMTP_MAX_RETRIES'] = '0'
    attempt_count =0

    assert_raises Net::SMTPError do
      retry_smtp_delivery do
        attempt_count +=1
        raise simulate_smtp_response(421, 'Service unavailable')
      end
    end

    assert_equal 1, attempt_count
    assert_equal 0, Prometheus::Client.registry.get(:email_delivery_retry_total).values.sum
  end

  # AC-5: WARN log on every retry
  def test_ac5_warn_log_emitted_before_each_retry
    retry_count = 2
    ENV['EMAIL_SMTP_MAX_RETRIES'] = retry_count.to_s
    attempts =0

    assert_raises Net::SMTPError do
      retry_smtp_delivery do
        attempts +=1
        raise simulate_smtp_response(451, 'Local error')
      end
    end

    log_lines = $stderr.string.split("\n").select { |l| l.include?('"level":"warn"') }
    assert_equal retry_count, log_lines.size
  end

  # AC-6: ERROR log on permanent failure
  def test_ac6_error_log_on_permanent_failure
    # Test permanent failure
    assert_raises Net::SMTPError do
      retry_smtp_delivery do
        raise simulate_smtp_response(535, 'Auth failed')
      end
    end
    log_lines = $stderr.string.split("\n").select { |l| l.include?('"level":"error"') }
    assert_equal 1, log_lines.size
    log_data = JSON.parse(log_lines.first)
    assert_equal 'permanent', log_data['failure_type']

    # Test exhausted temporary failures
    $stderr = StringIO.new
    assert_raises Net::SMTPError do
      retry_smtp_delivery do
        raise simulate_smtp_response(421, 'Unavailable')
      end
    end
    log_lines = $stderr.string.split("\n").select { |l| l.include?('"level":"error"') }
    assert_equal 1, log_lines.size
    log_data = JSON.parse(log_lines.first)
    assert_equal 'temporary', log_data['failure_type']
    assert_equal ENV['EMAIL_SMTP_MAX_RETRIES'].to_i, log_data['total_retries']
  end

  # AC-7: Success counter increments on successful delivery
  def test_ac7_success_counter_increments
    # Success on first attempt
    result = retry_smtp_delivery do
      # Simulate success
    end
    assert result
    assert_equal 1, Prometheus::Client.registry.get(:email_delivery_success_total).get

    # Success after retries
    attempts = 0
    result = retry_smtp_delivery do
      attempts +=1
      raise simulate_smtp_response(421, 'Unavailable') if attempts <=2
    end
    assert result
    assert_equal 2, Prometheus::Client.registry.get(:email_delivery_success_total).get
    assert_equal 2, Prometheus::Client.registry.get(:email_delivery_retry_total).values.sum
  end

  # AC-8: Failed counter increments correctly
  def test_ac8_failed_counter_increments
    # Permanent failure
    assert_raises Net::SMTPError do
      retry_smtp_delivery { raise simulate_smtp_response(550, 'Invalid') }
    end
    assert_equal 1, Prometheus::Client.registry.get(:email_delivery_failed_total).get(labels: { failure_type: 'permanent' })
    assert_equal 0, Prometheus::Client.registry.get(:email_delivery_failed_total).get(labels: { failure_type: 'temporary' })

    # Exhausted temporary failure
    assert_raises Net::SMTPError do
      retry_smtp_delivery { raise simulate_smtp_response(421, 'Unavailable') }
    end
    assert_equal 1, Prometheus::Client.registry.get(:email_delivery_failed_total).get(labels: { failure_type: 'permanent' })
    assert_equal 1, Prometheus::Client.registry.get(:email_delivery_failed_total).get(labels: { failure_type: 'temporary' })
  end

  # AC-9: Retry counter increments per attempt
  def test_ac9_retry_counter_per_attempt
    ENV['EMAIL_SMTP_MAX_RETRIES'] = '3'
    assert_raises Net::SMTPError do
      retry_smtp_delivery { raise simulate_smtp_response(421, 'Unavailable') }
    end
    assert_equal 1, Prometheus::Client.registry.get(:email_delivery_retry_total).get(labels: { retry_attempt: 1 })
    assert_equal 1, Prometheus::Client.registry.get(:email_delivery_retry_total).get(labels: { retry_attempt: 2 })
    assert_equal 1, Prometheus::Client.registry.get(:email_delivery_retry_total).get(labels: { retry_attempt: 3 })
  end
end
