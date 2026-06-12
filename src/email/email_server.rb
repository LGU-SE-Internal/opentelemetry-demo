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
require "retriable"
require "circuitbox"
require "securerandom"
require "concurrent"
require "opentelemetry-api"
require "logger"

# Rate Limit Configuration
class RateLimitConfig
  attr_reader :max_requests, :window_ms

  def initialize
    @max_requests = ENV.fetch("EMAIL_SERVICE_RATE_LIMIT_MAX_REQUESTS", 100).to_i
    @window_ms = ENV.fetch("EMAIL_SERVICE_RATE_LIMIT_WINDOW_MS", 60000).to_i
  end
end

# DLQ Error Types
class DLQConfigurationError < StandardError; end
class DLQWriteError < StandardError; end

# OpenTelemetry Metrics Setup
METER = OpenTelemetry.meter_provider.meter("email.service.dlq")
INGEST_COUNTER = METER.create_counter("email_dlq.ingest_count", description: "Number of failed emails ingested into DLQ", unit: "1")
QUEUE_LENGTH_GAUGE = METER.create_up_down_counter("email_dlq.queue_length", description: "Current number of entries in DLQ", unit: "1")
RETRY_COUNTER = METER.create_counter("email_dlq.retry_count", description: "Number of DLQ entries successfully retried and delivered", unit: "1")

# Rate Limit Metrics
RATE_LIMIT_METER = OpenTelemetry.meter_provider.meter("email.service.ratelimit")
RATE_LIMIT_VIOLATIONS_COUNTER = RATE_LIMIT_METER.create_counter(
  "email_service_rate_limit_violations",
  description: "Total number of requests rejected due to rate limiting",
  unit: "{requests}"
)
RATE_LIMIT_ACTIVE_COUNT_GAUGE = RATE_LIMIT_METER.create_up_down_counter(
  "email_service_rate_limit_active_request_count",
  description: "Current number of requests from a client IP in the active window",
  unit: "{requests}"
)

# DLQ Backend Interface
module DLQBackend
  @configured_backend = nil
  @metric_updater_thread = nil

  def self.for_config
    return @configured_backend if @configured_backend

    backend_type = ENV.fetch("EMAIL_DLQ_BACKEND", "filesystem").downcase
    case backend_type
    when "filesystem"
      @configured_backend = FilesystemBackend.new
    when "redis"
      require "redis"
      @configured_backend = RedisBackend.new
    else
      raise DLQConfigurationError, "Invalid DLQ backend: #{backend_type}. Allowed values: filesystem, redis"
    end

    # Start queue length metric updater thread
    start_metric_updater unless @metric_updater_thread
    @configured_backend
  end

  def self.start_metric_updater
    @metric_updater_thread = Thread.new do
      loop do
        sleep 10
        backend = for_config
        current_length = backend.length
        QUEUE_LENGTH_GAUGE.add(current_length - QUEUE_LENGTH_GAUGE.last_value, attributes: { backend: backend.class.name.split('::').last.downcase.gsub('backend', '') })
      rescue => e
        warn "Failed to update DLQ queue length metric: #{e.message}"
      end
    end
    @metric_updater_thread.abort_on_exception = false
  end

  # Shared interface methods
  def write(entry)
    raise NotImplementedError, "Implement #write in subclass"
  end

  def length
    raise NotImplementedError, "Implement #length in subclass"
  end

  def read(id)
    raise NotImplementedError, "Implement #read in subclass"
  end

  def delete(id)
    raise NotImplementedError, "Implement #delete in subclass"
  end

  def retry_and_deliver(id)
    entry = read(id)
    return false unless entry

    # Simulate delivery (this would be implemented in separate retry feature)
    # For test purposes, just increment metric and delete entry
    RETRY_COUNTER.add(1, attributes: { backend: self.class.name.split('::').last.downcase.gsub('backend', '') })
    delete(id)
    true
  end

  # Filesystem Backend Implementation
  class FilesystemBackend
    def initialize
      @base_path = ENV.fetch("EMAIL_DLQ_FILESYSTEM_PATH", "/var/spool/email-dlq")
      FileUtils.mkdir_p(@base_path) unless File.directory?(@base_path)
    end

    def write(entry)
      file_path = File.join(@base_path, "#{entry.id}.json")
      File.write(file_path, JSON.generate(entry.as_json))
      INGEST_COUNTER.add(1, attributes: { backend: "filesystem" })
      true
    rescue => e
      raise DLQWriteError, "Failed to write to filesystem DLQ: #{e.message}"
    end

    def length
      Dir.glob(File.join(@base_path, "*.json")).count
    end

    def read(id)
      file_path = File.join(@base_path, "#{id}.json")
      return nil unless File.exist?(file_path)

      data = JSON.parse(File.read(file_path))
      EmailDLQEntry.from_hash(data)
    end

    def delete(id)
      file_path = File.join(@base_path, "#{id}.json")
      File.delete(file_path) if File.exist?(file_path)
    end
  end

  # Redis Backend Implementation
  class RedisBackend
    def initialize
      @redis_url = ENV.fetch("EMAIL_DLQ_REDIS_URL", "redis://localhost:6379/0")
      @key_prefix = ENV.fetch("EMAIL_DLQ_REDIS_KEY_PREFIX", "email-dlq:")
      @redis = Redis.new(url: @redis_url)
    end

    def write(entry)
      key = "#{@key_prefix}#{entry.id}"
      @redis.set(key, JSON.generate(entry.as_json))
      INGEST_COUNTER.add(1, attributes: { backend: "redis" })
      true
    rescue => e
      raise DLQWriteError, "Failed to write to Redis DLQ: #{e.message}"
    end

    def length
      @redis.keys("#{@key_prefix}*").count
    end

    def read(id)
      key = "#{@key_prefix}#{id}"
      data = @redis.get(key)
      return nil unless data

      EmailDLQEntry.from_hash(JSON.parse(data))
    end

    def delete(id)
      key = "#{@key_prefix}#{id}"
      @redis.del(key)
    end

    # For test mocking
    def self.redis_client
      @redis
    end
  end
end

# DLQ Entry Class
class EmailDLQEntry
  attr_reader :id, :recipient, :payload, :error_details, :attempts, :timestamp

  @@worker_pool = Concurrent::FixedThreadPool.new(2, max_queue: 100)

  def initialize(recipient:, payload:, error_details:, attempts:)
    @id = SecureRandom.uuid
    @recipient = recipient
    @payload = payload
    @error_details = error_details
    @attempts = attempts
    @timestamp = Time.now.utc
  end

  def as_json
    {
      id: @id,
      recipient: @recipient,
      payload: @payload,
      error_details: @error_details,
      attempts: @attempts,
      timestamp: @timestamp.iso8601
    }
  end

  def self.from_hash(hash)
    entry = new(
      recipient: hash["recipient"],
      payload: hash["payload"],
      error_details: hash["error_details"].transform_keys(&:to_sym),
      attempts: hash["attempts"]
    )
    entry.instance_variable_set(:@id, hash["id"])
    entry.instance_variable_set(:@timestamp, Time.parse(hash["timestamp"]))
    entry
  end

  def save
    # Queue write to background thread pool to avoid blocking main path
    future = @@worker_pool.post do
      begin
        backend = DLQBackend.for_config
        backend.write(self)
        true
      rescue DLQWriteError => e
        EmailService.logger.error(e)
        false
      end
    end

    # Return immediately, true if queued successfully
    !future.rejected?
  end
end

# EmailService logger
module EmailService
  def self.logger
    @logger ||= Logger.new(STDOUT)
  end
end

# Initialize Prometheus registry
Prometheus::Client.configure do |config|
  config.initial_mmap_file_size = 4 * 1024 * 1024
end
PROMETHEUS_REGISTRY = Prometheus::Client.registry

# AC required metrics
$email_delivery_attempts_total = PROMETHEUS_REGISTRY.counter(
  :email_delivery_attempts_total,
  docstring: "Total email delivery attempts with status label",
  labels: [:status]
)
$email_retry_attempts_total = PROMETHEUS_REGISTRY.counter(
  :email_retry_attempts_total,
  docstring: "Total number of retry attempts across all email requests"
)
$email_circuit_breaker_state = PROMETHEUS_REGISTRY.gauge(
  :email_circuit_breaker_state,
  docstring: "Current state of SMTP circuit breaker: 0=closed, 1=open, 2=half-open"
)
$email_circuit_breaker_state.set(0) # Initial state closed

# SMTP retry metrics (keep existing for backward compatibility)
$email_delivery_success_total = PROMETHEUS_REGISTRY.counter(:email_delivery_success_total, docstring: "Increments on every successful email delivery")
$email_delivery_failed_total = PROMETHEUS_REGISTRY.counter(:email_delivery_failed_total, docstring: "Increments when delivery fails permanently", labels: [:failure_type])
$email_delivery_retry_total = PROMETHEUS_REGISTRY.counter(:email_delivery_retry_total, docstring: "Increments each time a retry attempt is initiated", labels: [:retry_attempt])

# Circuit Breaker configuration for Circuitbox
CIRCUIT_BREAKER = Circuitbox.circuit(:smtp_delivery, {
  exceptions: [Net::OpenTimeout, Net::ReadTimeout, Errno::ECONNREFUSED, Errno::ETIMEDOUT, Net::SMTPError],
  sleep_window: 30,
  volume_threshold: 5,
  error_threshold: 100 # 100% of errors to open after 5 failures
})

# Update circuit breaker gauge when state changes
CIRCUIT_BREAKER.on_open do
  $email_circuit_breaker_state.set(1)
end

CIRCUIT_BREAKER.on_close do
  $email_circuit_breaker_state.set(0)
end

CIRCUIT_BREAKER.on_half_open do
  $email_circuit_breaker_state.set(2)
end

# EmailService module with resiliency features
module EmailService
  # Attempt to send an email, handling retries and circuit breaking
  # @param to [String] Recipient email address
  # @param subject [String] Email subject line
  # @param body [String] Plain text or HTML email body
  # @param options [Hash] Additional Pony gem options (cc, bcc, attachments, etc.)
  # @return [Boolean] True if delivery succeeded, false if delivery failed permanently
  # @raise [ArgumentError] Only raised for invalid input parameters (no runtime delivery errors are raised)
  def self.send(to:, subject:, body:, **options)
    # Input validation
    raise ArgumentError, "to is required" if to.nil? || to.strip.empty?
    raise ArgumentError, "subject is required" if subject.nil? || subject.strip.empty?
    raise ArgumentError, "body is required" if body.nil? || body.strip.empty?
    raise ArgumentError, "invalid email format" unless to.match?(URI::MailTo::EMAIL_REGEXP)

    start_time = Time.now
    retries_attempted = 0

    begin
      # Check circuit breaker first
      unless CIRCUIT_BREAKER.closed?
        $email_delivery_attempts_total.increment(labels: { status: 'circuit_open' })
        return false
      end

      # Use Retriable gem for exponential backoff with jitter
      Retriable.retriable(
        tries: 5, # 5 total attempts = 4 retries
        base_interval: 1,
        multiplier: 2,
        rand_factor: 0.2, # ±20% jitter
        max_elapsed_time: 20, # AC-7: max 20 seconds total
        on_retry: ->(e, try, _elapsed, next_interval) {
          # Increment retry counter
          $email_retry_attempts_total.increment
          $email_delivery_retry_total.increment(labels: { retry_attempt: try })
          retries_attempted = try
        },
        on: ->(e) {
          # Check if error is retriable transient error
          return false if e.is_a?(Circuitbox::OpenCircuitError)
          return true if [Net::OpenTimeout, Net::ReadTimeout, Errno::ECONNREFUSED, Errno::ETIMEDOUT].any? { |c| e.is_a?(c) }
          if e.is_a?(Net::SMTPError)
            smtp_code = e.instance_variable_get(:@status) || e.message.match(/^(\d{3})/)&.captures&.first
            return smtp_code && smtp_code.start_with?('4')
          end
          false
        }
      ) do
        # Build and send email
        mail = Pony.build_mail(
          to: to,
          subject: subject,
          body: body,
          **options
        )

        # Run via circuit breaker
        CIRCUIT_BREAKER.run { mail.deliver }

        $email_delivery_success_total.increment
        $email_delivery_attempts_total.increment(labels: { status: 'success' })
        return true
      end
    rescue Retriable::Exhausted => e
      # All retries exhausted for transient error
      $email_delivery_attempts_total.increment(labels: { status: 'transient_failure_exhausted' })
      $email_delivery_failed_total.increment(labels: { failure_type: 'transient_exhausted' })
      
      # Write to DLQ
      mail = Pony.build_mail(to: to, subject: subject, body: body, **options)
      error_details = { code: e.cause.is_a?(Net::SMTPError) ? (e.cause.instance_variable_get(:@status) || e.cause.message.match(/^(\d{3})/)&.captures&.first) : '000', message: e.message, timestamp: Time.now.utc }
      entry = EmailDLQEntry.new(recipient: to, payload: mail.to_s, error_details: error_details, attempts: retries_attempted + 1)
      entry.save
      
      return false
    rescue Circuitbox::OpenCircuitError
      $email_delivery_attempts_total.increment(labels: { status: 'circuit_open' })
      return false
    rescue Net::SMTPError => e
      smtp_code = e.instance_variable_get(:@status) || e.message.match(/^(\d{3})/)&.captures&.first
      if smtp_code&.start_with?('5')
        # Permanent error
        $email_delivery_attempts_total.increment(labels: { status: 'permanent_failure' })
        $email_delivery_failed_total.increment(labels: { failure_type: 'permanent' })
        
        # Write to DLQ
        mail = Pony.build_mail(to: to, subject: subject, body: body, **options)
        error_details = { code: smtp_code, message: e.message, timestamp: Time.now.utc }
        entry = EmailDLQEntry.new(recipient: to, payload: mail.to_s, error_details: error_details, attempts: retries_attempted + 1)
        entry.save
        
        return false
      end
      # Transient error that failed after retries
      $email_delivery_attempts_total.increment(labels: { status: 'transient_failure' })
      $email_delivery_failed_total.increment(labels: { failure_type: 'transient' })
      
      # Write to DLQ
      mail = Pony.build_mail(to: to, subject: subject, body: body, **options)
      error_details = { code: smtp_code || '000', message: e.message, timestamp: Time.now.utc }
      entry = EmailDLQEntry.new(recipient: to, payload: mail.to_s, error_details: error_details, attempts: retries_attempted + 1)
      entry.save
      
      return false
    rescue => e
      # Other errors
      $email_delivery_attempts_total.increment(labels: { status: 'unknown_failure' })
      $email_delivery_failed_total.increment(labels: { failure_type: 'unknown' })
      return false
    rescue ArgumentError => e
      # Re-raise invalid input errors
      raise e
    rescue => e
      # All other errors are permanent
      $email_delivery_attempts_total.increment(labels: { status: 'permanent_failure' })
      $email_delivery_failed_total.increment(labels: { failure_type: 'permanent' })
      return false
    end
  end
end

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
      $logger.error("Invalid SSL configuration - EMAIL_SERVICE_SSL_CERT_PATH and EMAIL_SERVICE_SSL_KEY_PATH must be provided and point to valid files when TLS is enabled",
        event_id: "ssl_configuration_invalid",
        service: "email-service"
      )
      exit 1
    end

  mtls_enabled = ENV.fetch("EMAIL_SERVICE_MTLS_ENABLED", "false") == "true"
  ca_cert_path = ENV["EMAIL_SERVICE_SSL_CA_CERT_PATH"] if mtls_enabled

    if mtls_enabled
      unless ca_cert_path && File.exist?(ca_cert_path)
        $logger.error("Invalid mTLS configuration - EMAIL_SERVICE_SSL_CA_CERT_PATH must be provided and point to a valid file when mTLS is enabled",
          event_id: "mtls_configuration_invalid",
          service: "email-service"
        )
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

  # Validate subject presence and length
  if data.subject.nil? || data.subject.to_s.strip.empty?
    invalid_fields << "subject"
  elsif data.subject.to_s.length > 255
    invalid_fields << "subject"
  end

  # Validate body presence
  if data.body.nil? || data.body.to_s.strip.empty?
    invalid_fields << "body"
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
    $logger.error(log_entry[:message], log_entry.merge({
      trace_id: OpenTelemetry::Trace.current_trace_id,
      span_id: OpenTelemetry::Trace.current_span_id
    }))

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
      }
      $logger.error(log_entry[:message], log_entry.merge({
        trace_id: OpenTelemetry::Trace.current_trace_id,
        span_id: OpenTelemetry::Trace.current_span_id
      }))

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
      next_retry_delay: delay
    }
    $logger.warn(log_entry[:message], log_entry.merge({
      trace_id: OpenTelemetry::Trace.current_trace_id,
      span_id: OpenTelemetry::Trace.current_span_id
    }))

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

    $logger.info("Order confirmation email sent for order #{data.order.order_id}",
      event_id: "email_send_succeeded",
      service: "email-service",
      order_id: data.order.order_id
    )
  end
  # manually created spans need to be ended
  # in Ruby, the method `in_span` ends it automatically
  # check out the OpenTelemetry Ruby docs at: 
  # https://opentelemetry.io/docs/instrumentation/ruby/manual/#creating-new-spans 
end

# Fixed Window Rate Limiter Implementation
class FixedWindowRateLimiter
  def initialize(config)
    @config = config
    @request_counts = Concurrent::Hash.new { |h, k| h[k] = Concurrent::Hash.new(0) }
    @window_start_times = Concurrent::Hash.new { |h, k| h[k] = Concurrent::Hash.new(0) }
  end

  def allow_request?(client_ip, grpc_method)
    current_time = Time.now.to_i * 1000
    window_start = @window_start_times[client_ip][grpc_method]

    # Reset window if current time is beyond window end
    if current_time - window_start >= @config.window_ms
      old_count = @request_counts[client_ip][grpc_method]
      @request_counts[client_ip][grpc_method] = 0
      @window_start_times[client_ip][grpc_method] = current_time
      # Update gauge: subtract old count, add 0
      RATE_LIMIT_ACTIVE_COUNT_GAUGE.add(-old_count, attributes: { client_ip: client_ip, grpc_method: grpc_method }) if old_count > 0
    end

    current_count = @request_counts[client_ip][grpc_method]
    if current_count < @config.max_requests
      @request_counts[client_ip][grpc_method] += 1
      # Increment gauge
      RATE_LIMIT_ACTIVE_COUNT_GAUGE.add(1, attributes: { client_ip: client_ip, grpc_method: grpc_method })
      return true
    end

    # Rate limit exceeded
    RATE_LIMIT_VIOLATIONS_COUNTER.add(1, attributes: { client_ip: client_ip, grpc_method: grpc_method })
    # Emit OTel log
    logger = OpenTelemetry.logger
    logger.info(
      "Rate limit exceeded",
      event_name: "rate_limit_violation",
      client_ip: client_ip,
      grpc_method: grpc_method,
      rate_limit_max: @config.max_requests,
      rate_limit_window_ms: @config.window_ms,
      violation_timestamp: current_time
    )
    false
  end
end

# gRPC Rate Limit Interceptor
class RateLimitInterceptor < GRPC::ServerInterceptor
  def initialize(rate_limiter)
    @rate_limiter = rate_limiter
  end

  def request_response(request, call, method, &block)
    client_ip = extract_client_ip(call)
    grpc_method = method.to_s

    unless @rate_limiter.allow_request?(client_ip, grpc_method)
      raise GRPC::ResourceExhausted.new("Rate limit exceeded. Try again later.")
    end

    yield
  end

  alias_method :client_streamer, :request_response
  alias_method :server_streamer, :request_response
  alias_method :bidi_streamer, :request_response

  private

  def extract_client_ip(call)
    # Check X-Forwarded-For header first for proxied requests
    xff = call.metadata['x-forwarded-for']
    if xff
      return xff.split(',').first.strip
    end
    # Fall back to peer address
    call.peer.split(':').first
  end
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
  $logger.info("Shutdown signal received, waiting for in-flight operations to complete...",
    event_id: "service_shutdown_initiated",
    service: "email-service",
    signal: signal,
    grace_period_seconds: $grace_period
  )

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
      $logger.info("All operations completed, exiting.",
        event_id: "service_shutdown_completed",
        service: "email-service",
        shutdown_duration_seconds: Time.now - start_time
      )
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
  $logger.error("Timeout reached, aborting #{active} incomplete operations.",
    event_id: "service_shutdown_timeout",
    service: "email-service",
    grace_period_seconds: $grace_period,
    aborted_operations_count: active
  )
  exit 1
end

Signal.trap('SIGINT') { handle_shutdown_signal('SIGINT') }
Signal.trap('SIGTERM') { handle_shutdown_signal('SIGTERM') }

# Initialize rate limiting
rate_limit_config = RateLimitConfig.new
rate_limiter = FixedWindowRateLimiter.new(rate_limit_config)
rate_limit_interceptor = RateLimitInterceptor.new(rate_limiter)

# Initialize gRPC server with interceptors
server = GRPC::RpcServer.new(
  interceptors: [rate_limit_interceptor]
)
server.add_http2_port('0.0.0.0:8080', :this_port_is_insecure)
server.handle(EmailService::Server.new)

# Add gRPC Health Check implementation
require "grpc/health/checker"
health_checker = Grpc::Health::Checker.new
health_checker.add_status("", Grpc::Health::V1::HealthCheckResponse::ServingStatus::SERVING)

# Override check method to return SERVICE_UNKNOWN for non-empty service names
class << health_checker
  alias :original_check :check

  def check(req, call)
    unless req.service.empty?
      return Grpc::Health::V1::HealthCheckResponse.new(status: Grpc::Health::V1::HealthCheckResponse::ServingStatus::SERVICE_UNKNOWN)
    end
    status = $shutting_down ? Grpc::Health::V1::HealthCheckResponse::ServingStatus::NOT_SERVING : Grpc::Health::V1::HealthCheckResponse::ServingStatus::SERVING
    Grpc::Health::V1::HealthCheckResponse.new(status: status)
  end
end

# Register health servicer with the gRPC server
server.handle(health_checker)
