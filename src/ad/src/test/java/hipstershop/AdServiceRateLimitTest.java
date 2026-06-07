package hipstershop;

import io.restassured.RestAssured;
import io.restassured.http.ContentType;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.DisplayName;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.*;
import java.util.concurrent.TimeUnit;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
public class AdServiceRateLimitTest {

    @LocalServerPort
    private int port;

    private static final String AD_ENDPOINT = "/ads";
    private static final String X_FORWARDED_FOR_HEADER = "X-Forwarded-For";
    private static final String X_RATELIMIT_LIMIT_HEADER = "X-RateLimit-Limit";
    private static final String X_RATELIMIT_REMAINING_HEADER = "X-RateLimit-Remaining";
    private static final String X_RATELIMIT_RESET_HEADER = "X-RateLimit-Reset";
    private static final String RATE_LIMIT_EXCEEDED_MESSAGE = "Rate limit exceeded. Try again later.";

    @BeforeEach
    void setUp() {
        RestAssured.port = port;
    }

    @Test
    @DisplayName("AC-1: Requests under default rate limit return 200 with correct headers")
    void test_ac1_requests_under_default_limit_return_200() {
        String clientIp = "192.168.1.100";
        int defaultLimit = 100;
        
        for (int i = 0; i < defaultLimit; i++) {
            given()
                .header(X_FORWARDED_FOR_HEADER, clientIp)
                .contentType(ContentType.JSON)
                .body("{\"contextKeys\": [\"product\"]}")
            .when()
                .post(AD_ENDPOINT)
            .then()
                .statusCode(200)
                .header(X_RATELIMIT_LIMIT_HEADER, equalTo(String.valueOf(defaultLimit)))
                .header(X_RATELIMIT_REMAINING_HEADER, equalTo(String.valueOf(defaultLimit - i - 1)))
                .header(X_RATELIMIT_RESET_HEADER, notNullValue())
                .header(X_RATELIMIT_RESET_HEADER, matchesPattern("\\d+"));
        }
    }

    @Test
    @DisplayName("AC-2: Requests over default rate limit return 429 with correct headers")
    void test_ac2_requests_over_default_limit_return_429() {
        String clientIp = "192.168.1.101";
        int defaultLimit = 100;
        
        // Send requests up to limit
        for (int i = 0; i < defaultLimit; i++) {
            given()
                .header(X_FORWARDED_FOR_HEADER, clientIp)
                .contentType(ContentType.JSON)
                .body("{\"contextKeys\": [\"product\"]}")
            .when()
                .post(AD_ENDPOINT)
            .then()
                .statusCode(200);
        }

        // Next request should be rate limited
        given()
            .header(X_FORWARDED_FOR_HEADER, clientIp)
            .contentType(ContentType.JSON)
            .body("{\"contextKeys\": [\"product\"]}")
        .when()
            .post(AD_ENDPOINT)
        .then()
            .statusCode(429)
            .header(X_RATELIMIT_LIMIT_HEADER, equalTo(String.valueOf(defaultLimit)))
            .header(X_RATELIMIT_REMAINING_HEADER, equalTo("0"))
            .header(X_RATELIMIT_RESET_HEADER, notNullValue())
            .header(X_RATELIMIT_RESET_HEADER, matchesPattern("\\d+"))
            .body("message", equalTo(RATE_LIMIT_EXCEEDED_MESSAGE));
    }

    @Test
    @DisplayName("AC-3: Custom rate limit requests value works correctly")
    void test_ac3_custom_rate_limit_requests_value() {
        // Set environment variable AD_SERVICE_RATE_LIMIT_REQUESTS=500 before running this test
        String clientIp = "192.168.1.102";
        int customLimit = 500;
        
        for (int i = 0; i < customLimit; i++) {
            given()
                .header(X_FORWARDED_FOR_HEADER, clientIp)
                .contentType(ContentType.JSON)
                .body("{\"contextKeys\": [\"product\"]}")
            .when()
                .post(AD_ENDPOINT)
            .then()
                .statusCode(200);
        }

        // Next request should be rate limited
        given()
            .header(X_FORWARDED_FOR_HEADER, clientIp)
            .contentType(ContentType.JSON)
            .body("{\"contextKeys\": [\"product\"]}")
        .when()
            .post(AD_ENDPOINT)
        .then()
            .statusCode(429);
    }

    @Test
    @DisplayName("AC-4: Custom rate limit window value works correctly")
    void test_ac4_custom_rate_limit_window_value() throws InterruptedException {
        // Set environment variable AD_SERVICE_RATE_LIMIT_WINDOW_SECONDS=3 before running this test (shortened for test)
        String clientIp = "192.168.1.103";
        int defaultLimit = 100;
        int customWindowSeconds = 3;
        
        // Send requests up to limit
        for (int i = 0; i < defaultLimit; i++) {
            given()
                .header(X_FORWARDED_FOR_HEADER, clientIp)
                .contentType(ContentType.JSON)
                .body("{\"contextKeys\": [\"product\"]}")
            .when()
                .post(AD_ENDPOINT)
            .then()
                .statusCode(200);
        }

        // Request should be rate limited now
        given()
            .header(X_FORWARDED_FOR_HEADER, clientIp)
            .contentType(ContentType.JSON)
            .body("{\"contextKeys\": [\"product\"]}")
        .when()
            .post(AD_ENDPOINT)
        .then()
            .statusCode(429);

        // Wait for window to reset
        TimeUnit.SECONDS.sleep(customWindowSeconds + 1);

        // Request should succeed after window reset
        given()
            .header(X_FORWARDED_FOR_HEADER, clientIp)
            .contentType(ContentType.JSON)
            .body("{\"contextKeys\": [\"product\"]}")
        .when()
            .post(AD_ENDPOINT)
        .then()
            .statusCode(200);
    }

    @Test
    @DisplayName("AC-5: All successful responses include rate limit headers")
    void test_ac5_success_responses_include_rate_limit_headers() {
        String clientIp = "192.168.1.104";
        
        given()
            .header(X_FORWARDED_FOR_HEADER, clientIp)
            .contentType(ContentType.JSON)
            .body("{\"contextKeys\": [\"product\"]}")
        .when()
            .post(AD_ENDPOINT)
        .then()
            .statusCode(200)
            .header(X_RATELIMIT_LIMIT_HEADER, notNullValue())
            .header(X_RATELIMIT_LIMIT_HEADER, matchesPattern("\\d+"))
            .header(X_RATELIMIT_REMAINING_HEADER, notNullValue())
            .header(X_RATELIMIT_REMAINING_HEADER, matchesPattern("\\d+"))
            .header(X_RATELIMIT_RESET_HEADER, notNullValue())
            .header(X_RATELIMIT_RESET_HEADER, matchesPattern("\\d+"));
    }

    @Test
    @DisplayName("AC-6: All rate limited responses include rate limit headers")
    void test_ac6_rate_limited_responses_include_rate_limit_headers() {
        String clientIp = "192.168.1.105";
        int defaultLimit = 100;
        
        // Exhaust limit
        for (int i = 0; i < defaultLimit; i++) {
            given()
                .header(X_FORWARDED_FOR_HEADER, clientIp)
                .contentType(ContentType.JSON)
                .body("{\"contextKeys\": [\"product\"]}")
            .when()
                .post(AD_ENDPOINT)
            .then()
                .statusCode(200);
        }

        // Get rate limited response
        given()
            .header(X_FORWARDED_FOR_HEADER, clientIp)
            .contentType(ContentType.JSON)
            .body("{\"contextKeys\": [\"product\"]}")
        .when()
            .post(AD_ENDPOINT)
        .then()
            .statusCode(429)
            .header(X_RATELIMIT_LIMIT_HEADER, notNullValue())
            .header(X_RATELIMIT_LIMIT_HEADER, matchesPattern("\\d+"))
            .header(X_RATELIMIT_REMAINING_HEADER, notNullValue())
            .header(X_RATELIMIT_REMAINING_HEADER, matchesPattern("\\d+"))
            .header(X_RATELIMIT_RESET_HEADER, notNullValue())
            .header(X_RATELIMIT_RESET_HEADER, matchesPattern("\\d+"));
    }

    @Test
    @DisplayName("AC-7: Rate limits are independent per client IP")
    void test_ac7_rate_limits_independent_per_client_ip() {
        String clientIpA = "192.168.1.106";
        String clientIpB = "192.168.1.107";
        int defaultLimit = 100;
        
        // Exhaust limit for client A
        for (int i = 0; i < defaultLimit; i++) {
            given()
                .header(X_FORWARDED_FOR_HEADER, clientIpA)
                .contentType(ContentType.JSON)
                .body("{\"contextKeys\": [\"product\"]}")
            .when()
                .post(AD_ENDPOINT)
            .then()
                .statusCode(200);
        }

        // Client A should be rate limited
        given()
            .header(X_FORWARDED_FOR_HEADER, clientIpA)
            .contentType(ContentType.JSON)
            .body("{\"contextKeys\": [\"product\"]}")
        .when()
            .post(AD_ENDPOINT)
        .then()
            .statusCode(429);

        // Client B should still be able to make requests
        given()
            .header(X_FORWARDED_FOR_HEADER, clientIpB)
            .contentType(ContentType.JSON)
            .body("{\"contextKeys\": [\"product\"]}")
        .when()
            .post(AD_ENDPOINT)
        .then()
            .statusCode(200);
    }

    @Test
    @DisplayName("AC-8: Rate limits reset after window expires")
    void test_ac8_rate_limits_reset_after_window() throws InterruptedException {
        String clientIp = "192.168.1.108";
        int defaultLimit = 100;
        int defaultWindowSeconds = 60;
        
        // Exhaust limit
        for (int i = 0; i < defaultLimit; i++) {
            given()
                .header(X_FORWARDED_FOR_HEADER, clientIp)
                .contentType(ContentType.JSON)
                .body("{\"contextKeys\": [\"product\"]}")
            .when()
                .post(AD_ENDPOINT)
            .then()
                .statusCode(200);
        }

        // Request should be rate limited now
        given()
            .header(X_FORWARDED_FOR_HEADER, clientIp)
            .contentType(ContentType.JSON)
            .body("{\"contextKeys\": [\"product\"]}")
        .when()
            .post(AD_ENDPOINT)
        .then()
            .statusCode(429);

        // Wait for window to reset (shortened for test purposes, should use actual window value)
        TimeUnit.SECONDS.sleep(defaultWindowSeconds + 1);

        // Request should succeed after reset
        given()
            .header(X_FORWARDED_FOR_HEADER, clientIp)
            .contentType(ContentType.JSON)
            .body("{\"contextKeys\": [\"product\"]}")
        .when()
            .post(AD_ENDPOINT)
        .then()
            .statusCode(200);
    }
}
