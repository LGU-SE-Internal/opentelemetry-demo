import logging
import json
import psycopg2
from psycopg2 import errors
from typing import Callable, Any
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryCallState,
)

logger = logging.getLogger(__name__)

# Retriable PostgreSQL error types
RETRIABLE_ERRORS = (
    psycopg2.OperationalError,
    errors.DeadlockDetected,
    errors.LockNotAvailable,
    errors.ConnectionException,
    errors.AdminShutdown,
    errors.CrashShutdown,
    errors.CannotConnectNow,
)

def log_retry_attempt(retry_state: RetryCallState):
    """Log retry attempt with structured schema."""
    operation_type = retry_state.fn.__dict__.get("operation_type", "unknown")
    delay_ms = int(retry_state.next_action.sleep * 1000)
    error = retry_state.outcome.exception()
    
    log_entry = {
        "service": "product-reviews",
        "event": "postgres_retry_attempt",
        "operation_type": operation_type,
        "operation_desc": retry_state.fn.__name__,
        "attempt_number": retry_state.attempt_number,
        "max_attempts": 3,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "retry_delay_ms": delay_ms
    }
    
    logger.info(json.dumps(log_entry))

def postgres_retry(operation_type: str = "read") -> Callable:
    """
    Decorator to add retry logic for PostgreSQL operations.
    Args:
        operation_type: "read" or "write" - determines idempotency checks
    Returns:
        Wrapped function with retry logic
    """
    def decorator(func: Callable) -> Callable:
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=0.1, max=1),
            retry=retry_if_exception_type(RETRIABLE_ERRORS),
            before_sleep=log_retry_attempt,
            reraise=True
        )
        def wrapper(*args, **kwargs):
            if operation_type == "write":
                idempotency_key = kwargs.get("idempotency_key")
                if idempotency_key:
                    existing_review = check_existing_idempotency_key(idempotency_key)
                    if existing_review:
                        return existing_review
            return func(*args, **kwargs)
        
        # Store operation type on the function for logging
        wrapper.operation_type = operation_type
        return wrapper
    return decorator

def check_existing_idempotency_key(idempotency_key: str) -> Any:
    """Check if an idempotency key already exists in the product_reviews table."""
    # Actual implementation would query the database
    # This is a placeholder that will be used in actual operations
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT review_id FROM product_reviews WHERE idempotency_key = %s",
            (idempotency_key,)
        )
        result = cur.fetchone()
        if result:
            return {"success": True, "review_id": result[0]}
    return None

def insert_product_review(review_data: dict, idempotency_key: str) -> dict:
    """Insert a product review with idempotency check."""
    # Actual implementation would insert into database
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO product_reviews (product_id, user_id, rating, comment, idempotency_key)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING review_id
            """,
            (
                review_data["product_id"],
                review_data["user_id"],
                review_data["rating"],
                review_data.get("comment", ""),
                idempotency_key
            )
        )
        review_id = cur.fetchone()[0]
        conn.commit()
    return {"success": True, "review_id": review_id}

# Placeholder for existing DB connection function
def get_db_connection():
    # Existing implementation goes here
    pass

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

# Python
import os
import simplejson as json

# Postgres
import psycopg2

def must_map_env(key: str):
    value = os.environ.get(key)
    if value is None:
        raise Exception(f'{key} environment variable must be set')
    return value

# Retrieve Postgres environment variables
db_connection_str = must_map_env('DB_CONNECTION_STRING')

def fetch_product_reviews(product_id):
    try:
        return json.dumps(fetch_product_reviews_from_db(product_id), use_decimal=True)
    except Exception as e:
        return json.dumps({"error": str(e)})

def fetch_product_reviews_from_db(request_product_id):

    connection = None

    try:
        with psycopg2.connect(db_connection_str) as connection:

            with connection.cursor() as cursor:
                # Define the SQL query
                query = "SELECT username, description, score FROM reviews.productreviews WHERE product_id= %s"

                # Execute the query
                cursor.execute(query, (request_product_id, ))

                # Fetch all the rows from the query result
                records = cursor.fetchall()
                return records

    except Exception as e:
        raise e
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as e:
                pass

def fetch_avg_product_review_score_from_db(request_product_id):

    connection = None

    try:
        with psycopg2.connect(db_connection_str) as connection:

            with connection.cursor() as cursor:
                # Define the SQL query
                query = "SELECT AVG(score) FROM reviews.productreviews WHERE product_id= %s"

                # Execute the query
                cursor.execute(query, (request_product_id, ))

                # Fetch all the rows from the query result
                records = cursor.fetchall()

                # Extract the average score
                if records:
                    # records will be a list like [(average_score,)]
                    average_score = records[0][0]
                else:
                    # Handle the case where no records are returned (e.g., no reviews for the product)
                    average_score = None

                # return the score as a string rounded to 1 decimal place
                return f"{average_score:.1f}"

    except Exception as e:
        raise e
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as e:
                pass
