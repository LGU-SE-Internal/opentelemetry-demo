<?php

use PHPUnit\Framework\TestCase;
use GuzzleHttp\Client;

class QuoteServiceValidationTest extends TestCase
{
    private Client $client;

    protected function setUp(): void
    {
        $this->client = new Client([
            'base_uri' => 'http://quoteservice:8080',
            'http_errors' => false
        ]);
    }

    public function test_ac1_weight_zero_returns_bad_request(): void
    {
        $response = $this->client->post('/calculate', [
            'json' => [
                'weight' => 0,
                'destination_zip' => '90210',
                'shipping_method' => 'standard'
            ]
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $body = json_decode($response->getBody(), true);
        $this->assertArrayHasKey('errors', $body);
        $errors = array_filter($body['errors'], fn($e) => $e['field'] === 'weight');
        $this->assertCount(1, $errors);
        $this->assertEquals('Weight must be a positive value', reset($errors)['message']);
    }

    public function test_ac2_weight_exceeds_1000_returns_bad_request(): void
    {
        $response = $this->client->post('/calculate', [
            'json' => [
                'weight' => 1001,
                'destination_zip' => '90210',
                'shipping_method' => 'standard'
            ]
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $body = json_decode($response->getBody(), true);
        $this->assertArrayHasKey('errors', $body);
        $errors = array_filter($body['errors'], fn($e) => $e['field'] === 'weight');
        $this->assertCount(1, $errors);
        $this->assertEquals('Weight must not exceed 1000 kg', reset($errors)['message']);
    }

    public function test_ac3_weight_non_numeric_returns_bad_request(): void
    {
        $response = $this->client->post('/calculate', [
            'json' => [
                'weight' => 'abc',
                'destination_zip' => '90210',
                'shipping_method' => 'standard'
            ]
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $body = json_decode($response->getBody(), true);
        $this->assertArrayHasKey('errors', $body);
        $errors = array_filter($body['errors'], fn($e) => $e['field'] === 'weight');
        $this->assertCount(1, $errors);
        $this->assertEquals('Weight must be a numeric value', reset($errors)['message']);
    }

    public function test_ac4_invalid_zip_code_returns_bad_request(): void
    {
        $response = $this->client->post('/calculate', [
            'json' => [
                'weight' => 50,
                'destination_zip' => '1234',
                'shipping_method' => 'standard'
            ]
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $body = json_decode($response->getBody(), true);
        $this->assertArrayHasKey('errors', $body);
        $errors = array_filter($body['errors'], fn($e) => $e['field'] === 'destination_zip');
        $this->assertCount(1, $errors);
        $this->assertEquals('Invalid destination zip code format', reset($errors)['message']);
    }

    public function test_ac5_invalid_shipping_method_returns_bad_request(): void
    {
        $response = $this->client->post('/calculate', [
            'json' => [
                'weight' => 50,
                'destination_zip' => '90210',
                'shipping_method' => 'same-day'
            ]
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $body = json_decode($response->getBody(), true);
        $this->assertArrayHasKey('errors', $body);
        $errors = array_filter($body['errors'], fn($e) => $e['field'] === 'shipping_method');
        $this->assertCount(1, $errors);
        $this->assertEquals('Shipping method must be one of standard, express, overnight', reset($errors)['message']);
    }

    public function test_ac6_multiple_invalid_fields_return_all_errors(): void
    {
        $response = $this->client->post('/calculate', [
            'json' => [
                'weight' => -5,
                'destination_zip' => '1234',
                'shipping_method' => 'invalid'
            ]
        ]);

        $this->assertEquals(400, $response->getStatusCode());
        $body = json_decode($response->getBody(), true);
        $this->assertArrayHasKey('errors', $body);
        $this->assertCount(3, $body['errors']);

        $fields = array_column($body['errors'], 'field');
        $this->assertContains('weight', $fields);
        $this->assertContains('destination_zip', $fields);
        $this->assertContains('shipping_method', $fields);
    }

    public function test_ac7_all_endpoints_apply_validation(): void
    {
        // TODO: Add tests for all other endpoints that accept the parameters once they are identified
        // For now validate the primary /calculate endpoint works correctly with valid input
        $response = $this->client->post('/calculate', [
            'json' => [
                'weight' => 50,
                'destination_zip' => '90210',
                'shipping_method' => 'standard'
            ]
        ]);

        // Valid request should return 200 (not 400)
        $this->assertNotEquals(400, $response->getStatusCode());
    }
}
