#!/bin/sh

set -e

# Load base prometheus config
BASE_CONFIG_PATH="/etc/prometheus/prometheus-base.yaml"
GENERATED_CONFIG_PATH="/etc/prometheus/prometheus.yml"

# Copy base config to generated config
cp "$BASE_CONFIG_PATH" "$GENERATED_CONFIG_PATH"

# Validate Prometheus server TLS configuration
if [ -n "$PROMETHEUS_TLS_CERT_PATH" ]; then
  if [ -z "$PROMETHEUS_TLS_KEY_PATH" ]; then
    echo "ERROR: PROMETHEUS_TLS_KEY_PATH is required when PROMETHEUS_TLS_CERT_PATH is set"
    exit 1
  fi
  if [ ! -f "$PROMETHEUS_TLS_CERT_PATH" ] || [ ! -r "$PROMETHEUS_TLS_CERT_PATH" ]; then
    echo "ERROR: PROMETHEUS_TLS_CERT_PATH file $PROMETHEUS_TLS_CERT_PATH does not exist or is not readable"
    exit 1
  fi
  if [ ! -f "$PROMETHEUS_TLS_KEY_PATH" ] || [ ! -r "$PROMETHEUS_TLS_KEY_PATH" ]; then
    echo "ERROR: PROMETHEUS_TLS_KEY_PATH file $PROMETHEUS_TLS_KEY_PATH does not exist or is not readable"
    exit 1
  fi

  # Create web config yaml
  cat > /etc/prometheus/web-config.yaml << EOF_INNER
tls_server_config:
  cert_file: ${PROMETHEUS_TLS_CERT_PATH}
  key_file: ${PROMETHEUS_TLS_KEY_PATH}
EOF_INNER

  # Add mTLS configuration if client CA is set
  if [ -n "$PROMETHEUS_TLS_CLIENT_CA_PATH" ]; then
    if [ ! -f "$PROMETHEUS_TLS_CLIENT_CA_PATH" ] || [ ! -r "$PROMETHEUS_TLS_CLIENT_CA_PATH" ]; then
      echo "ERROR: PROMETHEUS_TLS_CLIENT_CA_PATH file $PROMETHEUS_TLS_CLIENT_CA_PATH does not exist or is not readable"
      exit 1
    fi
    cat >> /etc/prometheus/web-config.yaml << EOF_INNER
  client_ca_file: ${PROMETHEUS_TLS_CLIENT_CA_PATH}
  client_auth_type: RequireAndVerifyClientCert
EOF_INNER
  fi

  # Add web config flag to args
  set -- "$@" --web.config.file=/etc/prometheus/web-config.yaml
fi

# Validate and template Alertmanager TLS configuration
alertmanager_tls_config=""
if [ -n "$ALERTMANAGER_TLS_CA_PATH" ]; then
  if [ ! -f "$ALERTMANAGER_TLS_CA_PATH" ] || [ ! -r "$ALERTMANAGER_TLS_CA_PATH" ]; then
    echo "ERROR: ALERTMANAGER_TLS_CA_PATH file $ALERTMANAGER_TLS_CA_PATH does not exist or is not readable"
    exit 1
  fi
  alertmanager_tls_config="${alertmanager_tls_config}  ca_file: ${ALERTMANAGER_TLS_CA_PATH}\n"
fi

if [ -n "$ALERTMANAGER_TLS_CERT_PATH" ]; then
  if [ -z "$ALERTMANAGER_TLS_KEY_PATH" ]; then
    echo "ERROR: ALERTMANAGER_TLS_KEY_PATH is required when ALERTMANAGER_TLS_CERT_PATH is set"
    exit 1
  fi
  if [ ! -f "$ALERTMANAGER_TLS_CERT_PATH" ] || [ ! -r "$ALERTMANAGER_TLS_CERT_PATH" ]; then
    echo "ERROR: ALERTMANAGER_TLS_CERT_PATH file $ALERTMANAGER_TLS_CERT_PATH does not exist or is not readable"
    exit 1
  fi
  if [ ! -f "$ALERTMANAGER_TLS_KEY_PATH" ] || [ ! -r "$ALERTMANAGER_TLS_KEY_PATH" ]; then
    echo "ERROR: ALERTMANAGER_TLS_KEY_PATH file $ALERTMANAGER_TLS_KEY_PATH does not exist or is not readable"
    exit 1
  fi
  alertmanager_tls_config="${alertmanager_tls_config}  cert_file: ${ALERTMANAGER_TLS_CERT_PATH}\n"
  alertmanager_tls_config="${alertmanager_tls_config}  key_file: ${ALERTMANAGER_TLS_KEY_PATH}\n"
fi

# Add Alertmanager TLS config to generated prometheus.yml if present
if [ -n "$alertmanager_tls_config" ]; then
  sed -i '/alertmanager_config:/a \  tls_config:\n'"$alertmanager_tls_config" "$GENERATED_CONFIG_PATH"
fi

# Validate and template Remote Write TLS configuration
remote_write_tls_config=""
if [ -n "$REMOTE_WRITE_TLS_CA_PATH" ]; then
  if [ ! -f "$REMOTE_WRITE_TLS_CA_PATH" ] || [ ! -r "$REMOTE_WRITE_TLS_CA_PATH" ]; then
    echo "ERROR: REMOTE_WRITE_TLS_CA_PATH file $REMOTE_WRITE_TLS_CA_PATH does not exist or is not readable"
    exit 1
  fi
  remote_write_tls_config="${remote_write_tls_config}    tls_config:\n"
  remote_write_tls_config="${remote_write_tls_config}      ca_file: ${REMOTE_WRITE_TLS_CA_PATH}\n"
fi

if [ -n "$REMOTE_WRITE_TLS_CERT_PATH" ]; then
  if [ -z "$REMOTE_WRITE_TLS_KEY_PATH" ]; then
    echo "ERROR: REMOTE_WRITE_TLS_KEY_PATH is required when REMOTE_WRITE_TLS_CERT_PATH is set"
    exit 1
  fi
  if [ ! -f "$REMOTE_WRITE_TLS_CERT_PATH" ] || [ ! -r "$REMOTE_WRITE_TLS_CERT_PATH" ]; then
    echo "ERROR: REMOTE_WRITE_TLS_CERT_PATH file $REMOTE_WRITE_TLS_CERT_PATH does not exist or is not readable"
    exit 1
  fi
  if [ ! -f "$REMOTE_WRITE_TLS_KEY_PATH" ] || [ ! -r "$REMOTE_WRITE_TLS_KEY_PATH" ]; then
    echo "ERROR: REMOTE_WRITE_TLS_KEY_PATH file $REMOTE_WRITE_TLS_KEY_PATH does not exist or is not readable"
    exit 1
  fi
  if [ -z "$remote_write_tls_config" ]; then
    remote_write_tls_config="${remote_write_tls_config}    tls_config:\n"
  fi
  remote_write_tls_config="${remote_write_tls_config}      cert_file: ${REMOTE_WRITE_TLS_CERT_PATH}\n"
  remote_write_tls_config="${remote_write_tls_config}      key_file: ${REMOTE_WRITE_TLS_KEY_PATH}\n"
fi

# Add Remote Write TLS config to generated prometheus.yml if present
if [ -n "$remote_write_tls_config" ]; then
  sed -i '/remote_write:/a \'"$remote_write_tls_config" "$GENERATED_CONFIG_PATH"
fi

# Run prometheus
exec /bin/prometheus --config.file="$GENERATED_CONFIG_PATH" "$@"

