package io.opentelemetry.demo.frauddetection;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import java.util.Map;

@RestController
public class FraudCheckController {

    @PostMapping("/fraud/check")
    public ResponseEntity<Map<String, Object>> checkFraud(
            @RequestBody Map<String, Object> request,
            @RequestParam(value = "slow", required = false) Long slowMs
    ) throws InterruptedException {
        if (slowMs != null) {
            Thread.sleep(slowMs);
        }
        return new ResponseEntity<>(
                Map.of("fraudulent", false, "score", 0.1),
                HttpStatus.OK
        );
    }
}
