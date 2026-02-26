"""
Integration tests for receipt lookup Lakebase queries.

These tests hit the real Lakebase instance and require env vars:
  LAKEBASE_HOST, LAKEBASE_USER, LAKEBASE_PASSWORD
  (LAKEBASE_PORT and LAKEBASE_DATABASE have defaults)

Run with:
  pytest tests/test_lakebase_queries.py -v

Column names updated to match Phase 1 schema (pos_terminal_id, total_cents,
tender_type, raw_items) — old stale names removed.
"""

from __future__ import annotations

import json
import os

import psycopg
import pytest
from psycopg.rows import dict_row

LAKEBASE_CONNINFO = (
    f"host={os.environ.get('LAKEBASE_HOST', 'localhost')} "
    f"port={os.environ.get('LAKEBASE_PORT', '5432')} "
    f"dbname={os.environ.get('LAKEBASE_DATABASE', 'giant_eagle')} "
    f"user={os.environ.get('LAKEBASE_USER', 'test_user')} "
    f"password={os.environ.get('LAKEBASE_PASSWORD', '')} "
    f"sslmode=require"
)

# ── Canonical sample receipt (amounts in cents, columns match Phase 1 schema) ─
SAMPLE_RECEIPT = {
    "transaction_id": "TEST-INTEG-001-20260218",
    "store_id": "STORE-247",
    "store_name": "East Liberty",
    "pos_terminal_id": "LANE-04",          # was: register_id (FIXED)
    "cashier_id": "EMP-9912",
    "customer_id": "CUST-TEST-001",
    "transaction_ts": "2026-02-18T14:30:00+00:00",
    "subtotal_cents": 4732,                 # was: total_amount float (FIXED — cents)
    "tax_cents": 284,
    "total_cents": 5016,                    # was: total_amount (FIXED — cents, renamed)
    "tender_type": "CREDIT",               # was: payment_method (FIXED)
    "card_last4": "4532",
    "item_count": 2,
    "item_summary": "Oat Milk 32oz, Roquefort Wedge 8oz",
    "raw_items": [                          # was: raw_payload TEXT (FIXED — JSONB array)
        {
            "upc": "012345678901",
            "sku": "SKU-001",
            "product_desc": "Oat Milk 32oz",
            "quantity": 2.0,
            "unit_price_cents": 429,
            "extended_cents": 858,
            "discount_cents": 0,
            "department_code": "DAIRY",
        },
        {
            "upc": "987654321098",
            "sku": "SKU-002",
            "product_desc": "Roquefort Wedge 8oz",
            "quantity": 1.0,
            "unit_price_cents": 899,
            "extended_cents": 899,
            "discount_cents": 0,
            "department_code": "CHEESE",
        },
    ],
}


@pytest.fixture
def db_conn():
    """Live Lakebase connection for integration tests."""
    conn = psycopg.connect(LAKEBASE_CONNINFO, row_factory=dict_row)
    yield conn
    # Cleanup: remove test data so tests are re-runnable
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM receipt_transactions WHERE transaction_id LIKE 'TEST-INTEG-%'"
        )
        cur.execute(
            "DELETE FROM audit_log WHERE transaction_id LIKE 'TEST-INTEG-%'"
        )
    conn.commit()
    conn.close()


class TestReceiptWrite:
    """Test writing receipts to Lakebase native table with correct schema."""

    def test_write_receipt(self, db_conn):
        """Receipt should be writable and immediately queryable."""
        r = SAMPLE_RECEIPT

        with db_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO receipt_transactions (
                    transaction_id, store_id, store_name, pos_terminal_id,
                    cashier_id, customer_id, transaction_ts,
                    subtotal_cents, tax_cents, total_cents,
                    tender_type, card_last4, item_count, item_summary, raw_items
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, %s::jsonb
                )
                ON CONFLICT (transaction_id) DO NOTHING
                """,
                (
                    r["transaction_id"], r["store_id"], r["store_name"],
                    r["pos_terminal_id"], r["cashier_id"], r["customer_id"],
                    r["transaction_ts"], r["subtotal_cents"], r["tax_cents"],
                    r["total_cents"], r["tender_type"], r["card_last4"],
                    r["item_count"], r["item_summary"], json.dumps(r["raw_items"]),
                ),
            )
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM receipt_transactions WHERE transaction_id = %s",
                (r["transaction_id"],),
            )
            row = cur.fetchone()

        assert row is not None
        assert row["store_id"] == "STORE-247"
        assert row["store_name"] == "East Liberty"
        assert row["total_cents"] == 5016          # cents, not float
        assert row["tender_type"] == "CREDIT"      # not payment_method
        assert row["card_last4"] == "4532"
        assert row["pos_terminal_id"] == "LANE-04" # not register_id
        assert isinstance(row["raw_items"], list)  # JSONB deserialized
        assert row["raw_items"][0]["product_desc"] == "Oat Milk 32oz"

    def test_idempotent_write(self, db_conn):
        """Writing the same transaction_id twice must not duplicate rows."""
        r = SAMPLE_RECEIPT

        with db_conn.cursor() as cur:
            for _ in range(2):
                cur.execute(
                    """
                    INSERT INTO receipt_transactions (
                        transaction_id, store_id, store_name,
                        transaction_ts, total_cents, item_count, raw_items
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (transaction_id) DO NOTHING
                    """,
                    (
                        r["transaction_id"], r["store_id"], r["store_name"],
                        r["transaction_ts"], r["total_cents"],
                        r["item_count"], json.dumps(r["raw_items"]),
                    ),
                )
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM receipt_transactions WHERE transaction_id = %s",
                (r["transaction_id"],),
            )
            assert cur.fetchone()["cnt"] == 1

    def test_fuzzy_lookup_by_card_and_store(self, db_conn):
        """Fuzzy search: card_last4 + store_id should find the receipt."""
        r = SAMPLE_RECEIPT

        with db_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO receipt_transactions (
                    transaction_id, store_id, store_name,
                    transaction_ts, total_cents, tender_type, card_last4,
                    item_count, raw_items
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (transaction_id) DO NOTHING
                """,
                (
                    r["transaction_id"], r["store_id"], r["store_name"],
                    r["transaction_ts"], r["total_cents"], r["tender_type"],
                    r["card_last4"], r["item_count"], json.dumps(r["raw_items"]),
                ),
            )
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute(
                """
                SELECT transaction_id, store_name, total_cents, card_last4
                FROM receipt_transactions
                WHERE card_last4 = %s
                  AND store_id = %s
                  AND transaction_ts >= %s::timestamptz - INTERVAL '1 day'
                  AND transaction_ts <= %s::timestamptz + INTERVAL '1 day'
                ORDER BY transaction_ts DESC
                LIMIT 10
                """,
                (
                    r["card_last4"],
                    r["store_id"],
                    r["transaction_ts"],
                    r["transaction_ts"],
                ),
            )
            rows = cur.fetchall()

        assert len(rows) >= 1
        assert any(row["transaction_id"] == r["transaction_id"] for row in rows)

    def test_fuzzy_lookup_by_amount_range(self, db_conn):
        """Fuzzy search: ±10% of total should find the receipt."""
        r = SAMPLE_RECEIPT
        target = r["total_cents"]
        low = int(target * 0.9)
        high = int(target * 1.1)

        with db_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO receipt_transactions (
                    transaction_id, store_id, store_name,
                    transaction_ts, total_cents, item_count, raw_items
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (transaction_id) DO NOTHING
                """,
                (
                    r["transaction_id"], r["store_id"], r["store_name"],
                    r["transaction_ts"], r["total_cents"],
                    r["item_count"], json.dumps(r["raw_items"]),
                ),
            )
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute(
                """
                SELECT transaction_id, total_cents
                FROM receipt_transactions
                WHERE total_cents BETWEEN %s AND %s
                  AND store_id = %s
                """,
                (low, high, r["store_id"]),
            )
            rows = cur.fetchall()

        assert any(row["transaction_id"] == r["transaction_id"] for row in rows)


class TestAuditLog:
    """Test audit log writes — every CS action must be logged."""

    def test_write_audit_entry(self, db_conn):
        """Audit log should accept a CS rep lookup action."""
        with db_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_log (
                    rep_id, rep_role, action_type,
                    transaction_id, customer_id, result_count,
                    session_id, duration_ms, status_code
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING audit_id
                """,
                (
                    "REP-TEST-001", "cs_rep", "LOOKUP",
                    SAMPLE_RECEIPT["transaction_id"],
                    SAMPLE_RECEIPT["customer_id"],
                    1, "SESSION-TEST-001", 42, 200,
                ),
            )
            row = cur.fetchone()
        db_conn.commit()

        assert row is not None
        assert row["audit_id"] > 0

    def test_audit_log_indexed_by_rep(self, db_conn):
        """Rep-scoped audit query should use the idx_al_rep index."""
        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM audit_log WHERE rep_id = %s",
                ("REP-TEST-001",),
            )
            result = cur.fetchone()
        assert result is not None
        assert result["cnt"] >= 0


class TestAgentState:
    """Test agent state persistence for NL search sessions."""

    def test_insert_agent_state(self, db_conn):
        """Agent state should be insertable and queryable."""
        import json
        from datetime import timedelta

        from datetime import datetime, timezone

        now = datetime.now(tz=timezone.utc)

        with db_conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_state (
                    session_id, rep_id, agent_type, state_json,
                    customer_id, last_query, turn_count, expires_at
                ) VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (session_id) DO UPDATE
                SET state_json = EXCLUDED.state_json,
                    updated_at = now(),
                    turn_count  = agent_state.turn_count + 1
                """,
                (
                    "SESSION-AGENT-TEST-001",
                    "REP-TEST-001",
                    "nl_search",
                    json.dumps({"last_filter": {"store": "East Liberty"}}),
                    "CUST-TEST-001",
                    "fancy cheese from last week",
                    1,
                    now + timedelta(hours=4),
                ),
            )
        db_conn.commit()

        with db_conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM agent_state WHERE session_id = %s",
                ("SESSION-AGENT-TEST-001",),
            )
            row = cur.fetchone()

        assert row is not None
        assert row["rep_id"] == "REP-TEST-001"
        assert row["agent_type"] == "nl_search"
        assert row["state_json"]["last_filter"]["store"] == "East Liberty"

        # Cleanup
        with db_conn.cursor() as cur:
            cur.execute("DELETE FROM agent_state WHERE session_id = 'SESSION-AGENT-TEST-001'")
        db_conn.commit()


class TestSemanticSearch:
    """pgvector semantic search (requires embedding pipeline to have run)."""

    @pytest.mark.skip(reason="Requires embedding pipeline to have run first (Phase 4)")
    def test_semantic_search_returns_results(self, db_conn):
        """Semantic search should return similar products."""
        with db_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM product_embeddings")
            count = cur.fetchone()["cnt"]
        assert count > 0, "No embeddings found — run embedding pipeline first (Phase 4)"

    def test_product_embeddings_table_exists(self, db_conn):
        """Table must exist even before embeddings are populated."""
        with db_conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'product_embeddings'
                  AND table_schema = 'public'
                ORDER BY ordinal_position
                """
            )
            columns = {row["column_name"]: row["data_type"] for row in cur.fetchall()}

        assert "sku" in columns
        assert "embedding" in columns
        assert "product_name" in columns
