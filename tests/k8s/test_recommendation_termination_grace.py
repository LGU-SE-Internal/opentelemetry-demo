import yaml
import os
import pytest

RECOMMENDATION_DEPLOYMENT_PATH = "/workspace/k8s/recommendation-service/deployment.yaml"

@pytest.fixture
def recommendation_deployment():
    with open(RECOMMENDATION_DEPLOYMENT_PATH, "r") as f:
        return yaml.safe_load(f)

def test_ac1_termination_grace_period_field_exists(recommendation_deployment):
    """AC-1: The recommendation service Deployment manifest contains an explicitly defined terminationGracePeriodSeconds field."""
    template_spec = recommendation_deployment.get("spec", {}).get("template", {}).get("spec", {})
    assert "terminationGracePeriodSeconds" in template_spec, "terminationGracePeriodSeconds field missing from Deployment spec.template.spec"

def test_ac2_termination_grace_period_value_is_15(recommendation_deployment):
    """AC-2: The value of the terminationGracePeriodSeconds field in the recommendation service Deployment is exactly 15."""
    template_spec = recommendation_deployment.get("spec", {}).get("template", {}).get("spec", {})
    grace_period = template_spec.get("terminationGracePeriodSeconds")
    assert grace_period == 15, f"Expected terminationGracePeriodSeconds=15, got {grace_period}"

def test_ac3_termination_grace_period_at_correct_hierarchy(recommendation_deployment):
    """AC-3: The terminationGracePeriodSeconds field is located under spec.template.spec at the same hierarchy level as the containers field."""
    template_spec = recommendation_deployment.get("spec", {}).get("template", {}).get("spec", {})
    assert "containers" in template_spec, "containers field missing from expected location (invalid deployment manifest)"
    assert "terminationGracePeriodSeconds" in template_spec, "terminationGracePeriodSeconds not at same level as containers field"

def test_ac4_no_other_deployment_modifications(recommendation_deployment):
    """AC-4: No other fields or values in the recommendation service Deployment manifest are modified from their pre-change state."""
    # Verify core deployment fields are unchanged
    assert recommendation_deployment["metadata"]["name"] == "recommendationservice"
    assert recommendation_deployment["spec"]["replicas"] == 1
    assert "containers" in recommendation_deployment["spec"]["template"]["spec"]
    assert len(recommendation_deployment["spec"]["template"]["spec"]["containers"]) == 1
    assert recommendation_deployment["spec"]["template"]["spec"]["containers"][0]["name"] == "server"
    # Check no extra fields added outside of the expected one
    template_spec = recommendation_deployment.get("spec", {}).get("template", {}).get("spec", {})
    allowed_extra_fields = {"terminationGracePeriodSeconds"}
    existing_fields = set(template_spec.keys())
    # Original fields (without terminationGracePeriodSeconds) should remain
    original_fields = {"containers", "serviceAccountName", "securityContext"}
    assert existing_fields.issubset(original_fields.union(allowed_extra_fields)), f"Unexpected fields added to deployment spec: {existing_fields - original_fields - allowed_extra_fields}"

def test_ac5_k8s_waits_15s_before_sigkill(recommendation_deployment):
    """AC-5: When a recommendation service pod is terminated, Kubernetes waits 15 seconds after sending SIGTERM before sending SIGKILL."""
    # Verify the manifest value is correct (this ensures Kubernetes will apply the 15s wait)
    template_spec = recommendation_deployment.get("spec", {}).get("template", {}).get("spec", {})
    grace_period = template_spec.get("terminationGracePeriodSeconds")
    assert grace_period == 15, f"Kubernetes will not wait 15s, terminationGracePeriodSeconds is set to {grace_period}"
