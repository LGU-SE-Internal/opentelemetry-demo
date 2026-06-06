import pytest
import json
from app import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

def test_ac1_non_json_body(client):
    """AC-1: Non-JSON body returns 400 with correct error message"""
    response = client.post('/v1/chat/completions', data='not valid json', content_type='application/json')
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data['error']['message'] == 'Request body must be valid JSON'
    assert data['error']['type'] == 'invalid_request_error'
    assert data['error']['code'] == 'invalid_input'

def test_ac2_missing_messages_field(client):
    """AC-2: Missing 'messages' field returns 400 with correct error message"""
    payload = {'model': 'astronomy-llm'}
    response = client.post('/v1/chat/completions', json=payload)
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data['error']['message'] == "'messages' field is required"
    assert data['error']['param'] == 'messages'

def test_ac3_messages_not_array(client):
    """AC-3: 'messages' is string not array returns 400 with correct error message"""
    payload = {'messages': 'this is a string not array', 'model': 'astronomy-llm'}
    response = client.post('/v1/chat/completions', json=payload)
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data['error']['message'] == "'messages' must be an array"
    assert data['error']['param'] == 'messages'

def test_ac4_empty_messages_array(client):
    """AC-4: Empty 'messages' array returns 400 with correct error message"""
    payload = {'messages': [], 'model': 'astronomy-llm'}
    response = client.post('/v1/chat/completions', json=payload)
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data['error']['message'] == "'messages' array cannot be empty"
    assert data['error']['param'] == 'messages'

def test_ac5_message_missing_content_field(client):
    """AC-5: Message entry missing 'content' field returns 400 with correct error message"""
    payload = {
        'messages': [{'role': 'user'}],
        'model': 'astronomy-llm'
    }
    response = client.post('/v1/chat/completions', json=payload)
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data['error']['message'] == "All message entries must contain a 'content' field"
    assert data['error']['param'] == 'messages[0].content'

def test_ac6_content_field_not_string(client):
    """AC-6: Message 'content' is integer not string returns 400 with correct error message"""
    payload = {
        'messages': [{'role': 'user', 'content': 12345}],
        'model': 'astronomy-llm'
    }
    response = client.post('/v1/chat/completions', json=payload)
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data['error']['message'] == "'content' field must be a string"
    assert data['error']['param'] == 'messages[0].content'

def test_ac7_no_product_id_in_last_message(client):
    """AC-7: Last message has no valid product ID returns 400 with correct error message"""
    payload = {
        'messages': [{'role': 'user', 'content': 'Give me a summary of this product'}],
        'model': 'astronomy-llm'
    }
    response = client.post('/v1/chat/completions', json=payload)
    assert response.status_code == 400
    data = json.loads(response.data)
    assert data['error']['message'] == "Valid product ID not found in request message"
    assert data['error']['param'] == 'messages[-1].content'

def test_ac8_valid_request_returns_200(client):
    """AC-8: Valid request with proper format and product ID returns 200 OK"""
    payload = {
        'messages': [{'role': 'user', 'content': 'Summarize reviews for product ID ABC123'}],
        'model': 'astronomy-llm',
        'stream': False
    }
    response = client.post('/v1/chat/completions', json=payload)
    assert response.status_code == 200

def test_ac9_no_500_errors_for_invalid_inputs(client):
    """AC-9: All invalid input cases return 4xx not 500"""
    test_cases = [
        # Non-JSON
        ('not json', 'application/json'),
        # Missing messages
        (json.dumps({'model': 'test'}), 'application/json'),
        # Messages string
        (json.dumps({'messages': 'string'}), 'application/json'),
        # Empty messages
        (json.dumps({'messages': []}), 'application/json'),
        # Missing content
        (json.dumps({'messages': [{'role': 'user'}]}), 'application/json'),
        # Content int
        (json.dumps({'messages': [{'role': 'user', 'content': 123}]}), 'application/json'),
        # No product ID
        (json.dumps({'messages': [{'role': 'user', 'content': 'Hello'}]}), 'application/json')
    ]
    for payload, content_type in test_cases:
        response = client.post('/v1/chat/completions', data=payload, content_type=content_type)
        assert response.status_code < 500, f"Got 500 error for payload: {payload}"
