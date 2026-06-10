<?php

use PHPUnit\Framework\TestCase;

class LoggingStructuredACTest extends TestCase
{
    private const UNSTRUCTURED_STATEMENTS = ['echo', 'print', 'print_r', 'var_dump', 'var_export'];
    private const SERVICE_DIR = __DIR__ . '/../';

    /**
     * AC-1: All unstructured output statements in src/quote are replaced with PSR-3 logger calls
     */
    public function test_ac1_all_unstructured_statements_replaced(): void
    {
        $phpFiles = $this->getAllPhpFiles(self::SERVICE_DIR);
        $foundStatements = [];

        foreach ($phpFiles as $file) {
            $content = file_get_contents($file);
            $tokens = token_get_all($content);
            $inComment = false;

            foreach ($tokens as $token) {
                if (is_array($token)) {
                    [$tokenId, $tokenValue, $line] = $token;
                    
                    // Skip comments
                    if (in_array($tokenId, [T_COMMENT, T_DOC_COMMENT])) {
                        $inComment = true;
                        continue;
                    }
                    if ($tokenId === T_WHITESPACE) {
                        continue;
                    }
                    $inComment = false;

                    if ($tokenId === T_STRING && in_array(strtolower($tokenValue), self::UNSTRUCTURED_STATEMENTS)) {
                        $foundStatements[] = "{$file}:{$line} - {$tokenValue}";
                    }
                }
            }
        }

        $this->assertEmpty($foundStatements, "Unstructured output statements found:\n" . implode("\n", $foundStatements));
    }

    /**
     * AC-2: Startup logs are valid JSON with all required service_startup fields
     */
    public function test_ac2_startup_logs_valid_json_with_required_fields(): void
    {
        // Mock logger capture
        $logger = new class implements Psr\Log\LoggerInterface {
            public array $logs = [];
            public function log($level, \Stringable|string $message, array $context = []): void {
                $this->logs[] = ['level' => $level, 'message' => $message, 'context' => $context];
            }
            public function emergency(\Stringable|string $message, array $context = []): void {}
            public function alert(\Stringable|string $message, array $context = []): void {}
            public function critical(\Stringable|string $message, array $context = []): void {}
            public function error(\Stringable|string $message, array $context = []): void {}
            public function warning(\Stringable|string $message, array $context = []): void {}
            public function notice(\Stringable|string $message, array $context = []): void {}
            public function info(\Stringable|string $message, array $context = []): void { $this->log('info', $message, $context); }
            public function debug(\Stringable|string $message, array $context = []): void {}
        };

        // Simulate service startup (we'll mock the DI container to inject our logger)
        $container = require __DIR__ . '/../app/bootstrap.php';
        $container->set(Psr\Log\LoggerInterface::class, $logger);

        // Trigger startup
        require __DIR__ . '/../public/index.php';

        $startupLogs = array_filter($logger->logs, fn($log) => ($log['context']['event_type'] ?? '') === 'service_startup');
        $this->assertNotEmpty($startupLogs, "No service_startup logs found");

        foreach ($startupLogs as $log) {
            $this->assertEquals('info', $log['level']);
            $context = $log['context'];
            $this->assertEquals('service_startup', $context['event_type']);
            $this->assertEquals('quote', $context['service_name']);
            $this->assertArrayHasKey('startup_time', $context);
            $this->assertNotEmpty($context['startup_time']);
            $this->assertArrayHasKey('environment', $context);
            $this->assertNotEmpty($context['environment']);

            // Verify log entry is valid JSON when formatted
            $logJson = json_encode($log);
            $this->assertJson($logJson);
            $this->assertEquals(JSON_ERROR_NONE, json_last_error());
        }
    }

    /**
     * AC-3: Request access logs are valid JSON with all required request_access fields
     */
    public function test_ac3_request_logs_valid_json_with_required_fields(): void
    {
        // Mock logger capture
        $logger = new class implements Psr\Log\LoggerInterface {
            public array $logs = [];
            public function log($level, \Stringable|string $message, array $context = []): void {
                $this->logs[] = ['level' => $level, 'message' => $message, 'context' => $context];
            }
            public function emergency(\Stringable|string $message, array $context = []): void {}
            public function alert(\Stringable|string $message, array $context = []): void {}
            public function critical(\Stringable|string $message, array $context = []): void {}
            public function error(\Stringable|string $message, array $context = []): void {}
            public function warning(\Stringable|string $message, array $context = []): void {}
            public function notice(\Stringable|string $message, array $context = []): void {}
            public function info(\Stringable|string $message, array $context = []): void { $this->log('info', $message, $context); }
            public function debug(\Stringable|string $message, array $context = []): void {}
        };

        // Create mock request
        $request = Slim\Psr7\Factory\ServerRequestFactory::createFromGlobals()
            ->withMethod('GET')
            ->withUri(new Slim\Psr7\Uri('http', 'localhost', 80, '/get-quote'))
            ->withHeader('User-Agent', 'TestAgent/1.0')
            ->withHeader('X-Forwarded-For', '192.168.1.100');

        // Simulate request processing
        $app = require __DIR__ . '/../app/bootstrap.php';
        $app->getContainer()->set(Psr\Log\LoggerInterface::class, $logger);

        $response = $app->handle($request);

        $requestLogs = array_filter($logger->logs, fn($log) => ($log['context']['event_type'] ?? '') === 'request_access');
        $this->assertNotEmpty($requestLogs, "No request_access logs found for test request");

        foreach ($requestLogs as $log) {
            $this->assertEquals('info', $log['level']);
            $context = $log['context'];
            $this->assertEquals('request_access', $context['event_type']);
            $this->assertArrayHasKey('remote_address', $context);
            $this->assertEquals('192.168.1.100', $context['remote_address']);
            $this->assertArrayHasKey('status_code', $context);
            $this->assertEquals($response->getStatusCode(), $context['status_code']);
            $this->assertArrayHasKey('request_method', $context);
            $this->assertEquals('GET', $context['request_method']);
            $this->assertArrayHasKey('request_path', $context);
            $this->assertEquals('/get-quote', $context['request_path']);
            $this->assertArrayHasKey('execution_time_ms', $context);
            $this->assertIsFloat($context['execution_time_ms']);
            $this->assertGreaterThan(0, $context['execution_time_ms']);
            $this->assertArrayHasKey('user_agent', $context);
            $this->assertEquals('TestAgent/1.0', $context['user_agent']);

            // Verify log entry is valid JSON
            $logJson = json_encode($log);
            $this->assertJson($logJson);
            $this->assertEquals(JSON_ERROR_NONE, json_last_error());
        }
    }

    /**
     * AC-4: No information loss from original unstructured logs
     */
    public function test_ac4_no_log_information_loss(): void
    {
        // Capture original log content (from existing unstructured statements)
        $originalLogPatterns = [
            '/Service starting/',
            '/Listening on/',
            '/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/', // IP address
            '/GET|POST|PATCH|DELETE/', // HTTP method
            '/\/[a-z0-9\/_]+/', // URI path
            '/200|400|404|500/', // Status codes
            '/User-Agent:/i'
        ];

        // Capture structured logs
        $logger = new class implements Psr\Log\LoggerInterface {
            public array $messages = [];
            public function log($level, \Stringable|string $message, array $context = []): void {
                $this->messages[] = $message . ' ' . json_encode($context);
            }
            public function emergency(\Stringable|string $message, array $context = []): void {}
            public function alert(\Stringable|string $message, array $context = []): void {}
            public function critical(\Stringable|string $message, array $context = []): void {}
            public function error(\Stringable|string $message, array $context = []): void {}
            public function warning(\Stringable|string $message, array $context = []): void {}
            public function notice(\Stringable|string $message, array $context = []): void {}
            public function info(\Stringable|string $message, array $context = []): void { $this->log('info', $message, $context); }
            public function debug(\Stringable|string $message, array $context = []): void {}
        };

        // Simulate startup and request
        $app = require __DIR__ . '/../app/bootstrap.php';
        $app->getContainer()->set(Psr\Log\LoggerInterface::class, $logger);
        $request = Slim\Psr7\Factory\ServerRequestFactory::createFromGlobals()->withMethod('GET')->withUri(new Slim\Psr7\Uri('http', 'localhost', 80, '/'));
        $app->handle($request);

        $fullLogContent = implode("\n", $logger->messages);
        foreach ($originalLogPatterns as $pattern) {
            $this->assertMatchesRegularExpression($pattern, $fullLogContent, "Missing expected log pattern: {$pattern}");
        }
    }

    /**
     * AC-5: Zero unstructured output statements in active (non-commented) code
     */
    public function test_ac5_zero_unstructured_output_in_active_code(): void
    {
        // Same scan as AC1, but explicit AC5 verification
        $phpFiles = $this->getAllPhpFiles(self::SERVICE_DIR);
        $found = 0;

        foreach ($phpFiles as $file) {
            $content = file_get_contents($file);
            // Strip all comments first
            $contentWithoutComments = preg_replace('!/\*.*?\*/!s', '', $content);
            $contentWithoutComments = preg_replace('!//.*?$!m', '', $contentWithoutComments);
            $contentWithoutComments = preg_replace('!#.*?$!m', '', $contentWithoutComments);

            foreach (self::UNSTRUCTURED_STATEMENTS as $stmt) {
                if (stripos($contentWithoutComments, $stmt) !== false) {
                    // Verify it's not part of another string
                    if (preg_match('/\b' . preg_quote($stmt, '/') . '\b/i', $contentWithoutComments)) {
                        $found++;
                    }
                }
            }
        }

        $this->assertEquals(0, $found, "Found {$found} unstructured output statements in active code");
    }

    /**
     * AC-6: All log entries are valid JSON
     */
    public function test_ac6_all_logs_are_valid_json(): void
    {
        $logger = new class implements Psr\Log\LoggerInterface {
            public array $logs = [];
            public function log($level, \Stringable|string $message, array $context = []): void {
                $this->logs[] = ['level' => $level, 'message' => (string)$message, 'context' => $context];
            }
            public function emergency(\Stringable|string $message, array $context = []): void { $this->log('emergency', $message, $context); }
            public function alert(\Stringable|string $message, array $context = []): void { $this->log('alert', $message, $context); }
            public function critical(\Stringable|string $message, array $context = []): void { $this->log('critical', $message, $context); }
            public function error(\Stringable|string $message, array $context = []): void { $this->log('error', $message, $context); }
            public function warning(\Stringable|string $message, array $context = []): void { $this->log('warning', $message, $context); }
            public function notice(\Stringable|string $message, array $context = []): void { $this->log('notice', $message, $context); }
            public function info(\Stringable|string $message, array $context = []): void { $this->log('info', $message, $context); }
            public function debug(\Stringable|string $message, array $context = []): void { $this->log('debug', $message, $context); }
        };

        // Simulate all common actions that generate logs
        $app = require __DIR__ . '/../app/bootstrap.php';
        $app->getContainer()->set(Psr\Log\LoggerInterface::class, $logger);
        
        // Test multiple request types
        $requests = [
            ['GET', '/'],
            ['GET', '/get-quote'],
            ['POST', '/get-quote', ['items' => []]],
            ['GET', '/health'],
        ];

        foreach ($requests as [$method, $path, $body = null]) {
            $req = Slim\Psr7\Factory\ServerRequestFactory::createFromGlobals()->withMethod($method)->withUri(new Slim\Psr7\Uri('http', 'localhost', 80, $path));
            if ($body) $req = $req->withParsedBody($body);
            $app->handle($req);
        }

        foreach ($logger->logs as $idx => $log) {
            $json = json_encode($log);
            $this->assertJson($json, "Log entry #{$idx} is not valid JSON");
            $this->assertEquals(JSON_ERROR_NONE, json_last_error(), "JSON parse error for log entry #{$idx}: " . json_last_error_msg());
        }
    }

    private function getAllPhpFiles(string $dir): array
    {
        $files = [];
        $iterator = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($dir));
        foreach ($iterator as $file) {
            if ($file->isFile() && $file->getExtension() === 'php' && !str_contains($file->getPathname(), '/vendor/')) {
                $files[] = $file->getPathname();
            }
        }
        return $files;
    }
}
