"""Token bucket rate limiter with JSON persistence."""

from __future__ import annotations

import time
from pathlib import Path
from threading import Lock
from typing import ClassVar

_LIMITS_FILE = Path.home() / ".shortsforge" / "limits.json"
_lock = Lock()


class RateLimitExceeded(Exception):
    """Raised when a rate limit bucket is exhausted."""


class TokenBucket:
    """Thread-safe token bucket with optional file persistence."""

    _instances: ClassVar[dict[str, TokenBucket]] = {}

    def __init__(self, name: str, rate: float, burst: int) -> None:
        self.name = name
        self.rate = rate  # tokens per second
        self.burst = burst  # max tokens
        self._tokens: float = burst
        self._last_refill: float = time.monotonic()
        self._lock = Lock()

    @classmethod
    def get(cls, name: str, rate: float = 1.0, burst: int = 10) -> TokenBucket:
        if name not in cls._instances:
            cls._instances[name] = cls(name, rate, burst)
        return cls._instances[name]

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        added = elapsed * self.rate
        self._tokens = min(self.burst, self._tokens + added)
        self._last_refill = now

    def consume(self, tokens: int = 1) -> None:
        """Consume *tokens* from the bucket. Raises RateLimitExceeded if empty."""
        with self._lock:
            self._refill()
            if self._tokens < tokens:
                raise RateLimitExceeded(
                    f"Rate limit '{self.name}' exceeded. "
                    f"Available: {self._tokens:.1f}, requested: {tokens}"
                )
            self._tokens -= tokens

    def available(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens


def token_bucket(name: str, rate: float = 1.0, burst: int = 10) -> TokenBucket:
    """Return (or create) a named TokenBucket."""
    return TokenBucket.get(name, rate, burst)


# Convenience: 60 LLM calls / minute
LLM_BUCKET = token_bucket("llm", rate=1.0, burst=60)
# 6 uploads / hour
UPLOAD_HOUR_BUCKET = token_bucket("youtube_hour", rate=6 / 3600, burst=6)
# 20 uploads / day
UPLOAD_DAY_BUCKET = token_bucket("youtube_day", rate=20 / 86400, burst=20)
