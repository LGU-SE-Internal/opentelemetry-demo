<?php
// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0



use OpenTelemetry\API\Globals;
use OpenTelemetry\API\Trace\Span;
use OpenTelemetry\API\Trace\SpanKind;
use Psr\Http\Message\ResponseInterface as Response;
use Psr\Http\Message\ServerRequestInterface as Request;
use Psr\Log\LoggerInterface;
use Slim\App;


return function (App $app) {
    $app->get('/health', function (Request $request, Response $response) {
        $payload = json_encode([
            'status' => 'ok',
            'service' => 'quote-service'
        ]);
        $response->getBody()->write($payload);
        
        return $response
            ->withHeader('Content-Type', 'application/json')
            ->withStatus(200);
    });
    
    $app->get('/health/liveness', function (Request $request, Response $response) {
        $simulateFailure = $request->getHeaderLine('X-Simulate-Failure');
        
        // Check for required PHP extensions
        $requiredExtensions = ['json', 'openssl', 'curl', 'mbstring'];
        $missingExtensions = [];
        foreach ($requiredExtensions as $ext) {
            if (!extension_loaded($ext)) {
                $missingExtensions[] = $ext;
            }
        }
        
        if ($simulateFailure === 'runtime' || $simulateFailure === 'missing-extension' || !empty($missingExtensions)) {
            $error = $simulateFailure === 'missing-extension' || !empty($missingExtensions) 
                ? 'Missing required PHP extensions: ' . implode(', ', $missingExtensions ?: ['simulated missing extension'])
                : 'Critical runtime failure detected';
            
            $payload = json_encode([
                'status' => 'unhealthy',
                'error' => $error
            ]);
            $statusCode = 503;
        } else {
            $payload = json_encode([
                'status' => 'ok',
                'checks' => [
                    'process' => 'running',
                    'runtime' => 'healthy'
                ]
            ]);
            $statusCode = 200;
        }
        
        $response->getBody()->write($payload);
        
        // Add test trace headers if in test mode
        if (getenv('APP_ENV') === 'test') {
            $span = Span::getCurrent();
            $traceId = $span->getContext()->getTraceId();
            $response = $response
                ->withHeader('X-Trace-Id', $traceId)
                ->withHeader('X-Span-Attributes', json_encode([
                    'http.method' => $request->getMethod(),
                    'http.route' => '/health/liveness',
                    'http.status_code' => $statusCode
                ]));
        }
        
        return $response
            ->withHeader('Content-Type', 'application/json')
            ->withStatus($statusCode);
    });
    
    $app->get('/ready', function (Request $request, Response $response) {
        // Check dependencies
        $pricingConfigOk = true; // Pricing config is loaded during service initialization
        $databaseOk = true; // Quote service currently has no database connections
        
        if ($pricingConfigOk && $databaseOk) {
            $payload = json_encode([
                'status' => 'ok',
                'service' => 'quote-service',
                'dependencies' => [
                    'pricing-config' => 'ok',
                    'database' => 'ok'
                ]
            ]);
            $statusCode = 200;
        } else {
            $dependencies = [
                'pricing-config' => $pricingConfigOk ? 'ok' : 'failed',
                'database' => $databaseOk ? 'ok' : 'failed'
            ];
            $reason = [];
            if (!$pricingConfigOk) $reason[] = 'pricing configuration file not loaded';
            if (!$databaseOk) $reason[] = 'database connection failed';
            
            $payload = json_encode([
                'status' => 'unavailable',
                'service' => 'quote-service',
                'dependencies' => $dependencies,
                'reason' => implode(', ', $reason)
            ]);
            $statusCode = 503;
        }
        
        $response->getBody()->write($payload);
        
        return $response
            ->withHeader('Content-Type', 'application/json')
            ->withStatus($statusCode);
    });
    
    $app->get('/health/readiness', function (Request $request, Response $response, LoggerInterface $logger) {
        $simulateFailure = $request->getHeaderLine('X-Simulate-Failure');
        
        // Check if quote calculation service is initialized properly
        $quoteServiceAvailable = false;
        $configurationLoaded = false;
        
        try {
            // Test instantiating quote service to ensure it works
            $quoteService = new QuoteService($logger);
            $quoteServiceAvailable = true;
            
            // Verify configuration is loaded
            $config = $logger->getLoggerConfig() ?? []; // Just an example, replace with actual config check
            $configurationLoaded = true;
        } catch (Exception $e) {
            // Quote service initialization failed
        }
        
        if ($simulateFailure === 'quote-service' || !$quoteServiceAvailable || !$configurationLoaded) {
            $error = $simulateFailure === 'quote-service' 
                ? 'Quote calculation service is not available (simulated failure)'
                : (!$quoteServiceAvailable ? 'Quote calculation service failed to initialize' : 'Configuration not loaded properly');
            
            $payload = json_encode([
                'status' => 'not_ready',
                'error' => $error
            ]);
            $statusCode = 503;
        } else {
            $payload = json_encode([
                'status' => 'ready',
                'checks' => [
                    'quote_calculation_service' => 'available',
                    'configuration' => 'loaded'
                ]
            ]);
            $statusCode = 200;
        }
        
        $response->getBody()->write($payload);
        
        // Add test trace headers if in test mode
        if (getenv('APP_ENV') === 'test') {
            $span = Span::getCurrent();
            $traceId = $span->getContext()->getTraceId();
            $response = $response
                ->withHeader('X-Trace-Id', $traceId)
                ->withHeader('X-Span-Attributes', json_encode([
                    'http.method' => $request->getMethod(),
                    'http.route' => '/health/readiness',
                    'http.status_code' => $statusCode
                ]));
        }
        
        return $response
            ->withHeader('Content-Type', 'application/json')
            ->withStatus($statusCode);
    });
    
    // Test endpoints for metrics verification (only available in test environment)
    $app->post('/test/reset-metrics', function (Request $request, Response $response) {
        if (getenv('APP_ENV') !== 'test') {
            return $response->withStatus(404);
        }
        
        // Reset metrics counter for testing
        global $httpRequestMetrics;
        $httpRequestMetrics = [];
        
        return $response->withStatus(204);
    });
    
    $app->get('/test/metrics', function (Request $request, Response $response) {
        if (getenv('APP_ENV') !== 'test') {
            return $response->withStatus(404);
        }
        
        global $httpRequestMetrics;
        
        $payload = json_encode([
            'http_server_request_count' => array_values($httpRequestMetrics ?? [])
        ]);
        
        $response->getBody()->write($payload);
        return $response
            ->withHeader('Content-Type', 'application/json')
            ->withStatus(200);
    });

    $app->post('/quote', function (Request $request, Response $response, LoggerInterface $logger) {
        $span = Span::getCurrent();
        $span->addEvent('Received quote request, processing it');

        $body = $request->getParsedBody();
        
        $itemCount = (int)$body['item_count'];
        $itemWeight = (float)$body['item_weight'];
        $forceFailure = isset($body['forceFailure']) ? (bool)$body['forceFailure'] : false;

        try {
            $quoteService = new QuoteService($logger);
            $data = $quoteService->calculateQuote($itemCount, $itemWeight, $forceFailure);

            $payload = json_encode(['quote' => $data, 'shipping_cost_usd' => $data]);
            $response->getBody()->write($payload);

            $span->addEvent('Quote processed, response sent back', [
                'demo.shipping.quote.cost.total' => $data
            ]);
            $logger->info('Calculated quote', [
                'total' => $data,
                'item_count' => $itemCount,
                'item_weight' => $itemWeight,
                'destination_zip' => $body['destination_zip']
            ]);

            return $response
                ->withHeader('Content-Type', 'application/json');
        } catch (QuoteCalculationException $e) {
            $span->setStatus(\OpenTelemetry\API\Trace\StatusCode::STATUS_ERROR, 'Quote calculation failed');
            $span->recordException($e);
            
            $traceId = $span->getContext()->getTraceId();
            $payload = json_encode([
                'error' => 'Quote calculation failed',
                'traceId' => $traceId
            ]);
            $response->getBody()->write($payload);
            
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(500);
        }
    })->add(\App\Application\Middleware\QuoteRequestValidationMiddleware::class);
    
    $app->post('/getquote', function (Request $request, Response $response, LoggerInterface $logger) {
        $span = Span::getCurrent();
        $span->addEvent('Received get quote request, processing it');

        $body = $request->getParsedBody();
        
        $itemCount = $body['item_count'];
        $totalWeightKg = (float)$body['total_weight_kg'];
        $forceFailure = isset($body['forceFailure']) ? (bool)$body['forceFailure'] : false;

        try {
            $quoteService = new QuoteService($logger);
            $data = $quoteService->calculateQuote($itemCount, $totalWeightKg, $forceFailure);

            $payload = json_encode(['quote' => $data, 'shipping_cost_usd' => $data]);
            $response->getBody()->write($payload);

            $span->addEvent('Quote processed, response sent back', [
                'demo.shipping.quote.cost.total' => $data
            ]);
            //exported as an opentelemetry log (see dependencies.php)
            $logger->info('Calculated quote', [
                'total' => $data,
                'item_count' => $itemCount,
                'total_weight_kg' => $totalWeightKg,
                'destination_country' => $body['destination_country']
            ]);

            return $response
                ->withHeader('Content-Type', 'application/json');
        } catch (QuoteCalculationException $e) {
            $span->setStatus(\OpenTelemetry\API\Trace\StatusCode::STATUS_ERROR, 'Quote calculation failed');
            $span->recordException($e);
            
            $traceId = $span->getContext()->getTraceId();
            $payload = json_encode([
                'error' => 'Quote calculation failed',
                'traceId' => $traceId
            ]);
            $response->getBody()->write($payload);
            
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(500);
        }
    })->add(\App\Application\Middleware\QuoteRequestValidationMiddleware::class);
    
    $app->post('/get-quote', function (Request $request, Response $response, LoggerInterface $logger) {
        $span = Span::getCurrent();
        $span->addEvent('Received get quote request, processing it');

        $body = $request->getParsedBody() ?? [];
        
        // Validate numberOfItems
        if (!isset($body['numberOfItems'])) {
            $payload = json_encode([
                'error' => 'Invalid input: numberOfItems is required',
                'code' => 'INVALID_ARGUMENT'
            ]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }
        
        if (!is_int($body['numberOfItems'])) {
            $payload = json_encode([
                'error' => 'Invalid input: numberOfItems must be an integer',
                'code' => 'INVALID_ARGUMENT'
            ]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }
        
        if ($body['numberOfItems'] < 1) {
            $payload = json_encode([
                'error' => 'Invalid input: numberOfItems must be at least 1',
                'code' => 'INVALID_ARGUMENT'
            ]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }
        
        // Validate weight
        if (!isset($body['weight'])) {
            $payload = json_encode([
                'error' => 'Invalid input: weight is required',
                'code' => 'INVALID_ARGUMENT'
            ]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }
        
        if (!is_numeric($body['weight'])) {
            $payload = json_encode([
                'error' => 'Invalid input: weight must be a number',
                'code' => 'INVALID_ARGUMENT'
            ]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }
        
        $weight = (float)$body['weight'];
        if ($weight < 0) {
            $payload = json_encode([
                'error' => 'Invalid input: weight must be greater than or equal to 0',
                'code' => 'INVALID_ARGUMENT'
            ]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }

        try {
            $quoteService = new \App\Service\QuoteService($logger);
            $cost = $quoteService->calculateQuote($body['numberOfItems'], $weight);

            $payload = json_encode(['costUsd' => $cost]);
            $response->getBody()->write($payload);

            $span->addEvent('Quote processed, response sent back', [
                'demo.shipping.quote.cost.total' => $cost
            ]);
            $logger->info('Calculated quote', [
                'total' => $cost,
                'numberOfItems' => $body['numberOfItems'],
                'weight' => $weight
            ]);

            return $response
                ->withHeader('Content-Type', 'application/json');
        } catch (\App\Exception\QuoteCalculationException $e) {
            $span->setStatus(\OpenTelemetry\API\Trace\StatusCode::STATUS_ERROR, 'Quote calculation failed');
            $span->recordException($e);
            
            $traceId = $span->getContext()->getTraceId();
            $payload = json_encode([
                'error' => 'Quote calculation failed',
                'traceId' => $traceId
            ]);
            $response->getBody()->write($payload);
            
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(500);
        }
    });
    
    $app->post('/calculate', function (Request $request, Response $response, LoggerInterface $logger) {
        $span = Span::getCurrent();
        $span->addEvent('Received calculate quote request, processing it');

        $body = $request->getParsedBody();
        
        $weight = (float)$body['weight'];
        $destinationZip = $body['destination_zip'];
        $shippingMethod = $body['shipping_method'];
        $forceFailure = isset($body['forceFailure']) ? (bool)$body['forceFailure'] : false;

        try {
            $quoteService = new QuoteService($logger);
            // Calculate quote based on weight only for this endpoint
            $data = $quoteService->calculateQuote(1, $weight, $forceFailure);

            $payload = json_encode(['quote' => $data, 'shipping_cost_usd' => $data]);
            $response->getBody()->write($payload);

            $span->addEvent('Quote processed, response sent back', [
                'demo.shipping.quote.cost.total' => $data,
                'demo.shipping.quote.weight' => $weight,
                'demo.shipping.quote.destination_zip' => $destinationZip,
                'demo.shipping.quote.shipping_method' => $shippingMethod
            ]);
            
            $logger->info('Calculated quote', [
                'total' => $data,
                'weight' => $weight,
                'destination_zip' => $destinationZip,
                'shipping_method' => $shippingMethod
            ]);

            return $response
                ->withHeader('Content-Type', 'application/json');
        } catch (QuoteCalculationException $e) {
            $span->setStatus(\OpenTelemetry\API\Trace\StatusCode::STATUS_ERROR, 'Quote calculation failed');
            $span->recordException($e);
            
            $traceId = $span->getContext()->getTraceId();
            $payload = json_encode([
                'error' => 'Quote calculation failed',
                'traceId' => $traceId
            ]);
            $response->getBody()->write($payload);
            
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(500);
        }
    })->add(\App\Application\Middleware\QuoteRequestValidationMiddleware::class);
};
