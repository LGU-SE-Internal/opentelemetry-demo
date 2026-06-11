const grpc = require('@grpc/grpc-js');
const fs = require('fs');
const path = require('path');

// Environment variable names
const ENV_VARS = {
  TLS_ENABLED: 'CURRENCY_SERVICE_TLS_ENABLED',
  TLS_CERT_PATH: 'CURRENCY_SERVICE_TLS_CERT_PATH',
  TLS_KEY_PATH: 'CURRENCY_SERVICE_TLS_KEY_PATH',
  MTLS_ENABLED: 'CURRENCY_SERVICE_MTLS_ENABLED',
  MTLS_CA_CERT_PATH: 'CURRENCY_SERVICE_MTLS_CA_CERT_PATH'
};

// Validate configuration
function validateConfig() {
  const tlsEnabled = process.env[ENV_VARS.TLS_ENABLED] === 'true';
  const mtlsEnabled = process.env[ENV_VARS.MTLS_ENABLED] === 'true';
  
  // Check if mTLS is enabled without TLS
  if (mtlsEnabled && !tlsEnabled) {
    console.error('CONFIGURATION_ERROR: MTLS_ENABLED requires TLS_ENABLED to be true');
    process.exit(1);
  }
  
  if (tlsEnabled) {
    const certPath = process.env[ENV_VARS.TLS_CERT_PATH];
    const keyPath = process.env[ENV_VARS.TLS_KEY_PATH];
    
    // Check required TLS paths are present
    if (!certPath || certPath.trim() === '') {
      console.error('CONFIGURATION_ERROR: TLS_CERT_PATH is required when TLS_ENABLED is true');
      process.exit(1);
    }
    if (!keyPath || keyPath.trim() === '') {
      console.error('CONFIGURATION_ERROR: TLS_KEY_PATH is required when TLS_ENABLED is true');
      process.exit(1);
    }
    
    // Check files exist and are readable
    [certPath, keyPath].forEach(filePath => {
      try {
        fs.accessSync(filePath, fs.constants.F_OK);
      } catch (e) {
        console.error(`FILE_NOT_FOUND_ERROR: ${filePath} does not exist`);
        process.exit(1);
      }
      try {
        fs.accessSync(filePath, fs.constants.R_OK);
      } catch (e) {
        console.error(`PERMISSION_DENIED_ERROR: ${filePath} is not readable`);
        process.exit(1);
      }
    });
    
    // Check mTLS config if enabled
    if (mtlsEnabled) {
      const caCertPath = process.env[ENV_VARS.MTLS_CA_CERT_PATH];
      if (!caCertPath || caCertPath.trim() === '') {
        console.error('CONFIGURATION_ERROR: MTLS_CA_CERT_PATH is required when MTLS_ENABLED is true');
        process.exit(1);
      }
      try {
        fs.accessSync(caCertPath, fs.constants.F_OK);
      } catch (e) {
        console.error(`FILE_NOT_FOUND_ERROR: ${caCertPath} does not exist`);
        process.exit(1);
      }
      try {
        fs.accessSync(caCertPath, fs.constants.R_OK);
      } catch (e) {
        console.error(`PERMISSION_DENIED_ERROR: ${caCertPath} is not readable`);
        process.exit(1);
      }
    }
  }
}

// Create gRPC server credentials based on config
function createServerCredentials() {
  const tlsEnabled = process.env[ENV_VARS.TLS_ENABLED] === 'true';
  const mtlsEnabled = process.env[ENV_VARS.MTLS_ENABLED] === 'true';
  
  if (!tlsEnabled) {
    return grpc.ServerCredentials.createInsecure();
  }
  
  const certPath = process.env[ENV_VARS.TLS_CERT_PATH];
  const keyPath = process.env[ENV_VARS.TLS_KEY_PATH];
  
  let certChain, privateKey;
  try {
    certChain = fs.readFileSync(certPath);
    privateKey = fs.readFileSync(keyPath);
  } catch (e) {
    console.error(`INVALID_CERTIFICATE_ERROR: Failed to load ${e.path}`);
    process.exit(1);
  }
  
  if (!mtlsEnabled) {
    return grpc.ServerCredentials.createSsl(
      undefined,
      [{ cert_chain: certChain, private_key: privateKey }],
      false
    );
  }
  
  // mTLS enabled case
  const caCertPath = process.env[ENV_VARS.MTLS_CA_CERT_PATH];
  let caCert;
  try {
    caCert = fs.readFileSync(caCertPath);
  } catch (e) {
    console.error(`INVALID_CERTIFICATE_ERROR: Failed to load ${e.path}`);
    process.exit(1);
  }
  
  return grpc.ServerCredentials.createSsl(
    caCert,
    [{ cert_chain: certChain, private_key: privateKey }],
    true
  );
}

// Initialize and start server
async function main() {
  validateConfig();
  
  const server = new grpc.Server();
  
  // TODO: Add actual currency service implementation here
  // For testing purposes, we just need the server to bind successfully
  const port = process.env.PORT || '7000';
  const credentials = createServerCredentials();
  
  server.bindAsync(`0.0.0.0:${port}`, credentials, (err, boundPort) => {
    if (err) {
      console.error(`Server failed to bind: ${err.message}`);
      process.exit(1);
    }
    server.start();
    console.log(`Currency service running on port ${boundPort}`);
  });
}

main().catch(err => {
  console.error(`Unexpected error: ${err.message}`);
  process.exit(1);
});
