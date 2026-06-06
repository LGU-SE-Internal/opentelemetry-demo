<?php

use PHPUnit\Framework\TestCase;
use GuzzleHttp\Client;

class GetQuoteValidationTest extends TestCase
{
    protected Client $client;

    protected function setUp(): void
    {
        $this->client = new Client([
            'base_uri' => 'http://localhost:8080',
            'http_errors' => false
        ]);
    }

    public function test_ac1_invalid_json_payload_returns_400()
    {
        // Invalid JSON: missing quotes on key
        $invalidJson = '{numberOfItems: 3}';
        $response = $this->client->post('/getquote', [
            'body' => $invalidJson,
            'headers' => ['Content-Type' => 'application/json']
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Invalid JSON payload', $responseBody['error']);
    }

    public function test_ac2_missing_numberofitems_field_returns_400()
    {
        $payload = json_encode(['otherField' => 'value']);
        $response = $this->client->post('/getquote', [
            'body' => $payload,
            'headers' => ['Content-Type' => 'application/json']
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('Missing required field: numberOfItems', $responseBody['error']);
    }

    public function test_ac3_numberofitems_non_integer_returns_400()
    {
        // Test string value
        $payload1 = json_encode(['numberOfItems' => '3']);
        $response1 = $this->client->post('/getquote', [
            'body' => $payload1,
            'headers' => ['Content-Type' => 'application/json']
        ]);
        $this->assertEquals(400, $response1->getStatusCode());
        $body1 = json_decode($response1->getBody(), true);
        $this->assertEquals('numberOfItems must be an integer', $body1['error']);

        // Test float value
        $payload2 = json_encode(['numberOfItems' => 3.5]);
        $response2 = $this->client->post('/getquote', [
            'body' => $payload2,
            'headers' => ['Content-Type' => 'application/json']
        ]);
        $this->assertEquals(400, $response2->getStatusCode());
        $body2 = json_decode($response2->getBody(), true);
        $this->assertEquals('numberOfItems must be an integer', $body2['error']);

        // Test boolean value
        $payload3 = json_encode(['numberOfItems' => true]);
        $response3 = $this->client->post('/getquote', [
            'body' => $payload3,
            'headers' => ['Content-Type' => 'application/json']
        ]);
        $this->assertEquals(400, $response3->getStatusCode());
        $body3 = json_decode($response3->getBody(), true);
        $this->assertEquals('numberOfItems must be an integer', $body3['error']);
    }

    public function test_ac4_numberofitems_zero_returns_400()
    {
        $payload = json_encode(['numberOfItems' => 0]);
        $response = $this->client->post('/getquote', [
            'body' => $payload,
            'headers' => ['Content-Type' => 'application/json']
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $responseBody = json_decode($response->getBody(), true);
        $this->assertEquals('numberOfItems must be greater than 0', $responseBody['error']);
    }

    public function test_ac5_numberofitems_negative_integer_returns_400()
    {
        // Test -1
        $payload1 = json_encode(['numberOfItems' => -1]);
        $response1 = $this->client->post('/getquote', [
            'body' => $payload1,
            'headers' => ['Content-Type' => 'application/json']
        ]);
        $this->assertEquals(400, $response1->getStatusCode());
        $body1 = json_decode($response1->getBody(), true);
        $this->assertEquals('numberOfItems must be greater than 0', $body1['error']);

        // Test -10
        $payload2 = json_encode(['numberOfItems' => -10]);
        $response2 = $this->client->post('/getquote', [
            'body' => $payload2,
            'headers' => ['Content-Type' => 'application/json']
        ]);
        $this->assertEquals(400, $response2->getStatusCode());
        $body2 = json_decode($response2->getBody(), true);
        $this->assertEquals('numberOfItems must be greater than 0', $body2['error']);
    }

    public function test_ac6_positive_integer_numberofitems_returns_200()
    {
        // Test 1 item
        $payload1 = json_encode(['numberOfItems' => 1]);
        $response1 = $this->client->post('/getquote', [
            'body' => $payload1,
            'headers' => ['Content-Type' => 'application/json']
        ]);
        $this->assertEquals(200, $response1->getStatusCode());
        $body1 = json_decode($response1->getBody(), true);
        $this->assertArrayHasKey('quote', $body1);
        $this->assertIsNumeric($body1['quote']);
        $this->assertGreaterThan(0, $body1['quote']);

        // Test 5 items
        $payload2 = json_encode(['numberOfItems' => 5]);
        $response2 = $this->client->post('/getquote', [
            'body' => $payload2,
            'headers' => ['Content-Type' => 'application/json']
        ]);
        $this->assertEquals(200, $response2->getStatusCode());
        $body2 = json_decode($response2->getBody(), true);
        $this->assertArrayHasKey('quote', $body2);
        $this->assertIsNumeric($body2['quote']);
        $this->assertGreaterThan(0, $body2['quote']);

        // Test 100 items
        $payload3 = json_encode(['numberOfItems' => 100]);
        $response3 = $this->client->post('/getquote', [
            'body' => $payload3,
            'headers' => ['Content-Type' => 'application/json']
        ]);
        $this->assertEquals(200, $response3->getStatusCode());
        $body3 = json_decode($response3->getBody(), true);
        $this->assertArrayHasKey('quote', $body3);
        $this->assertIsNumeric($body3['quote']);
        $this->assertGreaterThan(0, $body3['quote']);
    }
}
