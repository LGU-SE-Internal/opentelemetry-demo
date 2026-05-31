#!/usr/bin/env python3

import os
import random
import sys
from unittest.mock import patch, MagicMock

# Add the src/load-generator directory to path so we can import locustfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../src/load-generator"))

import locustfile


def test_user_agent_list():
    """Test USER_AGENTS list is non-empty and has expected values"""
    assert len(locustfile.USER_AGENTS) > 0, "USER_AGENTS list is empty"
    for agent in locustfile.USER_AGENTS:
        assert isinstance(agent, str), f"User agent {agent} is not a string"
        assert len(agent) > 0, "User agent cannot be empty string"


def test_user_agent_random_selection():
    """Test that random.choice selects agents from the list correctly"""
    # Test multiple selections are all from the list
    for _ in range(20):
        selected = random.choice(locustfile.USER_AGENTS)
        assert selected in locustfile.USER_AGENTS, f"Selected agent {selected} not in USER_AGENTS"
    
    # Test all agents can be selected (run enough times to ensure coverage)
    selected_set = set()
    for _ in range(100):
        selected_set.add(random.choice(locustfile.USER_AGENTS))
    assert len(selected_set) == len(locustfile.USER_AGENTS), "Not all user agents are being selected"


def test_user_agent_on_start():
    """Test that on_start sets User-Agent header correctly"""
    # Create mock user class
    mock_user = MagicMock()
    mock_client = MagicMock()
    mock_client.headers = {}
    mock_user.client = mock_client
    mock_user.tracer = MagicMock()
    mock_user.index = MagicMock()

    # Call on_start method
    locustfile.HttpUser.on_start(mock_user)

    # Verify user agent is set correctly
    assert "User-Agent" in mock_client.headers
    assert mock_client.headers["User-Agent"] in locustfile.USER_AGENTS



def test_rotate_user_agent_valid_selection():
    """AC1: Test rotate_user_agent picks a random valid user agent from configured list"""
    for _ in range(50):
        selected = locustfile.rotate_user_agent()
        assert selected in locustfile.USER_AGENTS, f"Selected agent {selected} not in USER_AGENTS"
    
    # Test with exclude list
    exclude = [locustfile.USER_AGENTS[0]]
    for _ in range(50):
        selected = locustfile.rotate_user_agent(exclude=exclude)
        assert selected in locustfile.USER_AGENTS
        assert selected not in exclude
    
    # Test fallback when all agents are excluded
    exclude_all = locustfile.USER_AGENTS.copy()
    for _ in range(50):
        selected = locustfile.rotate_user_agent(exclude=exclude_all)
        assert selected in locustfile.USER_AGENTS


def test_rotate_user_agent_no_duplicates_consecutive():
    """AC2: Test no duplicate user agents are returned in 100 consecutive rotation calls when list size is sufficient"""
    # Use a large enough test agent list (200 unique agents)
    test_agents = [f"TestAgent/{i:03d}" for i in range(200)]
    with patch.object(locustfile, "USER_AGENTS", test_agents):
        last_selected = []
        duplicate_found = False
        for _ in range(100):
            # Exclude the last 100 selected agents to avoid duplicates
            agent = locustfile.rotate_user_agent(exclude=last_selected)
            if agent in last_selected:
                duplicate_found = True
                break
            last_selected.append(agent)
            if len(last_selected) > 100:
                last_selected.pop(0)
        assert not duplicate_found, "Duplicate user agent found in consecutive 100 rotation calls with sufficient list size"


def test_env_var_defaults():
    """Test that environment variables fall back to correct defaults when not set"""
    with patch.dict(os.environ, clear=True):
        # Reload locustfile to pick up env changes
        import importlib
        importlib.reload(locustfile)

        # Test REQUEST_TIMEOUT default
        assert locustfile.REQUEST_TIMEOUT == "10", f"Expected REQUEST_TIMEOUT default '10', got {locustfile.REQUEST_TIMEOUT}"
        
        # Test LOCUST_BROWSER_TRAFFIC_ENABLED default
        assert locustfile.browser_traffic_enabled is False, "Expected default browser_traffic_enabled to be False"


def test_env_var_loading():
    """Test that environment variables are correctly loaded when set"""
    # Test REQUEST_TIMEOUT
    with patch.dict(os.environ, {"REQUEST_TIMEOUT": "60"}):
        import importlib
        importlib.reload(locustfile)
        assert locustfile.REQUEST_TIMEOUT == "60", f"Expected REQUEST_TIMEOUT '60', got {locustfile.REQUEST_TIMEOUT}"
    
    # Test LOCUST_BROWSER_TRAFFIC_ENABLED truthy values
    truthy_values = ["true", "yes", "on", "TRUE", "YES", "ON", "  True  "]
    for val in truthy_values:
        with patch.dict(os.environ, {"LOCUST_BROWSER_TRAFFIC_ENABLED": val}):
            import importlib
            importlib.reload(locustfile)
            assert locustfile.browser_traffic_enabled is True, f"Expected True for value '{val}'"
    
    # Test LOCUST_BROWSER_TRAFFIC_ENABLED falsy values
    falsy_values = ["false", "no", "off", "FALSE", "NO", "OFF", "", "invalid", "0"]
    for val in falsy_values:
        with patch.dict(os.environ, {"LOCUST_BROWSER_TRAFFIC_ENABLED": val}):
            import importlib
            importlib.reload(locustfile)
            assert locustfile.browser_traffic_enabled is False, f"Expected False for value '{val}'"

def test_flagd_env_vars():
    """Test FLAGD related environment variables are loaded correctly"""
    # Test default values for FLAGD variables
    with patch.dict(os.environ, {}, clear=True):
        import importlib
        importlib.reload(locustfile)
        assert locustfile.base_url == "http://localhost:8016", f"Expected default base_url http://localhost:8016, got {locustfile.base_url}"
    
    # Test custom FLAGD_HOST
    with patch.dict(os.environ, {"FLAGD_HOST": "flagd.test"}):
        import importlib
        importlib.reload(locustfile)
        assert locustfile.base_url == "http://flagd.test:8016", f"Expected base_url with custom host, got {locustfile.base_url}"
    
    # Test custom FLAGD_OFREP_PORT
    with patch.dict(os.environ, {"FLAGD_OFREP_PORT": "8080"}):
        import importlib
        importlib.reload(locustfile)
        assert locustfile.base_url == "http://localhost:8080", f"Expected base_url with custom port, got {locustfile.base_url}"
    
    # Test both custom FLAGD_HOST and FLAGD_OFREP_PORT
    with patch.dict(os.environ, {"FLAGD_HOST": "my.host", "FLAGD_OFREP_PORT": "9090"}):
        import importlib
        importlib.reload(locustfile)
        assert locustfile.base_url == "http://my.host:9090", f"Expected base_url with custom host and port, got {locustfile.base_url}"

