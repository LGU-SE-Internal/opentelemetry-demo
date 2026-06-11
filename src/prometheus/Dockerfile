FROM quay.io/prometheus/prometheus:v2.47.0

USER root
RUN apk add --no-cache openssl
USER nobody

COPY prometheus-config.yaml /etc/prometheus/prometheus-base.yaml
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

