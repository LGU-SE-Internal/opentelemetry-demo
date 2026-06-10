package io.opentelemetry.demo.frauddetection;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.context.event.ContextClosedEvent;
import org.springframework.context.event.EventListener;
import sun.misc.Signal;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

@SpringBootApplication
public class FraudDetectionApplication {
    private static final Logger logger = LoggerFactory.getLogger(FraudDetectionApplication.class);

    public static void main(String[] args) {
        SpringApplication app = new SpringApplication(FraudDetectionApplication.class);
        app.run(args);

        // Register signal handlers for SIGINT and SIGTERM
        ShutdownHandler shutdownHandler = app.getApplicationContext().getBean(ShutdownHandler.class);
        registerSignalHandler("INT", shutdownHandler);
        registerSignalHandler("TERM", shutdownHandler);
    }

    private static void registerSignalHandler(String signalName, ShutdownHandler shutdownHandler) {
        Signal.handle(new Signal(signalName), signal -> {
            logger.info("Shutdown signal received: {}, initiating graceful shutdown", signal.getName());
            shutdownHandler.performGracefulShutdown();
            System.exit(0);
        });
    }

    @Bean
    public ShutdownHandler shutdownHandler() {
        return new DefaultShutdownHandler();
    }
}
