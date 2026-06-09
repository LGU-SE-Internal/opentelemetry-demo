package opentelemetry.demo.adservice;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.test.context.ActiveProfiles;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
public class AdServiceHealthEndpointsIntegrationTest {

    @LocalServerPort
    private int port;

    @Autowired
    private TestRestTemplate restTemplate;

    private String getBaseUrl() {
        return "http://localhost:" + port;
    }

    @Test
    public void test_ac1_liveness_endpoint_returns_200_ok_when_jvm_running_healthy() {
        ResponseEntity<Map> response = restTemplate.getForEntity(
                getBaseUrl() + "/health/liveness",
                Map.class
        );

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().get("status")).isEqualTo("UP");
    }

    @Test
    public void test_ac2_liveness_endpoint_returns_503_when_jvm_unhealthy() {
        // This test would simulate JVM unhealthy state (OOM, deadlock) in integration test scenario
        // For now, validates error response structure when endpoint returns failure
        ResponseEntity<Map> response = restTemplate.getForEntity(
                getBaseUrl() + "/health/liveness?simulateFailure=true",
                Map.class
        );

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().get("status")).isEqualTo("DOWN");
        assertThat(response.getBody()).containsKey("error");
    }

    @Test
    public void test_ac3_readiness_endpoint_returns_200_ok_when_all_dependencies_up() {
        ResponseEntity<Map> response = restTemplate.getForEntity(
                getBaseUrl() + "/health/readiness",
                Map.class
        );

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().get("status")).isEqualTo("READY");
        assertThat(response.getBody()).containsKey("checks");
        Map<String, String> checks = (Map<String, String>) response.getBody().get("checks");
        checks.values().forEach(status -> assertThat(status).isEqualTo("UP"));
    }

    @Test
    public void test_ac4_readiness_endpoint_returns_503_when_any_dependency_down() {
        // This test simulates failed backing store connection in integration test scenario
        ResponseEntity<Map> response = restTemplate.getForEntity(
                getBaseUrl() + "/health/readiness?simulateDependencyFailure=true",
                Map.class
        );

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().get("status")).isEqualTo("NOT_READY");
        assertThat(response.getBody()).containsKey("checks");
        assertThat(response.getBody()).containsKey("error");
        Map<String, String> checks = (Map<String, String>) response.getBody().get("checks");
        assertThat(checks.values()).contains("DOWN");
    }

    @Test
    public void test_ac5_existing_adservice_endpoints_function_correctly() {
        // Test existing ad service endpoint remains functional
        AdService.GetAdsRequest request = AdService.GetAdsRequest.newBuilder()
                .addContextKeys("test")
                .build();

        ResponseEntity<AdService.GetAdsResponse> response = restTemplate.postForEntity(
                getBaseUrl() + "/AdService/GetAds",
                request,
                AdService.GetAdsResponse.class
        );

        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        assertThat(response.getBody()).isNotNull();
        assertThat(response.getBody().getAdsCount()).isGreaterThanOrEqualTo(0);
    }

    @Test
    public void test_ac6_health_endpoints_require_no_authentication() {
        // Test liveness endpoint without auth headers
        ResponseEntity<Map> livenessResponse = restTemplate.getForEntity(
                getBaseUrl() + "/health/liveness",
                Map.class
        );
        assertThat(livenessResponse.getStatusCode()).isNotEqualTo(HttpStatus.UNAUTHORIZED);
        assertThat(livenessResponse.getStatusCode()).isNotEqualTo(HttpStatus.FORBIDDEN);

        // Test readiness endpoint without auth headers
        ResponseEntity<Map> readinessResponse = restTemplate.getForEntity(
                getBaseUrl() + "/health/readiness",
                Map.class
        );
        assertThat(readinessResponse.getStatusCode()).isNotEqualTo(HttpStatus.UNAUTHORIZED);
        assertThat(readinessResponse.getStatusCode()).isNotEqualTo(HttpStatus.FORBIDDEN);
    }
}
