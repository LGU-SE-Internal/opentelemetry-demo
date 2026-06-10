import os
import yaml
import subprocess
import pytest

PROMETHEUS_DIR = os.path.join(os.path.dirname(__file__), '..', 'src', 'prometheus')
ALERTS_FILE = os.path.join(PROMETHEUS_DIR, 'alerts.yaml')
PROM_CONFIG_FILE = os.path.join(PROMETHEUS_DIR, 'prometheus-config.yaml')
ALERTMANAGER_CONFIG_FILE = os.path.join(PROMETHEUS_DIR, 'alertmanager-config.yaml')
VALIDATE_SCRIPT = os.path.join(PROMETHEUS_DIR, 'validate-config.sh')

def test_ac1_alerts_validation_and_count():
    # AC1: promtool check rules passes, exactly 4 alert rules with correct names and expressions
    assert os.path.exists(ALERTS_FILE), "alerts.yaml not found"
    
    # Run promtool check
    result = subprocess.run(
        ['promtool', 'check', 'rules', ALERTS_FILE],
        capture_output=True,
        text=True
    )
    assert result.returncode == 0, f"promtool check rules failed: {result.stderr}"
    
    # Load alerts file
    with open(ALERTS_FILE, 'r') as f:
        alerts = yaml.safe_load(f)
    
    assert 'groups' in alerts, "No groups in alerts.yaml"
    critical_group = next(g for g in alerts['groups'] if g['name'] == 'critical-alerts')
    assert critical_group['interval'] == '10s', "Critical alerts interval should be 10s"
    rules = critical_group['rules']
    assert len(rules) == 4, f"Expected 4 alert rules, found {len(rules)}"
    
    rule_names = [r['alert'] for r in rules]
    expected_rules = ['TargetDown', 'HighServiceErrorRate', 'PrometheusHighMemoryUsage', 'HighScrapeFailureRate']
    assert set(rule_names) == set(expected_rules), f"Missing alert rules: {set(expected_rules) - set(rule_names)}"
    
    # Check expressions
    for rule in rules:
        if rule['alert'] == 'TargetDown':
            assert rule['expr'] == 'up == 0', "TargetDown expr incorrect"
            assert rule['for'] == '2m', "TargetDown for duration incorrect"
        elif rule['alert'] == 'HighServiceErrorRate':
            assert rule['expr'] == 'sum(rate(http_requests_total{status_code=~"5.."}[5m])) / sum(rate(http_requests_total[5m])) > 0.05', "HighServiceErrorRate expr incorrect"
            assert rule['for'] == '5m', "HighServiceErrorRate for duration incorrect"
        elif rule['alert'] == 'PrometheusHighMemoryUsage':
            assert rule['expr'] == 'container_memory_usage_bytes{container="prometheus"} / container_spec_memory_limit_bytes{container="prometheus"} > 0.9', "PrometheusHighMemoryUsage expr incorrect"
            assert rule['for'] == '2m', "PrometheusHighMemoryUsage for duration incorrect"
        elif rule['alert'] == 'HighScrapeFailureRate':
            assert rule['expr'] == 'count(up == 0) / count(up) > 0.2', "HighScrapeFailureRate expr incorrect"
            assert rule['for'] == '5m', "HighScrapeFailureRate for duration incorrect"

def test_ac2_alert_annotations():
    # AC2: Each alert has summary, description, runbook annotations
    with open(ALERTS_FILE, 'r') as f:
        alerts = yaml.safe_load(f)
    rules = alerts['groups'][0]['rules']
    for rule in rules:
        assert 'annotations' in rule, f"Alert {rule['alert']} missing annotations"
        annotations = rule['annotations']
        assert 'summary' in annotations, f"Alert {rule['alert']} missing summary annotation"
        assert 'description' in annotations, f"Alert {rule['alert']} missing description annotation"
        assert 'runbook' in annotations, f"Alert {rule['alert']} missing runbook annotation"
        assert len(annotations['runbook'].strip()) > 0, f"Alert {rule['alert']} runbook is empty"

def test_ac3_prometheus_config_sections():
    # AC3: prometheus-config.yaml has rule_files entry for alerts.yaml and alerting section pointing to alertmanager:9093
    with open(PROM_CONFIG_FILE, 'r') as f:
        config = yaml.safe_load(f)
    
    assert 'rule_files' in config, "No rule_files section in prometheus config"
    assert './alerts.yaml' in config['rule_files'], "alerts.yaml not in rule_files"
    
    assert 'alerting' in config, "No alerting section in prometheus config"
    alertmanagers = config['alerting'].get('alertmanagers', [])
    assert len(alertmanagers) > 0, "No alertmanagers configured"
    static_configs = alertmanagers[0].get('static_configs', [])
    assert len(static_configs) > 0, "No static configs for alertmanager"
    targets = static_configs[0].get('targets', [])
    assert 'alertmanager:9093' in targets, "Alertmanager endpoint not configured correctly"

def test_ac4_alertmanager_config():
    # AC4: alertmanager-config.yaml has default receiver, route to placeholder webhook, repeat_interval 4h
    assert os.path.exists(ALERTMANAGER_CONFIG_FILE), "alertmanager-config.yaml not found"
    
    with open(ALERTMANAGER_CONFIG_FILE, 'r') as f:
        config = yaml.safe_load(f)
    
    assert 'global' in config, "No global section in alertmanager config"
    assert 'route' in config, "No route section in alertmanager config"
    assert 'receivers' in config, "No receivers section in alertmanager config"
    
    assert len(config['receivers']) >= 1, "No receivers defined in alertmanager config"
    default_receiver = next(r for r in config['receivers'] if r['name'] == 'default')
    assert 'webhook_configs' in default_receiver, "Default receiver missing webhook config"
    assert len(default_receiver['webhook_configs']) > 0, "Default receiver has no webhook endpoints"
    
    assert config['route']['receiver'] == 'default', "Default route receiver not set to default"
    assert config['route']['repeat_interval'] == '4h', "repeat_interval not set to 4 hours"

def test_ac5_validate_script():
    # AC5: validate-config.sh runs promtool checks, exits non-zero on invalid config/rules
    assert os.path.exists(VALIDATE_SCRIPT), "validate-config.sh not found"
    assert os.access(VALIDATE_SCRIPT, os.X_OK), "validate-config.sh not executable"
    
    # Check that promtool check config is in the script
    with open(VALIDATE_SCRIPT, 'r') as f:
        script_content = f.read()
    assert 'promtool check config prometheus-config.yaml' in script_content, "promtool config check missing from validate script"
    assert 'promtool check rules alerts.yaml' in script_content, "promtool rules check missing from validate script"
    
    # Check that script exits non-zero on failure
    # Temporarily create invalid rules file
    invalid_rules = os.path.join(PROMETHEUS_DIR, 'invalid_alerts.yaml')
    with open(invalid_rules, 'w') as f:
        f.write("groups: [{name: test, rules: [{alert: Test, expr: 'invalid_expr'}]}]")
    # Modify script to test invalid case
    temp_script = os.path.join(PROMETHEUS_DIR, 'test_validate.sh')
    with open(temp_script, 'w') as f:
        f.write("""#!/bin/bash
set -e
promtool check rules invalid_alerts.yaml
exit 0
""")
    os.chmod(temp_script, 0o755)
    result = subprocess.run(
        [temp_script],
        cwd=PROMETHEUS_DIR,
        capture_output=True,
        text=True
    )
    assert result.returncode != 0, "Validation script did not exit non-zero on invalid rules"
    # Cleanup
    os.unlink(invalid_rules)
    os.unlink(temp_script)

def test_ac6_alert_severity_label():
    # AC6: All alert rules have severity: critical label
    with open(ALERTS_FILE, 'r') as f:
        alerts = yaml.safe_load(f)
    rules = alerts['groups'][0]['rules']
    for rule in rules:
        assert 'labels' in rule, f"Alert {rule['alert']} missing labels"
        assert rule['labels'].get('severity') == 'critical', f"Alert {rule['alert']} does not have severity: critical label"
