// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

/** @type {import('next').NextConfig} */

const dotEnv = require('dotenv');
const dotenvExpand = require('dotenv-expand');
const { resolve } = require('path');

const myEnv = dotEnv.config({
  path: resolve(__dirname, '../../.env'),
});
dotenvExpand.expand(myEnv);

const {
  AD_ADDR = '',
  CART_ADDR = '',
  CHECKOUT_ADDR = '',
  CURRENCY_ADDR = '',
  PRODUCT_CATALOG_ADDR = '',
  PRODUCT_REVIEWS_ADDR = '',
  RECOMMENDATION_ADDR = '',
  SHIPPING_ADDR = '',
  ENV_PLATFORM = '',
  OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = '',
  OTEL_SERVICE_NAME = 'frontend',
  PUBLIC_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT = '',
} = process.env;

// Validate required environment variables
const envVarSpecs = [
  { name: 'AD_ADDR', type: 'gRPC service address (host:port)', allowedValues: [] },
  { name: 'CART_ADDR', type: 'gRPC service address (host:port)', allowedValues: [] },
  { name: 'CHECKOUT_ADDR', type: 'gRPC service address (host:port)', allowedValues: [] },
  { name: 'CURRENCY_ADDR', type: 'gRPC service address (host:port)', allowedValues: [] },
  { name: 'PRODUCT_CATALOG_ADDR', type: 'gRPC service address (host:port)', allowedValues: [] },
  { name: 'PRODUCT_REVIEWS_ADDR', type: 'gRPC service address (host:port)', allowedValues: [] },
  { name: 'RECOMMENDATION_ADDR', type: 'gRPC service address (host:port)', allowedValues: [] },
  { name: 'SHIPPING_ADDR', type: 'HTTP service address (host:port)', allowedValues: [] },
];

envVarSpecs.forEach(spec => {
  const value = process.env[spec.name];
  if (!value) {
    let errorMsg = "Invalid environment variable configuration:\n";
    errorMsg += `  Variable name: ${spec.name}\n`;
    errorMsg += `  Invalid value: <not set>\n`;
    errorMsg += `  Expected: Required ${spec.type} value`;
    if (spec.allowedValues.length > 0) {
      errorMsg += `, allowed values: ${spec.allowedValues.join(', ')}`;
    }
    console.error(errorMsg);
    process.exit(1);
  }
});

const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  compiler: {
    styledComponents: true,
  },
  // Turbopack configuration (Next.js 16 default bundler)
  // Turbopack automatically handles Node.js polyfills for client bundles
  turbopack: {
    // Set root to current directory to avoid confusion with parent lockfile
    root: __dirname,
  },
  // Keep webpack config for backwards compatibility if --webpack flag is used
  webpack: (config, { isServer }) => {
    if (!isServer) {
      config.resolve.fallback.http2 = false;
      config.resolve.fallback.tls = false;
      config.resolve.fallback.net = false;
      config.resolve.fallback.dns = false;
      config.resolve.fallback.fs = false;
    }

    return config;
  },
  env: {
    AD_ADDR,
    CART_ADDR,
    CHECKOUT_ADDR,
    CURRENCY_ADDR,
    PRODUCT_CATALOG_ADDR,
    PRODUCT_REVIEWS_ADDR,
    RECOMMENDATION_ADDR,
    SHIPPING_ADDR,
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT,
    NEXT_PUBLIC_PLATFORM: ENV_PLATFORM,
    NEXT_PUBLIC_OTEL_SERVICE_NAME: OTEL_SERVICE_NAME,
    NEXT_PUBLIC_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: PUBLIC_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT,
  },
  images: {
    loader: "custom",
    loaderFile: "./utils/imageLoader.js"
  }
};

module.exports = nextConfig;
