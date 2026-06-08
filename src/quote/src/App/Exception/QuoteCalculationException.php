<?php

namespace App\Exception;

class QuoteCalculationException extends \RuntimeException
{
    private int $itemCount;
    private float $totalWeight;

    public function __construct(int $itemCount, float $totalWeight, string $message = "", int $code = 0, ?\Throwable $previous = null)
    {
        parent::__construct($message, $code, $previous);
        $this->itemCount = $itemCount;
        $this->totalWeight = $totalWeight;
    }

    public function getItemCount(): int
    {
        return $this->itemCount;
    }

    public function getTotalWeight(): float
    {
        return $this->totalWeight;
    }
}
