#!/usr/bin/env python3
import os
import re
import subprocess
import pytest

# Paths per spec
QUOTE_ENTRYPOINT = "./src/quote/public/index.php"
QUOTE_SOURCE_DIR = "./src/quote/"
UNSTRUCTURED_LOG_FUNCTIONS = ["echo", "print", "var_dump", "print_r", "fwrite(STDOUT", "fwrite(STDERR"]
ACCEPTABLE_UNSTRUCTURED = ["exception handler", "exit with error message"]

def test_ac1_no_unstructured_logging_in_entrypoint():
    """AC-1: All unstructured logging statements in entrypoint are replaced with PSR-3 logger calls"""
    with open(QUOTE_ENTRYPOINT, "r") as f:
        content = f.read()
    
    # Count unstructured logging calls
    unstructured_calls = []
    for func in UNSTRUCTURED_LOG_FUNCTIONS:
        matches = list(re.finditer(rf"\b{func}\b", content))
        if matches:
            for match in matches:
                # Check if this is in an acceptable context (only allowed in explicit exception exit cases)
                line_start = content.rfind("\n", 0, match.start()) + 1
                line_end = content.find("\n", match.end())
                line = content[line_start:line_end].strip()
                # Skip lines that are in error exit blocks before logger is initialized?
                # Wait no: spec says all operational logs must be replaced, including error exit logs
                unstructured_calls.append(f"{func} at line {content.count('\n', 0, match.start()) + 1}: {line}")
    
    # The only allowed unstructured calls are those in explicit exception handler output (per AC-5 note)
    assert len(unstructured_calls) == 0, f"Found unstructured logging calls in entrypoint: {unstructured_calls}"
    
    # Verify all logging uses existing PSR-3 logger instance
    assert "$logger->" in content, "PSR-3 logger instance not used in entrypoint"
    assert "$logger->info(" in content or "$logger->debug(" in content or "$logger->error(" in content, "No logger method calls found in entrypoint"


def test_ac2_correct_log_levels_used():
    """AC-2: Log levels are info for normal startup, debug for runtime messages, no error level for normal operation"""
    with open(QUOTE_ENTRYPOINT, "r") as f:
        content = f.read()
    
    # Startup messages: "service ready", "listening on address" should be info level
    startup_keywords = ["serving", "listening", "service ready", "server started"]
    info_calls = list(re.finditer(r"\$logger->info\((.*?)\)", content, re.DOTALL))
    debug_calls = list(re.finditer(r"\$logger->debug\((.*?)\)", content, re.DOTALL))
    error_calls = list(re.finditer(r"\$logger->error\((.*?)\)", content, re.DOTALL))
    
    # Check all startup messages are info level
    startup_found = False
    for call in info_calls:
        msg = call.group(1).strip()
        for keyword in startup_keywords:
            if keyword in msg.lower():
                startup_found = True
                break
    assert startup_found, "No info level startup messages found (expected service listening/ready logs)"
    
    # Check request logs are debug level
    runtime_keywords = ["request", "method", "path", "status", "client ip"]
    runtime_found = False
    for call in debug_calls:
        msg = call.group(1).strip()
        for keyword in runtime_keywords:
            if keyword in msg.lower():
                runtime_found = True
                break
    assert runtime_found, "No debug level runtime/request messages found (expected request logging)"
    
    # Check no error level logs for normal operation events
    for call in error_calls:
        msg = call.group(1).strip()
        # Error logs should only be for actual failures, not normal operation
        normal_ops_keywords = ["service ready", "listening", "request completed", "shutdown signal received"]
        for keyword in normal_ops_keywords:
            assert keyword not in msg.lower(), f"Error level log used for normal operation event: {msg}"


def test_ac3_metadata_in_structured_context():
    """AC-3: All metadata fields are passed as structured context array, not embedded in log messages"""
    with open(QUOTE_ENTRYPOINT, "r") as f:
        content = f.read()
    
    # Metadata fields that should be in context
    metadata_fields = ["ip", "address", "tls", "client_ip", "listen_address", "tls_status", "request_id", "trace_id", "status_code", "method", "path"]
    
    # Check logger calls for embedded metadata in message strings
    logger_calls = list(re.finditer(r"\$logger->(info|debug|error)\((.*?)\)", content, re.DOTALL))
    for call in logger_calls:
        log_level = call.group(1)
        call_content = call.group(2)
        
        # Split message and context
        parts = call_content.split(",", 1)
        if len(parts) < 2:
            # No context provided, check if message has embedded metadata
            msg = parts[0].strip()
            for field in metadata_fields:
                assert field not in msg.lower(), f"Metadata field '{field}' embedded in log message instead of context: {msg}"
            continue
        
        msg, context_part = parts
        msg = msg.strip()
        context_part = context_part.strip()
        
        # Check message doesn't contain metadata fields
        for field in metadata_fields:
            assert field not in msg.lower(), f"Metadata field '{field}' embedded in log message instead of context: {msg}"
        
        # Check context array has the metadata fields
        assert "[" in context_part and "]" in context_part, f"Context array missing from {log_level} log call"
        for field in metadata_fields:
            # At least some metadata fields should be present in context
            if field in ["client_ip", "tls_status", "listen_address", "status_code", "method", "path"]:
                assert field in context_part, f"Expected metadata field '{field}' missing from log context"


def test_ac4_all_original_log_information_preserved():
    """AC-4: All information from original unstructured logs is preserved in structured logs"""
    with open(QUOTE_ENTRYPOINT, "r") as f:
        current_content = f.read()
    
    # Original log messages we expect to be preserved
    original_log_contents = [
        "Overwriting Localhost IP",
        "TLS enabled, serving HTTPS on",
        "Serving plain HTTP on",
        "HTTP/",  # Request log line
        "status code",
        "method",
        "path",
        "client IP"
    ]
    
    # Check all original content is present in logger calls
    for content in original_log_contents:
        found = False
        # Check in message strings
        for match in re.finditer(r"\$logger->(info|debug|error)\((.*?)\)", current_content, re.DOTALL):
            if content.lower() in match.group(2).lower():
                found = True
                break
        assert found, f"Original log content '{content}' missing from structured logs"


def test_ac5_no_unstructured_logging_in_quote_service_source():
    """AC-5: Recursive search of all PHP files under src/quote/ returns zero unstructured logging statements outside exception handlers"""
    unstructured_calls = []
    
    for root, _, files in os.walk(QUOTE_SOURCE_DIR):
        for file in files:
            if file.endswith(".php"):
                filepath = os.path.join(root, file)
                with open(filepath, "r") as f:
                    content = f.read()
                
                for func in UNSTRUCTURED_LOG_FUNCTIONS:
                    matches = list(re.finditer(rf"\b{func}\b", content))
                    if matches:
                        for match in matches:
                            # Check if this is in an allowed exception handler context
                            line_start = content.rfind("\n", 0, match.start()) + 1
                            line_end = content.find("\n", match.end())
                            line = content[line_start:line_end].strip()
                            
                            # Allow unstructured only in exit blocks before logger is initialized (explicit exception handler output)
                            allowed = False
                            if "exit" in line or "throw" in line or "Exception" in line:
                                allowed = True
                            # Check surrounding context for exception handler
                            surrounding_start = max(0, match.start() - 200)
                            surrounding_end = min(len(content), match.end() + 200)
                            surrounding = content[surrounding_start:surrounding_end].lower()
                            if "catch" in surrounding or "exception" in surrounding or "error handler" in surrounding:
                                allowed = True
                            
                            if not allowed:
                                unstructured_calls.append(f"{func} in {filepath} at line {content.count('\n', 0, match.start()) + 1}: {line}")
    
    assert len(unstructured_calls) == 0, f"Found unallowed unstructured logging calls in quote service source: {unstructured_calls}"
