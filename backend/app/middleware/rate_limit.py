"""
In-memory sliding-window rate limiter for FastAPI.

No external dependencies (Redis, etc.) — suitable for single-process deployments.
For multi-process / multi-server, swap the store for Redis.

Usage in main.py:
    from app.middleware.rate_limit import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)
"""

import time
import logging
from collections import defaultdict
from typing import Dict, List, Tuple, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────
# (path_prefix, max_requests, window_seconds, methods_or_None)
# methods_or_None: if set, only limit these HTTP methods. None = all methods.
# More specific prefixes are checked first.
RATE_LIMITS: List[Tuple[str, int, int, Optional[List[str]]]] = [
    # Auth: 10 login attempts per minute (brute-force protection)
    ("/api/auth/login", 10, 60, ["POST"]),
    # Batch analysis: 5 LAUNCHES per minute (protects LLM budget)
    # GET polling is NOT rate-limited — it's read-only and must not be blocked
    ("/api/analysis/batch", 5, 60, ["POST"]),
    # File uploads: 20 per minute
    ("/api/candidates/upload", 20, 60, ["POST"]),
    ("/api/candidates/bulk-upload", 10, 60, ["POST"]),
    # General API: 120 mutating requests per minute (generous for normal use)
    ("/api/", 120, 60, ["POST", "PUT", "DELETE", "PATCH"]),
]

# Store: { "ip:path_prefix" -> [timestamp, timestamp, ...] }
_request_log: Dict[str, List[float]] = defaultdict(list)

# Cleanup old entries every N requests to prevent memory leak
_CLEANUP_EVERY = 500
_request_count = 0


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For behind a proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _find_limit(path: str, method: str) -> Optional[Tuple[str, int, int]]:
    """Find the most specific rate limit matching this request path."""
    for prefix, max_req, window, methods in RATE_LIMITS:
        if path.startswith(prefix):
            # Skip if this rule only applies to specific methods and current method isn't one
            if methods and method not in methods:
                return None
            return (prefix, max_req, window)
    return None


def _cleanup_old_entries():
    """Prune expired timestamps to prevent unbounded memory growth."""
    now = time.time()
    stale_keys = []
    for key, timestamps in _request_log.items():
        # Keep only timestamps from the last 120 seconds (max window * 2)
        _request_log[key] = [t for t in timestamps if now - t < 120]
        if not _request_log[key]:
            stale_keys.append(key)
    for key in stale_keys:
        del _request_log[key]


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        global _request_count

        path = request.url.path
        method = request.method

        # Find applicable rate limit
        limit = _find_limit(path, method)
        if not limit:
            return await call_next(request)

        prefix, max_requests, window = limit
        client_ip = _get_client_ip(request)
        key = f"{client_ip}:{prefix}"
        now = time.time()

        # Periodic cleanup
        _request_count += 1
        if _request_count % _CLEANUP_EVERY == 0:
            _cleanup_old_entries()

        # Sliding window: keep only timestamps within the window
        timestamps = _request_log[key]
        timestamps = [t for t in timestamps if now - t < window]
        _request_log[key] = timestamps

        if len(timestamps) >= max_requests:
            retry_after = int(window - (now - timestamps[0])) + 1
            logger.warning(
                f"Rate limit exceeded: {client_ip} on {prefix} "
                f"({len(timestamps)}/{max_requests} in {window}s)"
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Too many requests. Please try again in {retry_after} seconds.",
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(now + retry_after)),
                },
            )

        # Allow request — record timestamp
        timestamps.append(now)

        response = await call_next(request)

        # Add rate limit headers to successful responses
        remaining = max(0, max_requests - len(timestamps))
        response.headers["X-RateLimit-Limit"] = str(max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response
