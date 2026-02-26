"""
Giant Eagle — Databricks Native Auth + Unity Catalog RBAC

Authentication is handled by the Databricks Apps platform (SSO header injection).
Authorization is enforced by Unity Catalog (row filters, column masks, table grants).

How it works:
  1. Databricks Apps platform authenticates user via workspace SSO
  2. Platform injects verified identity headers:
       X-Forwarded-Email       → authenticated user's email
       X-Forwarded-User        → username
       X-Forwarded-Access-Token → user's OAuth token
  3. App reads email header and passes it to database queries
  4. Unity Catalog enforces permissions at query time:
       - Table grants (SELECT, INSERT, etc.)
       - Row filters (supervisors see all, cs_reps see only their own data)
       - Column masks (hide fraud flags from cs_reps)

No SCIM lookups, no role checks in code, no permission logic in the app layer.
All authorization is handled by Unity Catalog.

RBAC roles (Unity Catalog groups — managed via UC UI):
  cs_rep      — basic lookup, search, email receipts
  supervisor  — + refund approval, escalation, audit access
  fraud_team  — + cross-customer patterns, fraud flags, bulk export
"""

import logging
from typing import Any

from fastapi import HTTPException, Request, Depends

logger = logging.getLogger(__name__)


async def get_current_user(request: Request) -> dict[str, Any]:
    """
    Authenticate the current user.

    Reads the Databricks Apps platform-injected identity headers.
    Does NOT resolve roles or check group membership — Unity Catalog
    handles authorization at query time.

    Sets request.state.user for downstream use (audit middleware, logging).

    Raises 401 if no identity header is present.

    Returns:
        User dict with email, name, preferred_username
    """
    # Platform-injected after SSO — trust these headers (set by the Databricks proxy)
    email = (
        request.headers.get("X-Forwarded-Email")
        or request.headers.get("X-Databricks-User-Email")
    )

    if not email:
        # During local development there's no proxy — allow a fallback dev identity
        import os
        dev_email = os.environ.get("DEV_USER_EMAIL")
        if dev_email:
            logger.warning("DEV_USER_EMAIL set — using dev identity (not for production)")
            user = {
                "email": dev_email,
                "preferred_username": dev_email,
                "name": dev_email.split("@")[0],
            }
            request.state.user = user
            return user

        raise HTTPException(
            status_code=401,
            detail="No authenticated user identity. Ensure you are accessing this app through the Databricks Apps URL.",
        )

    display_name = request.headers.get("X-Forwarded-User", email.split("@")[0])

    user = {
        "email": email,
        "preferred_username": email,
        "name": display_name,
    }

    request.state.user = user
    return user
