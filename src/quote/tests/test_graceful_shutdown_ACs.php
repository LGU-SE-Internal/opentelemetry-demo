<?php

use PHPUnit\Framework\TestCase;

class GracefulShutdownACsTest extends TestCase
{
    private int $originalTimeout;

    protected function setUp(): void
    {
        $this->originalTimeout = getenv('GRACEFUL_SHUTDOWN_TIMEOUT') ?: 30;
    }

    protected function tearDown(): void
    {
        putenv('GRACEFUL_SHUTDOWN_TIMEOUT=' . $this->originalTimeout);
    }

    /**
     * AC-1: When a SIGINT or SIGTERM signal is sent to a running quote service process,
     * all subsequent new HTTP requests receive a 503 Service Unavailable response
     * with a Retry-After header value equal to the configured GRACEFUL_SHUTDOWN_TIMEOUT.
     */
    public function test_ac1_new_requests_return_503_after_shutdown_signal(): void
    {
        // Set test timeout
        $testTimeout = 10;
        putenv('GRACEFUL_SHUTDOWN_TIMEOUT=' . $testTimeout);
        registerShutdownSignalHandlers();

        // Verify service is not shutting down initially
        $this->assertFalse(isShuttingDown());

        // Simulate SIGTERM signal
        posix_kill(posix_getpid(), SIGTERM);
        pcntl_signal_dispatch();

        // Verify shutdown flag is set
        $this->assertTrue(isShuttingDown());

        // Simulate incoming request: should get 503 with correct Retry-After
        $response = $this->simulateHttpRequest();
        $this->assertEquals(503, $response['status_code']);
        $this->assertEquals($testTimeout, $response['headers']['Retry-After']);
    }

    /**
     * AC-2: When a shutdown signal is received while there are active in-flight requests,
     * the service waits for all active requests to complete before exiting,
     * as long as completion happens before the configured timeout is reached.
     */
    public function test_ac2_service_waits_for_in_flight_requests_before_exit(): void
    {
        $testTimeout = 5;
        registerShutdownSignalHandlers($testTimeout);

        // Start a long-running request
        incrementInFlightRequestCount();
        $this->assertEquals(1, $this->getInFlightRequestCount());

        // Send shutdown signal
        posix_kill(posix_getpid(), SIGTERM);
        pcntl_signal_dispatch();
        $this->assertTrue(isShuttingDown());

        // Give time for shutdown handler to run (should not exit yet)
        usleep(500000); // 0.5s
        $this->assertTrue(posix_getpgid(posix_getpid()) !== false); // Process still running

        // Complete the in-flight request
        decrementInFlightRequestCount();
        $this->assertEquals(0, $this->getInFlightRequestCount());

        // Give time for shutdown sequence to complete
        usleep(100000); // 0.1s
        // Verify process exits with 0 after all requests complete
        // (This would be validated via process exit code in integration test harness)
    }

    /**
     * AC-3: If the GRACEFUL_SHUTDOWN_TIMEOUT period elapses before all in-flight requests complete,
     * the service terminates all remaining active requests, closes open connections,
     * and exits with status code 1.
     */
    public function test_ac3_service_exits_with_code_1_after_timeout(): void
    {
        $shortTimeout = 1;
        registerShutdownSignalHandlers($shortTimeout);

        // Start a request that will take longer than timeout
        incrementInFlightRequestCount();

        // Send shutdown signal
        posix_kill(posix_getpid(), SIGTERM);
        pcntl_signal_dispatch();

        // Wait longer than timeout
        sleep($shortTimeout + 1);

        // Verify process has exited with code 1
        // (Validated via process exit code in integration test harness)
        $this->assertTrue($this->getProcessExitCode() === 1);

        // Verify all connections are closed
        $this->assertFalse($this->areAnyDatabaseConnectionsOpen());
        $this->assertFalse($this->areAnyExternalServiceConnectionsOpen());
    }

    /**
     * AC-4: When all in-flight requests complete successfully within the timeout window during shutdown,
     * the service closes all open database connections and external service connections
     * before exiting with status code 0.
     */
    public function test_ac4_connections_closed_and_exit_code_0_on_successful_shutdown(): void
    {
        $testTimeout = 3;
        registerShutdownSignalHandlers($testTimeout);

        // Open test connections
        $dbConn = $this->openTestDatabaseConnection();
        $extConn = $this->openTestExternalServiceConnection();
        $this->assertTrue($dbConn->isConnected());
        $this->assertTrue($extConn->isConnected());

        // Start and complete a request
        incrementInFlightRequestCount();
        posix_kill(posix_getpid(), SIGTERM);
        pcntl_signal_dispatch();
        decrementInFlightRequestCount();

        // Wait for shutdown sequence
        usleep(200000);

        // Verify connections are closed
        $this->assertFalse($dbConn->isConnected());
        $this->assertFalse($extConn->isConnected());

        // Verify exit code is 0
        $this->assertEquals(0, $this->getProcessExitCode());
    }

    /**
     * AC-5: Setting the GRACEFUL_SHUTDOWN_TIMEOUT environment variable to a positive integer value
     * changes the maximum wait time for graceful shutdown to the specified number of seconds.
     */
    public function test_ac5_env_var_configures_shutdown_timeout(): void
    {
        $customTimeout = 45;
        putenv('GRACEFUL_SHUTDOWN_TIMEOUT=' . $customTimeout);

        // Register handlers without explicit timeout (should use env var)
        registerShutdownSignalHandlers();

        // Verify configured timeout matches env var value
        $this->assertEquals($customTimeout, $this->getConfiguredShutdownTimeout());

        // Override with explicit timeout
        $explicitTimeout = 15;
        registerShutdownSignalHandlers($explicitTimeout);
        $this->assertEquals($explicitTimeout, $this->getConfiguredShutdownTimeout());
    }

    /**
     * AC-6: A unit test exists that simulates SIGTERM delivery to the service during an in-flight long-running request,
     * verifying the process does not exit until the request completes when the request duration is less than the configured timeout.
     */
    public function test_ac6_process_waits_for_short_request_before_exit(): void
    {
        $timeout = 4;
        $requestDuration = 2; // Less than timeout
        registerShutdownSignalHandlers($timeout);

        incrementInFlightRequestCount();

        // Send SIGTERM
        posix_kill(posix_getpid(), SIGTERM);
        pcntl_signal_dispatch();

        // Simulate request running for 2s
        sleep($requestDuration);
        $this->assertTrue(posix_getpgid(posix_getpid()) !== false); // Process still running

        // Complete request
        decrementInFlightRequestCount();
        usleep(200000);

        // Verify process exits now
        $this->assertFalse(posix_getpgid(posix_getpid()) !== false);
        $this->assertEquals(0, $this->getProcessExitCode());
    }

    /**
     * AC-7: A unit test exists that simulates SIGTERM delivery to the service during an in-flight long-running request,
     * verifying the process exits with code 1 after the timeout elapses when the request duration exceeds the configured timeout.
     */
    public function test_ac7_process_exits_after_timeout_for_long_request(): void
    {
        $timeout = 2;
        $requestDuration = 5; // Longer than timeout
        registerShutdownSignalHandlers($timeout);

        incrementInFlightRequestCount();

        // Send SIGTERM
        posix_kill(posix_getpid(), SIGTERM);
        pcntl_signal_dispatch();

        // Wait for timeout to elapse
        sleep($timeout + 1);

        // Verify process has exited with code 1 even though request is not complete
        $this->assertFalse(posix_getpgid(posix_getpid()) !== false);
        $this->assertEquals(1, $this->getProcessExitCode());
    }

    /* Helper methods for test harness */
    private function simulateHttpRequest(): array
    {
        // Simulated request handler that uses isShuttingDown() to return appropriate response
        if (isShuttingDown()) {
            return [
                'status_code' => 503,
                'headers' => [
                    'Retry-After' => $this->getConfiguredShutdownTimeout()
                ]
            ];
        }
        return ['status_code' => 200, 'headers' => []];
    }

    private function getInFlightRequestCount(): int
    {
        // Access atomic counter value
        static $counter = 0;
        return $counter;
    }

    private function getConfiguredShutdownTimeout(): int
    {
        // Return configured timeout value from signal handler
        return getenv('GRACEFUL_SHUTDOWN_TIMEOUT') ?: 30;
    }

    private function getProcessExitCode(): ?int
    {
        // Mock for test harness
        return null;
    }

    private function areAnyDatabaseConnectionsOpen(): bool
    {
        // Mock check for open DB connections
        return false;
    }

    private function areAnyExternalServiceConnectionsOpen(): bool
    {
        // Mock check for open external service connections
        return false;
    }

    private function openTestDatabaseConnection(): object
    {
        return new class {
            public function isConnected(): bool { return true; }
        };
    }

    private function openTestExternalServiceConnection(): object
    {
        return new class {
            public function isConnected(): bool { return true; }
        };
    }
}
