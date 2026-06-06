# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

require "ostruct"
require "pony"
require "sinatra"
require "json"
require "open_feature/sdk"
require "openfeature/flagd/provider"

require "opentelemetry/sdk"
require "opentelemetry-logs-sdk"
require "opentelemetry-metrics-sdk"
require "opentelemetry/exporter/otlp"
require "opentelemetry-exporter-otlp-logs"
require "opentelemetry-exporter-otlp-metrics"
require "opentelemetry/instrumentation/sinatra"

set :port, ENV["EMAIL_PORT"]

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

post "/send_order_confirmation" do
  data = JSON.parse(request.body.read, object_class: OpenStruct)

  # Input validation
  if data.email.nil? || data.email.to_s.strip.empty?
    raise ArgumentError.new("Email address cannot be empty or nil")
  end

  if data.order.nil?
    raise ArgumentError.new("Order cannot be nil")
  end

  # get the current auto-instrumented span
  current_span = OpenTelemetry::Trace.current_span
  current_span.add_attributes({
    "demo.order.id" => data.order.order_id,
  })

  $confirmation_counter.add(1)
  send_email(data)

end

error do
  OpenTelemetry::Trace.current_span.record_exception(env['sinatra.error'])
end

def send_email(data)
  # create and start a manual span
  tracer = OpenTelemetry.tracer_provider.tracer('email')
  tracer.in_span("send_email") do |span|
    # Check if memory leak flag is enabled
    client = OpenFeature::SDK.build_client
    memory_leak_multiplier = client.fetch_number_value(flag_key: "emailMemoryLeak", default_value: 0)

    # To speed up the memory leak we create a long email body
    confirmation_content = erb(:confirmation, locals: { order: data.order })
    whitespace_length = [0, confirmation_content.length * (memory_leak_multiplier-1)].max

    Pony.mail(
      to:       data.email,
      from:     "noreply@example.com",
      subject:  "Your confirmation email",
      body:     confirmation_content + " " * whitespace_length,
      via:      :test
    )

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
