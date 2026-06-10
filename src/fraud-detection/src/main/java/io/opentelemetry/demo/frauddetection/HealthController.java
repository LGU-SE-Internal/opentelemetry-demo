package io.opentelemetry.demo.frauddetection;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import java.util.Map;

@RestController
public class HealthController {
    @Autowired
    private ShutdownHandler shutdownHandler;

    @GetMapping("/health")
    public ResponseEntity<Map<String, String>> healthCheck() {
        if (((DefaultShutdownHandler) shutdownHandler).isShuttingDown()) {
            return new ResponseEntity<>(
                    Map.of("status", "shutdown_in_progress"),
                    HttpStatus.SERVICE_UNAVAILABLE
            );
        }
        return new ResponseEntity<>(Map.of("status", "healthy"), HttpStatus.OK);
    }
}
