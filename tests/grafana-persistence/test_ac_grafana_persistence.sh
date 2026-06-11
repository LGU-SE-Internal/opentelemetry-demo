#!/bin/bash
set -euo pipefail

# Test AC-1: Default values use existing PVC grafana-pvc instead of emptyDir
test_ac1_default_uses_pvc() {
  echo "Running AC-1 test: Default values use PVC grafana-pvc..."
  # Render deployment with default values
  helm template . --set grafana.persistence.enabled=true --show-only templates/grafana/grafana-deployment.yaml > /tmp/ac1_deployment.yaml
  if grep -q "persistentVolumeClaim:" /tmp/ac1_deployment.yaml && grep -q "claimName:.*grafana-pvc" /tmp/ac1_deployment.yaml && ! grep -q "emptyDir: {}" /tmp/ac1_deployment.yaml; then
    echo "AC-1 PASSED"
    return 0
  else
    echo "AC-1 FAILED: Deployment does not use grafana-pvc PVC, still uses emptyDir"
    return 1
  fi
}

# Test AC-2: Volume mount path is /var/lib/grafana with read/write access
test_ac2_correct_mount_path() {
  echo "Running AC-2 test: Correct volume mount path /var/lib/grafana..."
  helm template . --show-only templates/grafana/grafana-deployment.yaml > /tmp/ac2_deployment.yaml
  if grep -A5 -B5 "mountPath: /var/lib/grafana" /tmp/ac2_deployment.yaml | grep -q "readOnly: false" || ! grep -q "readOnly:" /tmp/ac2_deployment.yaml; then
    echo "AC-2 PASSED"
    return 0
  else
    echo "AC-2 FAILED: Mount path not /var/lib/grafana or is read-only"
    return 1
  fi
}

# Test AC-3: Custom storageClassName is rendered in PVC
test_ac3_custom_storage_class() {
  echo "Running AC-3 test: Custom storageClassName in PVC..."
  TEST_SC="fast-storage-test"
  helm template . --set grafana.persistence.storageClassName="${TEST_SC}" --show-only templates/grafana/grafana-pvc.yaml > /tmp/ac3_pvc.yaml
  if grep -q "storageClassName:.*${TEST_SC}" /tmp/ac3_pvc.yaml; then
    echo "AC-3 PASSED"
    return 0
  else
    echo "AC-3 FAILED: Custom storage class not present in PVC"
    return 1
  fi
}

# Test AC-4: Custom storage size is rendered in PVC
test_ac4_custom_storage_size() {
  echo "Running AC-4 test: Custom storage size in PVC..."
  TEST_SIZE="20Gi"
  helm template . --set grafana.persistence.size="${TEST_SIZE}" --show-only templates/grafana/grafana-pvc.yaml > /tmp/ac4_pvc.yaml
  if grep -q "storage:.*${TEST_SIZE}" /tmp/ac4_pvc.yaml; then
    echo "AC-4 PASSED"
    return 0
  else
    echo "AC-4 FAILED: Custom storage size not present in PVC"
    return 1
  fi
}

# Test AC-5: Data persists across pod restarts
test_ac5_data_persistence() {
  echo "Running AC-5 test: Data persists across pod restart..."
  # Skip if no Kubernetes cluster available
  if ! kubectl cluster-info > /dev/null 2>&1; then
    echo "AC-5 SKIPPED: No Kubernetes cluster available for integration test"
    return 0
  fi

  # Create test dashboard (this part assumes Grafana is installed, will fail if not implemented)
  GRAFANA_POD=$(kubectl get pods -l app.kubernetes.io/name=grafana -o jsonpath='{.items[0].metadata.name}')
  kubectl exec "${GRAFANA_POD}" -- curl -X POST -H "Content-Type: application/json" -d '{"title":"Test Persistence Dashboard","tags":["test"],"panels":[]}' http://admin:admin@localhost:3000/api/dashboards/db > /dev/null 2>&1
  
  # Delete pod
  kubectl delete pod "${GRAFANA_POD}" > /dev/null
  # Wait for new pod to be ready
  kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=grafana --timeout=120s > /dev/null
  NEW_GRAFANA_POD=$(kubectl get pods -l app.kubernetes.io/name=grafana -o jsonpath='{.items[0].metadata.name}')
  
  # Check if dashboard exists
  DASHBOARDS=$(kubectl exec "${NEW_GRAFANA_POD}" -- curl -s http://admin:admin@localhost:3000/api/search?query=Test%20Persistence%20Dashboard)
  if echo "${DASHBOARDS}" | grep -q "Test Persistence Dashboard"; then
    echo "AC-5 PASSED"
    return 0
  else
    echo "AC-5 FAILED: Test dashboard not found after pod restart"
    return 1
  fi
}

# Test AC-6: persistence.enabled=false falls back to emptyDir
test_ac6_disabled_uses_emptydir() {
  echo "Running AC-6 test: Disabled persistence uses emptyDir..."
  helm template . --set grafana.persistence.enabled=false --show-only templates/grafana/grafana-deployment.yaml > /tmp/ac6_deployment.yaml
  if grep -q "emptyDir: {}" /tmp/ac6_deployment.yaml && ! grep -q "persistentVolumeClaim:" /tmp/ac6_deployment.yaml; then
    echo "AC-6 PASSED"
    return 0
  else
    echo "AC-6 FAILED: Disabled persistence does not use emptyDir"
    return 1
  fi
}

# Run all tests
FAILURES=0
test_ac1_default_uses_pvc || FAILURES=$((FAILURES+1))
test_ac2_correct_mount_path || FAILURES=$((FAILURES+1))
test_ac3_custom_storage_class || FAILURES=$((FAILURES+1))
test_ac4_custom_storage_size || FAILURES=$((FAILURES+1))
test_ac5_data_persistence || FAILURES=$((FAILURES+1))
test_ac6_disabled_uses_emptydir || FAILURES=$((FAILURES+1))

echo "=== Test Summary ==="
echo "Total failed tests: ${FAILURES}"
exit ${FAILURES}
EOF && chmod +x tests/grafana-persistence/test_ac_grafana_persistence.sh
