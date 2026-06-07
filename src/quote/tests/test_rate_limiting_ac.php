<?php

use PHPUnit\Framework\TestCase;
use GuzzleHttp\Client;
use GuzzleHttp\Exception\ClientException;

class RateLimitingACTest extends TestCase
{
    private Client $client;
    private string $quoteServiceUrl = 'http://localhost:8080'; // Default test endpoint, adjust as needed in environment

    protected function setUp(): void
    {
        $this->client = new Client([
            'base_uri' => getenv('QUOTE_SERVICE_URL') ?: $this->quoteServiceUrl,
            'http_errors' => false,
        ]);
        // Reset rate limit storage between tests if possible, or run with isolated instances
        putenv('QUOTE_SERVICE_RATE_LIMIT_RPM=10'); // Reset to default before each test
    }

    /**
     * AC-1: When a single client IP sends more than the configured QUOTE_SERVICE_RATE_LIMIT_RPM quote calculation requests within a 60-second window, the (limit + 1)th request returns a 429 Too Many Requests HTTP status code.
     */
    public function test_ac1_exceed_rate_limit_returns_429(): void
    {
        $limit = (int)getenv('QUOTE_SERVICE_RATE_LIMIT_RPM') ?: 10;

        // Send limit successful requests
        for ($i = 0; $i < $limit; $i++) {
            $response = $this->client->post('/getQuote', [
                'json' => ['items' => [['price' => 10, 'quantity' => 1]]]
            ]);
            $this->assertEquals(200, $response->getStatusCode(), "Request $i failed unexpectedly");
        }

        // Send limit + 1th request, expect 429
        $response = $this->client->post('/getQuote', [
            'json' => ['items' => [['price' => 10, 'quantity' => 1]]]
        ]);
        $this->assertEquals(429, $response->getStatusCode(), "Expected 429 after exceeding rate limit");
    }

    /**
     * AC-2: All 429 responses include a Retry-After header with an integer value representing the number of seconds until the client's rate limit window resets.
     */
    public function test_ac2_429_response_has_retry_after_header(): void
    {
        // Exhaust rate limit first
        $limit = (int)getenv('QUOTE_SERVICE_RATE_LIMIT_RPM') ?: 10;
        for ($i = 0; $i < $limit; $i++) {
            $this->client->post('/getQuote', [
                'json' => ['items' => [['price' => 10, 'quantity' => 1]]]
            ]);
        }

        $response = $this->client->post('/getQuote', [
            'json' => ['items' => [['price' => 10, 'quantity' => 1]]]
        ]);

        $this->assertTrue($response->hasHeader('Retry-After'), "Missing Retry-After header in 429 response");
        $retryAfter = $response->getHeaderLine('Retry-After');
        $this->assertIsNumeric($retryAfter, "Retry-After header value is not an integer");
        $this->assertGreaterThanOrEqual(0, (int)$retryAfter, "Retry-After value is negative");
        $this->assertLessThanOrEqual(60, (int)$retryAfter, "Retry-After value exceeds 60 second window");
    }

    /**
     * AC-3: All 429 responses include a JSON body with error, message, and retry_after fields, where retry_after matches the value in the Retry-After header.
     */
    public function test_ac3_429_response_has_correct_json_body(): void
    {
        // Exhaust rate limit first
        $limit = (int)getenv('QUOTE_SERVICE_RATE_LIMIT_RPM') ?: 10;
        for ($i = 0; $i < $limit; $i++) {
            $this->client->post('/getQuote', [
                'json' => ['items' => [['price' => 10, 'quantity' => 1]]]
            ]);
        }

        $response = $this->client->post('/getQuote', [
            'json' => ['items' => [['price' => 10, 'quantity' => 1]]]
        ]);

        $body = json_decode($response->getBody()->getContents(), true);
        $this->assertIsArray($body, "429 response body is not valid JSON");

        $this->assertArrayHasKey('error', $body, "Missing 'error' field in 429 response");
        $this->assertEquals('Too Many Requests', $body['error'], "Incorrect error field value");

        $this->assertArrayHasKey('message', $body, "Missing 'message' field in 429 response");
        $this->assertEquals('You have exceeded the rate limit for quote calculation requests', $body['message'], "Incorrect message field value");

        $this->assertArrayHasKey('retry_after', $body, "Missing 'retry_after' field in 429 response");
        $this->assertIsInt($body['retry_after'], "retry_after field is not an integer");

        $retryAfterHeader = (int)$response->getHeaderLine('Retry-After');
        $this->assertEquals($retryAfterHeader, $body['retry_after'], "retry_after field does not match Retry-After header value");
    }

    /**
     * AC-4: When the QUOTE_SERVICE_RATE_LIMIT_RPM environment variable is set to a positive integer value, that value is used as the per-client rate limit instead of the default 10.
     */
    public function test_ac4_custom_rate_limit_from_environment_variable(): void
    {
        $customLimit = 5;
        putenv("QUOTE_SERVICE_RATE_LIMIT_RPM=$customLimit");

        // Send customLimit successful requests
        for ($i = 0; $i < $customLimit; $i++) {
            $response = $this->client->post('/getQuote', [
                'json' => ['items' => [['price' => 10, 'quantity' => 1]]]
            ]);
            $this->assertEquals(200, $response->getStatusCode(), "Request $i failed unexpectedly with custom limit $customLimit");
        }

        // Send customLimit + 1th request, expect 429
        $response = $this->client->post('/getQuote', [
            'json' => ['items' => [['price' => 10, 'quantity' => 1]]]
        ]);
        $this->assertEquals(429, $response->getStatusCode(), "Expected 429 after exceeding custom rate limit of $customLimit");
    }

    /**
     * AC-5: When the QUOTE_SERVICE_RATE_LIMIT_RPM environment variable is set to 0, no rate limiting is applied: no 429 responses are returned for any volume of quote calculation requests.
     */
    public function test_ac5_rate_limit_disabled_when_env_var_set_to_zero(): void
    {
        putenv('QUOTE_SERVICE_RATE_LIMIT_RPM=0');
        $requestCount = 20; // Significantly higher than default limit of 10

        for ($i = 0; $i < $requestCount; $i++) {
            $response = $this->client->post('/getQuote', [
                'json' => ['items' => [['price' => 10, 'quantity' => 1]]]
            ]);
            $this->assertNotEquals(429, $response->getStatusCode(), "Unexpected 429 response when rate limiting is disabled (request $i)");
            $this->assertEquals(200, $response->getStatusCode(), "Request $i failed unexpectedly when rate limiting is disabled");
        }
    }

    /**
     * AC-6: Requests to health and readiness check endpoints (/health, /healthz, /ready, /livez) never return 429 responses, even when request volume exceeds the configured rate limit.
     *
     * @dataProvider healthEndpointProvider
     */
    public function test_ac6_health_endpoints_never_rate_limited(string $endpoint): void
    {
        // Exhaust rate limit first for quote endpoint
        $limit = (int)getenv('QUOTE_SERVICE_RATE_LIMIT_RPM') ?: 10;
        for ($i = 0; $i < $limit; $i++) {
            $this->client->post('/getQuote', [
                'json' => ['items' => [['price' => 10, 'quantity' => 1]]]
            ]);
        }
        // Verify we are actually rate limited for quote endpoint
        $quoteResponse = $this->client->post('/getQuote', [
            'json' => ['items' => [['price' => 10, 'quantity' => 1]]]
        ]);
        $this->assertEquals(429, $quoteResponse->getStatusCode(), "Quote endpoint not rate limited as expected before testing health endpoints");

        // Send multiple requests to health endpoint, ensure no 429
        $requestCount = 15;
        for ($i = 0; $i < $requestCount; $i++) {
            $response = $this->client->get($endpoint);
            $this->assertNotEquals(429, $response->getStatusCode(), "Unexpected 429 response for health endpoint $endpoint (request $i)");
            $this->assertEquals(200, $response->getStatusCode(), "Health endpoint $endpoint failed unexpectedly (request $i)");
        }
    }

    public static function healthEndpointProvider(): array
    {
        return [
            ['/health'],
            ['/healthz'],
            ['/ready'],
            ['/livez'],
        ];
    }

    /**
     * AC-7: Rate limits are enforced per unique client IP: one client exceeding their limit does not result in 429 responses for requests from other distinct IP addresses.
     */
    public function test_ac7_rate_limits_per_unique_client_ip(): void
    {
        $limit = (int)getenv('QUOTE_SERVICE_RATE_LIMIT_RPM') ?: 10;

        // Exhaust rate limit for client IP 192.168.1.100
        for ($i = 0; $i < $limit; $i++) {
            $response = $this->client->post('/getQuote', [
                'json' => ['items' => [['price' => 10, 'quantity' => 1]]],
                'headers' => ['X-Forwarded-For' => '192.168.1.100']
            ]);
            $this->assertEquals(200, $response->getStatusCode(), "Request $i for client 192.168.1.100 failed unexpectedly");
        }
        // Verify client 192.168.1.100 is rate limited
        $responseClient1 = $this->client->post('/getQuote', [
            'json' => ['items' => [['price' => 10, 'quantity' => 1]]],
            'headers' => ['X-Forwarded-For' => '192.168.1.100']
        ]);
        $this->assertEquals(429, $responseClient1->getStatusCode(), "Expected 429 for client 192.168.1.100 after exceeding limit");

        // Verify client 192.168.1.200 is NOT rate limited
        for ($i = 0; $i < $limit; $i++) {
            $response = $this->client->post('/getQuote', [
                'json' => ['items' => [['price' => 10, 'quantity' => 1]]],
                'headers' => ['X-Forwarded-For' => '192.168.1.200']
            ]);
            $this->assertEquals(200, $response->getStatusCode(), "Request $i for client 192.168.1.200 failed unexpectedly while client 1 is rate limited");
        }
    }

    /**
     * AC-8: Requests with a valid X-Forwarded-For header use the first IP address in the header value as the client identifier for rate limiting.
     */
    public function test_ac8_x_forwarded_for_first_ip_used_as_client_id(): void
    {
        $limit = (int)getenv('QUOTE_SERVICE_RATE_LIMIT_RPM') ?: 10;
        $clientIp = '10.0.0.5';
        $proxyIps = '192.168.1.1, 172.17.0.1';
        $xffHeader = "$clientIp, $proxyIps";

        // Exhaust rate limit using XFF header with client IP first
        for ($i = 0; $i < $limit; $i++) {
            $response = $this->client->post('/getQuote', [
                'json' => ['items' => [['price' => 10, 'quantity' => 1]]],
                'headers' => ['X-Forwarded-For' => $xffHeader]
            ]);
            $this->assertEquals(200, $response->getStatusCode(), "Request $i with XFF header failed unexpectedly");
        }

        // Request with same first IP in XFF should be rate limited
        $responseSameIp = $this->client->post('/getQuote', [
            'json' => ['items' => [['price' => 10, 'quantity' => 1]]],
            'headers' => ['X-Forwarded-For' => $xffHeader]
        ]);
        $this->assertEquals(429, $responseSameIp->getStatusCode(), "Expected 429 for same first IP in XFF header after exceeding limit");

        // Request with different first IP in XFF should NOT be rate limited
        $responseDifferentIp = $this->client->post('/getQuote', [
            'json' => ['items' => [['price' => 10, 'quantity' => 1]]],
            'headers' => ['X-Forwarded-For' => "10.0.0.6, $proxyIps"]
        ]);
        $this->assertEquals(200, $responseDifferentIp->getStatusCode(), "Unexpected 429 for different first IP in XFF header");
    }
}
