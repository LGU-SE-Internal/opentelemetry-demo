# frozen_string_literal: true

require "test_helper"
require "minitest/autorun"
require "opentelemetry/sdk"

class TestDLQAcceptanceCriteria < Minitest::Test
  def setup
    # Reset environment variables before each test
    ENV.delete("EMAIL_DLQ_BACKEND")
    ENV.delete("EMAIL_DLQ_FILESYSTEM_PATH")
    ENV.delete("EMAIL_DLQ_REDIS_URL")
    ENV.delete("EMAIL_DLQ_REDIS_KEY_PREFIX")
    
    # Reset OpenTelemetry metrics for testing
    @meter_provider = OpenTelemetry::SDK::Metrics::MeterProvider.new
    OpenTelemetry.meter_provider = @meter_provider
    @meter = @meter_provider.meter("test.dlq.metrics")
  end

  # AC-1: 5xx permanent failure writes to DLQ within 1s, no exception to main thread, all fields present
  def test_ac1_5xx_permanent_failure_writes_to_dlq
    # Arrange
    smtp_response_code = 550
    recipient = "test@example.com"
    payload = "MIME-Version: 1.0\r\nFrom: sender@example.com\r\nTo: test@example.com\r\nSubject: Test\r\n\r\nTest body"
    error_details = { code: smtp_response_code, message: "5.1.1 User unknown", timestamp: Time.now.utc }
    attempts = 1

    # Act: Simulate 5xx delivery failure
    main_thread_raised = false
    begin
      # Simulate delivery failure flow that should write to DLQ
      entry = EmailDLQEntry.new(recipient: recipient, payload: payload, error_details: error_details, attempts: attempts)
      save_result = entry.save
    rescue => e
      main_thread_raised = true
    end

    # Assert
    assert_equal false, main_thread_raised, "Main delivery thread should not raise exceptions for DLQ operations"
    assert_equal true, save_result, "DLQ save should queue successfully"
    
    # Verify entry was written within 1 second
    start_time = Time.now
    entry_found = false
    while Time.now - start_time < 1.0
      # Check DLQ for entry
      backend = DLQBackend.for_config
      if backend.length > 0
        entry_found = true
        stored_entry = backend.read(entry.id) rescue nil
        if stored_entry
          assert_equal recipient, stored_entry.recipient
          assert_equal payload, stored_entry.payload
          assert_equal error_details, stored_entry.error_details
          assert_equal attempts, stored_entry.attempts
          assert_kind_of Time, stored_entry.timestamp
          break
        end
      end
      sleep 0.05
    end
    assert_equal true, entry_found, "DLQ entry should be written within 1 second of failure"
  end

  # AC-2: Transient error retries exhausted writes to DLQ within 1s, no exception to main thread
  def test_ac2_transient_retries_exhausted_writes_to_dlq
    # Arrange
    smtp_response_code = 451
    recipient = "transient@example.com"
    payload = "MIME-Version: 1.0\r\nFrom: sender@example.com\r\nTo: transient@example.com\r\nSubject: Transient Test\r\n\r\nTest body"
    error_details = { code: smtp_response_code, message: "4.3.0 Temporary system failure", timestamp: Time.now.utc }
    attempts = 3 # Max retries exhausted

    # Act: Simulate exhausted retries flow
    main_thread_raised = false
    begin
      entry = EmailDLQEntry.new(recipient: recipient, payload: payload, error_details: error_details, attempts: attempts)
      save_result = entry.save
    rescue => e
      main_thread_raised = true
    end

    # Assert
    assert_equal false, main_thread_raised, "Main delivery thread should not raise exceptions for DLQ operations"
    assert_equal true, save_result, "DLQ save should queue successfully"
    
    # Verify entry was written within 1 second
    start_time = Time.now
    entry_found = false
    while Time.now - start_time < 1.0
      backend = DLQBackend.for_config
      if backend.length > 0
        stored_entry = backend.read(entry.id) rescue nil
        if stored_entry
          assert_equal recipient, stored_entry.recipient
          assert_equal payload, stored_entry.payload
          assert_equal error_details, stored_entry.error_details
          assert_equal attempts, stored_entry.attempts
          entry_found = true
          break
        end
      end
      sleep 0.05
    end
    assert_equal true, entry_found, "DLQ entry should be written within 1 second of final failed attempt"
  end

  # AC-3: Filesystem backend writes JSON files to configured path with UUID filenames
  def test_ac3_filesystem_backend_persists_json_files
    # Arrange
    ENV["EMAIL_DLQ_BACKEND"] = "filesystem"
    test_path = Dir.mktmpdir("email-dlq-test")
    ENV["EMAIL_DLQ_FILESYSTEM_PATH"] = test_path
    entry = EmailDLQEntry.new(
      recipient: "fs-test@example.com",
      payload: "Test payload",
      error_details: { code: 550, message: "User unknown" },
      attempts: 1
    )
    expected_filename = "#{entry.id}.json"
    expected_filepath = File.join(test_path, expected_filename)

    # Act
    entry.save
    # Wait for async write
    sleep 0.5

    # Assert
    assert File.exist?(expected_filepath), "DLQ entry JSON file should exist at configured path"
    file_content = File.read(expected_filepath)
    parsed_content = JSON.parse(file_content)
    assert_equal entry.id, parsed_content["id"]
    assert_equal entry.recipient, parsed_content["recipient"]
    assert_equal entry.payload, parsed_content["payload"]
    assert_equal entry.error_details.stringify_keys, parsed_content["error_details"]
    assert_equal entry.attempts, parsed_content["attempts"]
    assert parsed_content.key?("timestamp")

  ensure
    FileUtils.remove_entry test_path if test_path
  end

  # AC-4: Redis backend writes JSON entries with correct key prefix
  def test_ac4_redis_backend_persists_entries
    # Arrange
    ENV["EMAIL_DLQ_BACKEND"] = "redis"
    test_prefix = "test-email-dlq:"
    ENV["EMAIL_DLQ_REDIS_KEY_PREFIX"] = test_prefix
    entry = EmailDLQEntry.new(
      recipient: "redis-test@example.com",
      payload: "Redis test payload",
      error_details: { code: 550, message: "User unknown" },
      attempts: 2
    )
    expected_key = "#{test_prefix}#{entry.id}"

    # Mock Redis client for test
    mock_redis = mock("Redis")
    DLQBackend::RedisBackend.stubs(:redis_client).returns(mock_redis)
    mock_redis.expects(:set).with(expected_key, instance_of(String)).returns(true)

    # Act
    entry.save
    sleep 0.2 # Wait for async write
  end

  # AC-5: email_dlq.ingest_count counter increments on successful write with backend attribute
  def test_ac5_ingest_count_metric_increments
    # Arrange
    ENV["EMAIL_DLQ_BACKEND"] = "filesystem"
    test_path = Dir.mktmpdir("dlq-metric-test")
    ENV["EMAIL_DLQ_FILESYSTEM_PATH"] = test_path
    entry = EmailDLQEntry.new(
      recipient: "metric-test@example.com",
      payload: "Test",
      error_details: { code: 550, message: "Fail" },
      attempts: 1
    )
    metric_exporter = OpenTelemetry::SDK::Metrics::Export::InMemoryMetricPullExporter.new
    @meter_provider.add_metric_reader(OpenTelemetry::SDK::Metrics::MetricReader.new(metric_exporter))

    # Act
    entry.save
    sleep 0.5
    @meter_provider.metric_readers.each(&:pull)

    # Assert
    metrics = metric_exporter.metrics
    ingest_metric = metrics.find { |m| m.name == "email_dlq.ingest_count" }
    assert ingest_metric, "email_dlq.ingest_count metric should exist"
    datapoint = ingest_metric.data_points.first
    assert_equal 1, datapoint.value
    assert_equal "filesystem", datapoint.attributes["backend"]

  ensure
    FileUtils.remove_entry test_path if test_path
  end

  # AC-6: email_dlq.queue_length metric updates every 10s with current DLQ length
  def test_ac6_queue_length_metric_updates
    # Arrange
    ENV["EMAIL_DLQ_BACKEND"] = "filesystem"
    test_path = Dir.mktmpdir("dlq-length-test")
    ENV["EMAIL_DLQ_FILESYSTEM_PATH"] = test_path
    metric_exporter = OpenTelemetry::SDK::Metrics::Export::InMemoryMetricPullExporter.new
    @meter_provider.add_metric_reader(OpenTelemetry::SDK::Metrics::MetricReader.new(metric_exporter))
    backend = DLQBackend.for_config

    # Act 1: Initial state, queue length 0
    sleep 10 # Wait for first metric update
    @meter_provider.metric_readers.each(&:pull)
    metrics = metric_exporter.metrics
    length_metric = metrics.find { |m| m.name == "email_dlq.queue_length" }
    assert length_metric
    assert_equal 0, length_metric.data_points.first.value

    # Act 2: Add 2 entries to DLQ
    2.times do |i|
      entry = EmailDLQEntry.new(
        recipient: "length-test-#{i}@example.com",
        payload: "Test #{i}",
        error_details: { code: 550, message: "Fail" },
        attempts: 1
      )
      entry.save
    end
    sleep 0.5 # Wait for writes to complete
    sleep 10 # Wait for next metric update
    metric_exporter.reset
    @meter_provider.metric_readers.each(&:pull)
    metrics = metric_exporter.metrics
    length_metric = metrics.find { |m| m.name == "email_dlq.queue_length" }

    # Assert
    assert_equal 2, length_metric.data_points.first.value
    assert_equal "filesystem", length_metric.data_points.first.attributes["backend"]

  ensure
    FileUtils.remove_entry test_path if test_path
  end

  # AC-7: email_dlq.retry_count increments on successful retry delivery
  def test_ac7_retry_count_metric_increments
    # Arrange
    ENV["EMAIL_DLQ_BACKEND"] = "filesystem"
    test_path = Dir.mktmpdir("dlq-retry-test")
    ENV["EMAIL_DLQ_FILESYSTEM_PATH"] = test_path
    # Create entry in DLQ
    entry = EmailDLQEntry.new(
      recipient: "retry-test@example.com",
      payload: "Retry test",
      error_details: { code: 451, message: "Temp fail" },
      attempts: 3
    )
    entry.save
    sleep 0.5
    metric_exporter = OpenTelemetry::SDK::Metrics::Export::InMemoryMetricPullExporter.new
    @meter_provider.add_metric_reader(OpenTelemetry::SDK::Metrics::MetricReader.new(metric_exporter))

    # Act: Simulate successful retry delivery
    # Assume DLQ retry method exists that deletes entry and increments metric
    DLQBackend.for_config.retry_and_deliver(entry.id)
    sleep 0.5
    @meter_provider.metric_readers.each(&:pull)

    # Assert
    metrics = metric_exporter.metrics
    retry_metric = metrics.find { |m| m.name == "email_dlq.retry_count" }
    assert retry_metric
    assert_equal 1, retry_metric.data_points.first.value
    assert_equal "filesystem", retry_metric.data_points.first.attributes["backend"]
    assert_equal 0, DLQBackend.for_config.length, "DLQ entry should be removed after successful retry"

  ensure
    FileUtils.remove_entry test_path if test_path
  end

  # AC-8: DLQ write failure logs error, no propagation to main thread, main request returns normal failure
  def test_ac8_dlq_write_failure_no_propagation
    # Arrange
    ENV["EMAIL_DLQ_BACKEND"] = "filesystem"
    invalid_path = "/root/non-existent/path/that/will/fail" # Path we can't write to
    ENV["EMAIL_DLQ_FILESYSTEM_PATH"] = invalid_path
    entry = EmailDLQEntry.new(
      recipient: "fail-test@example.com",
      payload: "Fail test",
      error_details: { code: 550, message: "Fail" },
      attempts: 1
    )
    # Mock logger to capture error
    mock_logger = mock("Logger")
    EmailService.stubs(:logger).returns(mock_logger)
    mock_logger.expects(:error).with(instance_of(DLQWriteError)).at_least_once

    # Act
    main_thread_raised = false
    save_result = nil
    begin
      save_result = entry.save
    rescue => e
      main_thread_raised = true
    end

    # Assert
    assert_equal false, main_thread_raised, "Main thread should not raise on DLQ write failure"
    assert_equal false, save_result, "Save should return false on write failure"
  end

  # AC-9: Invalid DLQ backend raises DLQConfigurationError on service startup
  def test_ac9_invalid_backend_configuration_fails_startup
    # Arrange
    ENV["EMAIL_DLQ_BACKEND"] = "invalid_backend_that_does_not_exist"

    # Act & Assert
    assert_raises DLQConfigurationError do
      # Simulate service startup which initializes DLQ backend
      DLQBackend.for_config
    end
  end
end
