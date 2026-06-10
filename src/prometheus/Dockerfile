FROM quay.io/prometheus/prometheus:v2.47.0

COPY prometheus-config.yaml /etc/prometheus/prometheus-base.yaml
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

