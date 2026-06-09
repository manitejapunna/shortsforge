"""Secrets retrieval — env vars + OS keyring, with structlog redaction filter."""

from __future__ import annotations

import os
import re
from typing import Any

import structlog

# Patterns for redacting secrets in log records
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9\-_]{10,}"),           # OpenAI keys
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),              # GitHub PAT
    re.compile(r"ghs_[A-Za-z0-9]{36,}"),              # GitHub Actions token
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*"),   # Bearer tokens
    re.compile(r"ya29\.[A-Za-z0-9\-_]+"),             # Google OAuth tokens
    re.compile(r"AIza[A-Za-z0-9\-_]{35}"),            # Google API keys
]

_REDACTED = "[REDACTED]"


def redact(value: str) -> str:
    """Replace known secret patterns in *value* with [REDACTED]."""
    for pat in _SECRET_PATTERNS:
        value = pat.sub(_REDACTED, value)
    return value


class SecretRedactionProcessor:
    """structlog processor that redacts secrets from log event dicts."""

    def __call__(self, logger: Any, method: str, event_dict: dict) -> dict:
        for key, value in event_dict.items():
            if isinstance(value, str):
                event_dict[key] = redact(value)
        return event_dict


def get_secret(name: str, *, service: str = "shortsforge") -> str | None:
    """Read a secret from env first, then OS keyring.

    Returns None if not found. Never raises.
    """
    value = os.getenv(name)
    if value:
        return value

    try:
        import keyring
        value = keyring.get_password(service, name)
        return value
    except Exception:
        return None


def configure_logging() -> None:
    """Configure structlog with the secret redaction processor."""
    structlog.configure(
        processors=[
            SecretRedactionProcessor(),
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO+
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
