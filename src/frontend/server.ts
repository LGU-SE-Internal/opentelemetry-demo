#!/usr/bin/env node
require('./Instrumentation.js');
import { startServerWithTls, loadTlsConfigFromEnv, TlsConfigError } from './utils/tls';

const port = parseInt(process.env.FRONTEND_PORT || '8080', 10);
const tlsConfig = loadTlsConfigFromEnv();

async function main() {
  const result = await startServerWithTls(tlsConfig, port);
  
  if (!result.success) {
    console.error('Server startup failed:');
    if (typeof result.error === 'string') {
      console.error(result.error);
    } else if ('code' in result.error!) {
      console.error(`${result.error.code}: ${'path' in result.error ? result.error.path : result.error.message}`);
    }
    process.exit(1);
  }

  console.log(`> Frontend server running on ${tlsConfig.enabled ? 'https' : 'http'}://localhost:${port}`);
  console.log(`> TLS enabled: ${tlsConfig.enabled}`);
  if (tlsConfig.enabled) {
    console.log(`> mTLS enabled: ${tlsConfig.mtlsEnabled}`);
  }
}

main().catch(err => {
  console.error('Unexpected error during server startup:', err);
  process.exit(1);
});
