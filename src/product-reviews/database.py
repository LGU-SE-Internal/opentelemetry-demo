import logging
import json
import psycopg2
from psycopg2 import errors
from typing import Callable, Any, Optional
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryCallState,
)
from pybreaker import CircuitBreaker, CircuitBreakerListener
from opentelemetry import metrics

logger = logging.getLogger(__name__)

# Initialize OTel metrics
meter = metrics.get_meter("product-reviews-service")
db_operation_retry_counter = meter.create_counter(
    "db_operation_retry_count",
    description="Number of retry attempts for database operations",
)
circuit_breaker_state_counter = meter.create_counter(
    "circuit_breaker_state_change",
    description="Number of circuit breaker state transitions",
)

# Retriable PostgreSQL error types
RETRIABLE_ERRORS = (
    psycopg2.OperationalError,
    psycopg2.ConnectionError,
    errors.DeadlockDetected,
    errors.LockNotAvailable,
    errors.ConnectionException,
    errors.AdminShutdown,
    errors.CrashShutdown,
    errors.CannotConnectNow,
)

# Non-transient errors (not retried)
NON_RETRIABLE_ERRORS = (
    psycopg2.IntegrityError,
    psycopg2.ProgrammingError,
)

def log_and_increment_retry(retry_state: RetryCallState):
    """Log retry attempt and increment OTel metric."""
    operation_name = retry_state.fn.__name__
    attempt_number = retry_state.attempt_number
    error = retry_state.outcome.exception()
    error_type = type(error).__name__
    
    # Increment retry counter
    db_operation_retry_counter.add(
        1,
        {
            "operation_name": operation_name,
            "error_type": error_type,
            "retry_attempt": str(attempt_number),
        }
    )
    
    # Log retry attempt
    log_entry = {
        "service": "product-reviews",
        "event": "postgres_retry_attempt",
        "operation_name": operation_name,
        "attempt_number": attempt_number,
        "max_attempts": 3,
        "error_type": error_type,
        "error_message": str(error),
    }
    logger.info(json.dumps(log_entry))

def postgres_read_retry() -> Callable:
    """
    Decorator to add exponential backoff retry for idempotent read PostgreSQL operations.
    Configured for 3 retries, initial 100ms delay, max 2s delay.
    """
    def decorator(func: Callable) -> Callable:
        @retry(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.1, min=0.1, max=2.0),
            retry=retry_if_exception_type(RETRIABLE_ERRORS),
            before_sleep=log_and_increment_retry,
            reraise=True
        )
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return decorator

# Circuit Breaker Listener for OTel metrics
class CircuitBreakerMetricsListener(CircuitBreakerListener):
    def __init__(self, operation_name: str):
        self.operation_name = operation_name
    
    def state_change(self, cb, old_state, new_state):
        state_name = new_state.__class__.__name__.lower().replace("state", "")
        circuit_breaker_state_counter.add(
            1,
            {
                "state": state_name,
                "operation_name": self.operation_name,
            }
        )
        logger.info(f"Circuit breaker for {self.operation_name} transitioned from {old_state} to {new_state}")

# Circuit breaker for create_review operation
create_review_circuit_breaker = CircuitBreaker(
    fail_max=5,
    reset_timeout=30,
    listeners=[CircuitBreakerMetricsListener("create_review")]
)

@postgres_read_retry()
def fetch_product_reviews(product_id: str, page: int = 1, limit: int = 20) -> list[dict]:
    """Fetch paginated reviews for a product, retries on transient DB errors"""
    offset = (page - 1) * limit
    connection = None
    try:
        with psycopg2.connect(db_connection_str) as connection:
            with connection.cursor() as cursor:
                query = """
                    SELECT username, description, score 
                    FROM reviews.productreviews 
                    WHERE product_id = %s
                    LIMIT %s OFFSET %s
                """
                cursor.execute(query, (product_id, limit, offset))
                records = cursor.fetchall()
                return [
                    {
                        "username": record[0],
                        "comment": record[1],
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

@postgres_read_retry()
def fetch_average_review_score(product_id: str) -> float:
    """Fetch average review score for a product, retries on transient DB errors"""
    connection = None
    try:
        with psycopg2.connect(db_connection_str) as connection:
            with connection.cursor() as cursor:
                query = """
                    SELECT AVG(score) FROM reviews.productreviews 
                    WHERE product_id = %s
                """
                cursor.execute(query, (product_id,))
                result = cursor.fetchone()
                return float(result[0]) if result[0] is not None else 0.0
    except Exception as e:
        raise e
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as e:
                pass

@create_review_circuit_breaker
def create_review(product_id: str, user_id: str, rating: int, comment: str | None) -> dict:
    """Create a new product review, uses circuit breaker for transient failure protection"""
    connection = None
    try:
        with psycopg2.connect(db_connection_str) as connection:
            with connection.cursor() as cursor:
                query = """
                    INSERT INTO reviews.productreviews (product_id, user_id, score, description)
                    VALUES (%s, %s, %s, %s)
                    RETURNING review_id, created_at
                """
                cursor.execute(query, (product_id, user_id, rating, comment or ""))
                result = cursor.fetchone()
                connection.commit()
                return {
                    "review_id": result[0],
                    "product_id": product_id,
                    "user_id": user_id,
                    "rating": rating,
                    "comment": comment,
                    "created_at": result[1].isoformat()
                }
    except Exception as e:
        connection.rollback() if connection else None
        raise e
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as e:
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
