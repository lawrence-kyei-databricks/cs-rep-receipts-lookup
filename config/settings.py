"""
Giant Eagle Receipt Lookup — Application Settings

All config is read from environment variables. No secrets in code.
Follows the pattern from CLAUDE.md Environment Variables section.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    # ── Databricks ──────────────────────────────────────────────────────────
    databricks_host: str = field(
        default_factory=lambda: _require("DATABRICKS_HOST")
    )
    databricks_token: str = field(
        default_factory=lambda: _require("DATABRICKS_TOKEN")
    )

    # ── Lakebase (Postgres wire protocol) ───────────────────────────────────
    lakebase_instance_name: str = field(
        default_factory=lambda: _require("LAKEBASE_INSTANCE_NAME")
    )
    lakebase_host: str = field(
        default_factory=lambda: _require("LAKEBASE_HOST")
    )
    lakebase_port: int = field(
        default_factory=lambda: int(os.environ.get("LAKEBASE_PORT", "5432"))
    )
    lakebase_database: str = field(
        default_factory=lambda: os.environ.get("LAKEBASE_DATABASE", "giant_eagle")
    )
    lakebase_user: str = field(
        default_factory=lambda: _require("LAKEBASE_USER")
    )
    lakebase_password: str = field(
        default_factory=lambda: _require("LAKEBASE_PASSWORD")
    )

    # ── Lakebase connection pool ─────────────────────────────────────────────
    lakebase_pool_min: int = field(
        default_factory=lambda: int(os.environ.get("LAKEBASE_POOL_MIN", "2"))
    )
    lakebase_pool_max: int = field(
        default_factory=lambda: int(os.environ.get("LAKEBASE_POOL_MAX", "10"))
    )

    # ── Azure AD / Entra ID (internal SSO) ──────────────────────────────────
    azure_tenant_id: str = field(
        default_factory=lambda: os.environ.get("AZURE_TENANT_ID", "")
    )
    azure_client_id: str = field(
        default_factory=lambda: os.environ.get("AZURE_CLIENT_ID", "")
    )
    azure_client_secret: str = field(
        default_factory=lambda: os.environ.get("AZURE_CLIENT_SECRET", "")
    )

    # ── Email delivery ───────────────────────────────────────────────────────
    smtp_host: str = field(
        default_factory=lambda: os.environ.get("SMTP_HOST", "")
    )
    smtp_from: str = field(
        default_factory=lambda: os.environ.get("SMTP_FROM", "receipts@gianteagle.com")
    )

    # ── Unity Catalog paths ──────────────────────────────────────────────────
    bronze_schema: str = "giant_eagle.bronze"
    gold_schema: str = "giant_eagle.gold"

    # Zerobus target tables
    zerobus_receipts_table: str = "giant_eagle.bronze.pos_raw_receipts"
    zerobus_items_table: str = "giant_eagle.bronze.pos_raw_items"

    @property
    def lakebase_conninfo(self) -> str:
        """psycopg3-compatible connection string (sslmode=require for Lakebase)."""
        return (
            f"host={self.lakebase_host} "
            f"port={self.lakebase_port} "
            f"dbname={self.lakebase_database} "
            f"user={self.lakebase_user} "
            f"password={self.lakebase_password} "
            f"sslmode=require"
        )


def _require(name: str) -> str:
    """Read a required environment variable; raise clearly if missing."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not set. "
            f"See CLAUDE.md 'Environment Variables' section."
        )
    return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the singleton Settings instance (cached after first call)."""
    return Settings()
