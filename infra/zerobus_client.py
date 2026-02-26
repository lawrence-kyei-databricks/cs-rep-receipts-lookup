"""
Giant Eagle — Zerobus Ingestion Client
Sends POS receipt records to Zerobus via gRPC for Delta materialization.

Zerobus writes directly to Delta tables — no Kafka, no message bus.
Sub-50ms durable acknowledgment per record.

Target tables (both have CDF enabled, set in Phase 1):
  giant_eagle.bronze.pos_raw_receipts  — one row per transaction
  giant_eagle.bronze.pos_raw_items     — one row per line item

This runs on the POS integration layer (or as a middleware service).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from databricks.sdk import WorkspaceClient

from pos_integration.models import POSReceiptEvent

logger = logging.getLogger(__name__)


class ZerobusReceiptIngester:
    """Ingests POS receipt data into Delta via Zerobus gRPC direct write.

    Produces records to TWO target tables:
      - pos_raw_receipts: one record per transaction (header)
      - pos_raw_items:    one record per line item

    Both tables use `event_id` (UUID) as the Zerobus deduplication key and
    `transaction_id` as the business-level idempotency key for downstream
    Silver dedup.
    """

    RECEIPTS_TABLE = "giant_eagle.bronze.pos_raw_receipts"
    ITEMS_TABLE = "giant_eagle.bronze.pos_raw_items"

    def __init__(self, workspace_client: WorkspaceClient | None = None):
        self.client = workspace_client or WorkspaceClient()
        logger.info(
            "ZerobusReceiptIngester initialized → "
            f"{self.RECEIPTS_TABLE} + {self.ITEMS_TABLE}"
        )

    def ingest_receipt(self, receipt: POSReceiptEvent) -> dict[str, Any]:
        """
        Send a single POS receipt (header + items) to Zerobus.

        Returns:
            dict with ingestion status and ack timestamps for both tables.

        Note: Zerobus provides at-least-once delivery. The Silver pipeline
        deduplicates on transaction_id using MERGE INTO with CDF.
        """
        event_id = str(uuid.uuid4())
        ingest_ts = datetime.now(tz=timezone.utc).isoformat()

        # ── Build receipt header record ──────────────────────────────────────
        receipt_record = {
            "event_id": event_id,
            "transaction_id": receipt.transaction_id,
            "store_id": receipt.store_id,
            "store_name": receipt.store_name,
            "pos_terminal_id": receipt.pos_terminal_id,
            "cashier_id": receipt.cashier_id,
            "customer_id": receipt.customer_id,
            "transaction_ts": receipt.transaction_ts.isoformat(),
            "subtotal_cents": receipt.subtotal_cents,
            "tax_cents": receipt.tax_cents,
            "total_cents": receipt.total_cents,
            "tender_type": receipt.tender_type,
            "card_last4": receipt.card_last4,
            "raw_payload": json.dumps(receipt.model_dump(mode="json")),
            "ingested_ts": ingest_ts,
        }

        # ── Build line item records ──────────────────────────────────────────
        item_records = [
            {
                "event_id": event_id,
                "transaction_id": receipt.transaction_id,
                "item_seq": seq,
                "upc": item.upc,
                "sku": item.sku,
                "product_desc": item.product_desc,
                "quantity": item.quantity,
                "unit_price_cents": item.unit_price_cents,
                "extended_cents": item.extended_cents,
                "discount_cents": item.discount_cents,
                "department_code": item.department_code,
                "ingested_ts": ingest_ts,
            }
            for seq, item in enumerate(receipt.items, start=1)
        ]

        # ── Send to Zerobus ──────────────────────────────────────────────────
        # TODO: Replace with native Zerobus gRPC client when Databricks SDK
        # exposes it directly. Current path uses the REST API proxy.
        #
        # Production path:
        #   from databricks.zerobus import ZerobusClient
        #   zb = ZerobusClient(workspace_url=self.client.config.host)
        #   ack = zb.ingest(table=self.RECEIPTS_TABLE, records=[receipt_record])
        #
        # Until then, use the /api/2.0/zerobus/ingest endpoint.
        receipt_ack = self._post_to_zerobus(self.RECEIPTS_TABLE, [receipt_record])
        items_ack: dict[str, Any] = {"ack_timestamp": None}
        if item_records:
            items_ack = self._post_to_zerobus(self.ITEMS_TABLE, item_records)

        result = {
            "status": "ingested",
            "event_id": event_id,
            "transaction_id": receipt.transaction_id,
            "receipts_ack": receipt_ack.get("ack_timestamp"),
            "items_ack": items_ack.get("ack_timestamp"),
            "item_count": len(item_records),
        }
        logger.info(
            f"Receipt {receipt.transaction_id} ingested via Zerobus "
            f"(event_id={event_id}, items={len(item_records)})"
        )
        return result

    def ingest_batch(self, receipts: list[POSReceiptEvent]) -> dict[str, Any]:
        """
        Batch ingest multiple receipts. Preferred during checkout rush.

        Each receipt still gets its own event_id for per-record traceability.
        Returns a summary with per-receipt outcomes.
        """
        results: dict[str, Any] = {"ingested": 0, "failed": 0, "errors": []}

        for receipt in receipts:
            try:
                self.ingest_receipt(receipt)
                results["ingested"] += 1
            except Exception as exc:
                results["failed"] += 1
                results["errors"].append(
                    {
                        "transaction_id": receipt.transaction_id,
                        "error": str(exc),
                    }
                )

        logger.info(
            f"Batch complete: {results['ingested']} ingested, "
            f"{results['failed']} failed out of {len(receipts)} total"
        )
        return results

    # ── Internal ──────────────────────────────────────────────────────────────

    def _post_to_zerobus(
        self, table_name: str, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """POST records to the Zerobus REST API. Raises on failure."""
        try:
            response = self.client.api_client.do(
                method="POST",
                path="/api/2.0/zerobus/ingest",
                body={"table_name": table_name, "records": records},
            )
            return response or {}
        except Exception as exc:
            logger.error(
                f"Zerobus ingest failed for table={table_name}, "
                f"records={len(records)}: {exc}"
            )
            raise
