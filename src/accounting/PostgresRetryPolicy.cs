using Npgsql;
using Polly;
using Polly.Retry;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace AccountingService;

public class PostgresRetryPolicyOptions
{
    public int MaxAttempts { get; set; } = 3;
    public int InitialDelayMs { get; set; } = 1000;
    public int MaxDelayMs { get; set; } = 10000;
}

public class PostgresRetryPolicy
{
    private readonly ResiliencePipeline _pipeline;
    private readonly ILogger<PostgresRetryPolicy> _logger;
    private readonly PostgresRetryPolicyOptions _options;
    private static readonly HashSet<string> TransientErrorCodes = new()
    {
        "08006", // Connection Failure
        "08001", // SQL Client Unable to Establish Connection
        "57P01", // Admin Shutdown
        "57P02", // Crash Shutdown
        "53300", // Too Many Connections
        "40001", // Serialization Failure / Deadlock
        "40P01", // Deadlock Detected
        "57014", // Query Cancelled / Timeout
        "HY000"  // General Error / Timeout
    };

    public PostgresRetryPolicy(IOptions<PostgresRetryPolicyOptions> options, ILogger<PostgresRetryPolicy> logger)
    {
        _logger = logger;
        _options = options.Value;
        
        var retryOptions = new RetryStrategyOptions
        {
            ShouldHandle = new PredicateBuilder().Handle<PostgresException>(ex => TransientErrorCodes.Contains(ex.SqlState)),
            MaxRetryAttempts = _options.MaxAttempts,
            Delay = TimeSpan.FromMilliseconds(_options.InitialDelayMs),
            BackoffType = DelayBackoffType.Exponential,
            MaxDelay = TimeSpan.FromMilliseconds(_options.MaxDelayMs),
            OnRetry = args =>
            {
                if (args.Outcome.Exception is PostgresException pgEx)
                {
                    var delayMs = (int)args.Delay.TotalMilliseconds;
                    var operationName = args.Context.Properties.TryGetValue("OperationName", out var op) ? op.ToString() : "unknown";
                    
                    _logger.LogInformation(
                        "Postgres retry attempt {AttemptNumber} of {MaxAttempts} after {DelayMs}ms for operation {Operation}, error code {ErrorCode}: {ErrorMessage}",
                        args.AttemptNumber + 1,
                        _options.MaxAttempts,
                        delayMs,
                        operationName,
                        pgEx.SqlState,
                        pgEx.Message);
                }
                return default;
            }
        };

        _pipeline = new ResiliencePipelineBuilder()
            .AddRetry(retryOptions)
            .Build();
    }

    public async Task ExecuteAsync(string operationName, Func<CancellationToken, Task> operation, CancellationToken cancellationToken = default)
    {
        var context = new ResilienceContext();
        context.Properties.Set("OperationName", operationName);
        
        try
        {
            await _pipeline.ExecuteAsync(async ct => await operation(ct), context, cancellationToken);
        }
        catch (PostgresException ex) when (TransientErrorCodes.Contains(ex.SqlState))
        {
            _logger.LogError(
                "Postgres retry failed after {AttemptsMade} attempts for operation {Operation}, error code {ErrorCode}: {ErrorMessage}",
                _options.MaxAttempts,
                operationName,
                ex.SqlState,
                ex.Message);
            throw;
        }
    }

    public async Task<T> ExecuteAsync<T>(string operationName, Func<CancellationToken, Task<T>> operation, CancellationToken cancellationToken = default)
    {
        var context = new ResilienceContext();
        context.Properties.Set("OperationName", operationName);
        
        try
        {
            return await _pipeline.ExecuteAsync(async ct => await operation(ct), context, cancellationToken);
        }
        catch (PostgresException ex) when (TransientErrorCodes.Contains(ex.SqlState))
        {
            _logger.LogError(
                "Postgres retry failed after {AttemptsMade} attempts for operation {Operation}, error code {ErrorCode}: {ErrorMessage}",
                _options.MaxAttempts,
                operationName,
                ex.SqlState,
                ex.Message);
            throw;
        }
    }
}
