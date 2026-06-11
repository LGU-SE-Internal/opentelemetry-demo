using Xunit;
using Moq;
using System;
using System.Data;
using System.Threading.Tasks;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Configuration;
using Npgsql;
using System.Collections.Generic;
using System.Linq;
using System.Threading;

namespace Accounting.Tests
{
    public class DbOperationRetryACTests : IDisposable
    {
        private readonly Mock<ILogger<DbOperationExecutor>> _mockLogger;
        private readonly IConfiguration _configuration;
        private readonly DbOperationExecutor _executor;
        private readonly Dictionary<string, string> _envVars = new();

        public DbOperationRetryACTests()
        {
            _mockLogger = new Mock<ILogger<DbOperationExecutor>>();
            _configuration = new ConfigurationBuilder()
                .AddInMemoryCollection(_envVars)
                .Build();
            _executor = new DbOperationExecutor(_configuration, _mockLogger.Object);
        }

        public void Dispose()
        {
            _envVars.Clear();
        }

        #region AC-1 Tests
        [Fact]
        public async Task test_ac1_transient_error_retried_up_to_max_attempts_with_exponential_backoff()
        {
            // Arrange
            const int expectedRetries = 3; // default max attempts
            var transientException = new NpgsqlException("Connection failure", new PostgresException("Connection failed", "08006"));
            int callCount = 0;
            List<TimeSpan> delays = new();
            var startTime = DateTime.UtcNow;

            // Act
            var exception = await Assert.ThrowsAsync<NpgsqlException>(() =>
                _executor.ExecuteAsync("TestOperation", async (ct) =>
                {
                    callCount++;
                    throw transientException;
                }, CancellationToken.None));

            var totalTime = DateTime.UtcNow - startTime;

            // Assert
            Assert.Equal(expectedRetries + 1, callCount); // 1 initial + 3 retries = 4 total calls
            // Verify exponential backoff delays: 100ms, 200ms, 400ms (capped at 2000ms, so all below)
            Assert.True(totalTime >= TimeSpan.FromMilliseconds(100 + 200 + 400));
            Assert.Same(transientException, exception);
        }

        [Fact]
        public async Task test_ac1_exponential_backoff_capped_at_max_delay()
        {
            // Arrange
            _envVars["ACCOUNTING_DB_RETRY_MAX_ATTEMPTS"] = "5";
            _envVars["ACCOUNTING_DB_RETRY_INITIAL_DELAY_MS"] = "500";
            _envVars["ACCOUNTING_DB_RETRY_MAX_DELAY_MS"] = "1000";
            var executor = new DbOperationExecutor(new ConfigurationBuilder().AddInMemoryCollection(_envVars).Build(), _mockLogger.Object);
            var transientException = new NpgsqlException("Connection failure", new PostgresException("Connection failed", "08006"));
            int callCount = 0;
            var startTime = DateTime.UtcNow;

            // Act
            await Assert.ThrowsAsync<NpgsqlException>(() =>
                executor.ExecuteAsync("TestOperation", async (ct) =>
                {
                    callCount++;
                    throw transientException;
                }, CancellationToken.None));

            var totalTime = DateTime.UtcNow - startTime;

            // Assert
            // Expected delays: 500ms, 1000ms, 1000ms, 1000ms, 1000ms = total 4500ms
            Assert.Equal(6, callCount); // 1 initial + 5 retries
            Assert.True(totalTime >= TimeSpan.FromMilliseconds(4500));
        }
        #endregion

        #region AC-2 Tests
        [Fact]
        public async Task test_ac2_custom_retry_parameters_from_env_vars_used_when_set()
        {
            // Arrange
            const int customMaxAttempts = 5;
            _envVars["ACCOUNTING_DB_RETRY_MAX_ATTEMPTS"] = customMaxAttempts.ToString();
            _envVars["ACCOUNTING_DB_RETRY_INITIAL_DELAY_MS"] = "50";
            _envVars["ACCOUNTING_DB_RETRY_MAX_DELAY_MS"] = "500";
            var executor = new DbOperationExecutor(new ConfigurationBuilder().AddInMemoryCollection(_envVars).Build(), _mockLogger.Object);
            var transientException = new NpgsqlException("Connection failure", new PostgresException("Connection failed", "08006"));
            int callCount = 0;

            // Act
            await Assert.ThrowsAsync<NpgsqlException>(() =>
                executor.ExecuteAsync("TestOperation", async (ct) =>
                {
                    callCount++;
                    throw transientException;
                }, CancellationToken.None));

            // Assert
            Assert.Equal(customMaxAttempts + 1, callCount);
        }

        [Fact]
        public async Task test_ac2_invalid_env_vars_use_default_values()
        {
            // Arrange
            _envVars["ACCOUNTING_DB_RETRY_MAX_ATTEMPTS"] = "not-a-number";
            _envVars["ACCOUNTING_DB_RETRY_INITIAL_DELAY_MS"] = "-100";
            _envVars["ACCOUNTING_DB_RETRY_MAX_DELAY_MS"] = "invalid";
            var executor = new DbOperationExecutor(new ConfigurationBuilder().AddInMemoryCollection(_envVars).Build(), _mockLogger.Object);
            var transientException = new NpgsqlException("Connection failure", new PostgresException("Connection failed", "08006"));
            int callCount = 0;

            // Act
            await Assert.ThrowsAsync<NpgsqlException>(() =>
                executor.ExecuteAsync("TestOperation", async (ct) =>
                {
                    callCount++;
                    throw transientException;
                }, CancellationToken.None));

            // Assert - default max attempts is 3, so total calls = 4
            Assert.Equal(4, callCount);
        }
        #endregion

        #region AC-3 Tests
        [Fact]
        public async Task test_ac3_non_transient_error_not_retried()
        {
            // Arrange
            const string nonTransientErrorCode = "23505"; // Unique violation
            var nonTransientException = new NpgsqlException("Unique constraint violation", new PostgresException("Duplicate key", nonTransientErrorCode));
            int callCount = 0;

            // Act
            var exception = await Assert.ThrowsAsync<NpgsqlException>(() =>
                _executor.ExecuteAsync("TestOperation", async (ct) =>
                {
                    callCount++;
                    throw nonTransientException;
                }, CancellationToken.None));

            // Assert
            Assert.Equal(1, callCount); // No retries
            Assert.Same(nonTransientException, exception);
        }
        #endregion

        #region AC-4 Tests
        [Fact]
        public async Task test_ac4_each_retry_logs_warning_with_correct_context()
        {
            // Arrange
            const string operationName = "TestInsertOperation";
            var transientException = new NpgsqlException("Connection failure", new PostgresException("Connection failed", "08006"));
            int callCount = 0;

            // Act
            await Assert.ThrowsAsync<NpgsqlException>(() =>
                _executor.ExecuteAsync(operationName, async (ct) =>
                {
                    callCount++;
                    throw transientException;
                }, CancellationToken.None));

            // Assert
            // Verify 3 warning logs (one per retry)
            _mockLogger.Verify(
                x => x.Log(
                    LogLevel.Warning,
                    It.IsAny<EventId>(),
                    It.Is<It.IsAnyType>((v, t) => 
                        v.ToString().Contains(operationName) &&
                        v.ToString().Contains("attempt") &&
                        v.ToString().Contains("delay") &&
                        v.ToString().Contains("08006") &&
                        v.ToString().Contains("Connection failure")),
                    It.IsAny<Exception>(),
                    It.IsAny<Func<It.IsAnyType, Exception, string>>()),
                Times.Exactly(3));
        }
        #endregion

        #region AC-5 Tests
        [Fact]
        public async Task test_ac5_retry_runs_before_circuit_breaker_single_failure_counted()
        {
            // Arrange - use circuit breaker threshold of 2 failures to trip
            _envVars["DB_CIRCUIT_BREAKER_FAILURE_THRESHOLD"] = "2";
            var executor = new DbOperationExecutor(new ConfigurationBuilder().AddInMemoryCollection(_envVars).Build(), _mockLogger.Object);
            var transientException = new NpgsqlException("Connection failure", new PostgresException("Connection failed", "08006"));
            int firstOperationCallCount = 0;
            int secondOperationCallCount = 0;

            // Act 1: Run first operation that fails 4 times (1 initial + 3 retries)
            await Assert.ThrowsAsync<NpgsqlException>(() =>
                executor.ExecuteAsync("Op1", async (ct) =>
                {
                    firstOperationCallCount++;
                    throw transientException;
                }, CancellationToken.None));

            // Act 2: Run second operation immediately
            var secondOpException = await Record.ExceptionAsync(() =>
                executor.ExecuteAsync("Op2", async (ct) =>
                {
                    secondOperationCallCount++;
                    return Task.CompletedTask;
                }, CancellationToken.None));

            // Assert - circuit should NOT be tripped yet (only 1 failure counted, threshold is 2)
            Assert.Null(secondOpException);
            Assert.Equal(1, secondOperationCallCount);
        }
        #endregion

        #region AC-6 Tests
        [Fact]
        public void test_ac6_public_api_unchanged_backward_compatible()
        {
            // Verify existing public method signature exists
            var method = typeof(DbOperationExecutor).GetMethod("ExecuteAsync", new[] { typeof(string), typeof(Func<CancellationToken, Task>), typeof(CancellationToken) });
            Assert.NotNull(method);
            Assert.Equal(typeof(Task), method.ReturnType);

            // Verify constructor signature remains compatible
            var constructor = typeof(DbOperationExecutor).GetConstructor(new[] { typeof(IConfiguration), typeof(ILogger<DbOperationExecutor>) });
            Assert.NotNull(constructor);
        }
        #endregion

        #region AC-7 Tests
        [Fact]
        public async Task test_ac7_original_exception_propagated_after_all_retries_exhausted()
        {
            // Arrange
            var expectedException = new NpgsqlException("Test transient error", new PostgresException("Connection failed", "08006"));

            // Act
            var actualException = await Assert.ThrowsAsync<NpgsqlException>(() =>
                _executor.ExecuteAsync("TestOp", async (ct) =>
                {
                    throw expectedException;
                }, CancellationToken.None));

            // Assert
            Assert.Same(expectedException, actualException);
            Assert.Equal(expectedException.Message, actualException.Message);
            Assert.Equal("08006", (actualException.InnerException as PostgresException)?.SqlState);
        }
        #endregion
    }
}
