"""
Giant Eagle — Dual Write Handler
POS systems call this to write receipts to BOTH paths simultaneously:
  1. JDBC → Lakebase (instant, sub-10ms, operational)
  2. gRPC → Zerobus → Delta (analytics, 2-5 min end-to-end, DR-protected)

This is the critical integration point. Both writes use transaction_id
as the idempotency key for reconciliation between the two paths.

Failure semantics:
  - Lakebase FAILS  → Raise error; POS must retry the entire operation.
  - Zerobus FAILS   → Receipt IS captured in Lakebase. Reconciliation job
                      (pipelines/reconcile.py) will sync missing records to
                      Delta. Log a warning; do NOT surface this to the POS.
  - BOTH FAIL       → POS retries the whole operation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import psycopg
import psycopg_pool
from psycopg.rows import dict_row

from infra.zerobus_client import ZerobusReceiptIngester
from pos_integration.models import DualWriteResult, POSReceiptEvent

logger = logging.getLogger(__name__)

# ── Connection pool (module-level singleton, shared across requests) ──────────
# Created lazily on first use via get_pool(). Sizing: min=2 keeps two warm
# connections during quiet hours; max=10 handles parallel POS writes at peak.
_pool: psycopg_pool.AsyncConnectionPool | None = None
_pool_lock: asyncio.Lock | None = None


async def get_pool(conninfo: str) -> psycopg_pool.AsyncConnectionPool:
    """
    Return (or lazily create) the shared Lakebase connection pool.

    Thread-safe with asyncio.Lock to prevent race conditions where multiple
    concurrent requests attempt to create the pool simultaneously.

    The lock is created lazily in the current event loop to avoid
    "attached to different loop" errors.
    """
    global _pool, _pool_lock

    # Fast path: pool already exists and is open
    if _pool is not None and not _pool.closed:
        return _pool

    # Lazy-create the lock in the current event loop
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()

    # Slow path: need to create or recreate the pool
    async with _pool_lock:
        # Double-check after acquiring lock (another task may have created it)
        if _pool is None or _pool.closed:
            logger.info("Creating Lakebase connection pool (min=2, max=10)...")
            _pool = psycopg_pool.AsyncConnectionPool(
                conninfo=conninfo,
                min_size=2,
                max_size=10,
                kwargs={"row_factory": dict_row},
                open=False,
            )
            await _pool.open()
            logger.info("Lakebase connection pool opened successfully")

    return _pool


class DualWriteHandler:
    """
    Handles dual-path receipt ingestion from POS systems.

    Strategy:
    - Lakebase write is the "hot path" — must succeed for the receipt to be
      considered captured. This gives instant queryability for CS reps.
    - Zerobus write is the "analytics path" — fires after Lakebase succeeds.
      If it fails, the receipt still exists in Lakebase, and the reconciliation
      job will catch the gap from Lakebase → Delta.

    Usage:
        handler = DualWriteHandler(conninfo, zerobus_ingester)
        result = await handler.write_receipt(receipt_event)
    """

    def __init__(
        self,
        lakebase_conninfo: str,
        zerobus_ingester: ZerobusReceiptIngester | None = None,
    ):
        self.lakebase_conninfo = lakebase_conninfo
        self.zerobus = zerobus_ingester or ZerobusReceiptIngester()

    async def write_receipt(self, receipt: POSReceiptEvent) -> DualWriteResult:
        """
        Dual-write a single POS receipt.

        Args:
            receipt: Validated POSReceiptEvent (amounts in cents).

        Returns:
            DualWriteResult with status of both paths.

        Raises:
            RuntimeError: If the Lakebase (critical) path fails.
        """
        lakebase_result: dict[str, Any] = {"status": "pending"}
        zerobus_result: dict[str, Any] = {"status": "pending"}

        # ── 1. Lakebase write (critical path — must succeed) ─────────────────
        try:
            pool = await get_pool(self.lakebase_conninfo)
            async with pool.connection() as conn:
                await self._write_to_lakebase(conn, receipt)
            lakebase_result = {"status": "success"}
            logger.info(f"Lakebase write OK: {receipt.transaction_id}")
        except Exception as exc:
            logger.error(
                f"Lakebase write FAILED for {receipt.transaction_id}: {exc}"
            )
            lakebase_result = {"status": "failed", "error": str(exc)}
            raise RuntimeError(
                f"Critical path (Lakebase) failed for {receipt.transaction_id}: {exc}"
            ) from exc

        # ── 2. Zerobus write (analytics path — best effort) ──────────────────
        try:
            zb_outcome = self.zerobus.ingest_receipt(receipt)
            zerobus_result = {
                "status": "success",
                "ack_timestamp": zb_outcome.get("receipts_ack"),
                "event_id": zb_outcome.get("event_id"),
            }
            logger.info(f"Zerobus write OK: {receipt.transaction_id}")
        except Exception as exc:
            # Non-critical: log warning; reconciliation job will catch the gap.
            logger.warning(
                f"Zerobus write FAILED for {receipt.transaction_id}: {exc}. "
                "Receipt is safe in Lakebase. Reconciliation will sync to Delta."
            )
            zerobus_result = {"status": "failed", "error": str(exc)}

        overall = (
            "success"
            if zerobus_result["status"] == "success"
            else "partial"  # Lakebase OK but Zerobus lagging
        )

        return DualWriteResult(
            transaction_id=receipt.transaction_id,
            lakebase=lakebase_result,
            zerobus=zerobus_result,
            overall_status=overall,
        )

    async def write_batch(
        self, receipts: list[POSReceiptEvent]
    ) -> list[DualWriteResult | BaseException]:
        """
        Process a batch of receipts concurrently.

        Returns a list aligned with the input — entries are DualWriteResult
        on success or the Exception on failure (asyncio.gather semantics with
        return_exceptions=True so one failure doesn't cancel others).
        """
        tasks = [self.write_receipt(r) for r in receipts]
        return await asyncio.gather(*tasks, return_exceptions=True)  # type: ignore[return-value]

    # ── Private ───────────────────────────────────────────────────────────────

    @staticmethod
    async def _write_to_lakebase(
        conn: psycopg.AsyncConnection, receipt: POSReceiptEvent
    ) -> None:
        """
        INSERT receipt into Lakebase receipt_transactions table.

        Uses ON CONFLICT DO NOTHING for idempotency — the POS may retry
        the same transaction_id after a network blip; we must not duplicate.

        Column mapping (matches Phase 1 schema exactly):
          pos_terminal_id  ← receipt.pos_terminal_id  (was: register_id — fixed)
          total_cents      ← receipt.total_cents       (was: total_amount float — fixed)
          tender_type      ← receipt.tender_type       (was: payment_method — fixed)
          raw_items        ← receipt.raw_items_json()  (JSONB, was: raw_payload TEXT — fixed)
        """
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO receipt_transactions (
                    transaction_id,
                    store_id,
                    store_name,
                    pos_terminal_id,
                    cashier_id,
                    customer_id,
                    transaction_ts,
                    subtotal_cents,
                    tax_cents,
                    total_cents,
                    tender_type,
                    card_last4,
                    item_count,
                    item_summary,
                    raw_items
                ) VALUES (
                    %(transaction_id)s,
                    %(store_id)s,
                    %(store_name)s,
                    %(pos_terminal_id)s,
                    %(cashier_id)s,
                    %(customer_id)s,
                    %(transaction_ts)s,
                    %(subtotal_cents)s,
                    %(tax_cents)s,
                    %(total_cents)s,
                    %(tender_type)s,
                    %(card_last4)s,
                    %(item_count)s,
                    %(item_summary)s,
                    %(raw_items)s::jsonb
                )
                ON CONFLICT (transaction_id) DO NOTHING
                """,
                {
                    "transaction_id": receipt.transaction_id,
                    "store_id": receipt.store_id,
                    "store_name": receipt.store_name,
                    "pos_terminal_id": receipt.pos_terminal_id,
                    "cashier_id": receipt.cashier_id,
                    "customer_id": receipt.customer_id,
                    "transaction_ts": receipt.transaction_ts,
                    "subtotal_cents": receipt.subtotal_cents,
                    "tax_cents": receipt.tax_cents,
                    "total_cents": receipt.total_cents,
                    "tender_type": receipt.tender_type,
                    "card_last4": receipt.card_last4,
                    "item_count": receipt.item_count,
                    "item_summary": receipt.item_summary,
                    "raw_items": json.dumps(receipt.raw_items_json()),
                },
            )
        await conn.commit()
