#!/bin/bash
set -eo pipefail

# Helper to print config error and exit
config_error() {
    echo "CONFIG_ERROR: $1" >&2
    exit 1
}

# Check if TLS is enabled
POSTGRES_TLS_ENABLED=${POSTGRES_TLS_ENABLED:-false}
POSTGRES_TLS_MTLS_ENABLED=${POSTGRES_TLS_MTLS_ENABLED:-false}

# Validate mTLS can't be enabled without TLS
if [ "$POSTGRES_TLS_MTLS_ENABLED" = "true" ] && [ "$POSTGRES_TLS_ENABLED" = "false" ]; then
    config_error "mTLS cannot be enabled without TLS being enabled"
fi

# Validate TLS configuration if enabled
if [ "$POSTGRES_TLS_ENABLED" = "true" ]; then
    # Check required TLS variables
    if [ -z "$POSTGRES_TLS_CERT_FILE" ] || [ -z "$POSTGRES_TLS_KEY_FILE" ]; then
        config_error "POSTGRES_TLS_CERT_FILE and POSTGRES_TLS_KEY_FILE are required when POSTGRES_TLS_ENABLED is true"
    fi

    # Check cert file exists and is readable
    if [ ! -f "$POSTGRES_TLS_CERT_FILE" ] || [ ! -r "$POSTGRES_TLS_CERT_FILE" ]; then
        config_error "POSTGRES_TLS_CERT_FILE $POSTGRES_TLS_CERT_FILE is missing or unreadable"
    fi

    # Check key file exists and is readable
    if [ ! -f "$POSTGRES_TLS_KEY_FILE" ] || [ ! -r "$POSTGRES_TLS_KEY_FILE" ]; then
        config_error "POSTGRES_TLS_KEY_FILE $POSTGRES_TLS_KEY_FILE is missing or unreadable"
    fi

    # Validate certificate is valid PEM format
    if ! openssl x509 -in "$POSTGRES_TLS_CERT_FILE" -noout >/dev/null 2>&1; then
        config_error "POSTGRES_TLS_CERT_FILE $POSTGRES_TLS_CERT_FILE is not a valid PEM certificate"
    fi

    # Validate private key is valid PEM format
    if ! openssl rsa -in "$POSTGRES_TLS_KEY_FILE" -noout >/dev/null 2>&1; then
        config_error "POSTGRES_TLS_KEY_FILE $POSTGRES_TLS_KEY_FILE is not a valid PEM private key"
    fi

    # Validate mTLS configuration if enabled
    if [ "$POSTGRES_TLS_MTLS_ENABLED" = "true" ]; then
        if [ -z "$POSTGRES_TLS_CA_FILE" ]; then
            config_error "POSTGRES_TLS_CA_FILE is required when POSTGRES_TLS_MTLS_ENABLED is true"
        fi

        # Check CA file exists and is readable
        if [ ! -f "$POSTGRES_TLS_CA_FILE" ] || [ ! -r "$POSTGRES_TLS_CA_FILE" ]; then
            config_error "POSTGRES_TLS_CA_FILE $POSTGRES_TLS_CA_FILE is missing or unreadable"
        fi

        # Validate CA certificate is valid PEM format
        if ! openssl x509 -in "$POSTGRES_TLS_CA_FILE" -noout >/dev/null 2>&1; then
            config_error "POSTGRES_TLS_CA_FILE $POSTGRES_TLS_CA_FILE is not a valid PEM CA certificate"
        fi
    fi

    # Now configure postgresql.conf for TLS
    echo "Configuring PostgreSQL for TLS support"
    cat >> /var/lib/postgresql/data/postgresql.conf <<EOF_CONF
ssl = on
ssl_cert_file = '$POSTGRES_TLS_CERT_FILE'
ssl_key_file = '$POSTGRES_TLS_KEY_FILE'
EOF_CONF

    if [ "$POSTGRES_TLS_MTLS_ENABLED" = "true" ]; then
        echo "Configuring PostgreSQL for mTLS client authentication"
        cat >> /var/lib/postgresql/data/postgresql.conf <<EOF_CONF
ssl_ca_file = '$POSTGRES_TLS_CA_FILE'
EOF_CONF
        # Update pg_hba.conf to require client certs for all hostssl connections
        sed -i 's/^host\s\+all\s\+all\s\+0\.0\.0\.0\/0\s\+scram-sha-256$/hostssl all all 0.0.0.0\/0 scram-sha-256 clientcert=verify-full/' /var/lib/postgresql/data/pg_hba.conf
        sed -i 's/^host\s\+all\s\+all\s\+::1\/128\s\+scram-sha-256$/hostssl all all ::1\/128 scram-sha-256 clientcert=verify-full/' /var/lib/postgresql/data/pg_hba.conf
    else
        # Update pg_hba.conf to allow only SSL connections
        sed -i 's/^host\s\+all\s\+all\s\+0\.0\.0\.0\/0\s\+scram-sha-256$/hostssl all all 0.0.0.0\/0 scram-sha-256/' /var/lib/postgresql/data/pg_hba.conf
        sed -i 's/^host\s\+all\s\+all\s\+::1\/128\s\+scram-sha-256$/hostssl all all ::1\/128 scram-sha-256/' /var/lib/postgresql/data/pg_hba.conf
    fi
fi

# Run the original postgres entrypoint script with all arguments
exec docker-entrypoint.sh "$@"
