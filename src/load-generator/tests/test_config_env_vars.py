import os
import pytest
import importlib
import locustfile

def test_ac1_graceful_shutdown_timeout_set_valid_value():
    # AC-1: When LOCUST_GRACEFUL_SHUTDOWN_TIMEOUT is set to valid non-negative integer N, use N instead of default 10s
    os.environ['LOCUST_GRACEFUL_SHUTDOWN_TIMEOUT'] = '30'
    importlib.reload(locustfile)
    assert locustfile.graceful_shutdown_timeout == 30
    del os.environ['LOCUST_GRACEFUL_SHUTDOWN_TIMEOUT']
    importlib.reload(locustfile)

def test_ac2_graceful_shutdown_timeout_not_set_use_default():
    # AC-2: When LOCUST_GRACEFUL_SHUTDOWN_TIMEOUT is not set, use default 10s
    if 'LOCUST_GRACEFUL_SHUTDOWN_TIMEOUT' in os.environ:
        del os.environ['LOCUST_GRACEFUL_SHUTDOWN_TIMEOUT']
    importlib.reload(locustfile)
    assert locustfile.graceful_shutdown_timeout == 10

def test_ac3_user_wait_time_min_max_set_valid_values():
    # AC-3: When LOCUST_USER_WAIT_TIME_MIN and LOCUST_USER_WAIT_TIME_MAX set to valid N <= M, use N-M instead of default 1-10s
    os.environ['LOCUST_USER_WAIT_TIME_MIN'] = '2'
    os.environ['LOCUST_USER_WAIT_TIME_MAX'] = '15'
    importlib.reload(locustfile)
    assert locustfile.wait_time.min_wait == 2
    assert locustfile.wait_time.max_wait == 15
    del os.environ['LOCUST_USER_WAIT_TIME_MIN']
    del os.environ['LOCUST_USER_WAIT_TIME_MAX']
    importlib.reload(locustfile)

def test_ac4_user_wait_time_not_set_use_default():
    # AC-4: When LOCUST_USER_WAIT_TIME_MIN/MAX not set, use default 1s-10s
    for var in ['LOCUST_USER_WAIT_TIME_MIN', 'LOCUST_USER_WAIT_TIME_MAX']:
        if var in os.environ:
            del os.environ[var]
    importlib.reload(locustfile)
    assert locustfile.wait_time.min_wait == 1
    assert locustfile.wait_time.max_wait == 10

def test_ac5_ui_interaction_delay_set_valid_value():
    # AC-5: When LOCUST_UI_INTERACTION_DELAY set to valid non-negative integer N, use N ms instead of default 2000ms
    os.environ['LOCUST_UI_INTERACTION_DELAY'] = '5000'
    importlib.reload(locustfile)
    assert locustfile.UI_INTERACTION_DELAY == 5000
    del os.environ['LOCUST_UI_INTERACTION_DELAY']
    importlib.reload(locustfile)

def test_ac6_ui_interaction_delay_not_set_use_default():
    # AC-6: When LOCUST_UI_INTERACTION_DELAY not set, use default 2000ms
    if 'LOCUST_UI_INTERACTION_DELAY' in os.environ:
        del os.environ['LOCUST_UI_INTERACTION_DELAY']
    importlib.reload(locustfile)
    assert locustfile.UI_INTERACTION_DELAY == 2000

def test_ac7_page_load_timeout_set_valid_value():
    # AC-7: When LOCUST_PAGE_LOAD_TIMEOUT set to valid non-negative integer N, use N ms instead of default 15000ms
    os.environ['LOCUST_PAGE_LOAD_TIMEOUT'] = '30000'
    importlib.reload(locustfile)
    assert locustfile.PAGE_LOAD_TIMEOUT == 30000
    del os.environ['LOCUST_PAGE_LOAD_TIMEOUT']
    importlib.reload(locustfile)

def test_ac8_page_load_timeout_not_set_use_default():
    # AC-8: When LOCUST_PAGE_LOAD_TIMEOUT not set, use default 15000ms
    if 'LOCUST_PAGE_LOAD_TIMEOUT' in os.environ:
        del os.environ['LOCUST_PAGE_LOAD_TIMEOUT']
    importlib.reload(locustfile)
    assert locustfile.PAGE_LOAD_TIMEOUT == 15000

def test_ac9_all_defaults_when_no_env_vars_set():
    # AC-9: All existing behavior unchanged when none of new env vars are set
    for var in [
        'LOCUST_GRACEFUL_SHUTDOWN_TIMEOUT',
        'LOCUST_USER_WAIT_TIME_MIN',
        'LOCUST_USER_WAIT_TIME_MAX',
        'LOCUST_UI_INTERACTION_DELAY',
        'LOCUST_PAGE_LOAD_TIMEOUT'
    ]:
        if var in os.environ:
            del os.environ[var]
    importlib.reload(locustfile)
    assert locustfile.graceful_shutdown_timeout == 10
    assert locustfile.wait_time.min_wait == 1
    assert locustfile.wait_time.max_wait == 10
    assert locustfile.UI_INTERACTION_DELAY == 2000
    assert locustfile.PAGE_LOAD_TIMEOUT == 15000

def test_ac10_invalid_env_var_values_fallback_to_default_and_log_warning(caplog):
    # AC-10: Invalid values fall back to default and log warning
    # Test non-integer value
    os.environ['LOCUST_GRACEFUL_SHUTDOWN_TIMEOUT'] = 'not_an_integer'
    importlib.reload(locustfile)
    assert locustfile.graceful_shutdown_timeout == 10
    assert any("Invalid value for LOCUST_GRACEFUL_SHUTDOWN_TIMEOUT" in record.message for record in caplog.records)
    
    # Test negative value
    caplog.clear()
    os.environ['LOCUST_USER_WAIT_TIME_MIN'] = '-5'
    os.environ['LOCUST_USER_WAIT_TIME_MAX'] = '3'
    importlib.reload(locustfile)
    assert locustfile.wait_time.min_wait == 1
    assert locustfile.wait_time.max_wait == 10
    assert any("Invalid value for LOCUST_USER_WAIT_TIME_MIN" in record.message for record in caplog.records)
    
    # Test max < min
    caplog.clear()
    os.environ['LOCUST_USER_WAIT_TIME_MIN'] = '5'
    os.environ['LOCUST_USER_WAIT_TIME_MAX'] = '3'
    importlib.reload(locustfile)
    assert locustfile.wait_time.min_wait == 1
    assert locustfile.wait_time.max_wait == 10
    assert any("LOCUST_USER_WAIT_TIME_MAX (3) is less than LOCUST_USER_WAIT_TIME_MIN (5)" in record.message for record in caplog.records)
    
    # Cleanup
    for var in [
        'LOCUST_GRACEFUL_SHUTDOWN_TIMEOUT',
        'LOCUST_USER_WAIT_TIME_MIN',
        'LOCUST_USER_WAIT_TIME_MAX'
    ]:
        if var in os.environ:
            del os.environ[var]
    importlib.reload(locustfile)
