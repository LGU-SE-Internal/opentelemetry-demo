#!/bin/sh

set -e

# Default values for environment variables
PROMETHEUS_TLS_ENABLED=${PROMETHEUS_TLS_ENABLED:-false}
PROMETHEUS_TLS_CERT_PATH=${PROMETHEUS_TLS_CERT_PATH:-/etc/prometheus/tls/tls.crt}
PROMETHEUS_TLS_KEY_PATH=${PROMETHEUS_TLS_KEY_PATH:-/etc/prometheus/tls/tls.key}
PROMETHEUS_MTLS_ENABLED=${PROMETHEUS_MTLS_ENABLED:-false}
PROMETHEUS_CA_CERT_PATH=${PROMETHEUS_CA_CERT_PATH:-/etc/prometheus/tls/ca.crt}

# Generate web config if TLS is enabled
if [ "$PROMETHEUS_TLS_ENABLED" = "true" ]; then
  # Check if cert and key exist
  if [ ! -f "$PROMETHEUS_TLS_CERT_PATH" ] || [ ! -f "$PROMETHEUS_TLS_KEY_PATH" ]; then
    echo "ERROR: TLS certificate or key not found at configured path"
    exit 1
  fi

  # Create web config yaml
  cat > /etc/prometheus/web-config.yaml << EOF_INNER
tls_server_config:
  cert_file: ${PROMETHEUS_TLS_CERT_PATH}
  key_file: ${PROMETHEUS_TLS_KEY_PATH}
EOF_INNER

  # Add mTLS configuration if enabled
  if [ "$PROMETHEUS_MTLS_ENABLED" = "true" ]; then
    if [ ! -f "$PROMETHEUS_CA_CERT_PATH" ]; then
      echo "ERROR: CA certificate required for mTLS not found at configured path"
      exit 1
    fi
    cat >> /etc/prometheus/web-config.yaml << EOF_INNER
  client_ca_file: ${PROMETHEUS_CA_CERT_PATH}
  client_auth_type: RequireAndVerifyClientCert
EOF_INNER
  fi

  # Add web config flag to args
  set -- "$@" --web.config.file=/etc/prometheus/web-config.yaml
fi

# Run prometheus
exec /bin/prometheus "$@"
