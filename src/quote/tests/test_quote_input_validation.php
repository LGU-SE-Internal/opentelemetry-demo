<?php

use PHPUnit\Framework\TestCase;
use Psr\Http\Message\ResponseInterface;
use Slim\Factory\AppFactory;

class QuoteInputValidationTest extends TestCase
{
    protected $app;
    protected $logHandler;

    protected function setUp(): void
    {
        parent::setUp();
        $this->app = AppFactory::create();
        (require __DIR__ . '/../app/routes.php')($this->app);

        // Setup mock log handler to capture validation error logs
        $this->logHandler = $this->getMockBuilder(\Monolog\Handler\HandlerInterface::class)
            ->onlyMethods(['handle', 'isHandling', 'close', 'pushProcessor', 'popProcessor'])
            ->getMock();
        
        $logger = new \Monolog\Logger('quote');
        $logger->pushHandler($this->logHandler);

        // Inject logger into the app container
        $container = $this->app->getContainer();
        $container->set('logger', function () use ($logger) {
            return $logger;
        });
    }

    /**
     * AC-1: When a request is missing any required field, return 400 with missing fields listed and log failure
     */
    public function test_ac1_missing_required_fields(): void
    {
        // Test missing item_weight
        $request = $this->createRequest('POST', '/quote', [
            'item_count' => 5,
            'destination_zip' => '90210'
        ]);

        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);

        $this->assertEquals(400, $response->getStatusCode());
        $this->assertEquals('Bad Request', $payload['error']);
        $this->assertEquals('Validation failed', $payload['message']);
        $this->assertContainsEquals(['field' => 'item_weight', 'error' => 'Field is required'], $payload['details']);

        // Test missing all fields
        $request = $this->createRequest('POST', '/quote', []);
        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);

        $missingFields = array_column($payload['details'], 'field');
        $this->assertContains('item_weight', $missingFields);
        $this->assertContains('item_count', $missingFields);
        $this->assertContains('destination_zip', $missingFields);
    }

    /**
     * AC-2: Validate item_weight is numeric and between 0.01 and 1000 kg
     */
    public function test_ac2_invalid_item_weight(): void
    {
        // Test non-numeric weight
        $request = $this->createRequest('POST', '/quote', [
            'item_weight' => 'heavy',
            'item_count' => 5,
            'destination_zip' => '90210'
        ]);
        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);
        $this->assertEquals(400, $response->getStatusCode());
        $this->assertContainsEquals(['field' => 'item_weight', 'error' => 'Must be a numeric value'], $payload['details']);

        // Test weight less than 0.01
        $request = $this->createRequest('POST', '/quote', [
            'item_weight' => 0.005,
            'item_count' => 5,
            'destination_zip' => '90210'
        ]);
        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);
        $this->assertContainsEquals(['field' => 'item_weight', 'error' => 'Must be between 0.01 and 1000 kg'], $payload['details']);

        // Test weight greater than 1000
        $request = $this->createRequest('POST', '/quote', [
            'item_weight' => 1500,
            'item_count' => 5,
            'destination_zip' => '90210'
        ]);
        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);
        $this->assertContainsEquals(['field' => 'item_weight', 'error' => 'Must be between 0.01 and 1000 kg'], $payload['details']);
    }

    /**
     * AC-3: Validate item_count is integer and between 1 and 100
     */
    public function test_ac3_invalid_item_count(): void
    {
        // Test non-integer count
        $request = $this->createRequest('POST', '/quote', [
            'item_weight' => 10.5,
            'item_count' => 5.5,
            'destination_zip' => '90210'
        ]);
        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);
        $this->assertEquals(400, $response->getStatusCode());
        $this->assertContainsEquals(['field' => 'item_count', 'error' => 'Must be an integer value'], $payload['details']);

        // Test count less than 1
        $request = $this->createRequest('POST', '/quote', [
            'item_weight' => 10.5,
            'item_count' => 0,
            'destination_zip' => '90210'
        ]);
        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);
        $this->assertContainsEquals(['field' => 'item_count', 'error' => 'Must be between 1 and 100'], $payload['details']);

        // Test count greater than 100
        $request = $this->createRequest('POST', '/quote', [
            'item_weight' => 10.5,
            'item_count' => 150,
            'destination_zip' => '90210'
        ]);
        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);
        $this->assertContainsEquals(['field' => 'item_count', 'error' => 'Must be between 1 and 100'], $payload['details']);
    }

    /**
     * AC-4: Validate destination_zip matches 5-digit US format
     */
    public function test_ac4_invalid_destination_zip(): void
    {
        // Test 4-digit zip
        $request = $this->createRequest('POST', '/quote', [
            'item_weight' => 10.5,
            'item_count' => 5,
            'destination_zip' => '9021'
        ]);
        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);
        $this->assertEquals(400, $response->getStatusCode());
        $this->assertContainsEquals(['field' => 'destination_zip', 'error' => 'Must be a 5-digit US zip code'], $payload['details']);

        // Test 6-digit zip
        $request = $this->createRequest('POST', '/quote', [
            'item_weight' => 10.5,
            'item_count' => 5,
            'destination_zip' => '902101'
        ]);
        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);
        $this->assertContainsEquals(['field' => 'destination_zip', 'error' => 'Must be a 5-digit US zip code'], $payload['details']);

        // Test non-numeric zip
        $request = $this->createRequest('POST', '/quote', [
            'item_weight' => 10.5,
            'item_count' => 5,
            'destination_zip' => 'ABC12'
        ]);
        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);
        $this->assertContainsEquals(['field' => 'destination_zip', 'error' => 'Must be a 5-digit US zip code'], $payload['details']);
    }

    /**
     * AC-5: All 400 responses follow structured schema with no exposed PHP errors/stack traces
     */
    public function test_ac5_validation_error_response_schema(): void
    {
        $request = $this->createRequest('POST', '/quote', [
            'item_weight' => 'invalid',
            'item_count' => -5,
            'destination_zip' => 'invalid'
        ]);
        $response = $this->app->handle($request);
        $payload = json_decode($response->getBody(), true);

        // Validate response structure
        $this->assertEquals(400, $response->getStatusCode());
        $this->assertEquals('application/json', $response->getHeaderLine('Content-Type'));
        $this->assertArrayHasKey('error', $payload);
        $this->assertArrayHasKey('message', $payload);
        $this->assertArrayHasKey('details', $payload);
        $this->assertIsArray($payload['details']);
        
        // Ensure no stack traces or internal error messages are exposed
        $responseContent = $response->getBody()->__toString();
        $this->assertStringNotContainsString('Stack trace', $responseContent);
        $this->assertStringNotContainsString('PHP Notice', $responseContent);
        $this->assertStringNotContainsString('PHP Warning', $responseContent);
        $this->assertStringNotContainsString('Fatal error', $responseContent);
    }

    /**
     * AC-6: Validation failures are logged with required context
     */
    public function test_ac6_validation_failure_log_context(): void
    {
        $invalidFields = [
            ['field' => 'item_weight', 'value' => 'invalid', 'error' => 'Must be a numeric value'],
            ['field' => 'item_count', 'value' => -5, 'error' => 'Must be between 1 and 100']
        ];

        $this->logHandler->expects($this->once())
            ->method('handle')
            ->with($this->callback(function ($record) use ($invalidFields) {
                $this->assertEquals('ERROR', $record['level_name']);
                $this->assertEquals('Input validation failed', $record['message']);
                $this->assertArrayHasKey('request_id', $record['context']);
                $this->assertNotEmpty($record['context']['request_id']);
                $this->assertArrayHasKey('client_ip', $record['context']);
                $this->assertNotEmpty($record['context']['client_ip']);
                $this->assertArrayHasKey('invalid_fields', $record['context']);
                $this->assertEquals($invalidFields, $record['context']['invalid_fields']);
                return true;
            }));

        $request = $this->createRequest('POST', '/quote', [
            'item_weight' => 'invalid',
            'item_count' => -5,
            'destination_zip' => '90210'
        ]);
        
        // Add client IP to server params
        $request = $request->withAttribute('ip_address', '192.168.1.100');
        
        $response = $this->app->handle($request);
        $this->assertEquals(400, $response->getStatusCode());
    }

    protected function createRequest(string $method, string $path, array $body = []): \Psr\Http\Message\ServerRequestInterface
    {
        $serverParams = [
            'REMOTE_ADDR' => '192.168.1.100',
            'REQUEST_ID' => bin2hex(random_bytes(16))
        ];
        
        $request = \Slim\Psr7\Factory\ServerRequestFactory::createServerRequest($method, $path, $serverParams);
        $request = $request->withHeader('Content-Type', 'application/json');
        $request->getBody()->write(json_encode($body));
        return $request->withParsedBody($body);
    }

    protected function tearDown(): void
    {
        parent::tearDown();
    }
}
