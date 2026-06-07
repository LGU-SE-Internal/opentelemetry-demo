<?php

use PHPUnit\Framework\TestCase;
use GuzzleHttp\Client;

class GetQuoteValidationTest extends TestCase
{
    protected Client $client;
    protected string $logFile = '/var/log/quote-service/validation_errors.log';

    protected function setUp(): void
    {
        $this->client = new Client([
            'base_uri' => 'http://localhost:8080',
            'http_errors' => false
        ]);
        // Clear validation logs before each test
        if (file_exists($this->logFile)) {
            file_put_contents($this->logFile, '');
        }
    }

    public function test_ac1_numberofitems_zero_returns_400()
    {
        $payload = json_encode(['numberOfItems' => 0]);
        $response = $this->client->post('/getQuote', [
            'body' => $payload,
            'headers' => ['Content-Type' => 'application/json']
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid request parameter', $responseBody['error']);
        $this->assertEquals('numberOfItems must be at least 1', $responseBody['message']);
        $this->assertArrayHasKey('requestId', $responseBody);
        $this->assertNotEmpty($responseBody['requestId']);
    }

    public function test_ac2_numberofitems_negative_integer_returns_400()
    {
        $payload = json_encode(['numberOfItems' => -10]);
        $response = $this->client->post('/getQuote', [
            'body' => $payload,
            'headers' => ['Content-Type' => 'application/json']
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid request parameter', $responseBody['error']);
        $this->assertEquals('numberOfItems must be a positive integer', $responseBody['message']);
        $this->assertArrayHasKey('requestId', $responseBody);
    }

    public function test_ac3_numberofitems_over_maximum_returns_400()
    {
        $payload = json_encode(['numberOfItems' => 1001]);
        $response = $this->client->post('/getQuote', [
            'body' => $payload,
            'headers' => ['Content-Type' => 'application/json']
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid request parameter', $responseBody['error']);
        $this->assertEquals('numberOfItems cannot exceed 1000', $responseBody['message']);
        $this->assertArrayHasKey('requestId', $responseBody);
    }

    public function test_ac4_numberofitems_string_type_returns_400()
    {
        $payload = json_encode(['numberOfItems' => "15"]);
        $response = $this->client->post('/getQuote', [
            'body' => $payload,
            'headers' => ['Content-Type' => 'application/json']
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid request parameter', $responseBody['error']);
        $this->assertEquals('numberOfItems must be an integer', $responseBody['message']);
        $this->assertArrayHasKey('requestId', $responseBody);
    }

    public function test_ac5_numberofitems_float_type_returns_400()
    {
        $payload = json_encode(['numberOfItems' => 7.5]);
        $response = $this->client->post('/getQuote', [
            'body' => $payload,
            'headers' => ['Content-Type' => 'application/json']
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid request parameter', $responseBody['error']);
        $this->assertEquals('numberOfItems must be an integer', $responseBody['message']);
        $this->assertArrayHasKey('requestId', $responseBody);
    }

    public function test_ac6_valid_numberofitems_returns_200_ok()
    {
        // Test minimum valid value
        $payload1 = json_encode(['numberOfItems' => 1]);
        $response1 = $this->client->post('/getQuote', [
            'body' => $payload1,
            'headers' => ['Content-Type' => 'application/json']
        ]);
        $this->assertEquals(200, $response1->getStatusCode());
        $body1 = json_decode($response1->getBody(), true);
        $this->assertArrayHasKey('quote', $body1);
        $this->assertIsNumeric($body1['quote']);
        $this->assertGreaterThan(0, $body1['quote']);

        // Test mid-range valid value
        $payload2 = json_encode(['numberOfItems' => 50]);
        $response2 = $this->client->post('/getQuote', [
            'body' => $payload2,
            'headers' => ['Content-Type' => 'application/json']
        ]);
        $this->assertEquals(200, $response2->getStatusCode());
        $body2 = json_decode($response2->getBody(), true);
        $this->assertArrayHasKey('quote', $body2);

        // Test maximum valid value
        $payload3 = json_encode(['numberOfItems' => 1000]);
        $response3 = $this->client->post('/getQuote', [
            'body' => $payload3,
            'headers' => ['Content-Type' => 'application/json']
        ]);
        $this->assertEquals(200, $response3->getStatusCode());
        $body3 = json_decode($response3->getBody(), true);
        $this->assertArrayHasKey('quote', $body3);
    }

    public function test_ac7_invalid_request_writes_structured_log_entry()
    {
        $invalidValue = -5;
        $payload = json_encode(['numberOfItems' => $invalidValue]);
        $response = $this->client->post('/getQuote', [
            'body' => $payload,
            'headers' => ['Content-Type' => 'application/json']
        ]);
        $responseBody = json_decode($response->getBody(), true);
        $requestId = $responseBody['requestId'];

        // Check log file exists and has content
        $this->assertFileExists($this->logFile);
        $logContent = file_get_contents($this->logFile);
        $this->assertNotEmpty($logContent);

        // Parse log entry (assuming JSON formatted log lines)
        $logEntries = array_filter(explode("\n", $logContent));
        $this->assertCount(1, $logEntries);
        $logEntry = json_decode($logEntries[0], true);
        
        $this->assertArrayHasKey('client_ip', $logEntry);
        $this->assertNotEmpty($logEntry['client_ip']);
        $this->assertEquals($invalidValue, $logEntry['invalid_value']);
        $this->assertEquals($requestId, $logEntry['request_id']);
        $this->assertEquals('negative/zero', $logEntry['error_type']);
        $this->assertArrayHasKey('timestamp', $logEntry);
        $this->assertNotEmpty($logEntry['timestamp']);
    }

    public function test_ac8_valid_request_creates_no_validation_log_entry()
    {
        $payload = json_encode(['numberOfItems' => 50]);
        $response = $this->client->post('/getQuote', [
            'body' => $payload,
            'headers' => ['Content-Type' => 'application/json']
        ]);
        $this->assertEquals(200, $response->getStatusCode());

        // Check log file is empty
        if (file_exists($this->logFile)) {
            $logContent = file_get_contents($this->logFile);
            $this->assertEmpty(trim($logContent));
        }
    }
}
