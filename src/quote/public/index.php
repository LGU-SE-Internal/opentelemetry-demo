<?php
// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0



declare(strict_types=1);

use DI\Bridge\Slim\Bridge;
use DI\ContainerBuilder;
use OpenTelemetry\API\Globals;
use OpenTelemetry\SDK\Common\Configuration\Configuration;
use OpenTelemetry\SDK\Common\Configuration\Variables;
use OpenTelemetry\SDK\Logs\LoggerProviderInterface;
use OpenTelemetry\SDK\Metrics\MeterProviderInterface;
use OpenTelemetry\SDK\Trace\TracerProviderInterface;
use Psr\Http\Message\ServerRequestInterface;
use React\EventLoop\Loop;
use React\Http\HttpServer;
use React\Socket\SocketServer;
use Slim\Factory\AppFactory;

require __DIR__ . '/../vendor/autoload.php';

// Instantiate PHP-DI ContainerBuilder
$containerBuilder = new ContainerBuilder();

// Set up settings
$settings = require __DIR__ . '/../app/settings.php';
$settings($containerBuilder);

// Set up dependencies
$dependencies = require __DIR__ . '/../app/dependencies.php';
$dependencies($containerBuilder);

// Build PHP-DI Container instance
$container = $containerBuilder->build();

// TLS/mTLS Configuration Validation
$tlsCertPath = getenv('QUOTESVC_TLS_CERT_PATH') ?: '';
$tlsKeyPath = getenv('QUOTESVC_TLS_KEY_PATH') ?: '';
$mtlsEnabled = filter_var(getenv('QUOTESVC_MTLS_ENABLED'), FILTER_VALIDATE_BOOLEAN);
$mtlsCaCertPath = getenv('QUOTESVC_MTLS_CA_CERT_PATH') ?: '';

// Validate TLS configuration
if ($tlsCertPath && !$tlsKeyPath) {
    fwrite(STDERR, "TLS key path must be provided when TLS cert path is configured\n");
    exit(1);
}
if ($tlsKeyPath && !$tlsCertPath) {
    fwrite(STDERR, "TLS cert path must be provided when TLS key path is configured\n");
    exit(1);
}

// Validate TLS files exist and are readable
if ($tlsCertPath) {
    if (!file_exists($tlsCertPath) || !is_readable($tlsCertPath)) {
        fwrite(STDERR, "TLS certificate file not found or unreadable: {$tlsCertPath}\n");
        exit(1);
    }
    if (!file_exists($tlsKeyPath) || !is_readable($tlsKeyPath)) {
        fwrite(STDERR, "TLS private key file not found or unreadable: {$tlsKeyPath}\n");
        exit(1);
    }
    
    // Validate PEM data
    $certContent = file_get_contents($tlsCertPath);
    $keyContent = file_get_contents($tlsKeyPath);
    if (!openssl_x509_read($certContent)) {
        fwrite(STDERR, "Invalid TLS certificate/key pair: Failed to parse certificate\n");
        exit(1);
    }
    $key = openssl_pkey_get_private($keyContent);
    if (!$key || !openssl_x509_check_private_key($certContent, $key)) {
        fwrite(STDERR, "Invalid TLS certificate/key pair: Certificate and key do not match or key is invalid\n");
        exit(1);
    }
}

// Validate mTLS configuration
if ($mtlsEnabled) {
    if (!$mtlsCaCertPath) {
        fwrite(STDERR, "mTLS CA cert path must be provided when mTLS is enabled\n");
        exit(1);
    }
    if (!file_exists($mtlsCaCertPath) || !is_readable($mtlsCaCertPath)) {
        fwrite(STDERR, "mTLS CA certificate file not found or unreadable: {$mtlsCaCertPath}\n");
        exit(1);
    }
    $caCertContent = file_get_contents($mtlsCaCertPath);
    if (!openssl_x509_read($caCertContent)) {
        fwrite(STDERR, "Invalid mTLS CA certificate: Failed to parse CA certificate\n");
        exit(1);
    }
}

// Instantiate the app
AppFactory::setContainer($container);
$app = Bridge::create($container);

// Register routes
$routes = require __DIR__ . '/../app/routes.php';
$routes($app);

// Track HTTP request metrics globally
global $httpRequestMetrics;
$httpRequestMetrics = [];

// Middleware to track HTTP request metrics
$app->add(function (ServerRequestInterface $request, Psr\Http\Server\RequestHandlerInterface $handler) {
    global $httpRequestMetrics;
    
    $response = $handler->handle($request);
    $statusCode = $response->getStatusCode();
    $method = $request->getMethod();
    $route = $request->getUri()->getPath();
    
    // Create metric key
    $key = md5($method . '|' . $route . '|' . $statusCode);
    
    if (!isset($httpRequestMetrics[$key])) {
        $httpRequestMetrics[$key] = [
            'labels' => [
                'http_method' => $method,
                'http_route' => $route,
                'http_status_code' => (string)$statusCode
            ],
            'value' => 0
        ];
    }
    
    $httpRequestMetrics[$key]['value']++;
    
    return $response;
});

// Register middleware
$app->addRoutingMiddleware();

// Add Body Parsing Middleware
$app->addBodyParsingMiddleware();

// Rate Limiting Middleware
$rateLimitStorage = [];
$app->add(function (Psr\Http\Message\ServerRequestInterface $request, Psr\Http\Server\RequestHandlerInterface $handler) use (&$rateLimitStorage) {
    $path = $request->getUri()->getPath();
    $method = $request->getMethod();
    
        // Skip rate limiting for health and readiness endpoints
        $excludedPaths = ['/health', '/healthz', '/ready', '/livez', '/health/liveness', '/health/readiness'];
        if (in_array($path, $excludedPaths)) {
            return $handler->handle($request);
        }
    
    // Only apply rate limiting to quote calculation endpoints
    $quoteEndpoints = ['/getquote', '/getQuote', '/api/calculate-quote'];
    if (!($method === 'POST' && in_array($path, $quoteEndpoints))) {
        return $handler->handle($request);
    }
    
    // Get rate limit configuration from environment variable
    $rateLimitRpm = (int)getenv('QUOTE_SERVICE_RATE_LIMIT_RPM') ?: 10;
    
    // If rate limit is set to 0, disable rate limiting entirely
    if ($rateLimitRpm === 0) {
        return $handler->handle($request);
    }
    
    // Get client IP address
    $xForwardedFor = $request->getHeaderLine('X-Forwarded-For');
    if (!empty($xForwardedFor)) {
        $ips = explode(',', $xForwardedFor);
        $clientIp = trim($ips[0]);
    } else {
        $serverParams = $request->getServerParams();
        $clientIp = $serverParams['REMOTE_ADDR'] ?? 'unknown';
    }
    
    // Calculate current window (fixed 60-second window)
    $currentTime = time();
    $windowStart = floor($currentTime / 60) * 60;
    $windowKey = $clientIp . '|' . $windowStart;
    
    // Initialize counter for current window if not exists
    if (!isset($rateLimitStorage[$windowKey])) {
        $rateLimitStorage[$windowKey] = 0;
        // Clean up old windows (optional, prevents memory leak for long-running processes)
        foreach ($rateLimitStorage as $key => $value) {
            list($ip, $oldWindowStart) = explode('|', $key);
            if ((int)$oldWindowStart < $windowStart) {
                unset($rateLimitStorage[$key]);
            }
        }
    }
    
    // Check if rate limit is exceeded
    if ($rateLimitStorage[$windowKey] >= $rateLimitRpm) {
        $retryAfter = $windowStart + 60 - $currentTime;
        
        $response = new Slim\Psr7\Response();
        $payload = json_encode([
            'error' => 'Too Many Requests',
            'message' => 'You have exceeded the rate limit for quote calculation requests',
            'retry_after' => $retryAfter
        ]);
        $response->getBody()->write($payload);
        
        return $response
            ->withHeader('Content-Type', 'application/json')
            ->withHeader('Retry-After', (string)$retryAfter)
            ->withStatus(429);
    }
    
    // Increment counter and proceed with request
    $rateLimitStorage[$windowKey]++;
    
    return $handler->handle($request);
});

// Add Error Middleware
$errorMiddleware = $app->addErrorMiddleware(true, true, true);

// Graceful Shutdown Implementation
$activeRequestCount = 0;
$isShuttingDown = false;
$logger = $container->get(Psr\Log\LoggerInterface::class);
$startShutdownTime = null;
global $shutdownHandler, $isShuttingDown, $activeRequestCount, $logger, $startShutdownTime, $socket, $container;

// Get configured timeout from env var
function getConfiguredShutdownTimeout(): int {
    $timeout = getenv('QUOTE_SERVICE_GRACEFUL_SHUTDOWN_TIMEOUT');
    return $timeout !== false ? (int)$timeout : 10;
}

// Public API functions as per interface
function registerGracefulShutdownHandlers(): void {
    global $logger;
    
    // Check if pcntl functions are available
    if (!function_exists('pcntl_signal') || !function_exists('React\EventLoop\Loop::get')->addSignal) {
        $logger->error('Signal registration failed: pcntl extension or React event loop signals not supported in this environment');
        throw new RuntimeException('POSIX signals are not supported in this environment');
    }
    
    try {
        $loop = React\EventLoop\Loop::get();
        $loop->addSignal(SIGTERM, function(int $signal) {
            initiateGracefulShutdown($signal);
        });
        $loop->addSignal(SIGINT, function(int $signal) {
            initiateGracefulShutdown($signal);
        });
    } catch (Exception $e) {
        $logger->error('Signal registration failed: ' . $e->getMessage());
        throw new RuntimeException('Failed to register signal handlers: ' . $e->getMessage());
    }
}

function isServiceShuttingDown(): bool {
    global $isShuttingDown;
    return $isShuttingDown;
}

function getActiveRequestCount(): int {
    global $activeRequestCount;
    return $activeRequestCount;
}

function incrementActiveRequestCounter(): void {
    global $activeRequestCount;
    $activeRequestCount++;
}

function decrementActiveRequestCounter(): void {
    global $activeRequestCount;
    $activeRequestCount = max(0, $activeRequestCount - 1);
}

function cleanupConnections(): void {
    global $logger, $container;
    
    try {
        // Close database connections if available
        if ($container->has('db')) {
            $db = $container->get('db');
            if (method_exists($db, 'close')) {
                $db->close();
            }
        }
        
        // Close external service clients if available
        if ($container->has('external_service_client')) {
            $extClient = $container->get('external_service_client');
            if (method_exists($extClient, 'close')) {
                $extClient->close();
            }
        }
        
        // Close resource manager if available
        if ($container->has('ResourceManager')) {
            $resourceManager = $container->get('ResourceManager');
            if (method_exists($resourceManager, 'shutdown')) {
                $resourceManager->shutdown();
            }
        }
        
        $logger->info('Connections cleaned up');
    } catch (Exception $e) {
        $logger->warning('Error during connection cleanup: ' . $e->getMessage());
    }
}

function initiateGracefulShutdown(int $signal): void {
    global $isShuttingDown, $activeRequestCount, $logger, $startShutdownTime, $socket, $container;
    
    if ($isShuttingDown) {
        return; // Already shutting down
    }
    
    $signalNames = [
        SIGTERM => 'SIGTERM',
        SIGINT => 'SIGINT'
    ];
    $signalName = $signalNames[$signal] ?? 'UNKNOWN';
    
    $logger->info('Shutdown signal received', [
        'signal' => $signal,
        'signal_name' => $signalName
    ]);
    
    $isShuttingDown = true;
    $startShutdownTime = microtime(true);
    $timeout = getConfiguredShutdownTimeout();
    
    $logger->info('Stopped accepting new requests');
    $logger->info('In-flight request count at shutdown start', [
        'active_requests' => $activeRequestCount
    ]);
    
    // Stop accepting new connections
    if (isset($socket) && method_exists($socket, 'close')) {
        $socket->close();
    }
    
    $loop = React\EventLoop\Loop::get();
    
    $checkShutdownComplete = function () use (&$checkShutdownComplete, $loop, $timeout, $logger) {
        global $activeRequestCount, $startShutdownTime;
        
        if ($activeRequestCount === 0) {
            // All requests completed successfully
            $duration = round(microtime(true) - $startShutdownTime, 2);
            $logger->info('All in-flight requests completed', [
                'duration_seconds' => $duration
            ]);
            
            cleanupConnections();
            
            $logger->info('Service exiting', [
                'exit_code' => 0
            ]);
            
            exit(0);
        }
        
        if (microtime(true) - $startShutdownTime >= $timeout) {
            // Grace period expired
            $logger->warning('Graceful shutdown timeout reached, forcing exit', [
                'timeout_seconds' => $timeout,
                'unfinished_requests' => $activeRequestCount
            ]);
            
            cleanupConnections();
            
            $logger->info('Service exiting', [
                'exit_code' => 1
            ]);
            
            exit(1);
        }
        
        // Check again in 100ms
        $loop->addTimer(0.1, $checkShutdownComplete);
    };
    
    $checkShutdownComplete();
}

// Middleware to track active requests and return 503 when shutting down
$app->add(function (ServerRequestInterface $request, Psr\Http\Server\RequestHandlerInterface $handler) {
    if (isServiceShuttingDown()) {
        $response = new Slim\Psr7\Response();
        $payload = json_encode([
            'error' => 'Service Unavailable',
            'message' => 'Service is shutting down gracefully'
        ]);
        $response->getBody()->write($payload);
        return $response
            ->withHeader('Content-Type', 'application/json')
            ->withHeader('Retry-After', (string)getConfiguredShutdownTimeout())
            ->withHeader('Connection', 'close')
            ->withStatus(503);
    }
    
    incrementActiveRequestCounter();
    try {
        $response = $handler->handle($request);
        return $response;
    } finally {
        decrementActiveRequestCounter();
    }
});

/* workaround for non-async batch processors */
if (($tracerProvider = Globals::tracerProvider()) instanceof TracerProviderInterface) {
    Loop::addPeriodicTimer(Configuration::getInt(Variables::OTEL_BSP_SCHEDULE_DELAY)/1000, function() use ($tracerProvider) {
        $tracerProvider->forceFlush();
    });
}
if (($loggerProvider = Globals::loggerProvider()) instanceof LoggerProviderInterface) {
    Loop::addPeriodicTimer(Configuration::getInt(Variables::OTEL_BLRP_SCHEDULE_DELAY)/1000, function() use ($loggerProvider) {
        $loggerProvider->forceFlush();
    });
}
if (($meterProvider = Globals::meterProvider()) instanceof MeterProviderInterface) {
    Loop::addPeriodicTimer(Configuration::getInt(Variables::OTEL_METRIC_EXPORT_INTERVAL)/1000, function() use ($meterProvider) {
        $meterProvider->forceFlush();
    });
}

$server = new HttpServer(function (ServerRequestInterface $request) use ($app) {
    $response = $app->handle($request);
    echo sprintf('[%s] "%s %s HTTP/%s" %d %d %s',
        date('Y-m-d H:i:sP'),
        $request->getMethod(),
        $request->getUri()->getPath(),
        $request->getProtocolVersion(),
        $response->getStatusCode(),
        $response->getBody()->getSize(),
        PHP_EOL,
    );

    return $response;
});

$ip = "0.0.0.0";

$ipv6_enabled = getenv('IPV6_ENABLED');

if ($ipv6_enabled == "true") {
    $ip = "[::]";
    echo "Overwriting Localhost IP: {$ip}" . PHP_EOL;
} 

$port = getenv('QUOTE_PORT') ?: '8080';
$address = $ip . ':' . $port;

// Prepare socket context with TLS if configured
$socketContext = [];
if ($tlsCertPath) {
    $tlsContext = [
        'local_cert' => $tlsCertPath,
        'local_pk' => $tlsKeyPath,
        'verify_peer' => false,
        'allow_self_signed' => true,
    ];
    
    if ($mtlsEnabled) {
        $tlsContext['verify_peer'] = true;
        $tlsContext['verify_peer_name'] = true;
        $tlsContext['cafile'] = $mtlsCaCertPath;
        $tlsContext['verify_depth'] = 5;
    }
    
    $socketContext['ssl'] = $tlsContext;
    // Use tls:// scheme for SSL/TLS
    $address = 'tls://' . $address;
    echo "TLS enabled, serving HTTPS on: {$address}" . PHP_EOL;
} else {
    echo "Serving plain HTTP on: {$address}" . PHP_EOL;
}

$socket = new SocketServer($address, ['tcp' => $socketContext]);
$server->listen($socket);

// Register signal handlers on application start
try {
    registerGracefulShutdownHandlers();
} catch (RuntimeException $e) {
    // Graceful shutdown not supported, fall back to normal operation
    $logger->warning('Graceful shutdown unavailable: ' . $e->getMessage() . ' - service will exit immediately on termination signals');
}
