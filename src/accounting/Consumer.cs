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


internal class Consumer : IDisposable
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
    private readonly TimeSpan _shutdownTimeout = TimeSpan.FromSeconds(30);

    // Configuration properties
    public int MaxRetryAttempts { get; }
    public int InitialRetryDelayMs { get; }
    public int MaxRetryDelayMs { get; }
    public string DlqTopicName { get; }

    // Test hooks
    public Func<ConsumeResult<string, byte[]>, CancellationToken, Task>? ProcessMessage { get; set; }
    public Func<TimeSpan, CancellationToken, Task> DelayFunction { get; set; } = Task.Delay;

    public Consumer(ILogger<Consumer> logger)
    {
        _logger = logger;

        var servers = Environment.GetEnvironmentVariable("KAFKA_ADDR")
            ?? throw new InvalidOperationException("The KAFKA_ADDR environment variable is not set.");

        // Load configuration from environment variables
        MaxRetryAttempts = int.TryParse(Environment.GetEnvironmentVariable("KAFKA_CONSUMER_MAX_RETRY_ATTEMPTS"), out int maxRetries) ? maxRetries : 3;
        InitialRetryDelayMs = int.TryParse(Environment.GetEnvironmentVariable("KAFKA_CONSUMER_INITIAL_RETRY_DELAY_MS"), out int initialDelay) ? initialDelay : 1000;
        MaxRetryDelayMs = int.TryParse(Environment.GetEnvironmentVariable("KAFKA_CONSUMER_MAX_RETRY_DELAY_MS"), out int maxDelay) ? maxDelay : 10000;
        DlqTopicName = Environment.GetEnvironmentVariable("KAFKA_CONSUMER_DLQ_TOPIC_NAME") ?? "accounting-service-dlq";

        _consumer = BuildConsumer(servers);
        _consumer.Subscribe(TopicName);

        _dlqProducer = BuildDlqProducer(servers);

       if (_logger.IsEnabled(LogLevel.Information))
       {
           _logger.LogInformation("Connecting to Kafka: {servers}", servers);
           _logger.LogInformation("Kafka consumer retry config: MaxRetries={MaxRetries}, InitialDelay={InitialDelay}ms, MaxDelay={MaxDelay}ms, DLQ={DlqTopic}", 
               MaxRetryAttempts, InitialRetryDelayMs, MaxRetryDelayMs, DlqTopicName);
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
            
            // Wait for in-flight messages to complete before shutting down
            DateTimeOffset shutdownDeadline = DateTimeOffset.UtcNow.Add(_shutdownTimeout);
            while (DateTimeOffset.UtcNow < shutdownDeadline)
            {
                lock (_lockObj)
                {
                    if (_inFlightMessages == 0)
                    {
                        break;
                    }
                }
                await Task.Delay(100, cancellationToken);
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

    private bool ProcessMessageInternal(ConsumeResult<string, byte[]> consumeResult)
    {
        try
        {
            var message = consumeResult.Message;
            var order = OrderResult.Parser.ParseFrom(message.Value);
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
                dbContext.SaveChanges();
                transaction.Commit();
                _logger.LogInformation("Successfully processed order {OrderId}", order.OrderId);
                return true;
            }
            catch (DbUpdateException ex) when (ex.InnerException is Npgsql.PostgresException pgEx && pgEx.SqlState == "23505")
            {
                // Unique constraint violation, duplicate order id
                transaction.Rollback();
                _logger.LogInformation("Duplicate order {OrderId} received, skipping processing", order.OrderId);
                return true;
            }
            catch (Exception ex)
            {
                transaction.Rollback();
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
