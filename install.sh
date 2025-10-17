helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm repo add prometheus https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add jaeger https://jaegertracing.github.io/helm-charts
helm repo add opensearch https://opensearch-project.github.io/helm-charts
helm dependency build helm-charts
helm install otel-demo helm-charts -n otel-demo  --create-namespace

helm upgrade otel-demo helm-charts -n otel-demo