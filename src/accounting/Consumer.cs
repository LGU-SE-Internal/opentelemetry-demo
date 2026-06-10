// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Confluent.Kafka;
using Microsoft.Extensions.Logging;
using Oteldemo;
using Microsoft.EntityFrameworkCore;
using System.Diagnostics;
using Npgsql;
using Microsoft.Extensions.Configuration;
using System.Net.Sockets;
using System.Text.Json;
using Polly;
using Polly.CircuitBreaker;
using System.Diagnostics.Metrics;

namespace Accounting;

internal class DBContext : DbContext
{
    public DbSet<OrderEntity> Orders { get; set; }
    public DbSet<OrderItemEntity> CartItems { get; set; }
    public DbSet<ShippingEntity> Shipping { get; set; }

    protected override void OnConfiguring(DbContextOptionsBuilder optionsBuilder)
    {
        var connectionString = Environment.GetEnvironmentVariable("DB_CONNECTION_STRING");

        var (pgTlsEnabled, caCertPath, clientCertPath, clientKeyPath) = TlsConfiguration.GetPostgresTlsConfig();
        
        var npgsqlBuilder = new NpgsqlConnectionStringBuilder(connectionString);
        
        if (pgTlsEnabled)
        {
            npgsqlBuilder.SslMode = SslMode.VerifyFull;
            npgsqlBuilder.RootCertificate = caCertPath;
            
            if (!string.IsNullOrWhiteSpace(clientCertPath) && !string.IsNullOrWhiteSpace(clientKeyPath))
            {
                npgsqlBuilder.SslCertificate = clientCertPath;
                npgsqlBuilder.SslKey = clientKeyPath;
            }
        }

        optionsBuilder.UseNpgsql(npgsqlBuilder.ConnectionString).UseSnakeCaseNamingConvention();
    }
}


internal class Consumer : IAsyncDisposable, IDisposable
{
    private const string TopicName = "orders";

    private ILogger _logger;
    private IConsumer<string, byte[]> _consumer;
    private IProducer<string, byte[]> _dlqProducer;
    private bool _isListening;
    private readonly string? _dbConnectionString;
    private static readonly ActivitySource MyActivitySource = new("Accounting.Consumer");
    private int _inFlightMessages = 0;
    private readonly object _lockObj = new();
    private readonly AsyncCircuitBreakerPolicy _circuitBreakerPolicy;
    private readonly Meter _meter;
    private string _currentCircuitState = "closed";

    // Configuration properties
    public int MaxRetryAttempts { get; }
    public int InitialRetryDelayMs { get; }
    public int MaxRetryDelayMs { get; }
    public string DlqTopicName { get; }
    public int ShutdownTimeoutSeconds { get; }
        public async Task StopAsync(CancellationToken cancellationToken)
        {
            _logger.LogInformation("Stopping Kafka consumer...");
            
            // Stop consuming new messages immediately
            _consumer.Pause(_consumer.Assignment);
            _isListening = false;

            // Wait for all in-flight messages to complete or until cancellation
            while (InFlightMessagesCount > 0 && !cancellationToken.IsCancellationRequested)
            {
                _logger.LogInformation("Waiting for {InFlightCount} in-flight messages to complete...", InFlightMessagesCount);
                await Task.Delay(100, cancellationToken);
            }

            if (cancellationToken.IsCancellationRequested)
            {
                _logger.LogWarning("Stop operation canceled, {InFlightCount} messages may not have completed processing", InFlightMessagesCount);
            }
            else
            {
                _logger.LogInformation("All in-flight messages completed");
            }
        }

        public async ValueTask DisposeAsync()
        {
            _logger.LogInformation("Disposing Kafka consumer...");
            
            // Commit any pending offsets
            try
            {
                _consumer.Commit();
                _logger.LogInformation("Offsets committed successfully");
            }
            catch (Exception ex)
            {
                _logger.LogWarning(ex, "Failed to commit offsets during disposal");
            }

            // Clean up consumer and producer resources
            _consumer.Dispose();
            _dlqProducer.Dispose();

            await DisposeDbContextAsync();
            
            GC.SuppressFinalize(this);
        }

        private async Task DisposeDbContextAsync()
        {
            // Clean up database context and pending transactions
            using var dbContext = new DBContext();
            if (dbContext.Database.CurrentTransaction != null)
            {
                try
                {
                    await dbContext.Database.CommitTransactionAsync();
                    _logger.LogInformation("Pending database transaction committed");
                }
                catch (Exception ex)
                {
                    _logger.LogError(ex, "Failed to commit database transaction, rolling back");
                    await dbContext.Database.RollbackTransactionAsync();
                }
            }
            await dbContext.DisposeAsync();
        }
    // Test hooks
    public Func<ConsumeResult<string, byte[]>, CancellationToken, Task>? ProcessMessage { get; set; }
    public Func<TimeSpan, CancellationToken, Task> DelayFunction { get; set; } = Task.Delay;

    public Consumer(ILogger<Consumer> logger, IConfiguration configuration)
    {
        _logger = logger;

        var servers = Environment.GetEnvironmentVariable("KAFKA_ADDR")
            ?? throw new InvalidOperationException("The KAFKA_ADDR environment variable is not set.");

        // Load configuration from environment variables
        MaxRetryAttempts = int.TryParse(Environment.GetEnvironmentVariable("KAFKA_CONSUMER_MAX_RETRY_ATTEMPTS"), out int maxRetries) ? maxRetries : 3;
        InitialRetryDelayMs = int.TryParse(Environment.GetEnvironmentVariable("KAFKA_CONSUMER_INITIAL_RETRY_DELAY_MS"), out int initialDelay) ? initialDelay : 1000;
        MaxRetryDelayMs = int.TryParse(Environment.GetEnvironmentVariable("KAFKA_CONSUMER_MAX_RETRY_DELAY_MS"), out int maxDelay) ? maxDelay : 10000;
        DlqTopicName = Environment.GetEnvironmentVariable("KAFKA_CONSUMER_DLQ_TOPIC_NAME") ?? "accounting-service-dlq";
        ShutdownTimeoutSeconds = Math.Min(120, configuration.GetValue<int?>("ShutdownTimeoutSeconds") ?? 30);

        _consumer = BuildConsumer(servers);
        _consumer.Subscribe(TopicName);

        _dlqProducer = BuildDlqProducer(servers);

        // Initialize circuit breaker policy
        _circuitBreakerPolicy = Policy
            .Handle<NpgsqlException>()
            .Or<DbUpdateException>()
            .Or<OperationCanceledException>()
            .Or<InvalidOperationException>()
            .CircuitBreakerAsync(
                exceptionsAllowedBeforeBreaking: 5,
                durationOfBreak: TimeSpan.FromSeconds(30),
                onBreak: (ex, breakDelay) =>
                {
                    _logger.LogWarning(
                        "Circuit breaker state changed: {previous_state} -> {circuit_state}, failure_count: {failure_count}, remaining_break_duration: {remaining_break_duration}s, timestamp: {timestamp}",
                        _currentCircuitState, "open", 5, breakDelay.TotalSeconds, DateTimeOffset.UtcNow);
                    _currentCircuitState = "open";
                },
                onReset: () =>
                {
                    _logger.LogInformation(
                        "Circuit breaker state changed: {previous_state} -> {circuit_state}, failure_count: {failure_count}, remaining_break_duration: {remaining_break_duration}s, timestamp: {timestamp}",
                        _currentCircuitState, "closed", 0, 0, DateTimeOffset.UtcNow);
                    _currentCircuitState = "closed";
                },
                onHalfOpen: () =>
                {
                    _logger.LogInformation(
                        "Circuit breaker state changed: {previous_state} -> {circuit_state}, failure_count: {failure_count}, remaining_break_duration: {remaining_break_duration}s, timestamp: {timestamp}",
                        _currentCircuitState, "half_open", 0, 0, DateTimeOffset.UtcNow);
                    _currentCircuitState = "half_open";
                });

        // Initialize metrics
        _meter = new Meter("Accounting.Service");
        _meter.CreateObservableGauge(
            "accounting_service_circuit_breaker_state",
            () => new List<Measurement<int>>
            {
                new(1, new KeyValuePair<string, object?>("state", _currentCircuitState)),
                new(0, new KeyValuePair<string, object?>("state", _currentCircuitState == "closed" ? "open" : "closed")),
                new(0, new KeyValuePair<string, object?>("state", _currentCircuitState == "half_open" ? "closed" : "half_open"))
            },
            description: "Current state of the accounting service circuit breaker (closed/open/half_open)");

       if (_logger.IsEnabled(LogLevel.Information))
       {
           _logger.LogInformation("Connecting to Kafka: {servers}", servers);
           _logger.LogInformation("Kafka consumer retry config: MaxRetries={MaxRetries}, InitialDelay={InitialDelay}ms, MaxDelay={MaxDelay}ms, DLQ={DlqTopic}", 
               MaxRetryAttempts, InitialRetryDelayMs, MaxRetryDelayMs, DlqTopicName);
           _logger.LogInformation("Shutdown timeout configured to {ShutdownTimeoutSeconds} seconds", ShutdownTimeoutSeconds);
       }

        _dbConnectionString = Environment.GetEnvironmentVariable("DB_CONNECTION_STRING");
    }

    // Test constructor
    public Consumer(IConfiguration configuration, IConsumer<string, byte[]> consumer, IProducer<string, byte[]> dlqProducer)
    {
        MaxRetryAttempts = configuration.GetValue<int?>("KAFKA_CONSUMER_MAX_RETRY_ATTEMPTS") ?? 3;
        InitialRetryDelayMs = configuration.GetValue<int?>("KAFKA_CONSUMER_INITIAL_RETRY_DELAY_MS") ?? 1000;
        MaxRetryDelayMs = configuration.GetValue<int?>("KAFKA_CONSUMER_MAX_RETRY_DELAY_MS") ?? 10000;
        DlqTopicName = configuration.GetValue<string?>("KAFKA_CONSUMER_DLQ_TOPIC_NAME") ?? "accounting-service-dlq";
        ShutdownTimeoutSeconds = Math.Min(120, configuration.GetValue<int?>("ShutdownTimeoutSeconds") ?? 30);
        
        _consumer = consumer;
        _dlqProducer = dlqProducer;
        _logger = new LoggerFactory().CreateLogger<Consumer>();
    }


    public async Task StartListening(CancellationToken cancellationToken = default)
    {
        _isListening = true;

        try
        {
            while (_isListening && !cancellationToken.IsCancellationRequested)
            {
                try
                {
                    using var activity = MyActivitySource.StartActivity("order-consumed",  ActivityKind.Internal);
                    var consumeResult = _consumer.Consume(cancellationToken);
                    
                    lock (_lockObj)
                    {
                        _inFlightMessages++;
                    }
                    
                    bool processedSuccessfully = await ProcessMessageWithRetry(consumeResult, cancellationToken);
                    
                    if (processedSuccessfully)
                    {
                        _consumer.Commit(consumeResult);
                    }
                    
                    lock (_lockObj)
                    {
                        _inFlightMessages--;
                    }
                }
                catch (ConsumeException e)
                {
                    if (_logger.IsEnabled(LogLevel.Error))
                    {
                        _logger.LogError(e, "Consume error: {reason}", e.Error.Reason);
                    }
                }
                catch (OperationCanceledException)
                {
                    _logger.LogInformation("Consume operation cancelled, initiating graceful shutdown");
                    break;
                }
            }
            
            _logger.LogInformation("Closing consumer");
            _consumer.Close();
            _dlqProducer.Dispose();
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Unexpected error in consumer loop");
        }
    }

    internal async Task<bool> ProcessMessageWithRetry(ConsumeResult<string, byte[]> message, CancellationToken cancellationToken)
    {
        Exception? lastException = null;
        int retryCount = 0;

        try
        {
            return await _circuitBreakerPolicy.ExecuteAsync(async () =>
            {
                while (true)
                {
                    try
                    {
                        if (ProcessMessage != null)
                        {
                            await ProcessMessage(message, cancellationToken);
                            return true;
                        }
                        else
                        {
                            bool success = ProcessMessageInternal(message);
                            if (success) return true;
                            throw new InvalidOperationException("Message processing failed permanently");
                        }
                    }
                    catch (Exception ex)
                    {
                        lastException = ex;
                        
                        if (!IsTransientException(ex))
                        {
                            _logger.LogError(ex, "Permanent exception processing message, routing to DLQ immediately");
                            await SendToDlq(message, ex, 0, cancellationToken);
                            return false;
                        }

                        if (retryCount >= MaxRetryAttempts)
                        {
                            _logger.LogError(ex, "Max retry attempts {MaxRetries} exceeded for message, routing to DLQ", MaxRetryAttempts);
                            await SendToDlq(message, ex, retryCount, cancellationToken);
                            return false;
                        }

                        int delayMs = Math.Min(InitialRetryDelayMs * (int)Math.Pow(2, retryCount), MaxRetryDelayMs);
                        _logger.LogWarning(ex, "Transient exception processing message, attempt {Attempt}/{MaxAttempts}, retrying in {DelayMs}ms", 
                            retryCount + 1, MaxRetryAttempts, delayMs);
                        
                        await DelayFunction(TimeSpan.FromMilliseconds(delayMs), cancellationToken);
                        retryCount++;
                    }
                }
            });
        }
        catch (BrokenCircuitException ex)
        {
            _logger.LogWarning(ex, "Circuit breaker is open, routing message directly to DLQ");
            await SendToDlq(message, ex, 0, cancellationToken);
            return false;
        }
    }

    internal async Task SendToDlq(ConsumeResult<string, byte[]> message, Exception failureException, int retryAttempts, CancellationToken cancellationToken)
    {
        try
        {
            var dlqMessage = new Message<string, byte[]>
            {
                Key = message.Message.Key,
                Value = message.Message.Value,
                Timestamp = message.Message.Timestamp,
                Headers = new Headers()
            };

            // Copy original headers
            foreach (var header in message.Message.Headers)
            {
                dlqMessage.Headers.Add(header);
            }

            // Add failure context headers
            dlqMessage.Headers.Add("x-failure-timestamp", System.Text.Encoding.UTF8.GetBytes(DateTimeOffset.UtcNow.ToString("O")));
            dlqMessage.Headers.Add("x-failure-reason", System.Text.Encoding.UTF8.GetBytes(failureException.Message ?? "Unknown error"));
            string stackTrace = failureException.StackTrace ?? string.Empty;
            if (stackTrace.Length > 1024)
            {
                stackTrace = stackTrace.Substring(0, 1021) + "...";
            }
            dlqMessage.Headers.Add("x-failure-stack-trace", System.Text.Encoding.UTF8.GetBytes(stackTrace));
            dlqMessage.Headers.Add("x-retry-attempts", System.Text.Encoding.UTF8.GetBytes(retryAttempts.ToString()));

            await _dlqProducer.ProduceAsync(DlqTopicName, dlqMessage, cancellationToken);
            _logger.LogInformation("Message routed to DLQ topic {DlqTopic} after {RetryAttempts} attempts", DlqTopicName, retryAttempts);
        }
        catch (Exception ex)
        {
            _logger.LogCritical(ex, "Failed to route message to DLQ, message will be reprocessed");
            throw;
        }
    }

    private static bool IsTransientException(Exception ex)
    {
        return ex is SocketException 
            or TimeoutException 
            or NpgsqlException { IsTransient: true }
            or DbUpdateException { InnerException: NpgsqlException { IsTransient: true } };
    }

    private readonly IOrderMessageValidator _validator = new OrderMessageValidator();

    private bool ProcessMessageInternal(ConsumeResult<string, byte[]> consumeResult)
    {
        try
        {
            var message = consumeResult.Message;
            
            // Validate message first
            var metadata = new KafkaMessageMetadata
            {
                Topic = consumeResult.Topic,
                Partition = consumeResult.Partition,
                Offset = consumeResult.Offset,
                Timestamp = consumeResult.Message.Timestamp.UtcDateTime,
                MessageKey = message.Key
            };
            
            var validationResult = _validator.Validate(message.Value, metadata);
            if (!validationResult.IsValid)
            {
                // Log structured error without PII
                _logger.LogError(
                    "Order message validation failed for offset {Offset} partition {Partition}: {ErrorCodes} - {Errors}",
                    metadata.Offset,
                    metadata.Partition,
                    string.Join(",", validationResult.ErrorCodes.Select(c => c.ToString())),
                    string.Join("; ", validationResult.ValidationErrors)
                );
                
                // Route to DLQ
                SendValidationFailureToDlq(message.Value, validationResult).GetAwaiter().GetResult();
                return false;
            }

            var order = validationResult.ValidPayload!;
            Log.OrderReceivedMessage(_logger, order);


            if (_dbConnectionString == null)
            {
                _logger.LogWarning("DB connection string not set, skipping processing");
                return true;
            }
            
            if (string.IsNullOrWhiteSpace(order.OrderId))
            {
                _logger.LogError("Order message is missing OrderId, skipping");
                return false;
            }

            using var dbContext = new DBContext();
            using var transaction = dbContext.Database.BeginTransaction();
            try
            {
                var orderEntity = new OrderEntity
                {
                    Id = order.OrderId
                };
                dbContext.Add(orderEntity);
                foreach (var item in order.Items)
                {
                    var orderItem = new OrderItemEntity
                    {
                        ItemCostCurrencyCode = item.Cost.CurrencyCode,
                        ItemCostUnits = item.Cost.Units,
                        ItemCostNanos = item.Cost.Nanos,
                        ProductId = item.Item.ProductId,
                        Quantity = item.Item.Quantity,
                        OrderId = order.OrderId
                    };

                    dbContext.Add(orderItem);
                }

                var shipping = new ShippingEntity
                {
                    ShippingTrackingId = order.ShippingTrackingId,
                    ShippingCostCurrencyCode = order.ShippingCost.CurrencyCode,
                    ShippingCostUnits = order.ShippingCost.Units,
                    ShippingCostNanos = order.ShippingCost.Nanos,
                    StreetAddress = order.ShippingAddress.StreetAddress,
                    City = order.ShippingAddress.City,
                    State = order.ShippingAddress.State,
                    Country = order.ShippingAddress.Country,
                    ZipCode = order.ShippingAddress.ZipCode,
                    OrderId = order.OrderId
                };
                dbContext.Add(shipping);
                await _circuitBreakerPolicy.ExecuteAsync(() => dbContext.SaveChangesAsync(cancellationToken), cancellationToken);
                await transaction.CommitAsync(cancellationToken);
                _logger.LogInformation("Successfully processed order {OrderId}", order.OrderId);
                return true;
            }
            catch (CircuitBreakerOpenException ex)
            {
                await transaction.RollbackAsync(cancellationToken);
                _logger.LogWarning(ex, "Circuit breaker is open, failing fast for order {OrderId}", order.OrderId);
                // Produce to DLQ
                await _invalidOrderDlqProducer.ProduceAsync(order, "Database circuit breaker open", cancellationToken);
                // Return 503 Service Unavailable (handled by caller)
                return false;
            }
            catch (DbUpdateException ex) when (ex.InnerException is Npgsql.PostgresException pgEx && pgEx.SqlState == "23505")
            {
                await transaction.RollbackAsync(cancellationToken);
                _logger.LogInformation("Duplicate order {OrderId} received, skipping processing", order.OrderId);
                return true;
            }
            catch (Exception ex)
            {
                await transaction.RollbackAsync(cancellationToken);
                _logger.LogError(ex, "Failed to process order {OrderId}", order.OrderId);
                return false;
            }
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Order parsing failed");
            return false;
        }
    }
    internal async Task SendValidationFailureToDlq(byte[] rawMessage, ValidationResult<Order> validationResult)
    {
        try
        {
            var dlqMessage = new Message<string, byte[]>
            {
                Key = validationResult.MessageMetadata.MessageKey,
                Value = rawMessage,
                Timestamp = new Timestamp(validationResult.MessageMetadata.Timestamp),
                Headers = new Headers()
            };

            // Add validation failure headers
            dlqMessage.Headers.Add("x-validation-error-code", System.Text.Encoding.UTF8.GetBytes(string.Join(",", validationResult.ErrorCodes.Select(c => ((int)c).ToString()))));
            dlqMessage.Headers.Add("x-validation-errors", System.Text.Encoding.UTF8.GetBytes(string.Join("; ", validationResult.ValidationErrors)));
            dlqMessage.Headers.Add("x-original-topic", System.Text.Encoding.UTF8.GetBytes(validationResult.MessageMetadata.Topic));
            dlqMessage.Headers.Add("x-original-partition", System.Text.Encoding.UTF8.GetBytes(validationResult.MessageMetadata.Partition.ToString()));
            dlqMessage.Headers.Add("x-original-offset", System.Text.Encoding.UTF8.GetBytes(validationResult.MessageMetadata.Offset.ToString()));
            dlqMessage.Headers.Add("x-original-timestamp", System.Text.Encoding.UTF8.GetBytes(validationResult.MessageMetadata.Timestamp.ToString("O")));

            await _dlqProducer.ProduceAsync(DlqTopicName, dlqMessage);
            _logger.LogInformation("Invalid order message routed to DLQ topic {DlqTopic}, offset {Offset}", DlqTopicName, validationResult.MessageMetadata.Offset);
        }
        catch (Exception ex)
        {
            _logger.LogCritical(ex, "Failed to route invalid order message to DLQ, message will be reprocessed");
            throw;
        }
    }

        private static IConsumer<string, byte[]> BuildConsumer(string servers)
        {
            var conf = new ConsumerConfig
            {
                GroupId = $"accounting",
                BootstrapServers = servers,
                // https://github.com/confluentinc/confluent-kafka-dotnet/tree/07de95ed647af80a0db39ce6a8891a630423b952#basic-consumer-example
                AutoOffsetReset = AutoOffsetReset.Earliest,
                EnableAutoCommit = false,
                EnableAutoOffsetStore = false
            };

            var (kafkaTlsEnabled, caCertPath, clientCertPath, clientKeyPath) = TlsConfiguration.GetKafkaTlsConfig();
            
            if (kafkaTlsEnabled)
            {
                conf.SecurityProtocol = SecurityProtocol.Ssl;
                conf.SslCaLocation = caCertPath;
                conf.SslEndpointIdentificationAlgorithm = SslEndpointIdentificationAlgorithm.Https;

                if (!string.IsNullOrWhiteSpace(clientCertPath) && !string.IsNullOrWhiteSpace(clientKeyPath))
                {
                    conf.SslCertificateLocation = clientCertPath;
                    conf.SslKeyLocation = clientKeyPath;
                }
            }

            return new ConsumerBuilder<string, byte[]>(conf)
                .Build();
        }

        private static IProducer<string, byte[]> BuildDlqProducer(string servers)
        {
            var conf = new ProducerConfig
            {
                BootstrapServers = servers,
                Acks = Acks.All,
                EnableIdempotence = true
            };

            var (kafkaTlsEnabled, caCertPath, clientCertPath, clientKeyPath) = TlsConfiguration.GetKafkaTlsConfig();
            
            if (kafkaTlsEnabled)
            {
                conf.SecurityProtocol = SecurityProtocol.Ssl;
                conf.SslCaLocation = caCertPath;
                conf.SslEndpointIdentificationAlgorithm = SslEndpointIdentificationAlgorithm.Https;

                if (!string.IsNullOrWhiteSpace(clientCertPath) && !string.IsNullOrWhiteSpace(clientKeyPath))
                {
                    conf.SslCertificateLocation = clientCertPath;
                    conf.SslKeyLocation = clientKeyPath;
                }
            }

            return new ProducerBuilder<string, byte[]>(conf)
                .Build();
        }

    public void Dispose()
    {
        _isListening = false;
        _consumer?.Dispose();
        _dlqProducer?.Dispose();
    }
}
