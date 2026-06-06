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

function calculateQuote($jsonObject): float
{
    $quote = 0.0;
    $childSpan = Globals::tracerProvider()->getTracer('manual-instrumentation')
        ->spanBuilder('calculate-quote')
        ->setSpanKind(SpanKind::KIND_INTERNAL)
        ->startSpan();
    $childSpan->addEvent('Calculating quote');

    try {
        if (!array_key_exists('numberOfItems', $jsonObject)) {
            throw new \InvalidArgumentException('numberOfItems not provided');
        }
        $numberOfItems = intval($jsonObject['numberOfItems']);
        $costPerItem = 8.99;
        $quote = round($costPerItem * $numberOfItems, 2);

        $childSpan->setAttribute('demo.shipping.quote.items_count', $numberOfItems);
        $childSpan->setAttribute('demo.shipping.quote.cost.total', $quote);

        $childSpan->addEvent('Quote calculated, returning its value');

        //manual metrics
        static $counter;
        $counter ??= Globals::meterProvider()
            ->getMeter('quotes')
            ->createCounter('quotes', 'quotes', 'number of quotes calculated');
        $counter->add(1, ['number_of_items' => $numberOfItems]);
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

        $rawPayload = $request->getBody()->__toString();
        $jsonObject = $request->getParsedBody();
        
        // Validate JSON payload
        if ($jsonObject === null) {
            $errorMsg = 'Invalid JSON payload';
            // Log invalid request
            $logger->warning($errorMsg, [
                'timestamp' => date('Y-m-d H:i:sP'),
                'client_ip' => $request->getServerParams()['REMOTE_ADDR'] ?? 'unknown',
                'error_type' => 'invalid_json',
                'raw_payload' => substr($rawPayload, 0, 100) . (strlen($rawPayload) > 100 ? '...' : ''),
                'request_id' => $request->getAttribute('request_id') ?? $request->getHeaderLine('X-Request-ID') ?: 'unknown',
            ]);
            
            $payload = json_encode(['error' => $errorMsg]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }
        
        // Validate presence of numberOfItems
        if (!array_key_exists('numberOfItems', $jsonObject)) {
            $errorMsg = 'Missing required field: numberOfItems';
            // Log invalid request
            $logger->warning($errorMsg, [
                'timestamp' => date('Y-m-d H:i:sP'),
                'client_ip' => $request->getServerParams()['REMOTE_ADDR'] ?? 'unknown',
                'error_type' => 'missing_field',
                'raw_payload' => substr($rawPayload, 0, 100) . (strlen($rawPayload) > 100 ? '...' : ''),
                'request_id' => $request->getAttribute('request_id') ?? $request->getHeaderLine('X-Request-ID') ?: 'unknown',
            ]);
            
            $payload = json_encode(['error' => $errorMsg]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }
        
        $numberOfItems = $jsonObject['numberOfItems'];
        // Validate numberOfItems is integer
        if (!is_int($numberOfItems)) {
            $errorMsg = 'numberOfItems must be an integer';
            // Log invalid request
            $logger->warning($errorMsg, [
                'timestamp' => date('Y-m-d H:i:sP'),
                'client_ip' => $request->getServerParams()['REMOTE_ADDR'] ?? 'unknown',
                'error_type' => 'invalid_type',
                'raw_payload' => substr($rawPayload, 0, 100) . (strlen($rawPayload) > 100 ? '...' : ''),
                'request_id' => $request->getAttribute('request_id') ?? $request->getHeaderLine('X-Request-ID') ?: 'unknown',
                'provided_type' => gettype($numberOfItems),
                'provided_value' => $numberOfItems,
            ]);
            
            $payload = json_encode(['error' => $errorMsg]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }
        
        // Validate numberOfItems is positive
        if ($numberOfItems <= 0) {
            $errorMsg = 'numberOfItems must be greater than 0';
            // Log invalid request
            $logger->warning($errorMsg, [
                'timestamp' => date('Y-m-d H:i:sP'),
                'client_ip' => $request->getServerParams()['REMOTE_ADDR'] ?? 'unknown',
                'error_type' => 'invalid_value',
                'raw_payload' => substr($rawPayload, 0, 100) . (strlen($rawPayload) > 100 ? '...' : ''),
                'request_id' => $request->getAttribute('request_id') ?? $request->getHeaderLine('X-Request-ID') ?: 'unknown',
                'provided_value' => $numberOfItems,
            ]);
            
            $payload = json_encode(['error' => $errorMsg]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }

        $data = calculateQuote($jsonObject);

        $payload = json_encode($data);
        $response->getBody()->write($payload);

        $span->addEvent('Quote processed, response sent back', [
            'demo.shipping.quote.cost.total' => $data
        ]);
        //exported as an opentelemetry log (see dependencies.php)
        $logger->info('Calculated quote', [
            'total' => $data,
        ]);

        return $response
            ->withHeader('Content-Type', 'application/json');
    });
};
