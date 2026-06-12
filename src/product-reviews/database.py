import logging
import json
import psycopg2
from psycopg2 import errors
from typing import Callable, Any, Optional, TypeVar
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryCallState,
)
from pybreaker import CircuitBreaker, CircuitBreakerListener, CircuitBreakerError
from opentelemetry import metrics
import os
import simplejson as json
import grpc

logger = logging.getLogger(__name__)

# Initialize OTel metrics
meter = metrics.get_meter("product-reviews")
db_operation_retry_counter = meter.create_counter(
    "db_operation_retry_count",
    description="Number of retry attempts for database operations",
)

# Circuit Breaker Metrics as per requirements
cb_state_gauge = meter.create_gauge(
    "product_reviews.db.circuit_breaker.state",
    description="Current circuit state: 0 = CLOSED, 1 = HALF_OPEN, 2 = OPEN"
)
cb_state_transitions_counter = meter.create_counter(
    "product_reviews.db.circuit_breaker.state_transitions_total",
    description="Count of circuit state transition events"
)
cb_requests_counter = meter.create_counter(
    "product_reviews.db.circuit_breaker.requests_total",
    description="Count of requests processed by circuit breaker"
)

T = TypeVar("T")

# Load circuit breaker configuration from environment variables
CB_ENABLED = os.environ.get("PRODUCT_REVIEWS_DB_CIRCUIT_BREAKER_ENABLED", "true").lower() == "true"
CB_FAILURE_THRESHOLD = int(os.environ.get("PRODUCT_REVIEWS_DB_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
CB_RECOVERY_TIMEOUT = int(os.environ.get("PRODUCT_REVIEWS_DB_CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "30"))
CB_SUCCESS_THRESHOLD = int(os.environ.get("PRODUCT_REVIEWS_DB_CIRCUIT_BREAKER_SUCCESS_THRESHOLD", "3"))

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
    TimeoutError,
)

# Non-transient errors (not retried)
NON_RETRIABLE_ERRORS = (
    psycopg2.IntegrityError,
    psycopg2.ProgrammingError,
    ValueError,
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
            "retry_attempt": attempt_number,
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
    def state_change(self, cb, old_state, new_state):
        # Map state names to enum values
        state_map = {
            "closed": 0,
            "half_open": 1,
            "open": 2
        }
        old_state_name = old_state.__class__.__name__.lower().replace("state", "")
        new_state_name = new_state.__class__.__name__.lower().replace("state", "")
        
        # Update state gauge
        cb_state_gauge.set(
            state_map[new_state_name],
            {"service": "product-reviews"}
        )
        
        # Increment transition counter
        cb_state_transitions_counter.add(
            1,
            {
                "service": "product-reviews",
                "from_state": old_state_name,
                "to_state": new_state_name
            }
        )
        
        logger.info(f"Database circuit breaker transitioned from {old_state_name} to {new_state_name}")

# Initialize circuit breaker if enabled
db_circuit_breaker: Optional[CircuitBreaker] = None
if CB_ENABLED:
    db_circuit_breaker = CircuitBreaker(
        fail_max=CB_FAILURE_THRESHOLD,
        reset_timeout=CB_RECOVERY_TIMEOUT,
        expected_exception=RETRIABLE_ERRORS,
        listeners=[CircuitBreakerMetricsListener()]
    )
    # Set initial state gauge to closed
    cb_state_gauge.set(0, {"service": "product-reviews"})

def with_db_circuit_breaker(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator that wraps PostgreSQL database operations with circuit breaker logic.
    Raises: grpc.StatusCode.UNAVAILABLE when circuit is OPEN, no database call is executed.
    """
    def wrapper(*args, **kwargs) -> T:
        if not CB_ENABLED or not db_circuit_breaker:
            # Circuit breaker disabled, call function directly
            return func(*args, **kwargs)
        
        current_state = db_circuit_breaker.current_state.__class__.__name__.lower().replace("state", "")
        
        try:
            result = db_circuit_breaker.call(func, *args, **kwargs)
            # Request succeeded
            cb_requests_counter.add(
                1,
                {
                    "service": "product-reviews",
                    "state": current_state,
                    "result": "success"
                }
            )
            return result
        except CircuitBreakerError:
            # Circuit is open, request rejected
            cb_requests_counter.add(
                1,
                {
                    "service": "product-reviews",
                    "state": current_state,
                    "result": "rejected"
                }
            )
            raise grpc.RpcError(
                grpc.StatusCode.UNAVAILABLE,
                "Product reviews database is temporarily unavailable, please try again later"
            )
        except Exception as e:
            # Request failed but circuit not open yet
            cb_requests_counter.add(
                1,
                {
                    "service": "product-reviews",
                    "state": current_state,
                    "result": "failure"
                }
            )
            raise e
    return wrapper

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

@postgres_read_retry()
@with_db_circuit_breaker
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
@with_db_circuit_breaker
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

@with_db_circuit_breaker
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
                    "id": result[0],
                    "product_id": product_id,
                    "user_id": user_id,
                    "rating": rating,
                    "comment": comment,
                    "created_at": result[1].isoformat()
                }
    except Exception as e:
        if connection:
            connection.rollback()
        raise e
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception as e:
                pass

# Attach breaker to module for test access
_db_circuit_breaker = db_circuit_breaker

# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0
