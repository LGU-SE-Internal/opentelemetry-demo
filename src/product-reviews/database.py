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
base_db_conn_str = must_map_env('DB_CONNECTION_STRING')

# TLS configuration for PostgreSQL
db_tls_mode = os.environ.get('PRODUCT_REVIEWS_DB_TLS_MODE', 'disable')
db_tls_ca_cert = os.environ.get('PRODUCT_REVIEWS_DB_TLS_CA_CERT_PATH')
db_tls_client_cert = os.environ.get('PRODUCT_REVIEWS_DB_TLS_CLIENT_CERT_PATH')
db_tls_client_key = os.environ.get('PRODUCT_REVIEWS_DB_TLS_CLIENT_KEY_PATH')

# Build connection string with TLS parameters
db_connection_str = base_db_conn_str
tls_params = []

if db_tls_mode != 'disable':
    tls_params.append(f'sslmode={db_tls_mode}')
    if db_tls_ca_cert:
        # Validate CA cert exists and is readable
        if not os.path.exists(db_tls_ca_cert) or not os.access(db_tls_ca_cert, os.R_OK):
            raise Exception(f"TLS configuration error: CA certificate file is missing or unreadable (path: {db_tls_ca_cert})")
        tls_params.append(f'sslrootcert={db_tls_ca_cert}')
    if db_tls_client_cert and db_tls_client_key:
        # Validate client cert and key exist and are readable
        if not os.path.exists(db_tls_client_cert) or not os.access(db_tls_client_cert, os.R_OK):
            raise Exception(f"TLS configuration error: Client certificate file is missing or unreadable (path: {db_tls_client_cert})")
        if not os.path.exists(db_tls_client_key) or not os.access(db_tls_client_key, os.R_OK):
            raise Exception(f"TLS configuration error: Client private key file is missing or unreadable (path: {db_tls_client_key})")
        tls_params.append(f'sslcert={db_tls_client_cert}')
        tls_params.append(f'sslkey={db_tls_client_key}')
    elif db_tls_client_cert or db_tls_client_key:
        raise Exception("TLS configuration error: Both client certificate and private key must be provided for database mTLS")

if tls_params:
    if '?' in db_connection_str:
        db_connection_str += '&' + '&'.join(tls_params)
    else:
        db_connection_str += '?' + '&'.join(tls_params)

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
@postgres_retry(operation_type="read")
def get_product_reviews(product_id: str, limit: int = 10, offset: int = 0) -> list[dict]:
    """Retrieves paginated reviews for a given product ID, no side effects"""
    connection = None
    try:
        with psycopg2.connect(db_connection_str) as connection:
            with connection.cursor() as cursor:
                query = """
                    SELECT username, description, score 
                    FROM reviews.productreviews 
                    WHERE product_id= %s
                    LIMIT %s OFFSET %s
                """
                cursor.execute(query, (product_id, limit, offset))
                records = cursor.fetchall()
                return [
                    {
                        "username": record[0],
                        "description": record[1],
                        "rating": float(record[2])
                    } for record in records
                ]
    except Exception as e:
        raise e
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as e:
                pass

@postgres_retry(operation_type="read")
def get_review_summary(product_id: str) -> dict:
    """Returns aggregated review metrics (average rating, count) for a given product ID, no side effects"""
    connection = None
    try:
        with psycopg2.connect(db_connection_str) as connection:
            with connection.cursor() as cursor:
                query = """
                    SELECT AVG(score) as average_rating, COUNT(*) as review_count
                    FROM reviews.productreviews 
                    WHERE product_id= %s
                """
                cursor.execute(query, (product_id,))
                record = cursor.fetchone()
                average_rating = float(record[0]) if record[0] is not None else None
                return {
                    "average_rating": round(average_rating, 1) if average_rating else None,
                    "review_count": int(record[1])
                }
    except Exception as e:
        raise e
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as e:
                pass
