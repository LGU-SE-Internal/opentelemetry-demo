#!/bin/bash
set -euo pipefail

# Test AC-1: Deployment manifest exists at correct path
test_ac1_deployment_exists() {
    echo "Running test_ac1_deployment_exists..."
    if [ ! -f "./src/shipping/k8s/deployment.yaml" ]; then
        echo "FAIL: Deployment manifest missing at ./src/shipping/k8s/deployment.yaml"
        exit 1
    fi
    echo "PASS: AC-1 verified"
}

# Test AC-2: Deployment has correct resource requests and limits
test_ac2_resource_requirements() {
    echo "Running test_ac2_resource_requirements..."
    DEPLOY_FILE="./src/shipping/k8s/deployment.yaml"
    # Check CPU request 100m
    if ! yq e '.spec.template.spec.containers[0].resources.requests.cpu' "$DEPLOY_FILE" | grep -q "100m"; then
        echo "FAIL: CPU request is not 100m"
        exit 1
    fi
    # Check CPU limit 500m
    if ! yq e '.spec.template.spec.containers[0].resources.limits.cpu' "$DEPLOY_FILE" | grep -q "500m"; then
        echo "FAIL: CPU limit is not 500m"
        exit 1
    fi
    # Check memory request 128Mi
    if ! yq e '.spec.template.spec.containers[0].resources.requests.memory' "$DEPLOY_FILE" | grep -q "128Mi"; then
        echo "FAIL: Memory request is not 128Mi"
        exit 1
    fi
    # Check memory limit 256Mi
    if ! yq e '.spec.template.spec.containers[0].resources.limits.memory' "$DEPLOY_FILE" | grep -q "256Mi"; then
        echo "FAIL: Memory limit is not 256Mi"
        exit 1
    fi
    echo "PASS: AC-2 verified"
}

# Test AC-3: livenessProbe is correctly configured
test_ac3_liveness_probe() {
    echo "Running test_ac3_liveness_probe..."
    DEPLOY_FILE="./src/shipping/k8s/deployment.yaml"
    # Check httpGet path /healthz
    if ! yq e '.spec.template.spec.containers[0].livenessProbe.httpGet.path' "$DEPLOY_FILE" | grep -q "/healthz"; then
        echo "FAIL: livenessProbe path is not /healthz"
        exit 1
    fi
    # Check port 8080
    if ! yq e '.spec.template.spec.containers[0].livenessProbe.httpGet.port' "$DEPLOY_FILE" | grep -q "8080"; then
        echo "FAIL: livenessProbe port is not 8080"
        exit 1
    fi
    # Check initialDelaySeconds 5
    if ! yq e '.spec.template.spec.containers[0].livenessProbe.initialDelaySeconds' "$DEPLOY_FILE" | grep -q "5"; then
        echo "FAIL: livenessProbe initialDelaySeconds is not 5"
        exit 1
    fi
    # Check periodSeconds 10
    if ! yq e '.spec.template.spec.containers[0].livenessProbe.periodSeconds' "$DEPLOY_FILE" | grep -q "10"; then
        echo "FAIL: livenessProbe periodSeconds is not 10"
        exit 1
    fi
    # Check timeoutSeconds 1
    if ! yq e '.spec.template.spec.containers[0].livenessProbe.timeoutSeconds' "$DEPLOY_FILE" | grep -q "1"; then
        echo "FAIL: livenessProbe timeoutSeconds is not 1"
        exit 1
    fi
    # Check failureThreshold 3
    if ! yq e '.spec.template.spec.containers[0].livenessProbe.failureThreshold' "$DEPLOY_FILE" | grep -q "3"; then
        echo "FAIL: livenessProbe failureThreshold is not 3"
        exit 1
    fi
    echo "PASS: AC-3 verified"
}

# Test AC-4: readinessProbe matches livenessProbe configuration
test_ac4_readiness_probe() {
    echo "Running test_ac4_readiness_probe..."
    DEPLOY_FILE="./src/shipping/k8s/deployment.yaml"
    # Compare liveness and readiness probes are identical
    LIVENESS=$(yq e '.spec.template.spec.containers[0].livenessProbe' "$DEPLOY_FILE")
    READINESS=$(yq e '.spec.template.spec.containers[0].readinessProbe' "$DEPLOY_FILE")
    if [ "$LIVENESS" != "$READINESS" ]; then
        echo "FAIL: readinessProbe does not match livenessProbe"
        exit 1
    fi
    echo "PASS: AC-4 verified"
}

# Test AC-5: Security context is least privilege
test_ac5_security_context() {
    echo "Running test_ac5_security_context..."
    DEPLOY_FILE="./src/shipping/k8s/deployment.yaml"
    # Check runAsNonRoot: true
    if ! yq e '.spec.template.spec.securityContext.runAsNonRoot' "$DEPLOY_FILE" | grep -q "true"; then
        echo "FAIL: runAsNonRoot is not true"
        exit 1
    fi
    # Check runAsUser: 10001
    if ! yq e '.spec.template.spec.securityContext.runAsUser' "$DEPLOY_FILE" | grep -q "10001"; then
        echo "FAIL: runAsUser is not 10001"
        exit 1
    fi
    # Check allowPrivilegeEscalation: false
    if ! yq e '.spec.template.spec.containers[0].securityContext.allowPrivilegeEscalation' "$DEPLOY_FILE" | grep -q "false"; then
        echo "FAIL: allowPrivilegeEscalation is not false"
        exit 1
    fi
    # Check readOnlyRootFilesystem: true
    if ! yq e '.spec.template.spec.containers[0].securityContext.readOnlyRootFilesystem' "$DEPLOY_FILE" | grep -q "true"; then
        echo "FAIL: readOnlyRootFilesystem is not true"
        exit 1
    fi
    # Check capabilities drop ALL
    if ! yq e '.spec.template.spec.containers[0].securityContext.capabilities.drop[]' "$DEPLOY_FILE" | grep -q "ALL"; then
        echo "FAIL: capabilities do not drop ALL"
        exit 1
    fi
    echo "PASS: AC-5 verified"
}

# Test AC-6: Correct labels and annotations for observability
test_ac6_observability_labels_annotations() {
    echo "Running test_ac6_observability_labels_annotations..."
    DEPLOY_FILE="./src/shipping/k8s/deployment.yaml"
    # Check labels
    if ! yq e '.metadata.labels["app.kubernetes.io/name"]' "$DEPLOY_FILE" | grep -q "shipping-service"; then
        echo "FAIL: Label app.kubernetes.io/name missing/incorrect"
        exit 1
    fi
    if ! yq e '.metadata.labels["app.kubernetes.io/component"]' "$DEPLOY_FILE" | grep -q "backend"; then
        echo "FAIL: Label app.kubernetes.io/component missing/incorrect"
        exit 1
    fi
    if ! yq e '.metadata.labels["app.kubernetes.io/part-of"]' "$DEPLOY_FILE" | grep -q "opentelemetry-demo"; then
        echo "FAIL: Label app.kubernetes.io/part-of missing/incorrect"
        exit 1
    fi
    # Check annotations
    if ! yq e '.metadata.annotations["prometheus.io/scrape"]' "$DEPLOY_FILE" | grep -q "true"; then
        echo "FAIL: Annotation prometheus.io/scrape missing/incorrect"
        exit 1
    fi
    if ! yq e '.metadata.annotations["prometheus.io/port"]' "$DEPLOY_FILE" | grep -q "8080"; then
        echo "FAIL: Annotation prometheus.io/port missing/incorrect"
        exit 1
    fi
    if ! yq e '.metadata.annotations["prometheus.io/path"]' "$DEPLOY_FILE" | grep -q "/metrics"; then
        echo "FAIL: Annotation prometheus.io/path missing/incorrect"
        exit 1
    fi
    echo "PASS: AC-6 verified"
}

# Test AC-7: Service manifest exists at correct path
test_ac7_service_exists() {
    echo "Running test_ac7_service_exists..."
    if [ ! -f "./src/shipping/k8s/service.yaml" ]; then
        echo "FAIL: Service manifest missing at ./src/shipping/k8s/service.yaml"
        exit 1
    fi
    echo "PASS: AC-7 verified"
}

# Test AC-8: Service is correctly configured ClusterIP with correct selector and ports
test_ac8_service_configuration() {
    echo "Running test_ac8_service_configuration..."
    SERVICE_FILE="./src/shipping/k8s/service.yaml"
    # Check type ClusterIP
    if ! yq e '.spec.type' "$SERVICE_FILE" | grep -q "ClusterIP"; then
        echo "FAIL: Service type is not ClusterIP"
        exit 1
    fi
    # Check selector matches app.kubernetes.io/name: shipping-service
    if ! yq e '.spec.selector["app.kubernetes.io/name"]' "$SERVICE_FILE" | grep -q "shipping-service"; then
        echo "FAIL: Service selector does not match deployment label"
        exit 1
    fi
    # Check service port 80 targets container port 8080
    if ! yq e '.spec.ports[0].port' "$SERVICE_FILE" | grep -q "80"; then
        echo "FAIL: Service port is not 80"
        exit 1
    fi
    if ! yq e '.spec.ports[0].targetPort' "$SERVICE_FILE" | grep -q "8080"; then
        echo "FAIL: Service targetPort is not 8080"
        exit 1
    fi
    echo "PASS: AC-8 verified"
}

# Test AC-9: Manifests pass kubectl dry-run validation
test_ac9_dry_run_validation() {
    echo "Running test_ac9_dry_run_validation..."
    if ! kubectl apply -f ./src/shipping/k8s/ --dry-run=server 2>/dev/null; then
        echo "FAIL: Kubernetes dry-run validation failed"
        exit 1
    fi
    echo "PASS: AC-9 verified"
}

# Run all tests
echo "Running K8s manifest tests for shipping service..."
test_ac1_deployment_exists
test_ac2_resource_requirements
test_ac3_liveness_probe
test_ac4_readiness_probe
test_ac5_security_context
test_ac6_observability_labels_annotations
test_ac7_service_exists
test_ac8_service_configuration
test_ac9_dry_run_validation
echo "All tests passed successfully!"
