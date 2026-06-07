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
    $excludedPaths = ['/health', '/healthz', '/ready', '/livez'];
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
Loop::get()->addSignal(SIGTERM, function() {
    exit;
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

$address = $ip . ':' . getenv('QUOTE_PORT');

$socket = new SocketServer($address);
$server->listen($socket);

echo "Listening on: {$address}" . PHP_EOL;
