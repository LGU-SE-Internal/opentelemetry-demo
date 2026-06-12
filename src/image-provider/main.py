import os
import logging
from urllib.parse import unquote
from typing import Tuple
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from opentelemetry import trace

# Configuration constants
IMAGE_STATIC_ROOT = os.environ.get("IMAGE_STATIC_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "static")))
ALLOWED_EXTENSIONS = os.environ.get("ALLOWED_IMAGE_EXTENSIONS", ".jpg,.jpeg,.png,.gif,.webp").split(",")
# Ensure static root is absolute path
IMAGE_STATIC_ROOT = os.path.abspath(IMAGE_STATIC_ROOT)

# Initialize FastAPI app
app = FastAPI(title="Image Provider Service")

# Configure structured logging
class StructuredFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "trace_id": getattr(record, "trace_id", "unknown"),
            "span_id": getattr(record, "span_id", "unknown"),
            "client_ip": getattr(record, "client_ip", "unknown"),
            "requested_path": getattr(record, "requested_path", "unknown"),
            "error_reason": getattr(record, "error_reason", "unknown"),
            "message": record.getMessage()
        }
        import json
        return json.dumps(log_record)

handler = logging.StreamHandler()
handler.setFormatter(StructuredFormatter())
logger = logging.getLogger(__name__)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

def validate_image_path(requested_path: str, static_root: str) -> Tuple[bool, str]:
    """
    Validates requested image path against traversal attacks and allowed extensions.
    
    Args:
        requested_path: Raw path from user request
        static_root: Absolute path to the static images directory
    
    Returns:
        (is_valid: bool, error_message: str) - is_valid True if path is acceptable, else False with error reason
    """
    # First, decode URL encoded path
    decoded_path = unquote(requested_path)
    
    # Check for null bytes or invalid characters
    if "\x00" in decoded_path:
        return False, "Path contains invalid null bytes"
    
    # Normalize the path to remove traversal sequences
    normalized_path = os.path.normpath(decoded_path)
    
    # If path starts with / or ~ it's an absolute/home path, invalid
    if normalized_path.startswith("/") or normalized_path.startswith("~"):
        return False, "Absolute or home directory paths not allowed"
    
    # Build full absolute path to the requested file
    full_path = os.path.abspath(os.path.join(static_root, normalized_path))
    
    # Verify the full path is within the static root directory
    if not full_path.startswith(static_root + os.sep) and full_path != static_root:
        return False, "Path traversal attempt detected: path resolves outside static directory"
    
    # Check file extension is allowed
    file_ext = os.path.splitext(full_path)[1].lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        return False, f"File extension {file_ext} not in allowed extensions list"
    
    # Check that the path points to a file that exists
    if not os.path.isfile(full_path):
        return False, "Requested file does not exist"
    
    return True, full_path

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 400 and isinstance(exc.detail, dict) and exc.detail.get("code") == "INVALID_IMAGE_PATH":
        return JSONResponse(
            status_code=400,
            content=exc.detail
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

@app.get("/images/{image_path:path}")
async def get_image(request: Request, image_path: str):
    # Get client IP address
    client_ip = request.client.host if request.client else "unknown"
    
    # Get current trace context
    current_span = trace.get_current_span()
    trace_id = format(current_span.get_span_context().trace_id, "016x") if current_span.is_recording() else "unknown"
    span_id = format(current_span.get_span_context().span_id, "016x") if current_span.is_recording() else "unknown"
    
    try:
        # Validate the requested path
        is_valid, result = validate_image_path(image_path, IMAGE_STATIC_ROOT)
        if not is_valid:
            # Log structured warning for security audit
            logger.warning(
                "Invalid image path request",
                extra={
                    "trace_id": trace_id,
                    "span_id": span_id,
                    "client_ip": client_ip,
                    "requested_path": image_path,
                    "error_reason": result
                }
            )
            raise HTTPException(
                status_code=400,
                detail={"error": "Invalid image path requested", "code": "INVALID_IMAGE_PATH"}
            )
        
        full_path = result
        
        # Return the image file with correct content type
        return FileResponse(full_path)
    
    except HTTPException as e:
        # Re-raise HTTP exceptions (we already handled logging for 400s)
        raise e
    except Exception as e:
        # Catch any other exceptions to ensure we don't return 500
        error_reason = f"Unexpected error processing path: {str(e)}"
        logger.warning(
            "Invalid image path request",
            extra={
                "trace_id": trace_id,
                "span_id": span_id,
                "client_ip": client_ip,
                "requested_path": image_path,
                "error_reason": error_reason
            }
        )
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid image path requested", "code": "INVALID_IMAGE_PATH"}
        )
