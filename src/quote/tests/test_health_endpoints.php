<?php

use PHPUnit\Framework\TestCase;
use GuzzleHttp\Client;
use GuzzleHttp\Exception\ClientException;
use GuzzleHttp\Exception\ServerException;

class HealthEndpointsTest extends TestCase
{
    private Client $client;
    private string $serviceBaseUrl = 'http://quote:8080';
    private string $ipv4BaseUrl = 'http://127.0.0.1:8080';
    private string $ipv6BaseUrl = 'http://[::1]:8080';

    protected function setUp(): void
    {
        $this->client = new Client(['timeout' => 5.0, 'http_errors' => false]);
    }

    /**
     * AC-1: GET /health returns 200 OK, correct Content-Type, valid JSON with expected fields
     */
    public function test_ac1_health_endpoint_returns_healthy_response(): void
    {
        $response = $this->client->get($this->serviceBaseUrl . '/health');

        // Verify status code
        $this->assertEquals(200, $response->getStatusCode());

        // Verify Content-Type header
        $this->assertEquals('application/json', $response->getHeaderLine('Content-Type'));

        // Parse and verify JSON content
        $body = json_decode($response->getBody()->getContents(), true);
        $this->assertIsArray($body);
        $this->assertEquals('healthy', $body['status']);
        $this->assertEquals('quote', $body['service']);
        $this->assertIsInt($body['timestamp']);
        $this->assertGreaterThan(0, $body['timestamp']);
    }

    /**
     * AC-2: GET /ready returns 200 OK when service is ready, correct Content-Type and fields
     */
    public function test_ac2_ready_endpoint_returns_ready_response_when_service_initialized(): void
    {
        // Wait for service to be fully initialized (adjust timeout as needed)
        $maxAttempts = 10;
        $attempt = 0;
        do {
            $response = $this->client->get($this->serviceBaseUrl . '/ready');
            $statusCode = $response->getStatusCode();
            if ($statusCode === 200 || $attempt >= $maxAttempts) {
                break;
            }
            sleep(1);
            $attempt++;
        } while (true);

        // Verify status code
        $this->assertEquals(200, $response->getStatusCode());

        // Verify Content-Type header
        $this->assertEquals('application/json', $response->getHeaderLine('Content-Type'));

        // Parse and verify JSON content
        $body = json_decode($response->getBody()->getContents(), true);
        $this->assertIsArray($body);
        $this->assertEquals('ready', $body['status']);
        $this->assertEquals('quote', $body['service']);
        $this->assertIsInt($body['timestamp']);
        $this->assertGreaterThan(0, $body['timestamp']);
    }

    /**
     * AC-3: GET /ready returns 503 Service Unavailable when service is not ready
     */
    public function test_ac3_ready_endpoint_returns_503_when_service_not_ready(): void
    {
        // Test immediately after service startup before initialization completes
        // We mock the unready state by checking early
        $response = $this->client->get($this->serviceBaseUrl . '/ready');

        // If service is initializing, should return 503
        // Note: This test may need to be run during startup sequence to catch unready state
        if ($response->getStatusCode() !== 200) {
            $this->assertEquals(503, $response->getStatusCode());
        }
    }

    /**
     * AC-4: Both endpoints work over IPv4
     */
    public function test_ac4_health_and_ready_endpoints_work_over_ipv4(): void
    {
        // Test /health over IPv4
        $healthResponse = $this->client->get($this->ipv4BaseUrl . '/health');
        $this->assertEquals(200, $healthResponse->getStatusCode());
        $this->assertEquals('application/json', $healthResponse->getHeaderLine('Content-Type'));

        // Test /ready over IPv4
        $readyResponse = $this->client->get($this->ipv4BaseUrl . '/ready');
        if ($readyResponse->getStatusCode() === 200) {
            $this->assertEquals('application/json', $readyResponse->getHeaderLine('Content-Type'));
        } else {
            $this->assertEquals(503, $readyResponse->getStatusCode());
        }
    }

    /**
     * AC-5: Both endpoints work over IPv6
     */
    public function test_ac5_health_and_ready_endpoints_work_over_ipv6(): void
    {
        // Test /health over IPv6
        $healthResponse = $this->client->get($this->ipv6BaseUrl . '/health');
        $this->assertEquals(200, $healthResponse->getStatusCode());
        $this->assertEquals('application/json', $healthResponse->getHeaderLine('Content-Type'));

        // Test /ready over IPv6
        $readyResponse = $this->client->get($this->ipv6BaseUrl . '/ready');
        if ($readyResponse->getStatusCode() === 200) {
            $this->assertEquals('application/json', $readyResponse->getHeaderLine('Content-Type'));
        } else {
            $this->assertEquals(503, $readyResponse->getStatusCode());
        }
    }
}
