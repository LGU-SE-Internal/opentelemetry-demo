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

function calculateQuote(int $itemCount, float $totalWeightKg): float
{
    $quote = 0.0;
    $childSpan = Globals::tracerProvider()->getTracer('manual-instrumentation')
        ->spanBuilder('calculate-quote')
        ->setSpanKind(SpanKind::KIND_INTERNAL)
        ->startSpan();
    $childSpan->addEvent('Calculating quote');

    try {
        $costPerItem = 8.99;
        $quote = round($costPerItem * $itemCount, 2);

        $childSpan->setAttribute('demo.shipping.quote.items_count', $itemCount);
        $childSpan->setAttribute('demo.shipping.quote.cost.total', $quote);
        $childSpan->setAttribute('demo.shipping.quote.total_weight_kg', $totalWeightKg);

        $childSpan->addEvent('Quote calculated, returning its value');

        //manual metrics
        static $counter;
        $counter ??= Globals::meterProvider()
            ->getMeter('quotes')
            ->createCounter('quotes', 'quotes', 'number of quotes calculated');
        $counter->add(1, ['number_of_items' => $itemCount]);
    } catch (\Exception $exception) {
        $childSpan->recordException($exception);
    } finally {
        $childSpan->end();
        return $quote;
    }
}

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

        $data = calculateQuote($itemCount, $totalWeightKg);

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
    })->add(\App\Application\Middleware\QuoteRequestValidationMiddleware::class);
};
