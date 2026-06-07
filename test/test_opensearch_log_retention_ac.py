#!/usr/bin/env python3
import os
import time
import pytest
from opensearchpy import OpenSearch

OPENSEARCH_ENDPOINT = os.getenv("OPENSEARCH_ENDPOINT", "http://localhost:9200")
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "admin")
POLICY_NAME = "otel-logs-retention-policy"
LOG_INDEX_PATTERNS = ["logs-*", "otel-*-logs-*", "otel-logs-*"]

@pytest.fixture(scope="module")
def opensearch_client():
    client = OpenSearch(
        hosts=[OPENSEARCH_ENDPOINT],
        http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD),
        verify_certs=False
    )
    yield client
    client.close()

@pytest.mark.ac1
def test_ac1_custom_retention_period_applied_to_ilm_policy(opensearch_client):
    """AC-1: Custom retention days set in env var are applied to ILM policy delete phase"""
    custom_days = 14
    os.environ["OPENSEARCH_LOG_RETENTION_DAYS"] = str(custom_days)
    # Simulate service restart (implementation will handle this)
    # Check policy exists
    assert opensearch_client.ilm.get_lifecycle(POLICY_NAME) is not None, "ILM policy does not exist"
    policy = opensearch_client.ilm.get_lifecycle(POLICY_NAME)[POLICY_NAME]["policy"]
    delete_phase = policy["phases"].get("delete", {})
    assert "min_age" in delete_phase, "Delete phase has no min_age configured"
    assert delete_phase["min_age"] == f"{custom_days}d", f"Delete phase min_age not set to {custom_days} days"
    assert delete_phase["actions"].get("delete", {}) == {}, "Delete phase has no delete action configured"

@pytest.mark.ac2
def test_ac2_default_retention_period_used_when_env_not_set(opensearch_client):
    """AC-2: Default 7 day retention is used when OPENSEARCH_LOG_RETENTION_DAYS is not set"""
    if "OPENSEARCH_LOG_RETENTION_DAYS" in os.environ:
        del os.environ["OPENSEARCH_LOG_RETENTION_DAYS"]
    # Simulate service restart
    assert opensearch_client.ilm.get_lifecycle(POLICY_NAME) is not None, "ILM policy does not exist"
    policy = opensearch_client.ilm.get_lifecycle(POLICY_NAME)[POLICY_NAME]["policy"]
    delete_phase = policy["phases"].get("delete", {})
    assert delete_phase["min_age"] == "7d", "Default delete phase min_age is not 7 days"

@pytest.mark.ac3
def test_ac3_new_log_indices_inherit_ilm_policy(opensearch_client):
    """AC-3: New log indices matching patterns are automatically associated with ILM policy"""
    test_indices = [
        "logs-test-2024.06.07",
        "otel-service-logs-test-2024.06.07",
        "otel-logs-test-2024.06.07"
    ]
    for idx_name in test_indices:
        # Create test index
        opensearch_client.indices.create(index=idx_name, ignore=400)
        # Check index settings
        settings = opensearch_client.indices.get_settings(index=idx_name)[idx_name]["settings"]["index"]
        assert "lifecycle" in settings, f"Index {idx_name} has no lifecycle settings"
        assert settings["lifecycle"]["name"] == POLICY_NAME, f"Index {idx_name} not associated with correct ILM policy"
        # Cleanup
        opensearch_client.indices.delete(index=idx_name, ignore=[400, 404])

@pytest.mark.ac4
def test_ac4_ilm_policy_and_template_created_on_startup(opensearch_client):
    """AC-4: ILM policy and index template are automatically created on service startup without manual intervention"""
    # Delete existing policy and template if present
    try:
        opensearch_client.ilm.delete_lifecycle(POLICY_NAME)
    except Exception:
        pass
    try:
        opensearch_client.indices.delete_index_template("otel-logs-retention-template")
    except Exception:
        pass
    # Simulate service restart
    # Verify policy exists
    assert opensearch_client.ilm.exists_lifecycle(POLICY_NAME), "ILM policy was not created on startup"
    # Verify template exists
    assert opensearch_client.indices.exists_index_template("otel-logs-retention-template"), "Index template was not created on startup"
    template = opensearch_client.indices.get_index_template("otel-logs-retention-template")["index_templates"][0]["index_template"]
    assert all(pattern in template["index_patterns"] for pattern in LOG_INDEX_PATTERNS), "Template does not match all required log index patterns"
    assert template["template"]["settings"]["index"]["lifecycle"]["name"] == POLICY_NAME, "Template does not set correct ILM policy"

@pytest.mark.ac5
def test_ac5_existing_log_indices_associated_with_policy_on_restart(opensearch_client):
    """AC-5: Existing log indices matching patterns are assigned policy on service restart"""
    test_idx = "logs-existing-test-2024.06.01"
    # Create index without policy
    opensearch_client.indices.create(index=test_idx, settings={"index.lifecycle.name": None}, ignore=400)
    # Verify no policy assigned initially
    settings = opensearch_client.indices.get_settings(index=test_idx)[test_idx]["settings"]["index"]
    assert "lifecycle" not in settings or settings["lifecycle"].get("name") != POLICY_NAME, "Test index already has policy assigned"
    # Simulate service restart
    # Verify policy is now assigned
    updated_settings = opensearch_client.indices.get_settings(index=test_idx)[test_idx]["settings"]["index"]
    assert updated_settings["lifecycle"]["name"] == POLICY_NAME, "Existing log index not assigned policy on restart"
    # Cleanup
    opensearch_client.indices.delete(index=test_idx, ignore=[400, 404])

@pytest.mark.ac6
def test_ac6_old_indices_deleted_after_retention_period(opensearch_client):
    """AC-6: Indices older than retention period are deleted within 24 hours"""
    custom_days = 1
    os.environ["OPENSEARCH_LOG_RETENTION_DAYS"] = str(custom_days)
    # Simulate service restart
    # Create index with creation date older than retention period
    test_idx = "logs-old-test-2024.06.05"
    opensearch_client.indices.create(index=test_idx, settings={"index.creation_date": int(time.time() - (custom_days * 86400 + 3600)) * 1000}, ignore=400)
    # Wait up to 1 hour (1s for test purposes, implementation will have ILM run interval)
    time.sleep(1)
    # Verify index is deleted
    assert not opensearch_client.indices.exists(index=test_idx), "Old index was not deleted after retention period"

@pytest.mark.ac7
def test_ac7_log_ingestion_query_works_for_young_indices(opensearch_client):
    """AC-7: Log ingestion and queries work normally for indices younger than retention period"""
    test_idx = "logs-recent-test-2024.06.07"
    # Create index
    opensearch_client.indices.create(index=test_idx, ignore=400)
    # Ingest test log
    test_doc = {"service": "test", "message": "test log entry", "@timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
    opensearch_client.index(index=test_idx, body=test_doc, refresh=True)
    # Query log
    res = opensearch_client.search(index=test_idx, q="message:test log entry")
    assert res["hits"]["total"]["value"] == 1, "Test log not found in query"
    # Verify index still exists
    assert opensearch_client.indices.exists(index=test_idx), "Recent index was incorrectly deleted"
    # Cleanup
    opensearch_client.indices.delete(index=test_idx, ignore=[400, 404])

@pytest.mark.ac8
def test_ac8_invalid_retention_value_causes_startup_error():
    """AC-8: Setting OPENSEARCH_LOG_RETENTION_DAYS < 1 causes service startup failure"""
    invalid_values = [0, -1, -7, "invalid", "0", "-1"]
    for val in invalid_values:
        os.environ["OPENSEARCH_LOG_RETENTION_DAYS"] = str(val)
        # Simulate service startup (implementation should fail)
        startup_exit_code = os.system("./opensearch-docker-entrypoint.sh >/dev/null 2>&1")
        assert startup_exit_code != 0, f"Service started successfully with invalid retention value {val}"
