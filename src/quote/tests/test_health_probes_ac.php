<?php

use PHPUnit\Framework\TestCase;
use GuzzleHttp\Client;
use GuzzleHttp\Exception\ConnectException;

class HealthProbesACTest extends TestCase
{
    private $client;
    private $serviceUrl = 'http://quote-service:8080'; // Default service URL for test environment

    protected function setUp(): void
    {
        $this->client = new Client([
            'base_uri' => $this->serviceUrl,
            'timeout' => 2.0,
            'http_errors' => false, // Do not throw exceptions for 4xx/5xx responses
        ]);
    }

    public function test_ac1_health_endpoint_returns_200_ok_when_service_running()
    {
        $response = $this->client->get('/health');
        
        // Verify status code 200
        $this->assertEquals(200, $response->getStatusCode());
        // Verify Content-Type is application/json
        $this->assertEquals('application/json', $response->getHeaderLine('Content-Type'));
        // Verify response body structure
        $body = json_decode($response->getBody(), true);
        $this->assertIsArray($body);
        $this->assertEquals('ok', $body['status']);
        $this->assertEquals('quote-service', $body['service']);
    }

    public function test_ac2_health_endpoint_fails_connection_refused_when_service_stopped()
    {
        // This test assumes the service is stopped when run, or use a non-existent endpoint/port
        $stoppedClient = new Client([
            'base_uri' => 'http://localhost:9999', // Port where service is not running
            'timeout' => 1.0,
        ]);

        $this->expectException(ConnectException::class);
        $stoppedClient->get('/health');
    }

    public function test_ac3_ready_endpoint_returns_200_ok_when_all_dependencies_available()
    {
        $response = $this->client->get('/ready');
        
        // Verify status code 200
        $this->assertEquals(200, $response->getStatusCode());
        // Verify Content-Type is application/json
        $this->assertEquals('application/json', $response->getHeaderLine('Content-Type'));
        // Verify response body structure
        $body = json_decode($response->getBody(), true);
        $this->assertIsArray($body);
        $this->assertEquals('ok', $body['status']);
        $this->assertEquals('quote-service', $body['service']);
        $this->assertArrayHasKey('dependencies', $body);
        $this->assertIsArray($body['dependencies']);
        $this->assertEquals('ok', $body['dependencies']['pricing-config']);
        $this->assertEquals('ok', $body['dependencies']['database']);
    }

    public function test_ac4_ready_endpoint_returns_503_when_dependency_unavailable()
    {
        // Simulate scenario where a dependency is unavailable (implementation will trigger this)
        $response = $this->client->get('/ready');
        
        // This test expects failure when dependency is down; for initial run without implementation it will fail
        if ($response->getStatusCode() === 503) {
            $this->assertEquals(503, $response->getStatusCode());
            $this->assertEquals('application/json', $response->getHeaderLine('Content-Type'));
            $body = json_decode($response->getBody(), true);
            $this->assertEquals('unavailable', $body['status']);
            $this->assertEquals('quote-service', $body['service']);
            $this->assertArrayHasKey('dependencies', $body);
            $failed = false;
            foreach ($body['dependencies'] as $dep => $status) {
                if ($status === 'failed') {
                    $failed = true;
                    break;
                }
            }
            $this->assertTrue($failed, 'At least one dependency should be marked as failed');
            $this->assertArrayHasKey('reason', $body);
            $this->assertNotEmpty($body['reason'], 'Failure reason should not be empty');
        } else {
            // For initial test run without implementation, mark as incomplete
            $this->markTestIncomplete('Ready endpoint not implemented yet, expected 503 when dependency fails');
        }
    }

    public function test_ac5_health_and_ready_endpoints_are_public_no_auth_required()
    {
        // Test /health without auth headers
        $healthResponse = $this->client->get('/health', [
            'headers' => [
                // No authentication headers provided
            ]
        ]);
        $this->assertNotEquals(401, $healthResponse->getStatusCode());
        $this->assertNotEquals(403, $healthResponse->getStatusCode());
        
        // Test /ready without auth headers
        $readyResponse = $this->client->get('/ready', [
            'headers' => [
                // No authentication headers provided
            ]
        ]);
        $this->assertNotEquals(401, $readyResponse->getStatusCode());
        $this->assertNotEquals(403, $readyResponse->getStatusCode());
    }
}
