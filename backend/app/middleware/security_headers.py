"""
Security headers middleware for FastAPI.

Adds standard protective headers to every response:
  - X-Content-Type-Options: prevent MIME-type sniffing
  - X-Frame-Options: prevent clickjacking
  - X-XSS-Protection: legacy XSS filter (still useful for older browsers)
  - Referrer-Policy: limit referrer leakage
  - Permissions-Policy: disable unnecessary browser APIs
  - Content-Security-Policy: restrict resource origins
  - Strict-Transport-Security: force HTTPS (production only)
  - Cache-Control: prevent caching of API responses with auth data

Usage in main.py:
    from app.middleware.security_headers import SecurityHeadersMiddleware
    app.add_middleware(SecurityHeadersMiddleware)
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.core.config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        # ── Always-on headers ────────────────────────────────────────
        # Prevent browsers from MIME-sniffing responses
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Block framing to prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Legacy XSS filter — still respected by some browsers
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Limit referrer information leakage
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Restrict browser features the app doesn't need
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )

        # Content Security Policy — restrict where resources can load from
        # script-src 'self' allows only same-origin scripts
        # style-src 'self' 'unsafe-inline' needed for Tailwind/inline styles
        # img-src 'self' data: allows inline images and same-origin
        # connect-src 'self' allows API calls to same origin
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )

        # ── Production-only headers ──────────────────────────────────
        if not settings.DEBUG:
            # Force HTTPS for 1 year, include subdomains
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        # ── Prevent caching of authenticated API responses ───────────
        if request.url.path.startswith("/api/") and request.url.path != "/api/health":
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"

        return response
