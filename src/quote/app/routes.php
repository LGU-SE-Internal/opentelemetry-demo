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
            'status' => 'healthy',
            'service' => 'quote',
            'timestamp' => time(),
        ]);
        $response->getBody()->write($payload);
        
        return $response
            ->withHeader('Content-Type', 'application/json')
            ->withStatus(200);
    });
    
    $app->get('/ready', function (Request $request, Response $response) {
        // For quote service, all initialization completes before server starts
        // So service is always ready when endpoints are reachable
        $payload = json_encode([
            'status' => 'ready',
            'service' => 'quote',
            'timestamp' => time(),
        ]);
        $response->getBody()->write($payload);
        
        return $response
            ->withHeader('Content-Type', 'application/json')
            ->withStatus(200);
    });

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
};
