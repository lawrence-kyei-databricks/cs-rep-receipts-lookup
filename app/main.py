"""
Giant Eagle — CS Receipt Lookup Databricks App
FastAPI application for internal Customer Service team.

Key differences from consumer version:
- Azure AD SSO (not customer OAuth)
- RBAC: cs_rep, supervisor, fraud_team
- Audit middleware on every request
- Fuzzy search, receipt delivery, CS context
- NO reorder endpoints
"""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from databricks.sdk import WorkspaceClient
import psycopg
from psycopg_pool import AsyncConnectionPool

from middleware.audit_middleware import AuditMiddleware
from middleware.rate_limit_middleware import RateLimitMiddleware
from routes import lookup, search, fuzzy_search, cs_context, receipt_delivery, audit, admin, debug, genie_search

logger = logging.getLogger(__name__)


def _get_lakebase_token() -> str:
    """
    Get OAuth token for Lakebase authentication.

    Uses the Databricks SDK's generate_database_credential() — works in both:
    - Local dev: SDK picks up DATABRICKS_TOKEN (PAT) or ~/.databrickscfg
    - Databricks Apps: SDK picks up injected DATABRICKS_CLIENT_ID/SECRET (M2M OAuth)

    Falls back to DATABRICKS_TOKEN env var and w.config.token for edge cases.
    """
    instance_name = os.environ.get("LAKEBASE_INSTANCE_NAME", "giant-eagle-receipt-db")

    # Primary: SDK generate_database_credential — works for PAT + M2M OAuth
    try:
        w = WorkspaceClient()
        cred = w.database.generate_database_credential(instance_names=[instance_name])
        if cred.token:
            logger.info("Lakebase token obtained via SDK generate_database_credential")
            return cred.token
    except Exception as exc:
        logger.warning("SDK generate_database_credential failed: %s — trying fallbacks", exc)

    # Fallback 1: static PAT (local dev explicit override)
    static_token = os.environ.get("DATABRICKS_TOKEN", "")
    if static_token:
        logger.info("Using DATABRICKS_TOKEN (PAT fallback) for Lakebase auth")
        return static_token

    # Fallback 2: w.config.token (covers Azure CLI / Databricks CLI auth)
    try:
        token = WorkspaceClient().config.token
        if token:
            logger.info("Using WorkspaceClient config.token fallback for Lakebase auth")
            return token
    except Exception as exc:
        logger.warning("WorkspaceClient config.token fallback failed: %s", exc)

    logger.error("Could not obtain Lakebase token — connection will likely fail")
    return ""


def _build_lakebase_conninfo() -> str:
    """
    Build psycopg3 connection string for Lakebase.

    The Databricks Apps platform injects DATABRICKS_CLIENT_ID/SECRET but does NOT
    inject PG* env vars from the lakebase resource declaration. We resolve connection
    params ourselves:
      - host:   from SDK get_database_instance (or PGHOST override)
      - user:   DATABRICKS_CLIENT_ID (the app SP) or PGUSER override
      - dbname: PGDATABASE env or 'giant_eagle'
      - password: fresh OAuth token via generate_database_credential
    """
    instance_name = os.environ.get("LAKEBASE_INSTANCE_NAME", "giant-eagle-receipt-db")
    token = _get_lakebase_token()

    # Host: prefer explicit PGHOST, then look it up from the SDK
    host = os.environ.get("PGHOST", "")
    if not host:
        try:
            w = WorkspaceClient()
            inst = w.database.get_database_instance(instance_name)
            host = inst.read_write_dns or ""
            logger.info("Resolved Lakebase host from SDK: %s", host)
        except Exception as exc:
            logger.warning("Could not resolve Lakebase host from SDK: %s", exc)

    # User: prefer explicit PGUSER, then use the SP's client_id
    user = os.environ.get("PGUSER", "") or os.environ.get("DATABRICKS_CLIENT_ID", "")

    port = os.environ.get("PGPORT", "5432")
    dbname = os.environ.get("PGDATABASE", "giant_eagle")
    sslmode = os.environ.get("PGSSLMODE", "require")

    logger.info("Lakebase conninfo: host=%s port=%s dbname=%s user=%s", host, port, dbname, user)

    return (
        f"host={host} port={port} dbname={dbname} "
        f"user={user} password={token} sslmode={sslmode}"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Giant Eagle CS Receipt Lookup starting...")
    logger.info(f"Lakebase host: {os.environ.get('PGHOST', 'not configured')}")

    # Build initial conninfo (fresh M2M token, valid ~1 hour).
    # Wrapped in try-except: token failure logs a warning but does NOT crash the app.
    # Routes will get a 500 on first DB call if Lakebase is unreachable.
    try:
        conninfo = _build_lakebase_conninfo()

        # Store conninfo for AI search agent (creates its own connections)
        app.state.lakebase_conninfo = conninfo

        # Create AsyncConnectionPool with optimized settings:
        # - min_size=2: Keep 2 connections always open (eliminates cold start overhead)
        # - max_size=10: Support up to 10 concurrent requests
        # - timeout=30: Wait up to 30s for an available connection
        # - max_idle=600: Keep connections alive for 10 min (reuse across requests)
        logger.info("Creating Lakebase connection pool (min=2, max=10)...")
        app.state.lakebase_pool = AsyncConnectionPool(
            conninfo=conninfo,
            min_size=2,
            max_size=10,
            timeout=30.0,
            max_idle=600.0,  # 10 minutes
            open=True,  # Open the pool immediately
        )
        logger.info("Lakebase connection pool created successfully")

    except Exception as exc:
        logger.error("Failed to create Lakebase connection pool: %s — routes requiring DB will fail", exc)
        app.state.lakebase_pool = None

    # Track token creation time for proactive refresh
    import time
    app.state.lakebase_token_created_at = time.time()

    # Define pool refresh function (used by db_utils.py on auth errors)
    async def refresh_lakebase_pool():
        """Refresh the connection pool with a new OAuth token."""
        try:
            logger.info("Refreshing Lakebase connection pool with new token...")
            new_conninfo = _build_lakebase_conninfo()

            # Update conninfo for AI search agent
            app.state.lakebase_conninfo = new_conninfo

            # Create new pool
            new_pool = AsyncConnectionPool(
                conninfo=new_conninfo,
                min_size=2,
                max_size=10,
                timeout=30.0,
                max_idle=600.0,
                open=True,
            )

            app.state.lakebase_pool = new_pool
            app.state.lakebase_token_created_at = time.time()
            logger.info("Lakebase connection pool refreshed successfully")

        except Exception as exc:
            logger.error(f"Failed to refresh Lakebase connection pool: {exc}")
            raise

    app.state.refresh_lakebase_pool = refresh_lakebase_pool

    # Start background task to refresh pool every 50 minutes (before 60-min expiry)
    import asyncio
    async def token_refresh_task():
        while True:
            try:
                # Calculate time until next refresh (50 min from token creation)
                token_age = time.time() - app.state.lakebase_token_created_at
                time_until_refresh = max(0, 3000 - token_age)  # 3000 sec = 50 min

                if time_until_refresh > 0:
                    logger.info(f"Pool refresh scheduled in {int(time_until_refresh/60)} minutes")
                    await asyncio.sleep(time_until_refresh)

                logger.info("Refreshing Lakebase connection pool (proactive 50-min refresh)...")

                # Close old pool
                if app.state.lakebase_pool:
                    await app.state.lakebase_pool.close()

                # Refresh pool
                await refresh_lakebase_pool()

            except Exception as exc:
                logger.error(f"Pool refresh failed: {exc} — will retry in 50 minutes")
                # On error, wait 50 min before next attempt
                await asyncio.sleep(3000)

    refresh_task = asyncio.create_task(token_refresh_task())

    yield

    # Cancel background task on shutdown
    refresh_task.cancel()
    try:
        await refresh_task
    except asyncio.CancelledError:
        pass

    # Close the connection pool
    if app.state.lakebase_pool:
        logger.info("Closing Lakebase connection pool...")
        await app.state.lakebase_pool.close()

    logger.info("Giant Eagle CS Receipt Lookup shutting down.")


app = FastAPI(
    title="Giant Eagle CS Receipt Lookup",
    description="Internal CS tool: AI-powered receipt search, customer context, and delivery",
    version="2.0.0",
    lifespan=lifespan,
)

# Audit middleware — logs EVERY request (must be first middleware)
app.add_middleware(AuditMiddleware)

# Rate limiting — prevent API abuse (token bucket algorithm)
# Enforces role-based limits: cs_rep (60/min), supervisor (120/min), fraud_team (300/min)
# Rejects excessive requests with 429 before they consume database resources
app.add_middleware(RateLimitMiddleware)

# GZip compression for large responses (fuzzy search, customer lists, etc.)
# Only compresses responses >= 500 bytes; reduces bandwidth usage by 60-80%
# Typical compression ratios: JSON ~70%, HTML ~75%, plain text ~60%
app.add_middleware(
    GZipMiddleware,
    minimum_size=500,  # Don't compress tiny responses (overhead not worth it)
    compresslevel=6,   # Balance between speed and compression (6 = zlib default)
)

# CORS for internal CS portal
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://cs.gianteagle.com",
        "https://cs-portal.gianteagle.internal",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(lookup.router, prefix="/receipt", tags=["Receipt Lookup"])
app.include_router(search.router, prefix="/search", tags=["AI Search"])
app.include_router(fuzzy_search.router, prefix="/search", tags=["Fuzzy Search"])
app.include_router(genie_search.router, prefix="/genie", tags=["Ask Genie"])
app.include_router(cs_context.router, prefix="/cs/context", tags=["CS Context"])
app.include_router(receipt_delivery.router, prefix="/receipt", tags=["Receipt Delivery"])
app.include_router(audit.router, prefix="/audit", tags=["Audit Trail"])
app.include_router(admin.router, prefix="/admin", tags=["Admin"])
app.include_router(debug.router, prefix="/debug", tags=["Debug"])


@app.get("/health")
async def health(request: Request):
    """
    Health check endpoint with database connectivity verification.

    Returns:
        - status: "healthy" (all systems OK), "degraded" (DB issues), or "unhealthy" (critical failure)
        - lakebase: Connection status
        - token_age: How long since token was created (for monitoring)
    """
    import time

    health_status = {
        "status": "healthy",
        "service": "giant-eagle-cs-receipt-lookup",
        "version": "2.0.0",
        "lakebase": "unknown",
        "token_age_minutes": None,
    }

    # Check Lakebase connectivity using the connection pool
    try:
        pool = request.app.state.lakebase_pool
        if pool:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    # Simple query to verify connection
                    await cur.execute("SELECT 1 as health_check")
                    result = await cur.fetchone()
                    if result and result[0] == 1:
                        health_status["lakebase"] = "connected"
                    else:
                        health_status["lakebase"] = "unhealthy"
                        health_status["status"] = "degraded"
        else:
            health_status["lakebase"] = "pool_not_initialized"
            health_status["status"] = "degraded"
    except Exception as exc:
        logger.error(f"Health check: Lakebase connection failed: {exc}")
        health_status["lakebase"] = f"disconnected: {type(exc).__name__}"
        health_status["status"] = "degraded"

    # Report token age for monitoring (warn if near expiry)
    try:
        token_created = request.app.state.lakebase_token_created_at
        token_age_seconds = time.time() - token_created
        token_age_minutes = int(token_age_seconds / 60)
        health_status["token_age_minutes"] = token_age_minutes

        # Warn if token is close to expiry (58+ minutes)
        if token_age_minutes >= 58:
            health_status["warning"] = "Token nearing expiry - refresh should trigger soon"
    except Exception:
        pass

    return health_status


# ── Serve React SPA (must be last — after all API routes) ─────────────────────
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(_static_dir):
    # Serve built React assets (JS/CSS bundles produced by `npm run build`)
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="spa")
else:
    @app.get("/", include_in_schema=False)
    async def root():
        return {"message": "React UI not built yet. Run: cd ui && npm install && npm run build"}
