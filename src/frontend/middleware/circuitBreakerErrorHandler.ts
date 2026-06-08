import { NextApiRequest, NextApiResponse } from 'next';
import { CircuitOpenError } from '../utils/circuitBreaker';

export function circuitBreakerErrorHandler(
  err: unknown,
  req: NextApiRequest,
  res: NextApiResponse,
  next: (err?: unknown) => void
) {
  if (err instanceof CircuitOpenError) {
    res.status(503).json({
      error: 'Service temporarily unavailable',
      code: 'SERVICE_UNAVAILABLE',
      upstreamService: err.message.match(/service (\w+),/)?.[1] || 'unknown'
    });
    return;
  }
  next(err);
}
