<?php

namespace App\Service;

use App\Exception\QuoteCalculationException;
use App\Exception\InvalidConfigurationException;
use OpenTelemetry\API\Globals;
use OpenTelemetry\API\Trace\SpanKind;
use Psr\Log\LoggerInterface;

class QuoteService
{
    private LoggerInterface $logger;
    private float $baseCostPerItem;
    private float $weightSurchargePerKg;
    private float $minimumOrderCost;

    public function __construct(LoggerInterface $logger)
    {
        $this->logger = $logger;
        
        // Load and validate configuration
        $this->baseCostPerItem = $this->loadAndValidateFloat('SHIPPING_BASE_COST_PER_ITEM', 8.99);
        $this->weightSurchargePerKg = $this->loadAndValidateFloat('SHIPPING_WEIGHT_SURCHARGE_PER_KG', 0.0);
        $this->minimumOrderCost = $this->loadAndValidateFloat('SHIPPING_MINIMUM_ORDER_COST', 8.99);
    }
    
    /**
     * Load a float value from environment variable, validate it is non-negative
     * @param string $envVarName Name of the environment variable
     * @param float $defaultValue Default value if env var is not set
     * @return float Validated non-negative float value
     * @throws InvalidConfigurationException If value is non-numeric or negative
     */
    private function loadAndValidateFloat(string $envVarName, float $defaultValue): float
    {
        $envValue = getenv($envVarName);
        if ($envValue === false) {
            return $defaultValue;
        }
        
        if (!is_numeric($envValue)) {
            throw new InvalidConfigurationException(sprintf(
                'Invalid non-numeric value for %s: "%s"',
                $envVarName,
                $envValue
            ));
        }
        
        $floatValue = (float)$envValue;
        if ($floatValue < 0) {
            throw new InvalidConfigurationException(sprintf(
                'Invalid negative value for %s: %s',
                $envVarName,
                $floatValue
            ));
        }
        
        return $floatValue;
    }
    
    /**
     * Calculates total shipping cost for an order
     * @param int $numberOfItems Number of items in the order
     * @param float $totalOrderWeightKg Total weight of the order in kilograms
     * @return float Total shipping cost in USD
     * @throws InvalidConfigurationException If environment variables are invalid during service initialization
     */
    public function calculateShippingCost(int $numberOfItems, float $totalOrderWeightKg): float
    {
        $calculatedCost = ($numberOfItems * $this->baseCostPerItem) + ($totalOrderWeightKg * $this->weightSurchargePerKg);
        $totalCost = max($calculatedCost, $this->minimumOrderCost);
        return round($totalCost, 2);
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

            // Use new dynamic configuration calculation
            $quote = $this->calculateShippingCost($itemCount, $totalWeightKg);

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
