// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;
using System.Runtime.InteropServices;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System;

namespace Accounting;

public interface IGracefulShutdownService
{
    void RegisterSignalHandlers();
    CancellationToken ShutdownInitiatedToken { get; }
    CancellationToken ShutdownImmediateToken { get; }
    void RegisterShutdownOperation(Func<CancellationToken, Task> shutdownOperation);
}

public class GracefulShutdownService : IGracefulShutdownService
{
    private readonly ILogger<GracefulShutdownService> _logger;
    private readonly IConfiguration _configuration;
    private readonly CancellationTokenSource _shutdownInitiatedCts = new();
    private readonly CancellationTokenSource _shutdownImmediateCts = new();
    private readonly List<Func<CancellationToken, Task>> _shutdownOperations = new();
    private readonly object _lock = new();

    public CancellationToken ShutdownInitiatedToken => _shutdownInitiatedCts.Token;
    public CancellationToken ShutdownImmediateToken => _shutdownImmediateCts.Token;

    public GracefulShutdownService(ILogger<GracefulShutdownService> logger, IConfiguration configuration)
    {
        _logger = logger;
        _configuration = configuration;
    }

    public void RegisterSignalHandlers()
    {
        void HandleShutdownSignal()
        {
            _logger.LogInformation("Shutdown signal received, initiating graceful shutdown");
            _ = InitiateGracefulShutdownAsync();
        }

        // Handle SIGINT (Ctrl+C)
        Console.CancelKeyPress += (sender, e) =>
        {
            e.Cancel = true; // Prevent immediate termination
            HandleShutdownSignal();
        };

        // Handle SIGTERM on POSIX systems
        if (RuntimeInformation.IsOSPlatform(OSPlatform.Linux) || RuntimeInformation.IsOSPlatform(OSPlatform.OSX))
        {
            PosixSignalRegistration.Create(PosixSignal.SIGTERM, ctx =>
            {
                ctx.Cancel = true; // Prevent immediate termination
                HandleShutdownSignal();
            });
        }
    }

    public void RegisterShutdownOperation(Func<CancellationToken, Task> shutdownOperation)
    {
        lock (_lock)
        {
            _shutdownOperations.Add(shutdownOperation);
        }
    }

        private async Task InitiateGracefulShutdownAsync()
        {
            // First signal that shutdown has been initiated - stop accepting new work
            var startTime = DateTimeOffset.UtcNow;
            Log.GracefulShutdownStarted(_logger, startTime);
            _shutdownInitiatedCts.Cancel();

            // Get configured grace period
            var gracePeriodSeconds = _configuration.GetValue<int>("Shutdown:GracePeriodSeconds", 30);
            _logger.LogInformation("Graceful shutdown initiated, grace period: {GracePeriodSeconds} seconds", gracePeriodSeconds);

            // Start timer for immediate shutdown
            _ = Task.Delay(TimeSpan.FromSeconds(gracePeriodSeconds)).ContinueWith(_ =>
            {
                _logger.LogWarning("Grace period expired, forcing immediate shutdown");
                _shutdownImmediateCts.Cancel();
            });

            List<Func<CancellationToken, Task>> operations;
            lock (_lock)
            {
                operations = _shutdownOperations.ToList();
            }

            try
            {
                // Run all shutdown operations in parallel
                var operationTasks = operations.Select(op => op(_shutdownImmediateCts.Token));
                await Task.WhenAll(operationTasks);

                _logger.LogInformation("All shutdown operations completed successfully");
            }
            catch (OperationCanceledException)
            {
                _logger.LogWarning("Some shutdown operations were canceled due to grace period expiration");
            }
            catch (Exception ex)
            {
                _logger.LogError(ex, "Error occurred during shutdown operations");
            }
            finally
            {
                var completeTime = DateTimeOffset.UtcNow;
                var duration = completeTime - startTime;
                Log.GracefulShutdownCompleted(_logger, completeTime, duration);
                _logger.LogInformation("Service shutting down with exit code 0");
                Environment.ExitCode = 0;
                Environment.Exit(0);
            }
        }
}
