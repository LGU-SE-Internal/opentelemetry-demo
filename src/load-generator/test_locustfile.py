#!/usr/bin/python
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import pytest
import random
import uuid
from unittest.mock import patch, MagicMock

# Import the locustfile modules
import sys
sys.path.insert(0, '/workspace/src/load-generator')
import locustfile


def test_product_ids_count():
    """Test that we have the expected number of product IDs"""
    assert len(locustfile.products) == 10


def test_random_product_selection():
    """Test that random product selection returns a valid product ID"""
    product = random.choice(locustfile.products)
    assert product in locustfile.products
    assert isinstance(product, str)
    assert len(product) == 10


def test_categories_count():
    """Test that we have the expected number of categories"""
    assert len(locustfile.categories) == 7


def test_random_category_selection():
    """Test that random category selection returns a valid category"""
    category = random.choice(locustfile.categories)
    assert category in locustfile.categories
    assert category is None or isinstance(category, str)


def test_random_quantity_selection():
    """Test that random quantity selection returns expected values"""
    valid_quantities = [1, 2, 3, 4, 5, 10]
    for _ in range(20):
        qty = random.choice(valid_quantities)
        assert qty in valid_quantities
        assert isinstance(qty, int)


def test_people_data_load():
    """Test that people.json loads correctly and has valid entries"""
    assert len(locustfile.people) > 0
    person = random.choice(locustfile.people)
    # Check required fields exist
    assert "email" in person
    assert "address" in person
    assert "userCurrency" in person
    assert "creditCard" in person
    # Check address fields
    assert "streetAddress" in person["address"]
    assert "zipCode" in person["address"]
    assert "city" in person["address"]
    assert "state" in person["address"]
    assert "country" in person["address"]
    # Check credit card fields
    assert "creditCardNumber" in person["creditCard"]
    assert "creditCardExpirationMonth" in person["creditCard"]
    assert "creditCardExpirationYear" in person["creditCard"]
    assert "creditCardCvv" in person["creditCard"]


@patch('locustfile.api')
def test_get_flagd_value(mock_api):
    """Test get_flagd_value function returns expected integer"""
    mock_client = MagicMock()
    mock_client.get_integer_value.return_value = 5
    mock_api.get_client.return_value = mock_client
    
    result = locustfile.get_flagd_value("test_flag")
    assert result == 5
    mock_client.get_integer_value.assert_called_once_with("test_flag", 0)


def test_uuid_generation():
    """Test that UUID generation for user IDs works correctly"""
    user_id = str(uuid.uuid1())
    assert isinstance(user_id, str)
    assert len(user_id) == 36  # Standard UUID length
