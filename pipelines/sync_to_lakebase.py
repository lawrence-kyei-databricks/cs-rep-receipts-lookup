"""
Giant Eagle — Synced Tables Reconciliation Utility
NOT a Lakeflow pipeline — runs as a standalone script or Databricks Job.

Synced Tables (giant_eagle.gold.* → giant_eagle_serving.public.*) were
provisioned in Phase 1 via the Databricks SDK. This script provides:

  1. check_sync_status()  — inspect current state of all four synced tables
  2. wait_for_active()    — block until all tables reach ACTIVE state
  3. reconcile_gaps()     — identify receipts in Lakebase native table that
                            are missing from the Gold Delta tables (receipts
                            written directly by POS that haven't flowed through
                            the Bronze→Silver→Gold pipeline yet)

Synced table mapping (provisioned in Phase 1):
  giant_eagle.gold.receipt_lookup   → giant_eagle_serving.public.receipt_lookup
  giant_eagle.gold.spending_summary → giant_eagle_serving.public.spending_summary
  giant_eagle.gold.customer_profiles → giant_eagle_serving.public.customer_profiles
  giant_eagle.gold.product_catalog  → giant_eagle_serving.public.product_catalog

All four use CONTINUOUS mode — CDF changes flow to Lakebase as soon as the
Gold pipeline produces them. No manual trigger needed.

Run reconcile_gaps() after the Gold pipeline catches up to find receipts that
are in the hot path (Lakebase native receipt_transactions) but haven't yet
appeared in the synced receipt_lookup table.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from databricks.sdk import WorkspaceClient

logger = logging.getLogger(__name__)

# Synced table source → target mapping (provisioned in Phase 1)
SYNCED_TABLE_MAP = {
    "giant_eagle.gold.receipt_lookup": "giant_eagle_serving.public.receipt_lookup",
    "giant_eagle.gold.spending_summary": "giant_eagle_serving.public.spending_summary",
    "giant_eagle.gold.customer_profiles": "giant_eagle_serving.public.customer_profiles",
    "giant_eagle.gold.product_catalog": "giant_eagle_serving.public.product_catalog",
}


def check_sync_status(client: WorkspaceClient | None = None) -> list[dict[str, Any]]:
    """
    Return the current sync state for all four Lakebase synced tables.

    Expected states:
      PROVISIONING — initial setup, not yet ready
      ACTIVE       — syncing normally (CDF updates flowing to Lakebase)
      FAILED       — check the Databricks UI for error details

    Returns:
        List of dicts with keys: source_table, target_table, state, message
    """
    w = client or WorkspaceClient()
    results: list[dict[str, Any]] = []

    for source_table, target_table in SYNCED_TABLE_MAP.items():
        try:
            table = w.online_tables.get(name=source_table)
            state = (
                table.status.detailed_state.value
                if table.status and table.status.detailed_state
                else "UNKNOWN"
            )
            message = table.status.message if table.status else None
            results.append({
                "source_table": source_table,
                "target_table": target_table,
                "state": state,
                "message": message,
            })
            logger.info(f"  {source_table}: {state}")
        except Exception as exc:
            results.append({
                "source_table": source_table,
                "target_table": target_table,
                "state": "ERROR",
                "message": str(exc),
            })
            logger.error(f"  {source_table}: ERROR — {exc}")

    return results


def wait_for_active(
    timeout_seconds: int = 600,
    poll_interval: int = 15,
    client: WorkspaceClient | None = None,
) -> bool:
    """
    Block until all synced tables reach ACTIVE state or timeout expires.

    Args:
        timeout_seconds: Maximum wait time (default: 10 minutes).
        poll_interval:   Seconds between status polls (default: 15s).
        client:          Optional pre-built WorkspaceClient.

    Returns:
        True if all tables are ACTIVE, False if timeout expired.
    """
    start = time.time()
    w = client or WorkspaceClient()

    while (time.time() - start) < timeout_seconds:
        statuses = check_sync_status(client=w)
        active_count = sum(1 for s in statuses if s["state"] == "ACTIVE")

        if active_count == len(SYNCED_TABLE_MAP):
            logger.info(f"All {len(SYNCED_TABLE_MAP)} synced tables ACTIVE.")
            return True

        non_active = [s for s in statuses if s["state"] != "ACTIVE"]
        logger.info(
            f"Waiting for {len(non_active)} tables to reach ACTIVE "
            f"({active_count}/{len(SYNCED_TABLE_MAP)} ready). "
            f"Elapsed: {int(time.time() - start)}s"
        )
        time.sleep(poll_interval)

    logger.warning(f"Timeout after {timeout_seconds}s — not all tables reached ACTIVE.")
    return False


def reconcile_gaps(
    lakebase_conninfo: str,
    lookback_hours: int = 24,
) -> dict[str, Any]:
    """
    Find receipts in Lakebase native table not yet in Gold Delta.

    The dual-write pattern means receipts land in Lakebase native
    (receipt_transactions) immediately via JDBC, but don't appear in
    giant_eagle.gold.receipt_lookup until the Bronze→Silver→Gold pipeline runs.

    During pipeline catch-up or after an outage, there will be a gap between
    the two tables. This function reports the gap size so ops can decide
    whether to trigger a Gold pipeline refresh.

    Args:
        lakebase_conninfo: psycopg3 connection string for Lakebase.
        lookback_hours:    Check receipts from the last N hours (default: 24).

    Returns:
        Dict with gap_count, oldest_missing_ts, newest_missing_ts.
    """
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        raise RuntimeError(
            "psycopg[binary] required for reconcile_gaps. "
            "Install with: pip install 'psycopg[binary]'"
        )

    # Query Lakebase native table for recent receipts not in the Gold Delta table.
    # We use the synced receipt_lookup table (Lakebase side) for the comparison
    # since it's a read-only copy of giant_eagle.gold.receipt_lookup.
    gap_query = """
        SELECT
            COUNT(*) AS gap_count,
            MIN(rt.transaction_ts) AS oldest_missing_ts,
            MAX(rt.transaction_ts) AS newest_missing_ts
        FROM receipt_transactions rt
        LEFT JOIN receipt_lookup rl
            ON rt.transaction_id = rl.transaction_id
        WHERE rt.transaction_ts >= NOW() - INTERVAL '%s hours'
          AND rl.transaction_id IS NULL
    """ % lookback_hours  # safe: lookback_hours is an int, not user input

    with psycopg.connect(lakebase_conninfo, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(gap_query)
            row = cur.fetchone()

    result = {
        "gap_count": row["gap_count"] if row else 0,
        "oldest_missing_ts": str(row["oldest_missing_ts"]) if row and row["oldest_missing_ts"] else None,
        "newest_missing_ts": str(row["newest_missing_ts"]) if row and row["newest_missing_ts"] else None,
        "lookback_hours": lookback_hours,
        "recommendation": (
            "Gap detected — consider triggering a Gold pipeline refresh."
            if (row and row["gap_count"] and row["gap_count"] > 0)
            else "No gap detected. Synced tables are current."
        ),
    }

    if result["gap_count"] > 0:
        logger.warning(
            f"Reconciliation gap: {result['gap_count']} receipts in Lakebase native "
            f"not yet in Gold Delta (lookback={lookback_hours}h). "
            f"Oldest missing: {result['oldest_missing_ts']}"
        )
    else:
        logger.info("Reconciliation check passed — no gaps found.")

    return result


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    print("\n── Synced Table Status ──────────────────────────────────────")
    statuses = check_sync_status()
    for s in statuses:
        print(f"  {s['state']:12s}  {s['source_table']}")

    # Optionally run reconciliation if LAKEBASE_HOST is set
    if os.environ.get("LAKEBASE_HOST"):
        from config.settings import get_settings
        settings = get_settings()
        print("\n── Reconciliation Check (last 24h) ──────────────────────────")
        result = reconcile_gaps(lakebase_conninfo=settings.lakebase_conninfo)
        print(f"  Gap count:   {result['gap_count']}")
        print(f"  Oldest miss: {result['oldest_missing_ts']}")
        print(f"  Newest miss: {result['newest_missing_ts']}")
        print(f"  {result['recommendation']}")
