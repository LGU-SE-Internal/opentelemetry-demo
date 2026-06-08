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
    $excludedPaths = ['/health', '/healthz', '/ready', '/readiness', '/livez'];
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
$gracePeriod = (int)getenv('QUOTE_SERVICE_SHUTDOWN_GRACE_PERIOD_SECONDS') ?: 30;
$logger = $container->get(Psr\Log\LoggerInterface::class);
global $shutdownHandler;

// Middleware to track active requests
$app->add(function (ServerRequestInterface $request, Psr\Http\Server\RequestHandlerInterface $handler) use (&$activeRequestCount, &$isShuttingDown) {
    if ($isShuttingDown) {
        $response = new Slim\Psr7\Response();
        return $response->withStatus(503)->withHeader('Connection', 'close');
    }
    
    $activeRequestCount++;
    try {
        $response = $handler->handle($request);
        return $response;
    } finally {
        $activeRequestCount--;
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

// Public API functions as per interface
function stopAcceptingConnections(): void {
    global $socket;
    $socket->close();
}

function getActiveRequestCount(): int {
    global $activeRequestCount;
    return $activeRequestCount;
}

// Now that socket exists, we can pass it to shutdown handler
$shutdownHandler = function () use (&$isShuttingDown, &$activeRequestCount, $gracePeriod, $logger, $socket, $container, $server) {
    if ($isShuttingDown) {
        return; // Already shutting down
    }
    
    $isShuttingDown = true;
    $logger->info('shutdown.initiated', ['grace_period_seconds' => $gracePeriod]);
    
    // Stop accepting new connections
    stopAcceptingConnections();
    
    $loop = React\EventLoop\Loop::get();
    $startTime = time();
    
    $checkComplete = function () use (&$activeRequestCount, &$checkComplete, $loop, $startTime, $gracePeriod, $logger, $container) {
        if ($activeRequestCount === 0) {
            // All requests completed successfully
            $logger->info('shutdown.completed');
            
            // Close all resources
            $resourceManager = $container->get('ResourceManager');
            $resourceManager->shutdown();
            $logger->info('shutdown.resources_closed');
            
            exit(0);
        }
        
        if (time() - $startTime >= $gracePeriod) {
            // Grace period expired
            $logger->warning('shutdown.timeout', ['dropped_requests' => $activeRequestCount]);
            
            // Close all resources
            $resourceManager = $container->get('ResourceManager');
            $resourceManager->shutdown();
            $logger->info('shutdown.resources_closed');
            
            exit(1);
        }
        
        // Check again in 100ms
        $loop->addTimer(0.1, $checkComplete);
    };
    
    $checkComplete();
};

// Public signal handler functions
function handleSigInt() {
    global $shutdownHandler;
    $shutdownHandler();
}

function handleSigTerm() {
    global $shutdownHandler;
    $shutdownHandler();
}

Loop::get()->addSignal(SIGTERM, 'handleSigTerm');
Loop::get()->addSignal(SIGINT, 'handleSigInt');
