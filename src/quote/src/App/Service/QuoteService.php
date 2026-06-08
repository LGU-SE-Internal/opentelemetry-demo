<?php

namespace App\Service;

use App\Exception\QuoteCalculationException;
use OpenTelemetry\API\Globals;
use OpenTelemetry\API\Trace\SpanKind;
use Psr\Log\LoggerInterface;

class QuoteService
{
    private LoggerInterface $logger;

    public function __construct(LoggerInterface $logger)
    {
        $this->logger = $logger;
    }

    public function calculateQuote(int $itemCount, float $totalWeightKg, bool $forceFailure = false): float
    {
        $tracer = Globals::tracerProvider()->getTracer('manual-instrumentation');
        $childSpan = $tracer->spanBuilder('calculate-quote')
            ->setSpanKind(SpanKind::KIND_INTERNAL)
            ->startSpan();

        $childSpan->addEvent('Calculating quote');

        try {
            if ($forceFailure) {
                throw new \RuntimeException('Forced quote calculation failure for testing');
            }

            // Original calculation logic: 8.99 per item
            $costPerItem = 8.99;
            $quote = round($costPerItem * $itemCount, 2);

            $childSpan->setAttribute('demo.shipping.quote.items_count', $itemCount);
            $childSpan->setAttribute('demo.shipping.quote.cost.total', $quote);
            $childSpan->setAttribute('demo.shipping.quote.total_weight_kg', $totalWeightKg);
            $childSpan->addEvent('Quote calculated, returning its value');

            // Metrics
            static $counter;
            $counter ??= Globals::meterProvider()
                ->getMeter('quotes')
                ->createCounter('quotes', 'quotes', 'number of quotes calculated');
            $counter->add(1, ['number_of_items' => $itemCount]);

            return $quote;
        } catch (\Exception $exception) {
            $childSpan->recordException($exception);
            $childSpan->setAttribute('demo.shipping.quote.items_count', $itemCount);
            $childSpan->setAttribute('demo.shipping.quote.total_weight_kg', $totalWeightKg);

            // Log structured error
            $spanContext = $childSpan->getContext();
            $this->logger->error('Quote calculation failed', [
                'item_count' => $itemCount,
                'total_weight' => $totalWeightKg,
                'exception.message' => $exception->getMessage(),
                'exception.stack_trace' => $exception->getTraceAsString(),
                'trace.id' => $spanContext->getTraceId(),
                'span.id' => $spanContext->getSpanId(),
            ]);

            // Throw custom exception with context
            throw new QuoteCalculationException(
                $itemCount,
                $totalWeightKg,
                'Quote calculation failed',
                0,
                $exception
            );
        } finally {
            $childSpan->end();
        }
    }
}
