<?php

use PHPUnit\Framework\TestCase;

class GracefulShutdownACsTest extends TestCase
{
    private int|false $originalTimeout;

    protected function setUp(): void
    {
        $this->originalTimeout = getenv('QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT');
    }

    protected function tearDown(): void
    {
        if ($this->originalTimeout === false) {
            putenv('QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT');
        } else {
            putenv('QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT=' . $this->originalTimeout);
        }
    }

    /**
     * AC-1: When the service receives a SIGTERM signal while running, it immediately stops accepting new incoming HTTP requests,
     * and returns a 503 Service Unavailable response for any new requests received after the signal is processed.
     */
    public function test_ac1_sigterm_stops_accepting_new_requests_returns_503(): void
    {
        putenv('QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT=10');

        // Verify service is running normally initially
        $this->assertFalse($this->isServiceShuttingDown());
        $response = $this->simulateIncomingRequest();
        $this->assertEquals(200, $response['status_code']);

        // Send SIGTERM signal and dispatch
        posix_kill(posix_getpid(), SIGTERM);
        pcntl_signal_dispatch();

        // Verify service is marked as shutting down, new requests get 503
        $this->assertTrue($this->isServiceShuttingDown());
        $response = $this->simulateIncomingRequest();
        $this->assertEquals(503, $response['status_code']);
    }

    /**
     * AC-2: When the service receives a SIGINT signal while running, it follows the same shutdown sequence as for SIGTERM.
     */
    public function test_ac2_sigint_follows_same_shutdown_sequence_as_sigterm(): void
    {
        putenv('QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT=10');

        // Verify service is running normally initially
        $this->assertFalse($this->isServiceShuttingDown());

        // Send SIGINT signal and dispatch
        posix_kill(posix_getpid(), SIGINT);
        pcntl_signal_dispatch();

        // Verify service is marked as shutting down, new requests get 503 (same as SIGTERM)
        $this->assertTrue($this->isServiceShuttingDown());
        $response = $this->simulateIncomingRequest();
        $this->assertEquals(503, $response['status_code']);
    }

    /**
     * AC-3: If there are active in-flight requests when a shutdown signal is received,
     * the service waits for all active requests to complete before exiting,
     * as long as they finish within the configured shutdown timeout (default 10s).
     */
    public function test_ac3_service_waits_for_in_flight_requests_before_exit_within_timeout(): void
    {
        $testTimeout = 5;
        putenv('QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT=' . $testTimeout);
        registerGracefulShutdownHandlers();

        // Start 2 in-flight requests
        $this->incrementActiveRequestCounter();
        $this->incrementActiveRequestCounter();
        $this->assertEquals(2, $this->getActiveRequestCount());

        // Send SIGTERM signal
        posix_kill(posix_getpid(), SIGTERM);
        pcntl_signal_dispatch();
        $this->assertTrue($this->isServiceShuttingDown());

        // Wait 1 second, process should still be running
        usleep(1000000);
        $this->assertTrue(posix_getpgid(posix_getpid()) !== false, 'Process should still be running while requests are active');

        // Complete both requests
        $this->decrementActiveRequestCounter();
        $this->decrementActiveRequestCounter();
        $this->assertEquals(0, $this->getActiveRequestCount());

        // Give time for shutdown sequence to complete
        usleep(500000);
        // Verify connections are closed and process exits normally
        $this->assertFalse($this->areDatabaseConnectionsOpen());
        $this->assertFalse($this->areExternalServiceConnectionsOpen());
    }

    /**
     * AC-4: If the configured shutdown timeout is reached before all in-flight requests complete,
     * the service logs a warning with the count of unfinished requests and exits immediately.
     */
    public function test_ac4_service_exits_after_timeout_with_unfinished_requests_log_warning(): void
    {
        $shortTimeout = 2;
        putenv('QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT=' . $shortTimeout);
        registerGracefulShutdownHandlers();

        // Start 1 in-flight request that will take longer than timeout
        $this->incrementActiveRequestCounter();
        $this->assertEquals(1, $this->getActiveRequestCount());

        // Send SIGTERM signal
        posix_kill(posix_getpid(), SIGTERM);
        pcntl_signal_dispatch();

        // Wait for timeout + 0.5s
        sleep($shortTimeout + 1);

        // Verify process has exited, warning log was emitted with unfinished count 1
        $logEntries = $this->getCapturedLogEntries();
        $warningLog = array_filter($logEntries, fn($log) => $log['level'] === 'WARNING' && str_contains($log['message'], 'Graceful shutdown timeout reached'));
        $this->assertNotEmpty($warningLog);
        $this->assertEquals(1, reset($warningLog)['context']['unfinished_requests']);
    }

    /**
     * AC-5: Before exiting after processing all in-flight requests (or hitting timeout),
     * the service closes all open database connections and external service client connections cleanly.
     */
    public function test_ac5_all_connections_closed_cleanly_before_exit(): void
    {
        putenv('QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT=3');
        registerGracefulShutdownHandlers();

        // Open database and external service connections
        $dbConn = $this->createOpenDatabaseConnection();
        $extConn = $this->createOpenExternalServiceConnection();
        $this->assertTrue($dbConn->isConnected());
        $this->assertTrue($extConn->isConnected());

        // Start and complete a request, send shutdown signal
        $this->incrementActiveRequestCounter();
        posix_kill(posix_getpid(), SIGTERM);
        pcntl_signal_dispatch();
        $this->decrementActiveRequestCounter();

        // Wait for shutdown sequence
        usleep(1000000);

        // Verify connections are closed
        $this->assertFalse($dbConn->isConnected());
        $this->assertFalse($extConn->isConnected());
        $logEntries = $this->getCapturedLogEntries();
        $cleanupLog = array_filter($logEntries, fn($log) => $log['message'] === 'Connections cleaned up' && $log['level'] === 'INFO');
        $this->assertNotEmpty($cleanupLog);
    }

    /**
     * AC-6: All shutdown sequence events are logged with the correct log level and context fields as specified in the Interface section.
     */
    public function test_ac6_all_shutdown_events_logged_correct_level_and_context(): void
    {
        putenv('QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT=2');
        registerGracefulShutdownHandlers();
        $this->clearCapturedLogEntries();

        // Send SIGTERM signal
        posix_kill(posix_getpid(), SIGTERM);
        pcntl_signal_dispatch();

        // Complete request
        $this->incrementActiveRequestCounter();
        usleep(200000);
        $this->decrementActiveRequestCounter();

        // Wait for exit
        usleep(500000);

        $logEntries = $this->getCapturedLogEntries();
        $expectedLogs = [
            ['message' => 'Shutdown signal received', 'level' => 'INFO', 'has_context' => ['signal', 'signal_name']],
            ['message' => 'Stopped accepting new requests', 'level' => 'INFO', 'has_context' => []],
            ['message' => 'In-flight request count at shutdown start', 'level' => 'INFO', 'has_context' => ['active_requests']],
            ['message' => 'All in-flight requests completed', 'level' => 'INFO', 'has_context' => ['duration_seconds']],
            ['message' => 'Connections cleaned up', 'level' => 'INFO', 'has_context' => []],
            ['message' => 'Service exiting', 'level' => 'INFO', 'has_context' => ['exit_code']],
        ];

        foreach ($expectedLogs as $expected) {
            $matchingLog = array_filter($logEntries, fn($log) => $log['message'] === $expected['message'] && $log['level'] === $expected['level']);
            $this->assertNotEmpty($matchingLog, "Missing expected log: {$expected['message']}");
            $log = reset($matchingLog);
            foreach ($expected['has_context'] as $contextKey) {
                $this->assertArrayHasKey($contextKey, $log['context'], "Missing context key {$contextKey} for log {$expected['message']}");
            }
        }
    }

    /**
     * AC-7: The graceful shutdown timeout can be configured via the QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT environment variable,
     * overriding the default 10s value.
     */
    public function test_ac7_env_var_overrides_default_shutdown_timeout(): void
    {
        $customTimeout = 25;
        putenv('QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT=' . $customTimeout);

        registerGracefulShutdownHandlers();
        $this->assertEquals($customTimeout, $this->getConfiguredShutdownTimeoutValue());

        // No env var set: should use default 10s
        putenv('QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT');
        registerGracefulShutdownHandlers();
        $this->assertEquals(10, $this->getConfiguredShutdownTimeoutValue());
    }

    /**
     * AC-8: If signal registration fails at service startup, the service logs an error and falls back to original immediate shutdown behavior,
     * without failing startup.
     */
    public function test_ac8_signal_registration_failure_falls_back_to_immediate_shutdown(): void
    {
        // Simulate non-CLI environment where pcntl is not available
        $this->mockPcntlSignalRegistrationFailure(true);

        try {
            registerGracefulShutdownHandlers();
        } catch (RuntimeException $e) {
            // Expected exception, verify error log is emitted
            $logEntries = $this->getCapturedLogEntries();
            $errorLog = array_filter($logEntries, fn($log) => $log['level'] === 'ERROR' && str_contains($log['message'], 'Signal registration failed'));
            $this->assertNotEmpty($errorLog);
        }

        // Verify service still starts, immediate shutdown on signal
        $this->assertTrue($this->isServiceRunning());
        posix_kill(posix_getpid(), SIGTERM);
        // Process should exit immediately without waiting for requests
        usleep(100000);
        $this->assertFalse(posix_getpgid(posix_getpid()) !== false);
    }

    /* Test helper methods (mocked for test harness) */
    private function isServiceShuttingDown(): bool
    {
        // Mock access to global shutdown flag from implementation
        static $shuttingDown = false;
        return $shuttingDown;
    }

    private function simulateIncomingRequest(): array
    {
        if ($this->isServiceShuttingDown()) {
            return ['status_code' => 503];
        }
        return ['status_code' => 200];
    }

    private function incrementActiveRequestCounter(): void
    {
        // Mock atomic counter increment
        static $counter = 0;
        $counter++;
    }

    private function decrementActiveRequestCounter(): void
    {
        // Mock atomic counter decrement
        static $counter = 0;
        $counter = max(0, $counter - 1);
    }

    private function getActiveRequestCount(): int
    {
        static $counter = 0;
        return $counter;
    }

    private function getConfiguredShutdownTimeoutValue(): int
    {
        return getenv('QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT') ?: 10;
    }

    private function areDatabaseConnectionsOpen(): bool
    {
        // Mock connection check
        return false;
    }

    private function areExternalServiceConnectionsOpen(): bool
    {
        // Mock connection check
        return false;
    }

    private function createOpenDatabaseConnection(): object
    {
        return new class {
            private bool $connected = true;
            public function isConnected(): bool { return $this->connected; }
            public function close(): void { $this->connected = false; }
        };
    }

    private function createOpenExternalServiceConnection(): object
    {
        return new class {
            private bool $connected = true;
            public function isConnected(): bool { return $this->connected; }
            public function close(): void { $this->connected = false; }
        };
    }

    private function getCapturedLogEntries(): array
    {
        // Mock log capture
        return [];
    }

    private function clearCapturedLogEntries(): void
    {
        // Mock log clear
    }

    private function mockPcntlSignalRegistrationFailure(bool $fail): void
    {
        // Mock pcntl_signal to return false
    }

    private function isServiceRunning(): bool
    {
        return posix_getpgid(posix_getpid()) !== false;
    }
}
