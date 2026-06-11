<?php
use PHPUnit\Framework\TestCase;
use GuzzleHttp\Client;
use GuzzleHttp\Exception\ConnectException;

class QuoteHealthEndpointsACTest extends TestCase
{
    private $defaultHealthPort = 8081;
    private $grpcPort = 8080;
    private $client;

    protected function setUp(): void
    {
        $healthPort = getenv('QUOTE_HEALTH_PORT') ?: $this->defaultHealthPort;
        $this->client = new Client([
            'base_uri' => sprintf('http://quote-service:%d', $healthPort),
            'timeout' => 2.0,
            'http_errors' => false, // Do not throw exceptions for 4xx/5xx responses
        ]);
    }

    /**
     * AC-1: When service is running normally, GET /liveness returns 200 OK with body OK and text/plain content type
     */
    public function test_ac1_liveness_success_running_service()
    {
        $response = $this->client->get('/liveness');
        
        $this->assertEquals(200, $response->getStatusCode());
        $this->assertEquals('text/plain', $response->getHeaderLine('Content-Type'));
        $this->assertEquals('OK', trim($response->getBody()->getContents()));
    }

    /**
     * AC-2: When gRPC server is in active serving state, GET /readiness returns 200 OK with body OK and text/plain content type
     */
    public function test_ac2_readiness_success_grpc_serving()
    {
        // Ensure gRPC server is running (test env should have it initialized)
        $response = $this->client->get('/readiness');
        
        $this->assertEquals(200, $response->getStatusCode());
        $this->assertEquals('text/plain', $response->getHeaderLine('Content-Type'));
        $this->assertEquals('OK', trim($response->getBody()->getContents()));
    }

    /**
     * AC-3: When gRPC server is not serving, GET /readiness returns 503 Service Unavailable
     */
    public function test_ac3_readiness_failure_grpc_not_serving()
    {
        // Simulate gRPC server shutdown/initialization failure (test env should support this via header)
        $response = $this->client->get('/readiness', [
            'headers' => ['X-Test-Simulate-Grpc-Not-Serving' => '1']
        ]);
        
        $this->assertEquals(503, $response->getStatusCode());
    }

    /**
     * AC-4: Health server listens on default port 8081 when QUOTE_HEALTH_PORT env var is not set
     */
    public function test_ac4_default_health_port_used_when_no_env_var()
    {
        // Temporarily unset env var for this test
        $originalPort = getenv('QUOTE_HEALTH_PORT');
        putenv('QUOTE_HEALTH_PORT');
        
        try {
            // Test connection to default port 8081
            $client = new Client([
                'base_uri' => 'http://quote-service:8081',
                'timeout' => 1.0,
            ]);
            $response = $client->get('/liveness');
            $this->assertEquals(200, $response->getStatusCode());
        } finally {
            // Restore original env var
            if ($originalPort !== false) {
                putenv("QUOTE_HEALTH_PORT=$originalPort");
            }
        }
    }

    /**
     * AC-5: Health server listens on specified port when QUOTE_HEALTH_PORT env var is set to valid value
     */
    public function test_ac5_custom_health_port_used_when_env_var_set()
    {
        $customPort = 9999;
        $originalPort = getenv('QUOTE_HEALTH_PORT');
        putenv("QUOTE_HEALTH_PORT=$customPort");
        
        try {
            // Test connection to custom port
            $client = new Client([
                'base_uri' => "http://quote-service:$customPort",
                'timeout' => 1.0,
            ]);
            $response = $client->get('/liveness');
            $this->assertEquals(200, $response->getStatusCode());
        } finally {
            if ($originalPort !== false) {
                putenv("QUOTE_HEALTH_PORT=$originalPort");
            }
        }
    }

    /**
     * AC-6: Service fails to start when QUOTE_HEALTH_PORT env var is set to invalid value
     * 
     * @dataProvider invalidPortProvider
     */
    public function test_ac6_service_fails_start_with_invalid_health_port($invalidPortValue)
    {
        $originalPort = getenv('QUOTE_HEALTH_PORT');
        putenv("QUOTE_HEALTH_PORT=$invalidPortValue");
        
        try {
            // Attempt to start service (test env command to start service)
            $output = [];
            $exitCode = 0;
            exec('php src/quote/app/service.php 2>&1', $output, $exitCode);
            
            $this->assertNotEquals(0, $exitCode, "Service started successfully with invalid port $invalidPortValue");
            $this->assertStringContainsStringIgnoringCase('invalid health port', implode("\n", $output), "Error message missing for invalid port $invalidPortValue");
        } finally {
            if ($originalPort !== false) {
                putenv("QUOTE_HEALTH_PORT=$originalPort");
            }
        }
    }

    public function invalidPortProvider()
    {
        return [
            'port 0' => [0],
            'port >65535' => [65536],
            'port negative' => [-1],
            'non-numeric port string' => ['not-a-number'],
            'empty string port' => [''],
            'port with spaces' => [' 8080 '],
        ];
    }

    /**
     * AC-7: Health endpoints follow standard OpenTelemetry demo conventions: no auth, correct paths, plain text responses
     */
    public function test_ac7_health_endpoints_conformance()
    {
        // Test no authentication required
        $response = $this->client->get('/liveness', ['headers' => ['Authorization' => 'InvalidToken123']]);
        $this->assertEquals(200, $response->getStatusCode());
        
        $response = $this->client->get('/readiness', ['headers' => ['Authorization' => 'InvalidToken123']]);
        $this->assertNotEquals(401, $response->getStatusCode());
        $this->assertNotEquals(403, $response->getStatusCode());
        
        // Test paths exactly match (no /health prefix)
        $response = $this->client->get('/health/liveness');
        $this->assertEquals(404, $response->getStatusCode());
        
        $response = $this->client->get('/health/readiness');
        $this->assertEquals(404, $response->getStatusCode());
        
        // Test plain text responses
        $response = $this->client->get('/liveness');
        $this->assertEquals('text/plain', $response->getHeaderLine('Content-Type'));
        $this->assertEquals('OK', trim($response->getBody()->getContents()));
        
        $response = $this->client->get('/readiness');
        $this->assertEquals('text/plain', $response->getHeaderLine('Content-Type'));
    }
}
