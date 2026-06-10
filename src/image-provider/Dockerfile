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
COPY src/image-provider/wrapper.sh /wrapper.sh

RUN chmod +x /wrapper.sh

EXPOSE 8080
EXPOSE 8443

STOPSIGNAL SIGTERM

# Start nginx
CMD ["/wrapper.sh"]
