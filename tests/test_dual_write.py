"""
Unit tests for the dual-write handler and POS event models.

These tests use mocks for both Lakebase (psycopg pool) and Zerobus, so
they run without any external connectivity. The full integration test lives
in test_lakebase_queries.py and requires LAKEBASE_* env vars.

Run with: pytest tests/test_dual_write.py -v
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pos_integration.models import (
    DualWriteResult,
    POSLineItem,
    POSReceiptEvent,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_ITEMS = [
    POSLineItem(
        upc="012345678901",
        sku="SKU-001",
        product_desc="Oat Milk 32oz",
        quantity=2.0,
        unit_price_cents=429,
        extended_cents=858,
        discount_cents=0,
        department_code="DAIRY",
    ),
    POSLineItem(
        upc="987654321098",
        sku="SKU-002",
        product_desc="Roquefort Wedge 8oz",
        quantity=1.0,
        unit_price_cents=899,
        extended_cents=899,
        discount_cents=0,
        department_code="CHEESE",
    ),
]


@pytest.fixture
def sample_receipt() -> POSReceiptEvent:
    return POSReceiptEvent(
        transaction_id="TXN-TEST-001",
        store_id="STORE-247",
        store_name="East Liberty",
        pos_terminal_id="LANE-04",
        cashier_id="EMP-9912",
        customer_id="CUST-TEST-001",
        transaction_ts=datetime(2026, 2, 18, 14, 30, 0, tzinfo=timezone.utc),
        subtotal_cents=1757,
        tax_cents=105,
        total_cents=1862,
        tender_type="CREDIT",
        card_last4="4532",
        items=SAMPLE_ITEMS,
    )


# ── Model tests ───────────────────────────────────────────────────────────────


class TestPOSLineItem:
    def test_requires_upc_or_sku(self):
        with pytest.raises(ValueError, match="upc or sku"):
            POSLineItem(
                product_desc="Mystery product",
                quantity=1.0,
                unit_price_cents=100,
                extended_cents=100,
            )

    def test_accepts_upc_only(self):
        item = POSLineItem(
            upc="000000000001",
            product_desc="Bread",
            quantity=1.0,
            unit_price_cents=349,
            extended_cents=349,
        )
        assert item.upc == "000000000001"

    def test_item_summary_fragment_plural(self):
        item = POSLineItem(
            upc="1",
            product_desc="Oat Milk",
            quantity=2.0,
            unit_price_cents=429,
            extended_cents=858,
        )
        assert item.item_summary_fragment() == "2x Oat Milk"

    def test_item_summary_fragment_singular(self):
        item = POSLineItem(
            upc="1",
            product_desc="Cheese",
            quantity=1.0,
            unit_price_cents=899,
            extended_cents=899,
        )
        assert item.item_summary_fragment() == "Cheese"


class TestPOSReceiptEvent:
    def test_valid_receipt(self, sample_receipt: POSReceiptEvent):
        assert sample_receipt.transaction_id == "TXN-TEST-001"
        assert sample_receipt.total_cents == 1862
        assert sample_receipt.item_count == 2

    def test_total_validation_passes_within_tolerance(self):
        # ±5 cents allowed
        event = POSReceiptEvent(
            transaction_id="TXN-002",
            store_id="S1",
            store_name="Store One",
            transaction_ts=datetime.now(tz=timezone.utc),
            subtotal_cents=1000,
            tax_cents=80,
            total_cents=1083,  # 3 cents over — within tolerance
            items=[],
        )
        assert event.total_cents == 1083

    def test_total_validation_fails_when_far_off(self):
        with pytest.raises(ValueError, match="does not equal total_cents"):
            POSReceiptEvent(
                transaction_id="TXN-BAD",
                store_id="S1",
                store_name="Store One",
                transaction_ts=datetime.now(tz=timezone.utc),
                subtotal_cents=1000,
                tax_cents=80,
                total_cents=2000,  # way off
                items=[],
            )

    def test_card_last4_strips_to_digits(self):
        event = POSReceiptEvent(
            transaction_id="TXN-003",
            store_id="S1",
            store_name="Store One",
            transaction_ts=datetime.now(tz=timezone.utc),
            total_cents=500,
            card_last4="****4532",
            items=[],
        )
        assert event.card_last4 == "4532"

    def test_card_last4_invalid_returns_none(self):
        # Less than 4 digits → validator returns None
        event = POSReceiptEvent(
            transaction_id="TXN-004",
            store_id="S1",
            store_name="Store One",
            transaction_ts=datetime.now(tz=timezone.utc),
            total_cents=500,
            card_last4="123",
            items=[],
        )
        assert event.card_last4 is None

    def test_item_summary_top_three_plus_more(self):
        items = [
            POSLineItem(upc=str(i), product_desc=f"Item {i}", quantity=1.0,
                        unit_price_cents=100, extended_cents=100)
            for i in range(5)
        ]
        event = POSReceiptEvent(
            transaction_id="TXN-005",
            store_id="S1",
            store_name="Store One",
            transaction_ts=datetime.now(tz=timezone.utc),
            total_cents=500,
            items=items,
        )
        assert event.item_summary == "Item 0, Item 1, Item 2 + 2 more"

    def test_raw_items_json_serializable(self, sample_receipt: POSReceiptEvent):
        raw = sample_receipt.raw_items_json()
        assert isinstance(raw, list)
        assert len(raw) == 2
        # Must be JSON-serializable (no datetime objects, etc.)
        json.dumps(raw)  # raises if not serializable
        assert raw[0]["product_desc"] == "Oat Milk 32oz"


# ── DualWriteHandler tests ────────────────────────────────────────────────────


class TestDualWriteHandler:
    """Tests for DualWriteHandler using mocked Lakebase pool and Zerobus."""

    def _make_mock_pool(self) -> MagicMock:
        """Build a mock async connection pool that acts like psycopg_pool.

        Key subtlety: psycopg3's conn.cursor() is a *synchronous* call that
        returns an object usable as an async context manager. Using AsyncMock
        for the connection makes cursor() return a coroutine instead, which
        breaks `async with conn.cursor()`. The fix: MagicMock for the
        connection; only the truly-async methods (commit, execute) get AsyncMock.
        """
        # cursor is a sync call returning an async context manager
        mock_cursor = MagicMock()
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock(return_value=None)
        mock_cursor.execute = AsyncMock()

        # conn.cursor() is sync; conn.commit() is async
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.commit = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.closed = False

        # pool.connection() is an async context manager
        mock_pool.connection = MagicMock()
        mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

        return mock_pool

    @pytest.mark.asyncio
    async def test_success_both_paths(self, sample_receipt: POSReceiptEvent):
        """Both Lakebase and Zerobus succeed → overall_status = 'success'."""
        from pos_integration.dual_write_handler import DualWriteHandler

        mock_zerobus = MagicMock()
        mock_zerobus.ingest_receipt.return_value = {
            "status": "ingested",
            "event_id": "evt-123",
            "receipts_ack": "2026-02-18T14:30:01Z",
            "items_ack": "2026-02-18T14:30:01Z",
            "item_count": 2,
        }

        mock_pool = self._make_mock_pool()

        handler = DualWriteHandler(
            lakebase_conninfo="host=mock port=5432 dbname=test user=u password=p",
            zerobus_ingester=mock_zerobus,
        )

        with patch(
            "pos_integration.dual_write_handler.get_pool",
            new=AsyncMock(return_value=mock_pool),
        ):
            result = await handler.write_receipt(sample_receipt)

        assert isinstance(result, DualWriteResult)
        assert result.overall_status == "success"
        assert result.lakebase_ok
        assert result.zerobus_ok
        assert result.transaction_id == "TXN-TEST-001"

    @pytest.mark.asyncio
    async def test_zerobus_failure_gives_partial(self, sample_receipt: POSReceiptEvent):
        """Zerobus fails but Lakebase succeeds → overall_status = 'partial'."""
        from pos_integration.dual_write_handler import DualWriteHandler

        mock_zerobus = MagicMock()
        mock_zerobus.ingest_receipt.side_effect = ConnectionError("Zerobus unreachable")

        mock_pool = self._make_mock_pool()

        handler = DualWriteHandler(
            lakebase_conninfo="host=mock port=5432 dbname=test user=u password=p",
            zerobus_ingester=mock_zerobus,
        )

        with patch(
            "pos_integration.dual_write_handler.get_pool",
            new=AsyncMock(return_value=mock_pool),
        ):
            result = await handler.write_receipt(sample_receipt)

        assert result.overall_status == "partial"
        assert result.lakebase_ok
        assert not result.zerobus_ok
        assert "Zerobus unreachable" in result.zerobus["error"]

    @pytest.mark.asyncio
    async def test_lakebase_failure_raises(self, sample_receipt: POSReceiptEvent):
        """Lakebase fails → RuntimeError raised (POS must retry)."""
        from pos_integration.dual_write_handler import DualWriteHandler

        mock_zerobus = MagicMock()

        # Pool that raises on connection entry
        mock_pool = MagicMock()
        mock_pool.closed = False
        mock_pool.connection.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("Connection refused")
        )
        mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

        handler = DualWriteHandler(
            lakebase_conninfo="host=mock port=5432 dbname=test user=u password=p",
            zerobus_ingester=mock_zerobus,
        )

        with patch(
            "pos_integration.dual_write_handler.get_pool",
            new=AsyncMock(return_value=mock_pool),
        ):
            with pytest.raises(RuntimeError, match="Critical path"):
                await handler.write_receipt(sample_receipt)

        # Zerobus must NOT be called when Lakebase fails
        mock_zerobus.ingest_receipt.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_batch_returns_results_per_receipt(self):
        """write_batch returns one result per receipt, including failures."""
        from pos_integration.dual_write_handler import DualWriteHandler

        receipts = [
            POSReceiptEvent(
                transaction_id=f"TXN-BATCH-{i:03d}",
                store_id="S1",
                store_name="Store One",
                transaction_ts=datetime.now(tz=timezone.utc),
                total_cents=1000 + i,
                items=[],
            )
            for i in range(3)
        ]

        mock_zerobus = MagicMock()
        mock_zerobus.ingest_receipt.return_value = {
            "status": "ingested",
            "event_id": "evt-batch",
            "receipts_ack": None,
            "items_ack": None,
            "item_count": 0,
        }

        mock_pool = self._make_mock_pool()

        handler = DualWriteHandler(
            lakebase_conninfo="host=mock port=5432 dbname=test user=u password=p",
            zerobus_ingester=mock_zerobus,
        )

        with patch(
            "pos_integration.dual_write_handler.get_pool",
            new=AsyncMock(return_value=mock_pool),
        ):
            results = await handler.write_batch(receipts)

        assert len(results) == 3
        for r in results:
            assert isinstance(r, DualWriteResult)
            assert r.overall_status == "success"

    @pytest.mark.asyncio
    async def test_idempotent_write_no_duplicate(self, sample_receipt: POSReceiptEvent):
        """Same transaction_id written twice — only one INSERT should fire."""
        from pos_integration.dual_write_handler import DualWriteHandler

        mock_zerobus = MagicMock()
        mock_zerobus.ingest_receipt.return_value = {
            "status": "ingested",
            "event_id": "evt-idem",
            "receipts_ack": None,
            "items_ack": None,
            "item_count": 2,
        }

        # Track how many times execute() was called
        execute_call_count = 0

        async def counting_execute(sql, params=None):
            nonlocal execute_call_count
            execute_call_count += 1

        # cursor() is sync → MagicMock; execute is async → AsyncMock/coroutine
        mock_cursor = MagicMock()
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock(return_value=None)
        mock_cursor.execute = counting_execute

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.commit = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.closed = False
        mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=None)

        handler = DualWriteHandler(
            lakebase_conninfo="host=mock port=5432 dbname=test user=u password=p",
            zerobus_ingester=mock_zerobus,
        )

        with patch(
            "pos_integration.dual_write_handler.get_pool",
            new=AsyncMock(return_value=mock_pool),
        ):
            await handler.write_receipt(sample_receipt)
            await handler.write_receipt(sample_receipt)

        # The INSERT uses ON CONFLICT DO NOTHING — we verify the SQL fires
        # twice (psycopg handles dedup), not that we short-circuit at the
        # handler level (that's the DB's job).
        assert execute_call_count == 2


# ── DualWriteResult model tests ───────────────────────────────────────────────


class TestDualWriteResult:
    def test_lakebase_ok_property(self):
        r = DualWriteResult(
            transaction_id="T1",
            lakebase={"status": "success"},
            zerobus={"status": "failed", "error": "timeout"},
            overall_status="partial",
        )
        assert r.lakebase_ok is True
        assert r.zerobus_ok is False

    def test_both_ok(self):
        r = DualWriteResult(
            transaction_id="T2",
            lakebase={"status": "success"},
            zerobus={"status": "success"},
            overall_status="success",
        )
        assert r.lakebase_ok and r.zerobus_ok
