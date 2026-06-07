<?php

use PHPUnit\Framework\TestCase;
use React\EventLoop\Loop;
use React\Http\Browser;
use Psr\Http\Message\ResponseInterface;

class GracefulShutdownTest extends TestCase
{
    private string $serviceUrl = 'http://localhost:8080';
    private int $defaultGracePeriod = 30;

    protected function setUp(): void
    {
        // Reset environment variable before each test
        putenv('QUOTE_SERVICE_SHUTDOWN_GRACE_PERIOD_SECONDS');
    }

    /**
     * AC-1: When SIGINT/SIGTERM received, log shutdown.initiated and stop accepting new connections
     */
    public function test_ac1_signal_triggers_shutdown_initiated_and_stops_connections(): void
    {
        // Start service in background
        $serviceProcess = proc_open(
            ['php', '-S', 'localhost:8080', '-t', 'public'],
            [
                0 => ['pipe', 'r'],
                1 => ['pipe', 'w'],
                2 => ['pipe', 'w'],
            ],
            $pipes,
            __DIR__ . '/../',
            []
        );
        $this->assertIsResource($serviceProcess);

        // Wait for service to be available
        sleep(2);

        // Send SIGTERM signal
        proc_terminate($serviceProcess, SIGTERM);

        // Check logs for shutdown.initiated event
        $logs = stream_get_contents($pipes[2]) . stream_get_contents($pipes[1]);
        $this->assertStringContainsString('shutdown.initiated', $logs);
        $this->assertStringContainsString((string)$this->defaultGracePeriod, $logs);

        // Verify new connections are rejected
        $browser = new Browser();
        $connectionFailed = false;
        try {
            $browser->get($this->serviceUrl . '/quote')->wait();
        } catch (\Exception $e) {
            $connectionFailed = true;
        }
        $this->assertTrue($connectionFailed, 'New connections should be rejected after shutdown signal');

        proc_close($serviceProcess);
    }

    /**
     * AC-2: In-flight requests complete within grace period return 200 OK
     */
    public function test_ac2_in_flight_requests_complete_successfully_within_grace_period(): void
    {
        // Set short grace period for test
        putenv('QUOTE_SERVICE_SHUTDOWN_GRACE_PERIOD_SECONDS=5');

        $serviceProcess = proc_open(
            ['php', '-S', 'localhost:8080', '-t', 'public'],
            [
                0 => ['pipe', 'r'],
                1 => ['pipe', 'w'],
                2 => ['pipe', 'w'],
            ],
            $pipes,
            __DIR__ . '/../',
            []
        );
        $this->assertIsResource($serviceProcess);

        sleep(2);

        // Start a long-running request that will take 2 seconds to complete
        $browser = new Browser();
        $requestPromise = $browser->get($this->serviceUrl . '/quote?delay=2');

        // Wait 500ms then send SIGTERM
        usleep(500000);
        proc_terminate($serviceProcess, SIGTERM);

        // Wait for request to complete
        /** @var ResponseInterface $response */
        $response = $requestPromise->wait();
        $this->assertEquals(200, $response->getStatusCode());
        $this->assertNotEmpty($response->getBody()->getContents());

        proc_close($serviceProcess);
    }

    /**
     * AC-3: All requests complete before grace period: log shutdown.completed, exit 0, close resources
     */
    public function test_ac3_all_requests_complete_before_grace_period_exits_cleanly(): void
    {
        putenv('QUOTE_SERVICE_SHUTDOWN_GRACE_PERIOD_SECONDS=5');

        $serviceProcess = proc_open(
            ['php', '-S', 'localhost:8080', '-t', 'public'],
            [
                0 => ['pipe', 'r'],
                1 => ['pipe', 'w'],
                2 => ['pipe', 'w'],
            ],
            $pipes,
            __DIR__ . '/../',
            []
        );
        $this->assertIsResource($serviceProcess);

        sleep(2);

        // Send a quick request
        $browser = new Browser();
        $browser->get($this->serviceUrl . '/quote')->wait();

        // Send SIGTERM
        proc_terminate($serviceProcess, SIGTERM);

        // Wait for process to exit
        $exitCode = proc_close($serviceProcess);
        $logs = stream_get_contents($pipes[1]) . stream_get_contents($pipes[2]);

        $this->assertEquals(0, $exitCode);
        $this->assertStringContainsString('shutdown.completed', $logs);
        $this->assertStringContainsString('shutdown.resources_closed', $logs);
    }

    /**
     * AC-4: Grace period expires: log shutdown.timeout, exit 1, close resources
     */
    public function test_ac4_grace_period_expires_exits_with_error(): void
    {
        putenv('QUOTE_SERVICE_SHUTDOWN_GRACE_PERIOD_SECONDS=2');

        $serviceProcess = proc_open(
            ['php', '-S', 'localhost:8080', '-t', 'public'],
            [
                0 => ['pipe', 'r'],
                1 => ['pipe', 'w'],
                2 => ['pipe', 'w'],
            ],
            $pipes,
            __DIR__ . '/../',
            []
        );
        $this->assertIsResource($serviceProcess);

        sleep(2);

        // Start a request that takes 5 seconds (longer than grace period)
        $browser = new Browser();
        $requestPromise = $browser->get($this->serviceUrl . '/quote?delay=5');

        // Send SIGTERM immediately
        proc_terminate($serviceProcess, SIGTERM);

        // Wait for process to exit (should take ~2s due to grace period)
        $exitCode = proc_close($serviceProcess);
        $logs = stream_get_contents($pipes[1]) . stream_get_contents($pipes[2]);

        $this->assertEquals(1, $exitCode);
        $this->assertStringContainsString('shutdown.timeout', $logs);
        $this->assertStringContainsString('1', $logs); // Count of dropped requests
        $this->assertStringContainsString('shutdown.resources_closed', $logs);

        // Verify request failed
        $requestFailed = false;
        try {
            $requestPromise->wait();
        } catch (\Exception $e) {
            $requestFailed = true;
        }
        $this->assertTrue($requestFailed);
    }

    /**
     * AC-5: Custom grace period environment variable is respected
     */
    public function test_ac5_custom_grace_period_environment_variable_is_used(): void
    {
        $customGracePeriod = 10;
        putenv("QUOTE_SERVICE_SHUTDOWN_GRACE_PERIOD_SECONDS={$customGracePeriod}");

        $serviceProcess = proc_open(
            ['php', '-S', 'localhost:8080', '-t', 'public'],
            [
                0 => ['pipe', 'r'],
                1 => ['pipe', 'w'],
                2 => ['pipe', 'w'],
            ],
            $pipes,
            __DIR__ . '/../',
            []
        );
        $this->assertIsResource($serviceProcess);

        sleep(2);

        proc_terminate($serviceProcess, SIGTERM);

        // Check logs for custom grace period value
        $startTime = time();
        proc_close($serviceProcess);
        $elapsedTime = time() - $startTime;

        $logs = stream_get_contents($pipes[1]) . stream_get_contents($pipes[2]);
        $this->assertStringContainsString('shutdown.initiated', $logs);
        $this->assertStringContainsString((string)$customGracePeriod, $logs);
        // Process should wait at least close to custom grace period (allow 1s margin)
        $this->assertGreaterThanOrEqual($customGracePeriod - 1, $elapsedTime);
    }

    /**
     * AC-6: All persistent connections are closed after shutdown
     */
    public function test_ac6_all_persistent_connections_closed_after_shutdown(): void
    {
        $serviceProcess = proc_open(
            ['php', '-S', 'localhost:8080', '-t', 'public'],
            [
                0 => ['pipe', 'r'],
                1 => ['pipe', 'w'],
                2 => ['pipe', 'w'],
            ],
            $pipes,
            __DIR__ . '/../',
            []
        );
        $this->assertIsResource($serviceProcess);

        sleep(2);

        // Send a request that opens a database connection
        $browser = new Browser();
        $browser->get($this->serviceUrl . '/quote')->wait();

        // Send SIGTERM
        proc_terminate($serviceProcess, SIGTERM);
        proc_close($serviceProcess);

        $logs = stream_get_contents($pipes[1]) . stream_get_contents($pipes[2]);
        $this->assertStringContainsString('shutdown.resources_closed', $logs);

        // Verify no orphaned connections exist (check via netstat or database connection count)
        $connectionCount = exec('netstat -an | grep -E ":3306|:5432" | grep ESTABLISHED | wc -l');
        $this->assertEquals(0, (int)$connectionCount, 'No orphaned database connections should remain');
    }

    /**
     * AC-7: New connections after shutdown signal are rejected immediately
     */
    public function test_ac7_new_connections_after_shutdown_are_rejected(): void
    {
        $serviceProcess = proc_open(
            ['php', '-S', 'localhost:8080', '-t', 'public'],
            [
                0 => ['pipe', 'r'],
                1 => ['pipe', 'w'],
                2 => ['pipe', 'w'],
            ],
            $pipes,
            __DIR__ . '/../',
            []
        );
        $this->assertIsResource($serviceProcess);

        sleep(2);

        // Send SIGTERM
        proc_terminate($serviceProcess, SIGTERM);

        // Try to establish multiple new connections, all should fail immediately
        $browser = new Browser();
        for ($i = 0; $i < 5; $i++) {
            $connectionFailed = false;
            $startTime = microtime(true);
            try {
                $browser->get($this->serviceUrl . '/quote')->wait();
            } catch (\Exception $e) {
                $connectionFailed = true;
            }
            $elapsedTime = microtime(true) - $startTime;

            $this->assertTrue($connectionFailed, "Connection attempt {$i} should fail");
            $this->assertLessThan(1, $elapsedTime, "Connection {$i} should be rejected immediately (within 1s)");
        }

        proc_close($serviceProcess);
    }
}
