# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

require "ostruct"
require "pony"
require "sinatra"
require "json"
require "uri"
require "rack/attack"
require "open_feature/sdk"
require "openfeature/flagd/provider"
require "openssl"
require "prometheus/client"
require "stoplight"

# Initialize Prometheus registry
Prometheus::Client.configure do |config|
  config.initial_mmap_file_size = 4 * 1024 * 1024
end
PROMETHEUS_REGISTRY = Prometheus::Client.registry

# SMTP retry metrics
$email_delivery_success_total = PROMETHEUS_REGISTRY.counter(:email_delivery_success_total, docstring: "Increments on every successful email delivery")
$email_delivery_failed_total = PROMETHEUS_REGISTRY.counter(:email_delivery_failed_total, docstring: "Increments when delivery fails permanently", labels: [:failure_type])
$email_delivery_retry_total = PROMETHEUS_REGISTRY.counter(:email_delivery_retry_total, docstring: "Increments each time a retry attempt is initiated", labels: [:retry_attempt])

# Circuit Breaker metrics
$circuit_breaker_state_gauge = PROMETHEUS_REGISTRY.gauge(:email_service_smtp_circuit_breaker_state, docstring: "Current state of SMTP circuit breaker (1 for active state)", labels: [:state])
$circuit_breaker_transitions_counter = PROMETHEUS_REGISTRY.counter(:email_service_smtp_circuit_breaker_transitions_total, docstring: "Total number of circuit breaker state transitions", labels: [:from_state, :to_state])

# Initialize gauge values to 0
%w[closed open half_open].each { |state| $circuit_breaker_state_gauge.set(0, labels: { state: state }) }
$circuit_breaker_state_gauge.set(1, labels: { state: "closed" })

# Circuit Breaker configuration
CIRCUIT_BREAKER_DEFAULTS = {
  failure_threshold: ENV.fetch('SMTP_CIRCUIT_FAILURE_THRESHOLD', 5).to_i,
  recovery_timeout: ENV.fetch('SMTP_CIRCUIT_RECOVERY_TIMEOUT', 30).to_i,
  expected_exceptions: [Net::OpenTimeout, Net::ReadTimeout, Net::SMTPFatalError, Net::SMTPServerBusy, Errno::ETIMEDOUT, Errno::ECONNRESET, SocketError]
}.freeze

# Wraps existing SMTP delivery calls with circuit breaker protection
# @param mail [Mail] Ruby Mail object to deliver
# @return [Boolean] true if delivery was successful
# @raise [Stoplight::Error::Open] if circuit is open, no delivery attempt made
# @raise [StandardError] any SMTP delivery error from upstream if circuit is closed
def deliver_with_circuit_breaker(mail)
  circuit = Stoplight('smtp-delivery') do
    mail.deliver
  end
  .with_threshold(CIRCUIT_BREAKER_DEFAULTS[:failure_threshold])
  .with_timeout(CIRCUIT_BREAKER_DEFAULTS[:recovery_timeout])
  .with_expected_errors(CIRCUIT_BREAKER_DEFAULTS[:expected_exceptions])

  # Add state transition handler
  circuit.on_transition do |from, to|
    $circuit_breaker_transitions_counter.increment(labels: { from_state: from.to_s, to_state: to.to_s })
    # Update OTel transitions counter
    $otel_circuit_transitions_counter.add(1, attributes: { 'from_state' => from.to_s, 'to_state' => to.to_s })
    # Update Prometheus gauge
    %w[closed open half_open].each { |state| $circuit_breaker_state_gauge.set(0, labels: { state: state }) }
    $circuit_breaker_state_gauge.set(1, labels: { state: to.to_s })
    # Update OTel state gauge
    %w[closed open half_open].each { |state| $otel_circuit_state_gauge.set(0, attributes: { 'state' => state }) }
    $otel_circuit_state_gauge.set(1, attributes: { 'state' => to.to_s })
    # Add span event for state change
    current_span = OpenTelemetry::Trace.current_span
    current_span.add_event(
      'circuit_breaker_state_change',
      attributes: {
        'from' => from.to_s,
        'to' => to.to_s,
        'failure_count' => circuit.failures.count
      }
    )
  end

  # Get current state and add to span
  current_state = circuit.state.to_s
  current_span = OpenTelemetry::Trace.current_span
  current_span.set_attribute('smtp.circuit_breaker.state', current_state)

  circuit.run
end

require "opentelemetry/sdk"
require "opentelemetry-logs-sdk"
require "opentelemetry-metrics-sdk"
require "opentelemetry/exporter/otlp"
require "opentelemetry-exporter-otlp-logs"
require "opentelemetry-exporter-otlp-metrics"
require "opentelemetry/instrumentation/sinatra"

# TLS/mTLS Configuration
tls_enabled = ENV.fetch("EMAIL_SERVICE_TLS_ENABLED", "false") == "true"
if tls_enabled
  cert_path = ENV["EMAIL_SERVICE_SSL_CERT_PATH"]
  key_path = ENV["EMAIL_SERVICE_SSL_KEY_PATH"]
  
  unless cert_path && File.exist?(cert_path) && key_path && File.exist?(key_path)
    STDERR.puts "Error: Invalid SSL configuration - EMAIL_SERVICE_SSL_CERT_PATH and EMAIL_SERVICE_SSL_KEY_PATH must be provided and point to valid files when TLS is enabled"
    exit 1
  end

  mtls_enabled = ENV.fetch("EMAIL_SERVICE_MTLS_ENABLED", "false") == "true"
  ca_cert_path = ENV["EMAIL_SERVICE_SSL_CA_CERT_PATH"] if mtls_enabled

  if mtls_enabled
    unless ca_cert_path && File.exist?(ca_cert_path)
      STDERR.puts "Error: Invalid mTLS configuration - EMAIL_SERVICE_SSL_CA_CERT_PATH must be provided and point to a valid file when mTLS is enabled"
      exit 1
    end
  end

  # Configure SSL settings for the server
  ssl_settings = {
    SSLEnable: true,
    SSLCertificate: OpenSSL::X509::Certificate.new(File.read(cert_path)),
    SSLPrivateKey: OpenSSL::PKey::RSA.new(File.read(key_path)),
  }

  if mtls_enabled
    ssl_settings[:SSLVerifyClient] = OpenSSL::SSL::VERIFY_PEER | OpenSSL::SSL::VERIFY_FAIL_IF_NO_PEER_CERT
    ssl_settings[:SSLClientCA] = [OpenSSL::X509::Certificate.new(File.read(ca_cert_path))]
  else
    ssl_settings[:SSLVerifyClient] = OpenSSL::SSL::VERIFY_NONE
  end

  set :server_settings, ssl_settings
  set :port, 8443
else
  # Default plain HTTP configuration (backward compatible)
  set :port, ENV["EMAIL_PORT"] || 8080
end

# Graceful shutdown state
$shutting_down = false
$active_operations = 0
$active_operations_mutex = Mutex.new
$grace_period = 30

# Reject new requests when shutting down
before do
  if $shutting_down
    halt 503, JSON.generate({ status: "NOT_SERVING" })
  end
end

# Track active in-flight operations
around do |controller, block|
  $active_operations_mutex.synchronize { $active_operations += 1 }
  begin
    block.call
  ensure
    $active_operations_mutex.synchronize { $active_operations -= 1 }
  end
end

# Health check endpoint
get '/health' do
  content_type :json
  status $shutting_down ? 503 : 200
  { status: $shutting_down ? "NOT_SERVING" : "SERVING" }.to_json
end

# Liveness probe endpoint
get '/health/liveness' do
  content_type :json
  status 200
  { status: "ok", service: "email" }.to_json
end

# Readiness probe endpoint
get '/health/readiness' do
  content_type :json
  if $shutting_down
    status 503
    { status: "unavailable", service: "email", ready: false }.to_json
  else
    status 200
    { status: "ok", service: "email", ready: true }.to_json
  end
end

# Return 405 Method Not Allowed for non-GET requests on health endpoints
['/health/liveness', '/health/readiness'].each do |path|
  post path do
    status 405
  end
  put path do
    status 405
  end
  patch path do
    status 405
  end
  delete path do
    status 405
  end
  options path do
    status 405
  end
  head path do
    status 405
  end
end

# Initialize OpenFeature SDK with flagd provider
flagd_client = OpenFeature::Flagd::Provider.build_client
flagd_client.configure do |config|
  config.host = ENV.fetch("FLAGD_HOST", "localhost")
  config.port = ENV.fetch("FLAGD_PORT", 8013).to_i
  config.tls = ENV.fetch("FLAGD_TLS", "false") == "true"
end

OpenFeature::SDK.configure do |config|
  config.set_provider(flagd_client)
end

OpenTelemetry::SDK.configure do |c|
  c.use "OpenTelemetry::Instrumentation::Sinatra"
end

$logger = OpenTelemetry.logger_provider.logger(name: 'email')

otlp_metric_exporter = OpenTelemetry::Exporter::OTLP::Metrics::MetricsExporter.new
OpenTelemetry.meter_provider.add_metric_reader(otlp_metric_exporter)
meter = OpenTelemetry.meter_provider.meter("email")
$confirmation_counter = meter.create_counter("demo.notification.confirmations", unit: "1", description: "Counts the number of order confirmation emails sent")
$rate_limited_counter = meter.create_counter("email_service_rate_limited_requests_total", unit: "1", description: "Counts the number of rate-limited email requests")

# Circuit Breaker OpenTelemetry metrics
$otel_circuit_state_gauge = meter.create_gauge(
  "email_service.smtp.circuit_breaker.state",
  unit: "1",
  description: "Current state of SMTP circuit breaker (1 for active state)"
)
$otel_circuit_transitions_counter = meter.create_counter(
  "email_service.smtp.circuit_breaker.transitions_total",
  unit: "1",
  description: "Total number of circuit breaker state transitions"
)

# Initialize OTel gauge values
%w[closed open half_open].each { |state| $otel_circuit_state_gauge.set(0, attributes: { 'state' => state }) }
$otel_circuit_state_gauge.set(1, attributes: { 'state' => 'closed' })

# Rate limit configuration
RATE_LIMIT_THRESHOLD = ENV.fetch("EMAIL_RATE_LIMIT_THRESHOLD", 100).to_i
RATE_LIMIT_WINDOW = ENV.fetch("EMAIL_RATE_LIMIT_WINDOW_SECONDS", 60).to_i
RATE_LIMIT_SCOPE = ENV.fetch("EMAIL_RATE_LIMIT_SCOPE", "global")

# Configure Rack::Attack
use Rack::Attack

Rack::Attack.throttle("email_send_confirmation", limit: RATE_LIMIT_THRESHOLD, period: RATE_LIMIT_WINDOW) do |req|
  if req.post? && req.path == "/send_order_confirmation"
    if RATE_LIMIT_SCOPE == "ip"
      req.ip
    else
      "global"
    end
  end
end

# Custom response for throttled requests
Rack::Attack.throttled_responder = lambda do |request|
  match_data = request.env['rack.attack.match_data']
  retry_after = match_data[:period] - (Time.now.to_i % match_data[:period])
  
  # Increment metric
  labels = { scope: RATE_LIMIT_SCOPE }
  labels[:ip] = request.ip if RATE_LIMIT_SCOPE == "ip"
  $rate_limited_counter.add(1, labels)
  
  # Emit structured log
  $logger.on_emit(
    timestamp: Time.now,
    severity_text: 'WARN',
    body: 'Request rate limited',
    attributes: {
      client_ip: request.ip,
      rate_limit_scope: RATE_LIMIT_SCOPE,
      rate_limit_threshold: RATE_LIMIT_THRESHOLD,
      rate_limit_window_seconds: RATE_LIMIT_WINDOW,
      retry_after_seconds: retry_after
    }
  )
  
  [
    429,
    {
      'Content-Type' => 'application/json',
      'Retry-After' => retry_after.to_s
    },
    [
      JSON.generate({
        error: "Too many requests",
        retry_after: retry_after
      })
    ]
  ]
end

post "/send" do
  data = JSON.parse(request.body.read, object_class: OpenStruct)
  request_id = request.uuid
  invalid_fields = []

  # Input validation
  if data.email.nil? || data.email.to_s.strip.empty?
    invalid_fields << "email"
  else
    # Validate email format with RFC 5322 compliant regex
    unless data.email.match?(URI::MailTo::EMAIL_REGEXP)
      invalid_fields << "email"
    end
  end

  if data.order.nil?
    invalid_fields << "order.order_id"
  else
    if data.order.order_id.nil? || data.order.order_id.to_s.strip.empty?
      invalid_fields << "order.order_id"
    end
  end

  # If any validation errors, return 400 and log
  unless invalid_fields.empty?
    # Mask email for logging: keep first character and domain
    masked_email = if data.email && !data.email.empty?
      parts = data.email.split('@')
      if parts.length == 2
        "#{parts[0][0]}***@#{parts[1]}"
      else
        "***"
      end
    else
      nil
    end

    order_id_provided = data.order&.order_id ? data.order.order_id : nil

    # Emit structured error log
    $logger.on_emit(
      timestamp: Time.now.utc.iso8601,
      severity_text: 'ERROR',
      body: 'Request validation failed for email send endpoint',
      attributes: {
        level: 'error',
        service: 'email-service',
        endpoint: 'POST /send',
        request_id: request_id,
        invalid_fields: invalid_fields,
        context: {
          email_provided: masked_email,
          order_id_provided: order_id_provided
        }
      }
    )

    # Also write to stderr as JSON for test capture
    log_entry = {
      level: "error",
      timestamp: Time.now.utc.iso8601,
      message: "Request validation failed for email send endpoint",
      service: "email-service",
      endpoint: "POST /send",
      request_id: request_id,
      invalid_fields: invalid_fields,
      context: {
        email_provided: masked_email,
        order_id_provided: order_id_provided
      }
    }
    $stderr.puts log_entry.to_json

    content_type :json
    status 400
    return {
      error: "Invalid request parameters",
      invalid_fields: invalid_fields,
      request_id: request_id
    }.to_json
  end

  # get the current auto-instrumented span
  current_span = OpenTelemetry::Trace.current_span
  current_span.add_attributes({
    "demo.order.id" => data.order.order_id,
  })

  $confirmation_counter.add(1)
  send_email(data)
end

def retry_smtp_delivery(&block)
  max_retries = ENV.fetch('EMAIL_SMTP_MAX_RETRIES', 3).to_i
  return yield if max_retries <= 0

  retries = 0
  recipient = nil
  begin
    return yield
  rescue => e
    # Extract recipient from the block context if available (for logging)
    if block.binding.eval('defined? data')
      recipient = block.binding.eval('data.email')
    end

    permanent_failure = false
    smtp_code = nil

    if e.is_a?(Net::SMTPError)
      smtp_code = e.instance_variable_get(:@status) || e.message.match(/^(\d{3})/)&.captures&.first
      if smtp_code && smtp_code.start_with?('5')
        permanent_failure = true
      end
    end

    # Network errors are always temporary
    if [Errno::ETIMEDOUT, Errno::ECONNRESET, SocketError].any? { |c| e.is_a?(c) }
      permanent_failure = false
    end

    if permanent_failure || retries >= max_retries
      # Log permanent failure
      failure_type = permanent_failure ? 'permanent' : 'temporary'
      log_entry = {
        level: "error",
        timestamp: Time.now.utc.iso8601,
        message: "Email delivery failed permanently",
        recipient: recipient,
        smtp_code: smtp_code,
        total_retries: retries,
        failure_type: failure_type
      }
      $stderr.puts log_entry.to_json

      $email_delivery_failed_total.add(1, labels: { failure_type: failure_type })

      raise e
    end

    # Calculate backoff with jitter
    base_delay = 2 ** retries
    jitter = rand * 0.4 - 0.2 # ±20% jitter
    delay = [base_delay * (1 + jitter), 30].min # cap at 30s

    # Log retry warning
    log_entry = {
      level: "warn",
      timestamp: Time.now.utc.iso8601,
      message: "Retrying email delivery",
      recipient: recipient,
      smtp_code: smtp_code,
      retry_count: retries + 1,
      max_retries: max_retries,
      next_retry_delay: delay
    }
    $stderr.puts log_entry.to_json

    $email_delivery_retry_total.add(1, labels: { retry_attempt: retries + 1 })

    sleep delay
    retries += 1
    retry
  end
end

error do
  OpenTelemetry::Trace.current_span.record_exception(env['sinatra.error'])
end

error Stoplight::Error::Open do
  content_type :json
  status 503
  headers['Retry-After'] = CIRCUIT_BREAKER_DEFAULTS[:recovery_timeout].to_s
  {
    error: "Email service temporarily unavailable: SMTP circuit is open",
    code: "SMTP_CIRCUIT_OPEN",
    retry_after: CIRCUIT_BREAKER_DEFAULTS[:recovery_timeout]
  }.to_json
end

def send_email(data)
  # create and start a manual span
  tracer.in_span("send_email") do |span|
    # Check if memory leak flag is enabled
    client = OpenFeature::SDK.build_client
    memory_leak_multiplier = client.fetch_number_value(flag_key: "emailMemoryLeak", default_value: 0)

    # To speed up the memory leak we create a long email body
    confirmation_content = erb(:confirmation, locals: { order: data.order })
    whitespace_length = [0, confirmation_content.length * (memory_leak_multiplier-1)].max

    retry_smtp_delivery do
      mail = Pony.build_mail(
        to:       data.email,
        from:     "noreply@example.com",
        subject:  "Your confirmation email",
        body:     confirmation_content + " " * whitespace_length,
        via:      :test
      )
      deliver_with_circuit_breaker(mail)
    end

    $email_delivery_success_total.add(1)

    # If not clearing the deliveries, the emails will accumulate in the test mailer
    # We use this to create a memory leak.
    if memory_leak_multiplier < 1
      Mail::TestMailer.deliveries.clear
    end

    span.set_attribute("demo.order.id", data.order.order_id)
    $logger.on_emit(
      timestamp: Time.now,
      severity_text: 'INFO',
      body: 'Order confirmation email sent',
      attributes: { 'demo.order.id' => data.order.order_id },
    )

    puts "Order confirmation email sent for order #{data.order.order_id}"
  end
  # manually created spans need to be ended
  # in Ruby, the method `in_span` ends it automatically
  # check out the OpenTelemetry Ruby docs at: 
  # https://opentelemetry.io/docs/instrumentation/ruby/manual/#creating-new-spans 
end

# Signal handlers for graceful shutdown
def handle_shutdown_signal(signal)
  return if $shutting_down

  $shutting_down = true
  $logger.on_emit(
    timestamp: Time.now,
    severity_text: 'INFO',
    body: "Received #{signal} signal, starting graceful shutdown with #{$grace_period}s grace period",
    attributes: { signal: signal, grace_period_seconds: $grace_period },
  )
  puts "Shutdown signal received, waiting for in-flight operations to complete..."

  start_time = Time.now
  while Time.now - start_time < $grace_period
    active = $active_operations_mutex.synchronize { $active_operations }
    if active == 0
      $logger.on_emit(
        timestamp: Time.now,
        severity_text: 'INFO',
        body: "All in-flight operations completed, exiting gracefully",
        attributes: { shutdown_duration_seconds: Time.now - start_time },
      )
      puts "All operations completed, exiting."
      exit 0
    end
    sleep 0.5
  end

  # Timeout reached
  active = $active_operations_mutex.synchronize { $active_operations }
  $logger.on_emit(
    timestamp: Time.now,
    severity_text: 'ERROR',
    body: "Grace period timeout reached, aborting #{active} in-flight operations",
    attributes: { grace_period_seconds: $grace_period, aborted_operations_count: active },
  )
  puts "Timeout reached, aborting #{active} incomplete operations."
  exit 1
end

Signal.trap('SIGINT') { handle_shutdown_signal('SIGINT') }
Signal.trap('SIGTERM') { handle_shutdown_signal('SIGTERM') }

# Add gRPC Health Check implementation
# health_checker = Grpc::Health::Checker.new
# health_checker.add_status("", Grpc::Health::V1::HealthCheckResponse::ServingStatus::SERVING)

# # Override check method to return INVALID_ARGUMENT for non-empty service names
# class << health_checker
#   alias :original_check :check

#   def check(req, call)
#     unless req.service.empty?
#       raise GRPC::InvalidArgument.new("service name parameter is not supported")
#     end
#     status = $shutting_down ? Grpc::Health::V1::HealthCheckResponse::ServingStatus::NOT_SERVING : Grpc::Health::V1::HealthCheckResponse::ServingStatus::SERVING
#     Grpc::Health::V1::HealthCheckResponse.new(status: status)
#   end
# end

# # Register health servicer with the gRPC server
# server.handle(health_checker)
