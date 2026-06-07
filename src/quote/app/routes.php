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
        
        // Get request ID
        $requestId = $request->getAttribute('request_id') ?? $request->getHeaderLine('X-Request-ID') ?: uniqid('quote_', true);
        
        // Get client IP
        $xForwardedFor = $request->getHeaderLine('X-Forwarded-For');
        if (!empty($xForwardedFor)) {
            $ips = explode(',', $xForwardedFor);
            $clientIp = trim($ips[0]);
        } else {
            $serverParams = $request->getServerParams();
            $clientIp = $serverParams['REMOTE_ADDR'] ?? 'unknown';
        }
        
        // Validate JSON payload
        if ($jsonObject === null) {
            $errorMsg = 'Invalid JSON payload';
            
            $payload = json_encode([
                'error' => 'Invalid request parameter',
                'message' => $errorMsg,
                'requestId' => $requestId
            ]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }
        
        // Validate presence of numberOfItems
        if (!array_key_exists('numberOfItems', $jsonObject)) {
            $errorMsg = 'Missing required field: numberOfItems';
            
            $payload = json_encode([
                'error' => 'Invalid request parameter',
                'message' => $errorMsg,
                'requestId' => $requestId
            ]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }
        
        $numberOfItems = $jsonObject['numberOfItems'];
        $logFile = '/var/log/quote-service/validation_errors.log';
        
        // Validate numberOfItems is integer
        if (!is_int($numberOfItems)) {
            $errorMsg = 'numberOfItems must be an integer';
            $errorType = 'non-integer';
            
            // Write structured log entry
            $logEntry = json_encode([
                'client_ip' => $clientIp,
                'invalid_value' => $numberOfItems,
                'request_id' => $requestId,
                'error_type' => $errorType,
                'timestamp' => gmdate('Y-m-d\TH:i:s\Z')
            ]) . PHP_EOL;
            file_put_contents($logFile, $logEntry, FILE_APPEND | LOCK_EX);
            
            $payload = json_encode([
                'error' => 'Invalid request parameter',
                'message' => $errorMsg,
                'requestId' => $requestId
            ]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }
        
        // Validate numberOfItems is at least 1
        if ($numberOfItems < 1) {
            $errorMsg = $numberOfItems === 0 ? 'numberOfItems must be at least 1' : 'numberOfItems must be a positive integer';
            $errorType = 'negative/zero';
            
            // Write structured log entry
            $logEntry = json_encode([
                'client_ip' => $clientIp,
                'invalid_value' => $numberOfItems,
                'request_id' => $requestId,
                'error_type' => $errorType,
                'timestamp' => gmdate('Y-m-d\TH:i:s\Z')
            ]) . PHP_EOL;
            file_put_contents($logFile, $logEntry, FILE_APPEND | LOCK_EX);
            
            $payload = json_encode([
                'error' => 'Invalid request parameter',
                'message' => $errorMsg,
                'requestId' => $requestId
            ]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }
        
        // Validate numberOfItems does not exceed 1000
        if ($numberOfItems > 1000) {
            $errorMsg = 'numberOfItems cannot exceed 1000';
            $errorType = 'over_maximum';
            
            // Write structured log entry
            $logEntry = json_encode([
                'client_ip' => $clientIp,
                'invalid_value' => $numberOfItems,
                'request_id' => $requestId,
                'error_type' => $errorType,
                'timestamp' => gmdate('Y-m-d\TH:i:s\Z')
            ]) . PHP_EOL;
            file_put_contents($logFile, $logEntry, FILE_APPEND | LOCK_EX);
            
            $payload = json_encode([
                'error' => 'Invalid request parameter',
                'message' => $errorMsg,
                'requestId' => $requestId
            ]);
            $response->getBody()->write($payload);
            return $response
                ->withHeader('Content-Type', 'application/json')
                ->withStatus(400);
        }

        $data = calculateQuote($jsonObject);

        $payload = json_encode(['quote' => $data]);
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
