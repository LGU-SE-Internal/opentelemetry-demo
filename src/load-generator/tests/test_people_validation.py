import json
import os
import tempfile
import pytest

from locustfile import validate_people_entries, load_and_validate_people

def test_validate_people_entries_valid():
    """AC-1: Validation passes for valid people entries with all required fields"""
    valid_people = [
        {"name": "John Doe", "email": "john@example.com", "id": "123"},
        {"name": "Jane Smith", "email": "jane@example.com", "id": "456", "extra_field": "value"}
    ]
    errors = validate_people_entries(valid_people)
    assert len(errors) == 0

def test_validate_people_entries_missing_required_fields():
    """AC-2: Validation fails correctly when entries are missing required fields"""
    # Missing name
    people_missing_name = [
        {"email": "john@example.com", "id": "123"}
    ]
    errors = validate_people_entries(people_missing_name)
    assert len(errors) == 1
    assert "missing required fields: name" in errors[0]

    # Missing email
    people_missing_email = [
        {"name": "John Doe", "id": "123"}
    ]
    errors = validate_people_entries(people_missing_email)
    assert len(errors) == 1
    assert "missing required fields: email" in errors[0]

    # Missing id
    people_missing_id = [
        {"name": "John Doe", "email": "john@example.com"}
    ]
    errors = validate_people_entries(people_missing_id)
    assert len(errors) == 1
    assert "missing required fields: id" in errors[0]

    # Multiple missing fields in one entry
    people_missing_multiple = [
        {"name": "John Doe"}
    ]
    errors = validate_people_entries(people_missing_multiple)
    assert len(errors) == 1
    assert "missing required fields: email, id" in errors[0]

    # Multiple entries with errors
    people_multiple_errors = [
        {"email": "john@example.com", "id": "123"},
        {"name": "Jane Smith", "id": "456"}
    ]
    errors = validate_people_entries(people_multiple_errors)
    assert len(errors) == 2

def test_load_and_validate_people_malformed_json():
    """AC-3: Validation fails correctly when people.json contains malformed JSON"""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        f.write("{ invalid json }")
        temp_file = f.name
    
    try:
        success, msg = load_and_validate_people(temp_file)
        assert success == False
        assert "Malformed JSON" in msg
    finally:
        os.unlink(temp_file)

def test_load_and_validate_people_valid_file():
    """Test valid JSON file passes validation"""
    valid_data = [
        {"name": "John Doe", "email": "john@example.com", "id": "123"},
        {"name": "Jane Smith", "email": "jane@example.com", "id": "456"}
    ]
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        json.dump(valid_data, f)
        temp_file = f.name
    
    try:
        success, msg = load_and_validate_people(temp_file)
        assert success == True
        assert msg == "Validation passed"
    finally:
        os.unlink(temp_file)

def test_load_and_validate_people_nonexistent_file():
    """Test non-existent file returns error"""
    success, msg = load_and_validate_people("nonexistent_file_1234.json")
    assert success == False
    assert "Failed to read file" in msg
