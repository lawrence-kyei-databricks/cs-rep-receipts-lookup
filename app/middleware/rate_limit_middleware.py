"""
Rate limiting middleware for Giant Eagle CS Receipt Lookup API.

Implements token bucket algorithm to prevent API abuse while allowing
reasonable burst traffic. Applies uniform rate limit to all authenticated users.

Security rationale:
- Prevents DoS attacks from rogue users or compromised accounts
- Protects database resources (Lakebase has finite connection limits)
- Audit logging helps identify abuse patterns

Rate limit: 120 req/min (2 per second sustained, 20-req burst) for all authenticated users
Authorization (who can see/modify what data) is handled by Unity Catalog, not app code.

Threading model:
- FastAPI async deployment uses single event loop (naturally thread-safe)
- Token bucket operations are safe in single-worker async contexts
- For multi-worker deployments, each worker maintains independent buckets
- For clustered rate limiting across workers, consider Redis-based solution
"""

import time
import logging
import threading
from typing import Callable
from collections import defaultdict

from fastapi import Request, Response, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class TokenBucket:
    """
    Token bucket rate limiter implementation.

    Algorithm:
    - Bucket holds tokens (= allowed requests)
    - Tokens refill at a constant rate
    - Each request consumes 1 token
    - If bucket is empty, request is denied (429 Too Many Requests)
    - Allows burst traffic (up to max_tokens) but enforces average rate
    """

    def __init__(self, rate: float, max_tokens: int):
        """
        Initialize token bucket.

        Args:
            rate: Token refill rate (tokens per second)
            max_tokens: Maximum bucket capacity (allows bursts)
        """
        self.rate = rate
        self.max_tokens = max_tokens
        self.tokens = max_tokens
        self.last_refill = time.time()

    def consume(self, tokens: int = 1) -> bool:
        """
        Try to consume tokens from bucket.

        Args:
            tokens: Number of tokens to consume (default: 1)

        Returns:
            True if tokens available (request allowed)
            False if bucket empty (request denied)
        """
        # Refill bucket based on elapsed time
        now = time.time()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.rate)
        self.last_refill = now

        # Try to consume tokens
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware using token bucket algorithm.

    Tracks requests per user (email from auth headers) and enforces
    uniform rate limits to prevent API abuse. Authorization (who can
    access which data) is handled by Unity Catalog, not this middleware.
    """

    # Uniform rate limit for all authenticated users (tokens per second, max burst size)
    DEFAULT_RATE_LIMIT = (2.0, 20)  # 120 req/min sustained, 20-req burst

    # Routes exempt from rate limiting (health checks, static assets)
    EXEMPT_PATHS = {
        "/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico",
        "/_next/", "/static/", "/assets/"
    }

    def __init__(self, app):
        super().__init__(app)
        # Store buckets per user: {user_email: TokenBucket}
        self._buckets = defaultdict(lambda: None)

        # Cleanup stale buckets every 10 minutes
        self._last_cleanup = time.time()
        self._cleanup_interval = 600  # 10 minutes

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip rate limiting for exempt paths
        path = request.url.path
        if path in self.EXEMPT_PATHS or any(path.startswith(exempt) for exempt in self.EXEMPT_PATHS if exempt.endswith("/")):
            return await call_next(request)

        # Skip static assets
        static_extensions = ('.js', '.css', '.map', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.woff', '.woff2', '.ttf', '.eot')
        if path.endswith(static_extensions):
            return await call_next(request)

        # Extract user identity from platform-injected headers
        # Databricks Apps injects X-Forwarded-Email after SSO authentication
        user_email = (
            request.headers.get("X-Forwarded-Email")
            or request.headers.get("X-Databricks-User-Email")
            or request.client.host if request.client else "unknown"
        )

        # Use uniform rate limit for all authenticated users
        rate, max_tokens = self.DEFAULT_RATE_LIMIT

        # Get or create token bucket for this user
        if self._buckets[user_email] is None:
            self._buckets[user_email] = TokenBucket(rate, max_tokens)

        bucket = self._buckets[user_email]

        # Try to consume a token
        if not bucket.consume():
            logger.warning(f"Rate limit exceeded for {user_email}")
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Rate limit exceeded",
                    "message": f"Too many requests. Rate limit is {int(rate * 60)} requests per minute. "
                               f"Please slow down and try again in a few seconds.",
                    "retry_after": int(1 / rate),  # Seconds until next token available
                    "limit_per_minute": int(rate * 60),
                }
            )

        # Cleanup stale buckets periodically
        now = time.time()
        if now - self._last_cleanup > self._cleanup_interval:
            self._cleanup_stale_buckets()
            self._last_cleanup = now

        # Add rate limit headers to response
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(int(rate * 60))
        response.headers["X-RateLimit-Remaining"] = str(int(bucket.tokens))
        response.headers["X-RateLimit-Reset"] = str(int(now + (max_tokens - bucket.tokens) / rate))

        return response

    def _cleanup_stale_buckets(self) -> None:
        """
        Remove buckets for users who haven't made requests recently.

        Prevents memory growth from inactive users. Keeps buckets for
        users active in last hour (3600 seconds).
        """
        now = time.time()
        stale_threshold = 3600  # 1 hour

        stale_users = [
            user_email
            for user_email, bucket in self._buckets.items()
            if bucket and (now - bucket.last_refill) > stale_threshold
        ]

        for user_email in stale_users:
            del self._buckets[user_email]

        if stale_users:
            logger.info(f"Cleaned up {len(stale_users)} stale rate limit buckets")
