/*
 * Copyright The OpenTelemetry Authors
 * SPDX-License-Identifier: Apache-2.0
 */

package oteldemo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.Map;

@SpringBootApplication
@RestController
public class AdServiceApplication {

    private static volatile boolean simulateLivenessFailure = false;
    private static volatile boolean simulateDependencyFailure = false;

    public static void main(String[] args) throws Exception {
        // Start Spring Boot application
        SpringApplication.run(AdServiceApplication.class, args);
        
        // Start existing gRPC AdService
        AdService.main(args);
    }

    @GetMapping("/health/liveness")
    public Map<String, Object> liveness() {
        Map<String, Object> response = new HashMap<>();
        if (simulateLivenessFailure) {
            response.put("status", "DOWN");
            response.put("error", "Simulated JVM unhealthy state");
            return response;
        }
        response.put("status", "UP");
        return response;
    }

    @GetMapping("/health/readiness")
    public Map<String, Object> readiness() {
        Map<String, Object> response = new HashMap<>();
        Map<String, String> checks = new HashMap<>();
        
        // Check flagd dependency
        try {
            if (simulateDependencyFailure) {
                throw new RuntimeException("Simulated dependency failure");
            }
            // Verify OpenFeature client is initialized
            if (dev.openfeature.sdk.OpenFeatureAPI.getInstance().getProvider() == null) {
                checks.put("flagd", "DOWN");
            } else {
                checks.put("flagd", "UP");
            }
        } catch (Exception e) {
            checks.put("flagd", "DOWN");
        }
        
        // Check if service is marked as ready
        if (AdService.isReady() && !checks.containsValue("DOWN")) {
            response.put("status", "READY");
        } else {
            response.put("status", "NOT_READY");
            response.put("error", "One or more dependencies are unavailable");
        }
        
        response.put("checks", checks);
        return response;
    }

    // For testing purposes
    public static void setSimulateLivenessFailure(boolean value) {
        simulateLivenessFailure = value;
    }

    public static void setSimulateDependencyFailure(boolean value) {
        simulateDependencyFailure = value;
    }
}
