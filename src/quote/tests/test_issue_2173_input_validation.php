<?php

use GuzzleHttp\Client;
use Grpc\ChannelCredentials;
use Opentelemetry\Demo\Proto\Quote\V1\GetQuoteRequest;
use Opentelemetry\Demo\Proto\Quote\V1\QuoteServiceClient;
use Google\Rpc\Code;
use PHPUnit\Framework\TestCase;

class Issue2173InputValidationTest extends TestCase
{
    private Client $httpClient;
    private QuoteServiceClient $grpcClient;

    protected function setUp(): void
    {
        // Setup HTTP client for /get-quote endpoint
        $this->httpClient = new Client([
            'base_uri' => 'http://localhost:8080',
            'http_errors' => false,
        ]);

        // Setup gRPC client for QuoteService
        $this->grpcClient = new QuoteServiceClient('localhost:8080', [
            'credentials' => ChannelCredentials::createInsecure(),
        ]);
    }

    // ==================== HTTP ENDPOINT TESTS ====================

    // AC-1: When numberOfItems is missing from request, return 400/INVALID_ARGUMENT error with message "Invalid input: numberOfItems is required"
    public function test_ac1_http_missing_number_of_items_returns_400(): void
    {
        $response = $this->httpClient->post('/get-quote', [
            'json' => [
                'weight' => 5.5
            ]
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid input: numberOfItems is required', $responseBody['error']);
        $this->assertEquals('INVALID_ARGUMENT', $responseBody['code']);
    }

    // AC-2: When numberOfItems is not an integer (e.g. string, float, boolean), return 400/INVALID_ARGUMENT error with message "Invalid input: numberOfItems must be an integer"
    public function test_ac2_http_non_integer_number_of_items_returns_400(): void
    {
        // Test string value
        $response = $this->httpClient->post('/get-quote', [
            'json' => [
                'numberOfItems' => 'invalid',
                'weight' => 5.5
            ]
        ]);
        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid input: numberOfItems must be an integer', $responseBody['error']);

        // Test float value
        $response = $this->httpClient->post('/get-quote', [
            'json' => [
                'numberOfItems' => 2.5,
                'weight' => 5.5
            ]
        ]);
        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid input: numberOfItems must be an integer', $responseBody['error']);

        // Test boolean value
        $response = $this->httpClient->post('/get-quote', [
            'json' => [
                'numberOfItems' => true,
                'weight' => 5.5
            ]
        ]);
        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid input: numberOfItems must be an integer', $responseBody['error']);
    }

    // AC-3: When numberOfItems is < 1, return 400/INVALID_ARGUMENT error with message "Invalid input: numberOfItems must be at least 1"
    public function test_ac3_http_negative_or_zero_number_of_items_returns_400(): void
    {
        // Test negative value
        $response = $this->httpClient->post('/get-quote', [
            'json' => [
                'numberOfItems' => -1,
                'weight' => 5.5
            ]
        ]);
        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid input: numberOfItems must be at least 1', $responseBody['error']);

        // Test zero value
        $response = $this->httpClient->post('/get-quote', [
            'json' => [
                'numberOfItems' => 0,
                'weight' => 5.5
            ]
        ]);
        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid input: numberOfItems must be at least 1', $responseBody['error']);
    }

    // AC-4: When weight is missing from request, return 400/INVALID_ARGUMENT error with message "Invalid input: weight is required"
    public function test_ac4_http_missing_weight_returns_400(): void
    {
        $response = $this->httpClient->post('/get-quote', [
            'json' => [
                'numberOfItems' => 3
            ]
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid input: weight is required', $responseBody['error']);
        $this->assertEquals('INVALID_ARGUMENT', $responseBody['code']);
    }

    // AC-5: When weight is not a numeric type (e.g. string, boolean), return 400/INVALID_ARGUMENT error with message "Invalid input: weight must be a number"
    public function test_ac5_http_non_numeric_weight_returns_400(): void
    {
        // Test string value
        $response = $this->httpClient->post('/get-quote', [
            'json' => [
                'numberOfItems' => 3,
                'weight' => 'heavy'
            ]
        ]);
        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid input: weight must be a number', $responseBody['error']);

        // Test boolean value
        $response = $this->httpClient->post('/get-quote', [
            'json' => [
                'numberOfItems' => 3,
                'weight' => false
            ]
        ]);
        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid input: weight must be a number', $responseBody['error']);
    }

    // AC-6: When weight is < 0, return 400/INVALID_ARGUMENT error with message "Invalid input: weight must be greater than or equal to 0"
    public function test_ac6_http_negative_weight_returns_400(): void
    {
        $response = $this->httpClient->post('/get-quote', [
            'json' => [
                'numberOfItems' => 3,
                'weight' => -2.3
            ]
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid input: weight must be greater than or equal to 0', $responseBody['error']);
    }

    // AC-7: When both parameters are valid (numberOfItems >=1 integer, weight >=0 numeric), the request proceeds to quote calculation with unchanged existing behavior
    public function test_ac7_http_valid_input_returns_success(): void
    {
        // Test integer weight
        $response = $this->httpClient->post('/get-quote', [
            'json' => [
                'numberOfItems' => 3,
                'weight' => 5
            ]
        ]);
        $this->assertEquals(200, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertNotNull($responseBody['costUsd']);
        $this->assertGreaterThan(0, $responseBody['costUsd']);

        // Test float weight
        $response = $this->httpClient->post('/get-quote', [
            'json' => [
                'numberOfItems' => 1,
                'weight' => 0.5
            ]
        ]);
        $this->assertEquals(200, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertNotNull($responseBody['costUsd']);

        // Test zero weight
        $response = $this->httpClient->post('/get-quote', [
            'json' => [
                'numberOfItems' => 5,
                'weight' => 0
            ]
        ]);
        $this->assertEquals(200, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertNotNull($responseBody['costUsd']);
    }

    // ==================== gRPC ENDPOINT TESTS ====================

    // AC-1: When numberOfItems is missing from request, return INVALID_ARGUMENT error with message "Invalid input: numberOfItems is required"
    public function test_ac1_grpc_missing_number_of_items_returns_invalid_argument(): void
    {
        $request = new GetQuoteRequest();
        $request->setWeight(5.5);

        $call = $this->grpcClient->GetQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Invalid input: numberOfItems is required', $status->details);
    }

    // AC-2: When numberOfItems is not an integer / wrong type, return INVALID_ARGUMENT error with message "Invalid input: numberOfItems must be an integer"
    public function test_ac2_grpc_invalid_type_number_of_items_returns_invalid_argument(): void
    {
        $request = new GetQuoteRequest();
        // Simulate invalid type (gRPC will enforce type but we test invalid value handling)
        $request->setNumberOfItems('invalid'); // @phpstan-ignore-line
        $request->setWeight(5.5);

        $call = $this->grpcClient->GetQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Invalid input: numberOfItems must be an integer', $status->details);
    }

    // AC-3: When numberOfItems is < 1, return INVALID_ARGUMENT error with message "Invalid input: numberOfItems must be at least 1"
    public function test_ac3_grpc_negative_or_zero_number_of_items_returns_invalid_argument(): void
    {
        // Test negative value
        $request = new GetQuoteRequest();
        $request->setNumberOfItems(-1);
        $request->setWeight(5.5);

        $call = $this->grpcClient->GetQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Invalid input: numberOfItems must be at least 1', $status->details);

        // Test zero value
        $request->setNumberOfItems(0);
        $call = $this->grpcClient->GetQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Invalid input: numberOfItems must be at least 1', $status->details);
    }

    // AC-4: When weight is missing from request, return INVALID_ARGUMENT error with message "Invalid input: weight is required"
    public function test_ac4_grpc_missing_weight_returns_invalid_argument(): void
    {
        $request = new GetQuoteRequest();
        $request->setNumberOfItems(3);

        $call = $this->grpcClient->GetQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Invalid input: weight is required', $status->details);
    }

    // AC-5: When weight is not a numeric type / wrong type, return INVALID_ARGUMENT error with message "Invalid input: weight must be a number"
    public function test_ac5_grpc_invalid_type_weight_returns_invalid_argument(): void
    {
        $request = new GetQuoteRequest();
        $request->setNumberOfItems(3);
        // Simulate invalid type
        $request->setWeight('heavy'); // @phpstan-ignore-line

        $call = $this->grpcClient->GetQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Invalid input: weight must be a number', $status->details);
    }

    // AC-6: When weight is < 0, return INVALID_ARGUMENT error with message "Invalid input: weight must be greater than or equal to 0"
    public function test_ac6_grpc_negative_weight_returns_invalid_argument(): void
    {
        $request = new GetQuoteRequest();
        $request->setNumberOfItems(3);
        $request->setWeight(-2.3);

        $call = $this->grpcClient->GetQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::INVALID_ARGUMENT, $status->code);
        $this->assertEquals('Invalid input: weight must be greater than or equal to 0', $status->details);
    }

    // AC-7: When both parameters are valid, the request proceeds to quote calculation with unchanged existing behavior
    public function test_ac7_grpc_valid_input_returns_success(): void
    {
        // Test integer weight
        $request = new GetQuoteRequest();
        $request->setNumberOfItems(3);
        $request->setWeight(5);

        $call = $this->grpcClient->GetQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::OK, $status->code);
        $this->assertNotNull($response->getCostUsd());
        $this->assertGreaterThan(0, $response->getCostUsd());

        // Test float weight
        $request->setNumberOfItems(1);
        $request->setWeight(0.5);

        $call = $this->grpcClient->GetQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::OK, $status->code);
        $this->assertNotNull($response->getCostUsd());

        // Test zero weight
        $request->setNumberOfItems(5);
        $request->setWeight(0);

        $call = $this->grpcClient->GetQuote($request);
        [$response, $status] = $call->wait();

        $this->assertEquals(Code::OK, $status->code);
        $this->assertNotNull($response->getCostUsd());
    }
}
