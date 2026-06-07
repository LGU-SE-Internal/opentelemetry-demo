// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Confluent.Kafka;
using Microsoft.Extensions.Logging;
using Oteldemo;
using Microsoft.EntityFrameworkCore;
using System.Diagnostics;
using Npgsql;
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
    private bool _isListening;
    private readonly string? _dbConnectionString;
    private static readonly ActivitySource MyActivitySource = new("Accounting.Consumer");
    private int _inFlightMessages = 0;
    private readonly object _lockObj = new();
    private readonly TimeSpan _shutdownTimeout = TimeSpan.FromSeconds(30);
    private readonly List<ConsumeResult<string, byte[]>> _processedMessagesToCommit = new();

    public Consumer(ILogger<Consumer> logger)
    {
        _logger = logger;

        var servers = Environment.GetEnvironmentVariable("KAFKA_ADDR")
            ?? throw new InvalidOperationException("The KAFKA_ADDR environment variable is not set.");

        _consumer = BuildConsumer(servers);
        _consumer.Subscribe(TopicName);

       if (_logger.IsEnabled(LogLevel.Information))
       {
           _logger.LogInformation("Connecting to Kafka: {servers}", servers);
       }

        _dbConnectionString = Environment.GetEnvironmentVariable("DB_CONNECTION_STRING");
    }

    public void PauseConsumption()
    {
        _logger.LogInformation("Pausing Kafka consumption");
        var assignments = _consumer.Assignment;
        if (assignments.Any())
        {
            _consumer.Pause(assignments);
        }
        _isListening = false;
    }

    public async Task WaitForInFlightProcessingAsync(TimeSpan? timeout = null)
    {
        var waitTimeout = timeout ?? _shutdownTimeout;
        DateTimeOffset shutdownDeadline = DateTimeOffset.UtcNow.Add(waitTimeout);
        _logger.LogInformation("Waiting for up to {TimeoutSeconds}s for in-flight processing to complete", waitTimeout.TotalSeconds);
        
        while (DateTimeOffset.UtcNow < shutdownDeadline)
        {
            lock (_lockObj)
            {
                if (_inFlightMessages == 0)
                {
                    _logger.LogInformation("All in-flight processing completed successfully");
                    return;
                }
            }
            await Task.Delay(100);
        }

        _logger.LogCritical("In-flight processing timed out after {TimeoutSeconds}s with {Remaining} messages still processing", 
            waitTimeout.TotalSeconds, _inFlightMessages);
        throw new TimeoutException("In-flight processing timed out during shutdown");
    }

    public async Task CommitOffsetsAsync(int maxRetries = 3)
    {
        if (!_processedMessagesToCommit.Any())
        {
            _logger.LogInformation("No offsets to commit during shutdown");
            return;
        }

        var latestOffsets = _processedMessagesToCommit
            .GroupBy(m => m.TopicPartition)
            .Select(g => g.OrderByDescending(m => m.Offset).First())
            .ToList();

        for (int retry = 0; retry < maxRetries; retry++)
        {
            try
            {
                _logger.LogInformation("Committing offsets for {Count} processed messages", latestOffsets.Count);
                _consumer.Commit(latestOffsets);
                _processedMessagesToCommit.Clear();
                _logger.LogInformation("Offsets committed successfully");
                return;
            }
            catch (KafkaException ex)
            {
                _logger.LogError(ex, "Failed to commit offsets (attempt {Retry}/{MaxRetries})", retry + 1, maxRetries);
                if (retry == maxRetries - 1)
                {
                    _logger.LogCritical("All {MaxRetries} offset commit attempts failed", maxRetries);
                    throw;
                }
                await Task.Delay(1000 * (retry + 1));
            }
        }
    }

    public void StartListening(CancellationToken cancellationToken = default)
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
                    
                    bool processedSuccessfully = ProcessMessage(consumeResult);
                    
                    if (processedSuccessfully)
                    {
                        lock (_processedMessagesToCommit)
                        {
                            _processedMessagesToCommit.Add(consumeResult);
                        }
                        // Commit immediately for normal operation
                        _consumer.Commit(consumeResult);
                        lock (_processedMessagesToCommit)
                        {
                            _processedMessagesToCommit.Remove(consumeResult);
                        }
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
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "Unexpected error in consumer loop");
        }
    }

    private bool ProcessMessage(ConsumeResult<string, byte[]> consumeResult)
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

    public void Dispose()
    {
        _logger.LogInformation("Disposing Kafka consumer resources");
        _isListening = false;
        _consumer?.Close();
        _consumer?.Dispose();
        
        // Clear all database connection pools
        NpgsqlConnection.ClearAllPools();
        _logger.LogInformation("All PostgreSQL connections closed");
    }
}
