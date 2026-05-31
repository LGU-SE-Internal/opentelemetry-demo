import os
# Validate MAX_REQUESTS_PER_SECOND environment variable if provided
max_rps = os.environ.get("MAX_REQUESTS_PER_SECOND")
if max_rps is not None:
    try:
        max_rps_int = int(max_rps)
        if max_rps_int <= 0:
            raise ValueError("Value must be a positive integer")
    except ValueError as e:
        raise ValueError(f"Invalid MAX_REQUESTS_PER_SECOND environment variable: {e}. Please provide a valid positive integer.") from e
print("Validation passed, value is", max_rps_int if max_rps else "not set")
