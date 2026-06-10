# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM docker.io/nginxinc/nginx-unprivileged:1.29.0-alpine3.22-otel@sha256:5a41b6424e817a6c97c057e4be7fb8fdc19ec95845c784487dee1fa795ef4d03

USER root

# Create SSL directory with correct permissions for nginx user
RUN mkdir -p /etc/nginx/ssl && \
    chown -R nginx:nginx /etc/nginx/ssl && \
    chmod 750 /etc/nginx/ssl

USER 101

COPY src/image-provider/static/ /static/
COPY src/image-provider/nginx.conf.template /nginx.conf.template

EXPOSE 8080
EXPOSE 8443

STOPSIGNAL SIGTERM

# Start nginx
CMD ["/bin/sh" , "-c" , "\
  # Handle TLS and mTLS environment variables\n\
  if [ \"${TLS_ENABLED:-false}\" = \"true\" ]; then\n\
    # Check required TLS files exist\n\
    if [ -z \"${TLS_CERT_PATH}\" ] || [ ! -f \"${TLS_CERT_PATH}\" ]; then\n\
      echo \"ERROR: TLS enabled but missing certificate file at ${TLS_CERT_PATH}\" >&2\n\
      exit 1\n\
    fi\n\
    if [ -z \"${TLS_KEY_PATH}\" ] || [ ! -f \"${TLS_KEY_PATH}\" ]; then\n\
      echo \"ERROR: TLS enabled but missing private key file at ${TLS_KEY_PATH}\" >&2\n\
      exit 1\n\
    fi\n\
    # Handle mTLS if enabled\n\
    if [ \"${MTLS_ENABLED:-false}\" = \"true\" ]; then\n\
      if [ -z \"${MTLS_CA_CERT_PATH}\" ] || [ ! -f \"${MTLS_CA_CERT_PATH}\" ]; then\n\
        echo \"ERROR: mTLS enabled but missing CA bundle file at ${MTLS_CA_CERT_PATH}\" >&2\n\
        exit 1\n\
      fi\n\
    fi\n\
  fi\n\
  # Generate nginx.conf from template with conditional TLS support\n\
  envsubst '$OTEL_COLLECTOR_HOST $OTEL_COLLECTOR_PORT_GRPC $OTEL_SERVICE_NAME $NGINX_RATE_LIMIT_RPS $TLS_ENABLED $TLS_CERT_PATH $TLS_KEY_PATH $MTLS_ENABLED $MTLS_CA_CERT_PATH $TLS_MIN_VERSION $TLS_CIPHER_SUITES' < /nginx.conf.template > /etc/nginx/nginx.conf && \n\
  exec nginx -g 'daemon off;'\n"]
