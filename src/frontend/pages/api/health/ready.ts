import type { NextApiRequest, NextApiResponse } from 'next'
import CartGateway from '../../../gateways/rpc/Cart.gateway';
import ProductCatalogService from '../../../services/ProductCatalog.service';

type CheckStatus = 'ok' | 'failed' | 'pending';

type ReadySuccessResponse = {
  status: 'ok'
  timestamp: string
  checks: {
    backend_connections: 'ok'
    service_initialized: 'ok'
  }
}

type ReadyFailureResponse = {
  status: 'unavailable'
  timestamp: string
  checks: {
    backend_connections: CheckStatus
    service_initialized: CheckStatus
  }
  error: string
}

type ReadyResponse = ReadySuccessResponse | ReadyFailureResponse

let serviceInitialized = false;
let initializationError: string | null = null;

// Simulate initialization on first request
async function initializeService() {
  if (serviceInitialized) return;
  try {
    // Test connection to a simple endpoint to verify backend connectivity
    await ProductCatalogService.getProduct('OLJCESPC7Z', 'USD');
    serviceInitialized = true;
  } catch (err) {
    initializationError = err instanceof Error ? err.message : 'Unknown initialization error';
  }
}

export default async function handler(
  req: NextApiRequest,
  res: NextApiResponse<ReadyResponse>
) {
  const timestamp = new Date().toISOString();
  
  if (!serviceInitialized) {
    await initializeService();
  }

  if (!serviceInitialized) {
    return res.status(503).json({
      status: 'unavailable',
      timestamp,
      checks: {
        backend_connections: 'failed',
        service_initialized: 'pending'
      },
      error: initializationError || 'Service is still initializing'
    });
  }

  try {
    // Verify backend connections are still alive
    await CartGateway.getCart('test-health-check-id');
    
    return res.status(200).json({
      status: 'ok',
      timestamp,
      checks: {
        backend_connections: 'ok',
        service_initialized: 'ok'
      }
    });
  } catch (err) {
    return res.status(503).json({
      status: 'unavailable',
      timestamp,
      checks: {
        backend_connections: 'failed',
        service_initialized: 'ok'
      },
      error: err instanceof Error ? err.message : 'Backend connection failed'
    });
  }
}
