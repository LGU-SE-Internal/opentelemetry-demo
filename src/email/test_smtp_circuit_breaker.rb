require 'minitest/autorun'
require 'minitest/mock'
require 'net/smtp'
require 'prometheus/client'
require 'rack/test'
require 'stoplight'
require_relative 'email_server'

class SMTPCircuitBreakerTest < Minitest::Test
  include Rack::Test::Methods

  def app
    Sinatra::Application
  end

  def setup
    # Reset circuit breaker config to defaults
    ENV.delete('SMTP_CIRCUIT_FAILURE_THRESHOLD')
    ENV.delete('SMTP_CIRCUIT_RECOVERY_TIMEOUT')
    Prometheus::Client.registry.reset!
    Stoplight::Light.default_data_store = Stoplight::DataStore::Memory.new
    $stderr = StringIO.new
    @test_email_payload = {
      to: 'test@example.com',
      subject: 'Test Email',
      body: 'Test body'
    }.to_json
  end

  def teardown
    ENV.delete('SMTP_CIRCUIT_FAILURE_THRESHOLD')
    ENV.delete('SMTP_CIRCUIT_RECOVERY_TIMEOUT')
    $stderr = STDERR
  end

  # Helper to simulate SMTP failure
  def simulate_smtp_failure
    raise Net::OpenTimeout.new('Connection timed out')
  end

  # Helper to simulate SMTP success
  def simulate_smtp_success
    true
  end

  # AC-1: When fewer than 5 consecutive SMTP delivery failures occur, circuit remains closed, all delivery attempts are passed through to SMTP server
  def test_ac1_fewer_than_5_failures_circuit_stays_closed
    attempt_count = 0
    # 4 consecutive failures
    4.times do
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) do
          attempt_count += 1
          simulate_smtp_failure
        end
      end
    end

    # Verify all 4 attempts were made
    assert_equal 4, attempt_count
    # Verify circuit is still closed
    state = Prometheus::Client.registry.get(:email_service_smtp_circuit_breaker_state)
    assert_equal 1, state.get(labels: { state: 'closed' })
    assert_equal 0, state.get(labels: { state: 'open' })
    assert_equal 0, state.get(labels: { state: 'half_open' })
  end

  # AC-2: When 5 consecutive SMTP delivery failures occur, circuit transitions to open state, no SMTP calls are made for 30 seconds, all send requests return 503 error with correct body and retry_after:30 header
  def test_ac2_5_consecutive_failures_open_circuit_returns_503
    # Trigger 5 failures to open circuit
    5.times do
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) { simulate_smtp_failure }
      end
    end

    # Verify circuit is open
    state = Prometheus::Client.registry.get(:email_service_smtp_circuit_breaker_state)
    assert_equal 1, state.get(labels: { state: 'open' })

    # Test next delivery attempt - no SMTP call made, raises OpenError
    smtp_called = false
    assert_raises CircuitBreaker::OpenError do
      deliver_with_circuit_breaker(Mail.new) do
        smtp_called = true
        simulate_smtp_failure
      end
    end
    assert_equal false, smtp_called, "SMTP call was made when circuit is open"

    # Test HTTP endpoint returns 503 with correct response
    post '/send_email', @test_email_payload, { 'CONTENT_TYPE' => 'application/json' }
    assert_equal 503, last_response.status
    response_body = JSON.parse(last_response.body)
    assert_equal 'Email service temporarily unavailable: SMTP circuit is open', response_body['error']
    assert_equal 'SMTP_CIRCUIT_OPEN', response_body['code']
    assert_equal 30, response_body['retry_after']
    assert_equal '30', last_response.headers['Retry-After']
  end

  # AC-3: After 30 seconds in open state, circuit transitions to half-open state, allowing 1 test delivery attempt to SMTP server
  def test_ac3_30_seconds_open_transitions_to_half_open
    # Open circuit
    5.times do
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) { simulate_smtp_failure }
      end
    end
    assert_equal 1, Prometheus::Client.registry.get(:email_service_smtp_circuit_breaker_state).get(labels: { state: 'open' })

    # Simulate time passing 30 seconds
    Time.stub :now, Time.now + 30 do
      # First attempt should be allowed (half-open state)
      smtp_called = false
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) do
          smtp_called = true
          simulate_smtp_failure
        end
      end
      assert_equal true, smtp_called, "SMTP call not made in half-open state"
    end
  end

  # AC-4: If the half-open test delivery succeeds, circuit transitions back to closed state, all subsequent delivery attempts are passed through normally
  def test_ac4_half_open_success_transitions_to_closed
    # Open circuit
    5.times do
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) { simulate_smtp_failure }
      end
    end

    # Go to half-open and succeed
    Time.stub :now, Time.now + 30 do
      result = deliver_with_circuit_breaker(Mail.new) { simulate_smtp_success }
      assert_equal true, result
    end

    # Verify circuit is closed
    state = Prometheus::Client.registry.get(:email_service_smtp_circuit_breaker_state)
    assert_equal 1, state.get(labels: { state: 'closed' })
    assert_equal 0, state.get(labels: { state: 'open' })

    # Verify subsequent calls pass through
    call_count = 0
    3.times do
      result = deliver_with_circuit_breaker(Mail.new) do
        call_count += 1
        simulate_smtp_success
      end
      assert_equal true, result
    end
    assert_equal 3, call_count
  end

  # AC-5: If the half-open test delivery fails, circuit transitions back to open state for another 30 seconds
  def test_ac5_half_open_failure_re_opens_circuit
    # Open circuit
    5.times do
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) { simulate_smtp_failure }
      end
    end

    # Go to half-open and fail
    Time.stub :now, Time.now + 30 do
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) { simulate_smtp_failure }
      end
    end

    # Verify circuit is back to open
    assert_equal 1, Prometheus::Client.registry.get(:email_service_smtp_circuit_breaker_state).get(labels: { state: 'open' })

    # Verify no SMTP calls are allowed
    smtp_called = false
    assert_raises CircuitBreaker::OpenError do
      deliver_with_circuit_breaker(Mail.new) do
        smtp_called = true
        simulate_smtp_failure
      end
    end
    assert_equal false, smtp_called
  end

  # AC-6: All circuit state transitions are recorded as increments to transitions_total counter with correct labels
  def test_ac6_state_transitions_recorded_in_counter
    transitions_counter = Prometheus::Client.registry.get(:email_service_smtp_circuit_breaker_transitions_total)

    # Closed -> Open
    5.times do
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) { simulate_smtp_failure }
      end
    end
    assert_equal 1, transitions_counter.get(labels: { from_state: 'closed', to_state: 'open' })

    # Open -> Half-open
    Time.stub :now, Time.now + 30 do
      # First call after timeout transitions to half-open
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) { simulate_smtp_failure }
      end
    end
    assert_equal 1, transitions_counter.get(labels: { from_state: 'open', to_state: 'half_open' })

    # Half-open -> Open
    assert_equal 1, transitions_counter.get(labels: { from_state: 'half_open', to_state: 'open' })

    # Open -> Half-open -> Closed
    Time.stub :now, Time.now + 30 do
      deliver_with_circuit_breaker(Mail.new) { simulate_smtp_success }
    end
    assert_equal 1, transitions_counter.get(labels: { from_state: 'open', to_state: 'half_open' })
    assert_equal 1, transitions_counter.get(labels: { from_state: 'half_open', to_state: 'closed' })
  end

  # AC-7: The state gauge always reports 1 for current active state and 0 for others
  def test_ac7_state_gauge_reports_correct_values
    state_gauge = Prometheus::Client.registry.get(:email_service_smtp_circuit_breaker_state)

    # Initial state: closed
    assert_equal 1, state_gauge.get(labels: { state: 'closed' })
    assert_equal 0, state_gauge.get(labels: { state: 'open' })
    assert_equal 0, state_gauge.get(labels: { state: 'half_open' })

    # Open circuit
    5.times do
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) { simulate_smtp_failure }
      end
    end
    assert_equal 0, state_gauge.get(labels: { state: 'closed' })
    assert_equal 1, state_gauge.get(labels: { state: 'open' })
    assert_equal 0, state_gauge.get(labels: { state: 'half_open' })

    # Half-open
    Time.stub :now, Time.now + 30 do
      # We need to trigger the transition to half-open
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) { simulate_smtp_failure }
      end
      # Verify during half-open state
      assert_equal 0, state_gauge.get(labels: { state: 'closed' })
      assert_equal 0, state_gauge.get(labels: { state: 'open' })
      assert_equal 1, state_gauge.get(labels: { state: 'half_open' })
    end

    # Back to open after half-open failure
    assert_equal 0, state_gauge.get(labels: { state: 'closed' })
    assert_equal 1, state_gauge.get(labels: { state: 'open' })
    assert_equal 0, state_gauge.get(labels: { state: 'half_open' })
  end

  # AC-8: All email delivery spans include smtp.circuit_breaker.state attribute matching current state
  def test_ac8_delivery_spans_include_circuit_state_attribute
    # Test closed state span attribute
    OpenTelemetry::Trace.stub :current_span, OpenTelemetry::Trace::Span.new do |span|
      span.expect(:set_attribute, nil, ['smtp.circuit_breaker.state', 'closed'])
      deliver_with_circuit_breaker(Mail.new) { simulate_smtp_success }
      span.verify
    end

    # Open circuit
    5.times do
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) { simulate_smtp_failure }
      end
    end

    # Test open state span attribute
    OpenTelemetry::Trace.stub :current_span, OpenTelemetry::Trace::Span.new do |span|
      span.expect(:set_attribute, nil, ['smtp.circuit_breaker.state', 'open'])
      assert_raises CircuitBreaker::OpenError do
        deliver_with_circuit_breaker(Mail.new) { simulate_smtp_success }
      end
      span.verify
    end
  end

  # AC-9: When circuit is open, no SMTP network calls are made during delivery attempts
  def test_ac9_open_circuit_no_smtp_calls
    # Open circuit
    5.times do
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) { simulate_smtp_failure }
      end
    end

    smtp_called = false
    10.times do
      assert_raises CircuitBreaker::OpenError do
        deliver_with_circuit_breaker(Mail.new) do
          smtp_called = true
          simulate_smtp_failure
        end
      end
    end

    assert_equal false, smtp_called, "SMTP calls were made when circuit was open"
  end

  # AC-10: Existing retry and backoff logic for SMTP delivery remains intact and executes normally when circuit is in closed or half-open state
  def test_ac10_existing_retry_logic_works_with_circuit_closed
    ENV['EMAIL_SMTP_MAX_RETRIES'] = '3'
    retry_count = 0

    # Circuit closed - retries should work as normal
    assert_raises Net::OpenTimeout do
      deliver_with_circuit_breaker(Mail.new) do
        retry_count += 1
        simulate_smtp_failure
      end
    end

    # 1 initial + 3 retries = 4 attempts
    assert_equal 4, retry_count
    assert_equal 3, Prometheus::Client.registry.get(:email_delivery_retry_total).values.sum

    # Half-open state - retries should also work
    5.times do
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) { simulate_smtp_failure }
      end
    end

    # Go to half-open
    Time.stub :now, Time.now + 30 do
      retry_count_half = 0
      assert_raises Net::OpenTimeout do
        deliver_with_circuit_breaker(Mail.new) do
          retry_count_half += 1
          simulate_smtp_failure
        end
      end
      assert_equal 4, retry_count_half, "Retries not working in half-open state"
    end
  end
end
