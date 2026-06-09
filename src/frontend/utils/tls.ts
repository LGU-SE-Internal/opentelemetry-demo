import { createServer, Server as HttpServer } from 'http';
import { createServer as createHttpsServer, Server as HttpsServer, ServerOptions } from 'https';
import next, { NextServer } from 'next';
import { readFileSync, existsSync, accessSync, constants } from 'fs';

// Exact interface definitions from spec
export enum TlsConfigError {
  MISSING_CERT_KEY = "TLS enabled but cert or key path not provided",
  MISSING_CA_CERT = "mTLS enabled but CA cert path not provided",
  TLS_DISABLED_WITH_MTLS = "mTLS enabled but TLS is disabled"
}
export type FileReadError = { code: "FILE_NOT_FOUND" | "PERMISSION_DENIED"; path: string };
export type CertificateValidationError = { code: "INVALID_CERTIFICATE" | "INVALID_PRIVATE_KEY" | "INVALID_CA_CERT"; message: string };

export interface TlsConfig {
  enabled: boolean;
  certPath?: string;
  keyPath?: string;
  mtlsEnabled: boolean;
  caCertPath?: string;
}

export interface ServerStartupResult {
  success: boolean;
  server: NextServer | null;
  error?: TlsConfigError | FileReadError | CertificateValidationError;
}

async function readAndValidateFile(path: string): Promise<Buffer | FileReadError> {
  try {
    if (!existsSync(path)) {
      return { code: "FILE_NOT_FOUND", path };
    }
    accessSync(path, constants.R_OK);
    return readFileSync(path);
  } catch (err: any) {
    if (err.code === 'EACCES') {
      return { code: "PERMISSION_DENIED", path };
    }
    return { code: "FILE_NOT_FOUND", path };
  }
}

export async function startServerWithTls(config: TlsConfig, port: number = parseInt(process.env.FRONTEND_PORT || '8080', 10)): Promise<ServerStartupResult> {
  // Validate configuration first
  if (config.mtlsEnabled && !config.enabled) {
    return { success: false, server: null, error: TlsConfigError.TLS_DISABLED_WITH_MTLS };
  }

  if (config.enabled) {
    if (!config.certPath || !config.keyPath) {
      return { success: false, server: null, error: TlsConfigError.MISSING_CERT_KEY };
    }

    if (config.mtlsEnabled && !config.caCertPath) {
      return { success: false, server: null, error: TlsConfigError.MISSING_CA_CERT };
    }

    // Read certificate files
    const certResult = await readAndValidateFile(config.certPath);
    if ('code' in certResult) {
      return { success: false, server: null, error: certResult };
    }

    const keyResult = await readAndValidateFile(config.keyPath);
    if ('code' in keyResult) {
      return { success: false, server: null, error: keyResult };
    }

    let caCert: Buffer | undefined;
    if (config.mtlsEnabled) {
      const caResult = await readAndValidateFile(config.caCertPath!);
      if ('code' in caResult) {
        return { success: false, server: null, error: caResult };
      }
      caCert = caResult;
    }

    // Initialize Next.js app
    const app = next({ dev: process.env.NODE_ENV !== 'production' });
    const handle = app.getRequestHandler();

    try {
      await app.prepare();

      const httpsOptions: ServerOptions = {
        cert: certResult,
        key: keyResult,
      };

      if (config.mtlsEnabled) {
        httpsOptions.ca = caCert;
        httpsOptions.requestCert = true;
        httpsOptions.rejectUnauthorized = true;
      }

      const server = createHttpsServer(httpsOptions, (req, res) => {
        handle(req, res);
      });

      await new Promise<void>((resolve, reject) => {
        server.listen(port, (err?: Error) => {
          if (err) reject(err);
          resolve();
        });
      });

      // Attach server instance to app for testing access
      (app as any).server = server;

      return { success: true, server: app };
    } catch (err: any) {
      if (err.message.includes('PEM') || err.message.includes('certificate') || err.message.includes('key')) {
        let code: "INVALID_CERTIFICATE" | "INVALID_PRIVATE_KEY" | "INVALID_CA_CERT" = "INVALID_CERTIFICATE";
        if (err.message.includes('key')) {
          code = "INVALID_PRIVATE_KEY";
        } else if (err.message.includes('CA')) {
          code = "INVALID_CA_CERT";
        }
        return { success: false, server: null, error: { code, message: err.message } };
      }
      throw err;
    }
  } else {
    // Default HTTP server
    const app = next({ dev: process.env.NODE_ENV !== 'production' });
    const handle = app.getRequestHandler();

    await app.prepare();

    const server = createServer((req, res) => {
      handle(req, res);
    });

    await new Promise<void>((resolve, reject) => {
      server.listen(port, (err?: Error) => {
        if (err) reject(err);
        resolve();
      });
    });

    // Attach server instance to app for testing access
    (app as any).server = server;

    return { success: true, server: app };
  }
}

// Function to load TLS config from environment variables
export function loadTlsConfigFromEnv(): TlsConfig {
  return {
    enabled: process.env.FRONTEND_TLS_ENABLED === 'true',
    certPath: process.env.FRONTEND_TLS_CERT_PATH,
    keyPath: process.env.FRONTEND_TLS_KEY_PATH,
    mtlsEnabled: process.env.FRONTEND_TLS_MTLS_ENABLED === 'true',
    caCertPath: process.env.FRONTEND_TLS_CA_CERT_PATH,
  };
}
