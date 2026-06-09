import logging
import json
import psycopg2
from psycopg2 import errors
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extensions import connection
from dataclasses import dataclass
from typing import Callable, Any, Optional
import os
import time
from opentelemetry import metrics

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    RetryCallState,
)

logger = logging.getLogger(__name__)
meter = metrics.get_meter("product-reviews.service")

# Metrics definitions
active_connections_gauge = meter.create_up_down_counter(
    name="db.pool.connections.active",
    description="Number of currently in-use connections",
    unit="connections"
)
idle_connections_gauge = meter.create_up_down_counter(
    name="db.pool.connections.idle",
    description="Number of idle available connections",
    unit="connections"
)
wait_time_histogram = meter.create_histogram(
    name="db.pool.connections.wait_time",
    description="Time spent waiting to acquire a connection from the pool",
    unit="milliseconds"
)
stale_rejected_counter = meter.create_counter(
    name="db.pool.connections.stale_rejected",
    description="Number of stale/broken connections rejected during validation",
    unit="connections"
)

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


@dataclass
class PoolConfig:
    max_connections: int = 20
    idle_timeout: int = 300  # seconds
    max_connection_lifetime: int = 3600  # seconds
    db_host: Optional[str] = None
    db_port: Optional[int] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None
    db_name: Optional[str] = None
    db_connection_string: Optional[str] = None


class PostgresConnectionPool:
    def __init__(self, config: PoolConfig) -> None:
        """Initialize connection pool with provided configuration"""
        self.config = config
        self._pool: Optional[SimpleConnectionPool] = None
        self._connection_timestamps: dict[connection, float] = {}
        self._idle_since: dict[connection, float] = {}

        if config.db_connection_string:
            self._pool = SimpleConnectionPool(
                minconn=1,
                maxconn=config.max_connections,
                dsn=config.db_connection_string
            )
        else:
            self._pool = SimpleConnectionPool(
                minconn=1,
                maxconn=config.max_connections,
                host=config.db_host,
                port=config.db_port,
                user=config.db_user,
                password=config.db_password,
                dbname=config.db_name
            )

        # Initialize idle gauge
        idle_connections_gauge.add(self._pool.maxconn - self._pool.closed)

    def acquire(self) -> connection:
        """Acquire a valid connection from the pool, validating before return"""
        start_time = time.time()
        conn = self._pool.getconn()
        wait_ms = (time.time() - start_time) * 1000
        wait_time_histogram.record(wait_ms)

        # Validate connection
        valid = False
        try:
            # Check connection lifetime
            create_time = self._connection_timestamps.get(conn, None)
            if create_time and (time.time() - create_time) > self.config.max_connection_lifetime:
                stale_rejected_counter.add(1)
                raise psycopg2.OperationalError("Connection expired")

            # Check if connection is alive
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            valid = True
        except Exception as e:
            logger.debug(f"Rejecting stale connection: {str(e)}")
            stale_rejected_counter.add(1)
            # Remove bad connection
            self._pool.putconn(conn, close=True)
            if conn in self._connection_timestamps:
                del self._connection_timestamps[conn]
            if conn in self._idle_since:
                del self._idle_since[conn]
            # Recursively get new connection
            return self.acquire()

        # Update metrics and timestamps
        if conn in self._idle_since:
            idle_connections_gauge.add(-1)
            del self._idle_since[conn]
        active_connections_gauge.add(1)

        if conn not in self._connection_timestamps:
            self._connection_timestamps[conn] = time.time()

        return conn

    def release(self, conn: connection) -> None:
        """Return connection back to the pool for reuse"""
        if conn.closed:
            # Connection is already closed, discard it
            self._pool.putconn(conn, close=True)
            if conn in self._connection_timestamps:
                del self._connection_timestamps[conn]
            active_connections_gauge.add(-1)
            return

        # Check if idle timeout is reached
        idle_since = time.time()
        self._idle_since[conn] = idle_since
        self._pool.putconn(conn)
        active_connections_gauge.add(-1)
        idle_connections_gauge.add(1)

        # Evict idle connections in background (simplified)
        self._evict_idle_connections()

    def _evict_idle_connections(self) -> None:
        """Evict connections that have been idle longer than idle_timeout"""
        now = time.time()
        to_evict = []
        for conn, idle_since in self._idle_since.items():
            if (now - idle_since) > self.config.idle_timeout:
                to_evict.append(conn)

        for conn in to_evict:
            self._pool.putconn(conn, close=True)
            del self._idle_since[conn]
            if conn in self._connection_timestamps:
                del self._connection_timestamps[conn]
            idle_connections_gauge.add(-1)

    def _sync_close(self) -> None:
        """Close all open connections in the pool on service shutdown"""
        if self._pool:
            self._pool.closeall()
        # Reset metrics
        active_connections_gauge.add(-active_connections_gauge.get())
        idle_connections_gauge.add(-idle_connections_gauge.get())

    async def close(self) -> None:
        """Async wrapper for close method to support graceful shutdown in async server"""
        self._sync_close()


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


# Initialize connection pool
def must_map_env(key: str):
    value = os.environ.get(key)
    if value is None:
        raise Exception(f'{key} environment variable must be set')
    return value

# Retrieve Postgres environment variables
db_connection_str = must_map_env('DB_CONNECTION_STRING')

# Read pool configuration from environment variables
pool_config = PoolConfig(
    max_connections=int(os.environ.get('POSTGRES_POOL_MAX_CONN', 20)),
    idle_timeout=int(os.environ.get('POSTGRES_POOL_IDLE_TIMEOUT', 300)),
    max_connection_lifetime=int(os.environ.get('POSTGRES_POOL_MAX_LIFETIME', 3600)),
    db_connection_string=db_connection_str
)

# Global pool instance
db_pool = PostgresConnectionPool(pool_config)


def check_existing_idempotency_key(idempotency_key: str) -> Any:
    """Check if an idempotency key already exists in the product_reviews table."""
    # Actual implementation would query the database
    conn = db_pool.acquire()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT review_id FROM product_reviews WHERE idempotency_key = %s",
                (idempotency_key,)
            )
            result = cur.fetchone()
            if result:
                return {"success": True, "review_id": result[0]}
        return None
    finally:
        db_pool.release(conn)


def insert_product_review(review_data: dict, idempotency_key: str) -> dict:
    """Insert a product review with idempotency check."""
    # Actual implementation would insert into database
    conn = db_pool.acquire()
    try:
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
    finally:
        db_pool.release(conn)


def fetch_product_reviews(product_id):
    try:
        return json.dumps(fetch_product_reviews_from_db(product_id), use_decimal=True)
    except Exception as e:
        return json.dumps({"error": str(e)})


def fetch_product_reviews_from_db(request_product_id):
    conn = db_pool.acquire()
    try:
        with conn.cursor() as cursor:
            # Define the SQL query
            query = "SELECT username, description, score FROM reviews.productreviews WHERE product_id= %s"

            # Execute the query
            cursor.execute(query, (request_product_id, ))

            # Fetch all the rows from the query result
            records = cursor.fetchall()
            return records
    finally:
        db_pool.release(conn)


def fetch_avg_product_review_score_from_db(request_product_id):
    conn = db_pool.acquire()
    try:
        with conn.cursor() as cursor:
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
    finally:
        db_pool.release(conn)


@postgres_retry(operation_type="read")
def get_product_reviews(product_id: str, limit: int = 10, offset: int = 0) -> list[dict]:
    """Retrieves paginated reviews for a given product ID, no side effects"""
    conn = db_pool.acquire()
    try:
        with conn.cursor() as cursor:
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
    finally:
        db_pool.release(conn)


@postgres_retry(operation_type="read")
def get_review_summary(product_id: str) -> dict:
    """Returns aggregated review metrics (average rating, count) for a given product ID, no side effects"""
    conn = db_pool.acquire()
    try:
        with conn.cursor() as cursor:
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
    finally:
        db_pool.release(conn)
