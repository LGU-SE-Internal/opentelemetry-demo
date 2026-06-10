#!/usr/bin/python
# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0

import os
import ssl
import pybreaker
from typing import Optional, Any, Type
from openfeature.contrib.provider.flagd import FlagdProvider
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode, OpenFeatureError
from openfeature.provider import Metadata


class ResilientFlagdProvider:
    def __init__(self):
        # Load configuration from environment variables
        self._load_config()
        self._validate_config()
        
        # Setup TLS context if enabled
        ssl_context = None
        if self.tls_enabled:
            ssl_context = ssl.create_default_context(cafile=self.tls_cert_path if self.tls_cert_path else None)
            if self.tls_client_cert_path and self.tls_client_key_path:
                ssl_context.load_cert_chain(
                    certfile=self.tls_client_cert_path,
                    keyfile=self.tls_client_key_path
                )
            if self.tls_insecure_skip_verify:
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
        
        # Initialize underlying Flagd provider
        self.flagd_provider = FlagdProvider(
            host=os.environ.get("FLAGD_HOST", "localhost"),
            port=int(os.environ.get("FLAGD_PORT", "8013")),
            timeout=self.timeout / 1000,  # Convert ms to seconds
            ssl_context=ssl_context
        )
        
        # Initialize circuit breaker
        self.circuit_breaker = pybreaker.CircuitBreaker(
            fail_max=self.circuit_breaker_failure_threshold,
            reset_timeout=self.circuit_breaker_recovery_timeout / 1000,  # Convert ms to seconds
            on_failure=self._on_circuit_failure
        )

    def _load_config(self):
        self.timeout = int(os.environ.get("FLAGD_TIMEOUT", "2000"))
        self.circuit_breaker_failure_threshold = int(os.environ.get("FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
        self.circuit_breaker_recovery_timeout = int(os.environ.get("FLAGD_CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "30000"))
        self.tls_enabled = os.environ.get("FLAGD_TLS_ENABLED", "false").lower() == "true"
        self.tls_cert_path = os.environ.get("FLAGD_TLS_CERT_PATH", "")
        self.tls_client_cert_path = os.environ.get("FLAGD_TLS_CLIENT_CERT_PATH", "")
        self.tls_client_key_path = os.environ.get("FLAGD_TLS_CLIENT_KEY_PATH", "")
        self.tls_insecure_skip_verify = os.environ.get("FLAGD_TLS_INSECURE_SKIP_VERIFY", "false").lower() == "true"

    def _validate_config(self):
        errors = []
        if self.timeout <= 0:
            errors.append(f"FLAGD_TIMEOUT must be a positive integer, got {self.timeout}")
        
        if self.circuit_breaker_failure_threshold <= 0:
            errors.append(f"FLAGD_CIRCUIT_BREAKER_FAILURE_THRESHOLD must be a positive integer, got {self.circuit_breaker_failure_threshold}")
        
        if self.circuit_breaker_recovery_timeout <= 0:
            errors.append(f"FLAGD_CIRCUIT_BREAKER_RECOVERY_TIMEOUT must be a positive integer, got {self.circuit_breaker_recovery_timeout}")
        
        if self.tls_enabled:
            if self.tls_cert_path and not os.path.isfile(self.tls_cert_path):
                errors.append(f"FLAGD_TLS_CERT_PATH '{self.tls_cert_path}' does not exist or is not readable")
            
            if (self.tls_client_cert_path and not self.tls_client_key_path) or (self.tls_client_key_path and not self.tls_client_cert_path):
                errors.append("Both FLAGD_TLS_CLIENT_CERT_PATH and FLAGD_TLS_CLIENT_KEY_PATH must be provided for mTLS")
            
            if self.tls_client_cert_path and not os.path.isfile(self.tls_client_cert_path):
                errors.append(f"FLAGD_TLS_CLIENT_CERT_PATH '{self.tls_client_cert_path}' does not exist or is not readable")
            
            if self.tls_client_key_path and not os.path.isfile(self.tls_client_key_path):
                errors.append(f"FLAGD_TLS_CLIENT_KEY_PATH '{self.tls_client_key_path}' does not exist or is not readable")
        
        if errors:
            raise ValueError("Invalid feature flag configuration:\n" + "\n".join(f"- {err}" for err in errors))

    def _on_circuit_failure(self, exc):
        # Log failure if needed
        pass

    def _run_with_resilience(self, func, flag_name: str, default_value: Any, context: Optional[EvaluationContext] = None) -> Any:
        try:
            return self.circuit_breaker(func, flag_key=flag_name, context=context)
        except (pybreaker.CircuitBreakerError, OpenFeatureError, Exception):
            # Return default value on any failure
            return default_value

    def get_metadata(self) -> Metadata:
        return self.flagd_provider.get_metadata()

    def get_boolean_flag(self, flag_name: str, default_value: bool, context: Optional[EvaluationContext] = None) -> bool:
        return self._run_with_resilience(
            self.flagd_provider.get_boolean_flag,
            flag_name=flag_name,
            default_value=default_value,
            context=context
        )

    def get_string_flag(self, flag_name: str, default_value: str, context: Optional[EvaluationContext] = None) -> str:
        return self._run_with_resilience(
            self.flagd_provider.get_string_flag,
            flag_name=flag_name,
            default_value=default_value,
            context=context
        )

    def get_number_flag(self, flag_name: str, default_value: float, context: Optional[EvaluationContext] = None) -> float:
        return self._run_with_resilience(
            self.flagd_provider.get_number_flag,
            flag_name=flag_name,
            default_value=default_value,
            context=context
        )

    def get_struct_flag(self, flag_name: str, default_value: dict, context: Optional[EvaluationContext] = None) -> dict:
        return self._run_with_resilience(
            self.flagd_provider.get_struct_flag,
            flag_name=flag_name,
            default_value=default_value,
            context=context
        )

# Global provider instance
_provider_instance: Optional[ResilientFlagdProvider] = None

def init_flag_provider():
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = ResilientFlagdProvider()

def get_flag_provider() -> ResilientFlagdProvider:
    if _provider_instance is None:
        raise RuntimeError("Flag provider not initialized. Call init_flag_provider() first.")
    return _provider_instance

# Public API functions matching the requirement
def get_boolean_flag(flag_name: str, default_value: bool, context: Optional[dict] = None) -> bool:
    eval_context = EvaluationContext(context) if context else None
    return get_flag_provider().get_boolean_flag(flag_name, default_value, eval_context)

def get_string_flag(flag_name: str, default_value: str, context: Optional[dict] = None) -> str:
    eval_context = EvaluationContext(context) if context else None
    return get_flag_provider().get_string_flag(flag_name, default_value, eval_context)

def get_number_flag(flag_name: str, default_value: float, context: Optional[dict] = None) -> float:
    eval_context = EvaluationContext(context) if context else None
    return get_flag_provider().get_number_flag(flag_name, default_value, eval_context)
