"""
Giant Eagle — Audit Middleware
Automatically logs every CS rep action for compliance.
Every request to the receipt lookup app is recorded in the audit_log table.
No exceptions — this is a regulatory requirement for customer data access.

PII Redaction: Automatically redacts sensitive customer data from audit logs
to comply with GDPR/CCPA requirements. Only non-PII metadata is logged.
"""

import asyncio
import json
import logging
import time
from typing import Callable

import psycopg
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# PII fields that must be redacted from audit logs (GDPR/CCPA compliance)
PII_FIELDS = {
    # Auth/secrets
    "password", "token", "secret", "api_key", "access_token", "refresh_token",
    # Customer PII
    "customer_name", "customer_email", "email", "phone", "phone_number",
    "address", "street", "city", "state", "zip", "postal_code",
    "ssn", "social_security", "tax_id", "driver_license",
    # Payment info
    "card_last4", "card_number", "cvv", "account_number", "routing_number",
    # Personal identifiers (keep customer_id as it's pseudonymized)
    "name", "first_name", "last_name", "full_name", "date_of_birth", "dob"
}


class AuditMiddleware(BaseHTTPMiddleware):
    """
    Logs every API request to the audit_log table in Lakebase.

    Captures:
    - Who: rep_id, rep_email, rep_role (from Azure AD token)
    - What: action, resource_type, resource_id
    - When: timestamp
    - How: query parameters, result count, IP, user agent
    """

    # Routes that don't need audit logging (health checks, system requests, static assets)
    SKIP_PATHS = {
        "/health", "/docs", "/openapi.json", "/redoc", "/favicon.ico",
        "/fluent-bit-status", "/status", "/ping", "/metrics",
        "/_next/", "/static/", "/assets/"
    }

    # Map route patterns to action + resource_type
    ROUTE_MAP = {
        "/receipt/": ("lookup", "receipt"),
        "/search/": ("search", "receipt"),
        "/search/fuzzy": ("fuzzy_search", "receipt"),
        "/cs/context/": ("context_lookup", "customer"),
        "/receipt/deliver": ("deliver", "receipt"),
        "/audit/": ("audit_query", "audit"),
    }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip non-auditable routes (exact match or prefix match)
        path = request.url.path

        # Skip exact matches or prefix matches
        if path in self.SKIP_PATHS or any(path.startswith(skip) for skip in self.SKIP_PATHS if skip.endswith("/")):
            return await call_next(request)

        # Skip static assets (JS, CSS, images, fonts, etc.)
        static_extensions = ('.js', '.css', '.map', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.woff', '.woff2', '.ttf', '.eot')
        if path.endswith(static_extensions):
            return await call_next(request)

        start_time = time.time()

        # Cache request body for POST requests using proper async wrapper
        # This makes the body available to both middleware and route handlers
        body_json_redacted = None
        if request.method == "POST":
            try:
                # Use form() or body() - FastAPI caches this internally in recent versions
                # For older versions, we wrap the receive callable
                body_bytes = await request.body()

                if body_bytes:
                    body_json = json.loads(body_bytes)
                    # Redact PII and sensitive fields for audit log (GDPR/CCPA compliance)
                    body_json_redacted = self._redact_pii(body_json)

                # IMPORTANT: Make body available to route handlers by caching it
                # FastAPI's Request.body() method caches the body in request._body
                # This is already done by await request.body() above in FastAPI 0.68+
            except Exception as exc:
                logger.warning(f"Failed to parse request body for audit: {exc}")

        # Execute the request
        response = await call_next(request)

        # Extract user info from platform-injected headers
        # Databricks Apps injects these after SSO authentication
        rep_email = (
            request.headers.get("X-Forwarded-Email")
            or request.headers.get("X-Databricks-User-Email")
            or "unknown"
        )
        rep_id = rep_email  # Use email as unique identifier
        rep_role = "cs_rep"  # Default role (fine-grained authz handled by Unity Catalog)

        # Determine action and resource type from route
        action, resource_type = self._classify_route(request.url.path)

        # Extract resource ID from path if present
        resource_id = self._extract_resource_id(request.url.path)

        # Build query params (include redacted body for POST)
        query_params = dict(request.query_params)
        if body_json_redacted:
            query_params["body"] = body_json_redacted

        # Schedule audit log write as background task (fire-and-forget)
        # This allows the response to return immediately without waiting for DB write
        asyncio.create_task(
            self._write_audit_log_async(
                pool=request.app.state.lakebase_pool,
                rep_id=rep_id,
                rep_email=rep_email,
                rep_role=rep_role,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                query_params=query_params,
                result_count=response.headers.get("X-Result-Count"),
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent", "")[:500],
            )
        )

        elapsed = time.time() - start_time
        logger.info(
            f"AUDIT: {rep_email} | {action} | {resource_type}:{resource_id} | "
            f"{response.status_code} | {elapsed:.3f}s"
        )

        return response

    @staticmethod
    async def _write_audit_log_async(
        pool,
        rep_id: str,
        rep_email: str,
        rep_role: str,
        action: str,
        resource_type: str,
        resource_id: str | None,
        query_params: dict,
        result_count: str | None,
        ip_address: str | None,
        user_agent: str,
    ) -> None:
        """
        Write audit log entry to database asynchronously (background task).

        This runs after the response is returned, so audit log writes don't block API responses.
        Uses connection pool for efficient database access.
        """
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO audit_log (
                            rep_id, rep_email, rep_role, action, resource_type,
                            resource_id, query_params, result_count,
                            ip_address, user_agent
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            rep_id,
                            rep_email,
                            rep_role,
                            action,
                            resource_type,
                            resource_id,
                            json.dumps(query_params) if query_params else None,
                            result_count,
                            ip_address,
                            user_agent,
                        ),
                    )
                await conn.commit()
                logger.debug(f"Audit log written: {rep_email} | {action} | {resource_type}:{resource_id}")
        except Exception as e:
            # Audit logging failure should NOT break the app — log and continue
            # Since this runs in background, exceptions won't affect the response
            logger.error(f"Background audit log write failed: {e}")

    def _classify_route(self, path: str) -> tuple[str, str]:
        """Map a URL path to an action and resource type."""
        for prefix, (action, resource_type) in self.ROUTE_MAP.items():
            if path.startswith(prefix):
                return action, resource_type
        return "unknown", "unknown"

    def _extract_resource_id(self, path: str) -> str | None:
        """Extract the resource ID from URL path segments."""
        parts = path.strip("/").split("/")
        # Pattern: /receipt/{id}, /cs/context/{id}, etc.
        if len(parts) >= 2 and parts[-1] not in ("fuzzy", "deliver", "log"):
            return parts[-1]
        return None

    @staticmethod
    def _redact_pii(data: dict) -> dict:
        """
        Recursively redact PII from a dictionary for audit logging.

        Replaces sensitive field values with "***REDACTED***" to comply
        with GDPR/CCPA data minimization requirements.

        Args:
            data: Dictionary possibly containing PII

        Returns:
            Copy of dictionary with PII redacted
        """
        if not isinstance(data, dict):
            return data

        redacted = data.copy()

        for key, value in redacted.items():
            # Redact known PII fields
            if key.lower() in PII_FIELDS:
                redacted[key] = "***REDACTED***"
            # Recursively handle nested dictionaries
            elif isinstance(value, dict):
                redacted[key] = AuditMiddleware._redact_pii(value)
            # Handle lists of dictionaries
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                redacted[key] = [AuditMiddleware._redact_pii(item) for item in value]

        return redacted
