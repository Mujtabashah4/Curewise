"""Minimal in-process rate limiting.

Deliberately simple: a fixed-window counter per (scope, client) held in memory.

**Know the limits of this.** Counters live in the worker process, so with N
uvicorn workers a client effectively gets N times the budget, and everything
resets on restart. It exists to blunt credential stuffing and accidental
hammering of the expensive AI routes — it is not a hard security control. Put a
real limiter (nginx, an API gateway, or a Redis-backed one) in front for
production.
"""

from __future__ import annotations

import logging
import threading
import time

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

# (scope, client_key) -> [hits, window_reset_epoch]
_buckets: dict[tuple[str, str], list[float]] = {}
_lock = threading.Lock()

# Prune no more than once a minute; the table is tiny either way.
_PRUNE_INTERVAL = 60.0
_last_prune = 0.0


def _client_key(request: Request) -> str:
    """Identify the caller by socket address.

    Deliberately does NOT trust X-Forwarded-For: any client can set it, which
    would turn the limiter into a no-op. If you deploy behind a proxy, resolve
    the real IP there (or via Starlette's ProxyHeadersMiddleware) instead.
    """
    return request.client.host if request.client else "unknown"


def _prune(now: float) -> None:
    global _last_prune
    if now - _last_prune < _PRUNE_INTERVAL:
        return
    _last_prune = now
    for key in [k for k, (_, reset) in _buckets.items() if reset <= now]:
        _buckets.pop(key, None)


def rate_limit(*, max_calls: int, window_seconds: int, scope: str):
    """Build a FastAPI dependency allowing ``max_calls`` per ``window_seconds``."""

    async def dependency(request: Request) -> None:
        now = time.monotonic()
        key = (scope, _client_key(request))

        with _lock:
            _prune(now)
            bucket = _buckets.get(key)
            if bucket is None or bucket[1] <= now:
                _buckets[key] = [1, now + window_seconds]
                return
            if bucket[0] >= max_calls:
                retry_after = max(1, int(bucket[1] - now))
                logger.warning(
                    "Rate limit hit: scope=%s client=%s", scope, key[1]
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many attempts. Please wait a moment and try again.",
                    headers={"Retry-After": str(retry_after)},
                )
            bucket[0] += 1

    return dependency
