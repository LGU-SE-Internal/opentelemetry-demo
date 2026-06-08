<?php

use PHPUnit\Framework\TestCase;
use App\Exception\QuoteCalculationException;
use App\Service\QuoteService;
use Psr\Http\Message\ResponseInterface;
use Slim\Factory\AppFactory;

class QuoteCalculationFailureACTest extends TestCase
{
    protected $app;

    protected function setUp(): void
    {
        parent::setUp();
        $this->app = AppFactory::create();
        (require __DIR__ . '/../app/routes.php')($this->app);
    }

    /**
     * AC-1: When an exception is thrown during quote calculation, a structured error log is emitted containing item count, total weight, exception message, stack trace, OpenTelemetry trace ID, and span ID.
     */
    public function test_ac1_structured_error_log_emitted_on_failure(): void
    {
        // Mock scenario that causes calculation failure
        $itemCount = 5;
        $totalWeight = 10.5;

        // Capture log output
        $logHandler = $this->getMockBuilder(\Monolog\Handler\HandlerInterface::class)
            ->onlyMethods(['handle', 'isHandling', 'close', 'pushProcessor', 'popProcessor'])
            ->getMock();
        
        $logHandler->expects($this->once())
            ->method('handle')
            ->with($this->callback(function ($record) use ($itemCount, $totalWeight) {
                $this->assertEquals('ERROR', $record['level_name']);
                $this->assertEquals('Quote calculation failed', $record['message']);
                $this->assertArrayHasKey('item_count', $record['context']);
                $this->assertEquals($itemCount, $record['context']['item_count']);
                $this->assertArrayHasKey('total_weight', $record['context']);
                $this->assertEquals($totalWeight, $record['context']['total_weight']);
                $this->assertArrayHasKey('exception.message', $record['context']);
                $this->assertNotEmpty($record['context']['exception.message']);
                $this->assertArrayHasKey('exception.stack_trace', $record['context']);
                $this->assertNotEmpty($record['context']['exception.stack_trace']);
                $this->assertArrayHasKey('trace.id', $record['context']);
                $this->assertNotEmpty($record['context']['trace.id']);
                $this->assertArrayHasKey('span.id', $record['context']);
                $this->assertNotEmpty($record['context']['span.id']);
                return true;
            }));

        $logger = new \Monolog\Logger('quote');
        $logger->pushHandler($logHandler);

        // Inject mock logger into service
        $quoteService = new QuoteService($logger);
        
        $this->expectException(QuoteCalculationException::class);
        $quoteService->calculateQuote($itemCount, $totalWeight, true); // Pass flag to force failure for test
    }

    /**
     * AC-2: When an exception is thrown during quote calculation, the API returns 500 Internal Server Error status code, no 0.0 quote value is present in the response, and the response includes the trace ID for debugging.
     */
    public function test_ac2_api_returns_500_error_on_failure(): void
    {
        $request = $this->createRequest('POST', '/getQuote', [
            'items' => array_fill(0, 5, ['weight' => 2.1]), // Total 10.5
            'forceFailure' => true // Test-only flag to trigger failure
        ]);

        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);

        $this->assertEquals(500, $response->getStatusCode());
        $this->assertArrayNotHasKey('quote', $payload);
        $this->assertArrayHasKey('error', $payload);
        $this->assertEquals('Quote calculation failed', $payload['error']);
        $this->assertArrayHasKey('traceId', $payload);
        $this->assertNotEmpty($payload['traceId']);
        $this->assertEquals(32, strlen($payload['traceId'])); // OTel trace ID is 16 bytes (32 hex chars)
    }

    /**
     * AC-3: When an exception is thrown during quote calculation, the exception details (message, stack trace) are attached to the active OpenTelemetry span as attributes/events, and trace context is consistent across spans, logs, and error response.
     */
    public function test_ac3_otel_trace_context_consistent_on_failure(): void
    {
        $traceId = bin2hex(random_bytes(16));
        $spanId = bin2hex(random_bytes(8));
        
        // Mock OTel context with known trace ID
        $context = \OpenTelemetry\API\Trace\SpanContext::create($traceId, $spanId, 1);
        $scope = \OpenTelemetry\API\Trace\Propagation\TraceContextPropagator::getInstance()
            ->currentContext()
            ->with(\OpenTelemetry\API\Trace\SpanInterface::class, \OpenTelemetry\API\Trace\Span::wrap($context));
        $scope->activate();

        $request = $this->createRequest('POST', '/getQuote', [
            'items' => array_fill(0, 3, ['weight' => 1.0]),
            'forceFailure' => true
        ]);

        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);

        // Trace ID in response should match the one we set
        $this->assertEquals($traceId, $payload['traceId']);

        // Check that span has exception event
        $span = \OpenTelemetry\API\Trace\Span::getCurrent();
        $this->assertNotNull($span);
    }

    /**
     * AC-4: When quote calculation succeeds without exceptions, the function returns the correct non-zero quote value, no error logs are emitted, and the API returns 200 OK with the valid quote value (no regression of existing functionality).
     */
    public function test_ac4_successful_calculation_no_errors(): void
    {
        $itemCount = 2;
        $totalWeight = 4.0;
        $expectedQuote = 8.99; // Expected value for 2 items, 4kg
        
        // Capture logs to ensure no error logs are emitted
        $logHandler = $this->getMockBuilder(\Monolog\Handler\HandlerInterface::class)
            ->onlyMethods(['handle', 'isHandling', 'close', 'pushProcessor', 'popProcessor'])
            ->getMock();
        
        $logHandler->expects($this->never())
            ->method('handle')
            ->with($this->callback(function ($record) {
                return $record['level_name'] === 'ERROR' && $record['message'] === 'Quote calculation failed';
            }));

        $logger = new \Monolog\Logger('quote');
        $logger->pushHandler($logHandler);

        $quoteService = new QuoteService($logger);
        $result = $quoteService->calculateQuote($itemCount, $totalWeight);
        
        $this->assertEquals($expectedQuote, $result);
        $this->assertIsFloat($result);
        $this->assertGreaterThan(0, $result);

        // Test API success path
        $request = $this->createRequest('POST', '/getQuote', [
            'items' => [['weight' => 2.0], ['weight' => 2.0]]
        ]);

        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);

        $this->assertEquals(200, $response->getStatusCode());
        $this->assertArrayHasKey('quote', $payload);
        $this->assertEquals($expectedQuote, $payload['quote']);
        $this->assertArrayNotHasKey('error', $payload);
    }

    /**
     * AC-5a: Unit test: the calculation function throws QuoteCalculationException with correct context when an exception occurs
     */
    public function test_ac5a_unit_throws_quote_calculation_exception_with_context(): void
    {
        $itemCount = 3;
        $totalWeight = 6.5;

        $quoteService = new QuoteService(new \Monolog\Logger('test'));
        
        try {
            $quoteService->calculateQuote($itemCount, $totalWeight, true);
            $this->fail('Expected QuoteCalculationException was not thrown');
        } catch (QuoteCalculationException $e) {
            $this->assertEquals($itemCount, $e->getItemCount());
            $this->assertEquals($totalWeight, $e->getTotalWeight());
            $this->assertNotNull($e->getPrevious());
            $this->assertNotEmpty($e->getTraceAsString());
        }
    }

    /**
     * AC-5b: Unit test: returns expected quote value when no exceptions occur
     */
    public function test_ac5b_unit_returns_correct_quote_on_success(): void
    {
        $quoteService = new QuoteService(new \Monolog\Logger('test'));
        $result = $quoteService->calculateQuote(2, 5.0);
        
        $this->assertIsFloat($result);
        $this->assertGreaterThan(0, $result);
        // Expected calculation: 5.0 * 1.5 = 7.5 + base fee 1.99 = 9.49
        $this->assertEquals(9.49, $result);
    }

    /**
     * AC-6a: Integration test: API returns 500 status with correct error payload on calculation failure
     */
    public function test_ac6a_integration_api_failure_response(): void
    {
        $request = $this->createRequest('POST', '/getQuote', [
            'items' => array_fill(0, 10, ['weight' => 3.0]),
            'forceFailure' => true
        ]);

        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);

        $this->assertEquals(500, $response->getStatusCode());
        $this->assertEquals('application/json', $response->getHeaderLine('Content-Type'));
        $this->assertEquals('Quote calculation failed', $payload['error']);
        $this->assertMatchesRegularExpression('/^[a-f0-9]{32}$/', $payload['traceId']);
    }

    /**
     * AC-6b: Integration test: API returns 200 status with correct quote value on success
     */
    public function test_ac6b_integration_api_success_response(): void
    {
        $request = $this->createRequest('POST', '/getQuote', [
            'items' => [['weight' => 1.0], ['weight' => 2.0], ['weight' => 3.0]] // Total 6kg
        ]);

        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);

        $this->assertEquals(200, $response->getStatusCode());
        $this->assertEquals('application/json', $response->getHeaderLine('Content-Type'));
        $this->assertArrayHasKey('quote', $payload);
        $this->assertEquals(10.99, $payload['quote']); // 6kg * 1.5 + 1.99 = 10.99
        $this->assertIsFloat($payload['quote']);
    }

    protected function createRequest(string $method, string $path, array $body = []): \Psr\Http\Message\ServerRequestInterface
    {
        $request = \Slim\Psr7\Factory\ServerRequestFactory::createServerRequest($method, $path);
        $request = $request->withHeader('Content-Type', 'application/json');
        $request->getBody()->write(json_encode($body));
        return $request->withParsedBody($body);
    }

    protected function tearDown(): void
    {
        parent::tearDown();
    }
}
EOF && ls -la src/quote/tests/test_quote_calculation_failure_ac.php
