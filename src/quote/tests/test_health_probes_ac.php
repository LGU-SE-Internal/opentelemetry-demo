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

    /**
     * AC-1: GET /health/liveness returns 200 OK with correct JSON on healthy service
     */
    public function test_ac1_liveness_success_healthy_service()
    {
        $response = $this->client->get('/health/liveness');
        
        // Verify status code 200
        $this->assertEquals(200, $response->getStatusCode());
        // Verify Content-Type is application/json
        $this->assertEquals('application/json', $response->getHeaderLine('Content-Type'));
        // Verify response body structure
        $body = json_decode($response->getBody(), true);
        $this->assertIsArray($body);
        $this->assertEquals('ok', $body['status']);
        $this->assertArrayHasKey('checks', $body);
        $this->assertEquals('running', $body['checks']['process']);
        $this->assertEquals('healthy', $body['checks']['runtime']);
    }

    /**
     * AC-2: GET /health/liveness returns 503 with correct JSON on unhealthy service
     */
    public function test_ac2_liveness_failure_critical_runtime_error()
    {
        // Simulate scenario where runtime has critical failure (implementation will trigger via env var)
        $response = $this->client->get('/health/liveness', [
            'headers' => ['X-Simulate-Failure' => 'runtime']
        ]);
        
        // Verify status code 503
        $this->assertEquals(503, $response->getStatusCode());
        // Verify Content-Type is application/json
        $this->assertEquals('application/json', $response->getHeaderLine('Content-Type'));
        // Verify response body structure
        $body = json_decode($response->getBody(), true);
        $this->assertIsArray($body);
        $this->assertEquals('unhealthy', $body['status']);
        $this->assertArrayHasKey('error', $body);
        $this->assertNotEmpty($body['error']);
    }

    /**
     * AC-3: GET /health/readiness returns 200 OK with correct JSON when service is ready
     */
    public function test_ac3_readiness_success_initialized_service()
    {
        $response = $this->client->get('/health/readiness');
        
        // Verify status code 200
        $this->assertEquals(200, $response->getStatusCode());
        // Verify Content-Type is application/json
        $this->assertEquals('application/json', $response->getHeaderLine('Content-Type'));
        // Verify response body structure
        $body = json_decode($response->getBody(), true);
        $this->assertIsArray($body);
        $this->assertEquals('ready', $body['status']);
        $this->assertArrayHasKey('checks', $body);
        $this->assertEquals('available', $body['checks']['quote_calculation_service']);
        $this->assertEquals('loaded', $body['checks']['configuration']);
    }

    /**
     * AC-4: GET /health/readiness returns 503 with correct JSON when service not ready
     */
    public function test_ac4_readiness_failure_uninitialized_service()
    {
        // Simulate scenario where service is still initializing (implementation will trigger via env var)
        $response = $this->client->get('/health/readiness', [
            'headers' => ['X-Simulate-Failure' => 'quote-service']
        ]);
        
        // Verify status code 503
        $this->assertEquals(503, $response->getStatusCode());
        // Verify Content-Type is application/json
        $this->assertEquals('application/json', $response->getHeaderLine('Content-Type'));
        // Verify response body structure
        $body = json_decode($response->getBody(), true);
        $this->assertIsArray($body);
        $this->assertEquals('not_ready', $body['status']);
        $this->assertArrayHasKey('error', $body);
        $this->assertNotEmpty($body['error']);
    }

    /**
     * AC-5: Health endpoints generate correct OpenTelemetry traces with required attributes
     */
    public function test_ac5_health_endpoints_otel_trace_instrumentation()
    {
        // Test that liveness endpoint generates valid trace
        $response = $this->client->get('/health/liveness', [
            'headers' => ['traceparent' => '00-1234567890abcdef1234567890abcdef-1234567890abcdef-01']
        ]);
        
        // Verify traceparent is propagated and trace exists (implementation will expose this in test env)
        $traceId = $response->getHeaderLine('X-Trace-Id');
        $this->assertEquals(32, strlen($traceId));
        $this->assertEquals('1234567890abcdef1234567890abcdef', $traceId);
        
        // Verify span attributes exist (implementation will return them in test mode)
        $attributes = json_decode($response->getHeaderLine('X-Span-Attributes'), true);
        $this->assertEquals('GET', $attributes['http.method']);
        $this->assertEquals('/health/liveness', $attributes['http.route']);
        $this->assertEquals(200, $attributes['http.status_code']);
    }

    /**
     * AC-6: Health endpoints are counted in HTTP request metrics with correct labels
     */
    public function test_ac6_health_endpoints_http_metrics()
    {
        // Reset metrics before test
        $this->client->post('/test/reset-metrics');
        
        // Make 5 requests to readiness endpoint
        for ($i = 0; $i < 5; $i++) {
            $this->client->get('/health/readiness');
        }
        
        // Fetch metrics
        $response = $this->client->get('/test/metrics');
        $metrics = json_decode($response->getBody(), true);
        
        // Verify request count metric exists with correct labels
        $this->assertArrayHasKey('http_server_request_count', $metrics);
        $readinessMetric = null;
        foreach ($metrics['http_server_request_count'] as $metric) {
            if ($metric['labels']['http_route'] === '/health/readiness' 
                && $metric['labels']['http_method'] === 'GET'
                && $metric['labels']['http_status_code'] === '200') {
                $readinessMetric = $metric;
                break;
            }
        }
        $this->assertNotNull($readinessMetric, 'Readiness endpoint metric not found');
        $this->assertEquals(5, $readinessMetric['value']);
    }

    /**
     * AC-7: Test all success and failure edge cases for health endpoints
     */
    public function test_ac7_health_endpoints_edge_cases()
    {
        // Test invalid method returns 405
        $response = $this->client->post('/health/liveness');
        $this->assertEquals(405, $response->getStatusCode());
        
        // Test endpoints are public, no auth required
        $response = $this->client->get('/health/liveness', ['headers' => ['Authorization' => '']]);
        $this->assertNotEquals(401, $response->getStatusCode());
        $this->assertNotEquals(403, $response->getStatusCode());
        
        $response = $this->client->get('/health/readiness', ['headers' => ['Authorization' => '']]);
        $this->assertNotEquals(401, $response->getStatusCode());
        $this->assertNotEquals(403, $response->getStatusCode());
        
        // Test liveness fails when PHP extensions missing
        $response = $this->client->get('/health/liveness', [
            'headers' => ['X-Simulate-Failure' => 'missing-extension']
        ]);
        $this->assertEquals(503, $response->getStatusCode());
    }
}
