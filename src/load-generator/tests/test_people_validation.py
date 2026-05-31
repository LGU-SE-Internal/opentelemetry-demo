import json
import pytest
from tempfile import NamedTemporaryFile
import sys
import os

# Add parent directory to path to import locustfile modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from locustfile import validate_people_entries, load_and_validate_people

def test_validate_people_entries_passes_for_valid_entries():
    """Test validation passes for entries with all required fields"""
    valid_entries = [
        {"id": "1", "name": "Test User 1", "email": "test1@example.com"},
        {"id": "2", "name": "Test User 2", "email": "test2@example.com", "extra_field": "value"}
    ]
    errors = validate_people_entries(valid_entries)
    assert len(errors) == 0

def test_validate_people_entries_fails_for_missing_fields():
    """Test validation fails correctly when entries are missing required fields"""
    # Missing name
    entries_missing_name = [{"id": "1", "email": "test@example.com"}]
    errors = validate_people_entries(entries_missing_name)
    assert len(errors) == 1
    assert "missing required fields: name" in errors[0]
    
    # Missing email
    entries_missing_email = [{"id": "1", "name": "Test User"}]
    errors = validate_people_entries(entries_missing_email)
    assert len(errors) == 1
    assert "missing required fields: email" in errors[0]
    
    # Missing id
    entries_missing_id = [{"name": "Test User", "email": "test@example.com"}]
    errors = validate_people_entries(entries_missing_id)
    assert len(errors) == 1
    assert "missing required fields: id" in errors[0]
    
    # Missing multiple fields
    entries_missing_multiple = [{"name": "Test User"}]
    errors = validate_people_entries(entries_missing_multiple)
    assert len(errors) == 1
    assert "missing required fields: email, id" in errors[0]
    
    # Multiple bad entries
    multiple_bad_entries = [
        {"email": "test1@example.com"},  # Missing id and name
        {"id": "2", "name": "Test User 2"}  # Missing email
    ]
    errors = validate_people_entries(multiple_bad_entries)
    assert len(errors) == 2

def test_load_and_validate_people_fails_for_malformed_json():
    """Test validation fails for malformed JSON file"""
    with NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        f.write("this is not valid json [")
        temp_file = f.name
    
    try:
        success, msg = load_and_validate_people(temp_file)
        assert success == False
        assert "Malformed JSON" in msg
    finally:
        os.unlink(temp_file)

def test_load_and_validate_people_passes_for_valid_file():
    """Test validation passes for valid JSON file with correct entries"""
    valid_data = [
        {"id": "1", "name": "Test User 1", "email": "test1@example.com"},
        {"id": "2", "name": "Test User 2", "email": "test2@example.com"}
    ]
    with NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(valid_data, f)
        temp_file = f.name
    
    try:
        success, msg = load_and_validate_people(temp_file)
        assert success == True
        assert "Validation passed" in msg
    finally:
        os.unlink(temp_file)
