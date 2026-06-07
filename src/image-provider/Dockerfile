# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

FROM docker.io/nginxinc/nginx-unprivileged:1.29.0-alpine3.22-otel@sha256:5a41b6424e817a6c97c057e4be7fb8fdc19ec95845c784487dee1fa795ef4d03

USER 101

COPY src/image-provider/static/ /static/
COPY src/image-provider/nginx.conf.template /nginx.conf.template

EXPOSE ${IMAGE_PROVIDER_PORT}
EXPOSE 8443

STOPSIGNAL SIGQUIT

# Start nginx
CMD ["/bin/sh" , "-c" , "\
  # Generate nginx.conf from template with conditional TLS support\n\
  envsubst '$OTEL_COLLECTOR_HOST $IMAGE_PROVIDER_PORT $OTEL_COLLECTOR_PORT_GRPC $OTEL_SERVICE_NAME $NGINX_RATE_LIMIT_RPS $IMAGE_PROVIDER_TLS_CERT_PATH $IMAGE_PROVIDER_TLS_KEY_PATH $IMAGE_PROVIDER_TLS_CA_CERT_PATH' < /nginx.conf.template > /etc/nginx/nginx.conf && \n\
  exec nginx -g 'daemon off;'\n"]
