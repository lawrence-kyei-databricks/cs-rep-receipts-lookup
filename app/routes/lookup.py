"""
Receipt lookup routes — direct Lakebase queries at sub-10ms.

All monetary values are in cents (BIGINT):
  total_cents, subtotal_cents, tax_cents
tender_type replaces the old payment_method column.
departments replaces the old categories column.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
import psycopg
from psycopg.rows import dict_row

from middleware.auth import get_current_user
from cache_utils import receipt_cache, customer_receipts_cache
from db_utils import get_lakebase_connection
from response_utils import optimize_receipt_response, filter_fields

router = APIRouter()


class ReceiptWriteRequest(BaseModel):
    """
    Direct POS write to Lakebase native receipt_transactions table.
    Monetary values in cents (BIGINT). tender_type: CREDIT/DEBIT/CASH/EBT.

    Input length limits prevent DoS via memory exhaustion and ensure
    compatibility with database column constraints.
    """
    transaction_id: str = Field(..., min_length=1, max_length=100)
    store_id: str = Field(..., min_length=1, max_length=50)
    store_name: str | None = Field(None, max_length=200)
    customer_id: str | None = Field(None, max_length=100)
    transaction_ts: str = Field(..., max_length=50)  # ISO 8601 timestamp
    subtotal_cents: int = Field(default=0, ge=0, le=1_000_000_000)  # Max $10M
    tax_cents: int = Field(default=0, ge=0, le=100_000_000)  # Max $1M tax
    total_cents: int = Field(..., ge=0, le=1_000_000_000)  # Max $10M
    tender_type: str | None = Field(None, max_length=20)  # CREDIT, DEBIT, CASH, EBT
    card_last4: str | None = Field(None, min_length=4, max_length=4)
    item_summary: str | None = Field(None, max_length=1000)
    items: list[dict] = Field(default_factory=list, max_length=500)  # Max 500 items per receipt

    @field_validator('card_last4')
    @classmethod
    def validate_card_last4(cls, v):
        """Ensure card_last4 is numeric if provided."""
        if v is not None and not v.isdigit():
            raise ValueError('card_last4 must contain only digits')
        return v


async def fetch_line_items(conn, transaction_id: str) -> list[dict]:
    """
    Fetch real line items from receipt_line_items table.

    Returns list of {"name": product_name, "price_cents": line_total_cents, ...}

    NOTE: This function is kept for backward compatibility and specific use cases
    where line items need to be fetched separately. For single receipt lookups,
    use the optimized get_receipt() which fetches receipt + line items in one query.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT
                line_number,
                sku,
                product_name,
                brand,
                category_l1,
                category_l2,
                quantity,
                unit_price_cents,
                line_total_cents,
                discount_cents
            FROM receipt_line_items
            WHERE transaction_id = %s
            ORDER BY line_number
            """,
            (transaction_id,),
        )
        items = await cur.fetchall()

        # Format for frontend: simple {"name": ..., "price_cents": ...} format
        return [
            {
                "name": item["product_name"],
                "price_cents": item["line_total_cents"],
                "sku": item["sku"],
                "brand": item["brand"],
                "quantity": float(item["quantity"]),
                "category": item["category_l1"]
            }
            for item in items
        ]


@router.get("/{transaction_id}")
async def get_receipt(
    transaction_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    fields: str | None = None,
    include_line_items: bool = True,
):
    """
    Fetch a single receipt by transaction ID with line items.

    Optimization: Uses single LEFT JOIN query to fetch receipt + line items
    instead of two separate queries (reduces latency by ~3-5ms per lookup).

    Cache strategy:
    - Cache hit: ~1-2ms (in-memory)
    - Cache miss: ~5-10ms (optimized single query via connection pool)
    - Previous implementation: ~8-15ms (two separate queries)

    Query parameters:
    - fields: Comma-separated field names to include (e.g., "transaction_id,total_cents,store_name")
              If not provided, returns all fields (default behavior)
    - include_line_items: If False, excludes line_items array (default: True)
              Useful for summary views where detailed line items aren't needed (~60-80% smaller payload)

    Payload optimization examples:
    - Full receipt: GET /receipt/{id}  (~2-5KB with line items)
    - Summary only: GET /receipt/{id}?include_line_items=false  (~400-800 bytes)
    - ID + total only: GET /receipt/{id}?fields=transaction_id,total_cents&include_line_items=false  (~100 bytes)

    Reads from Lakebase receipt_lookup + receipt_line_items (synced from Delta Gold).
    Returns total_cents (BIGINT); divide by 100 for dollar display.
    """
    # Check cache first (cache key is transaction_id only, filtering applied post-cache)
    cached_receipt = receipt_cache.get(transaction_id)
    if cached_receipt is not None:
        # Apply field filtering to cached result
        return optimize_receipt_response(cached_receipt, fields=fields, include_line_items=include_line_items)

    # Cache miss - fetch receipt + line items in ONE query using LEFT JOIN
    async with get_lakebase_connection(request) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT
                    rl.transaction_id, rl.store_id, rl.store_name, rl.customer_id,
                    rl.customer_name, rl.transaction_ts, rl.transaction_date,
                    rl.subtotal_cents, rl.tax_cents, rl.total_cents,
                    rl.tender_type, rl.card_last4, rl.item_count, rl.item_summary,
                    rl.category_tags,
                    li.line_number, li.sku, li.product_name, li.brand,
                    li.category_l1, li.quantity, li.unit_price_cents,
                    li.line_total_cents, li.discount_cents
                FROM receipt_lookup rl
                LEFT JOIN receipt_line_items li ON rl.transaction_id = li.transaction_id
                WHERE rl.transaction_id = %s
                ORDER BY li.line_number
                """,
                (transaction_id,),
            )
            rows = await cur.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="Receipt not found")

    # First row contains receipt data (same for all rows)
    first_row = rows[0]
    receipt_dict = {
        "transaction_id": first_row["transaction_id"],
        "store_id": first_row["store_id"],
        "store_name": first_row["store_name"],
        "customer_id": first_row["customer_id"],
        "customer_name": first_row["customer_name"],
        "transaction_ts": first_row["transaction_ts"],
        "transaction_date": first_row["transaction_date"],
        "subtotal_cents": first_row["subtotal_cents"],
        "tax_cents": first_row["tax_cents"],
        "total_cents": first_row["total_cents"],
        "tender_type": first_row["tender_type"],
        "card_last4": first_row["card_last4"],
        "item_count": first_row["item_count"],
        "item_summary": first_row["item_summary"],
        "category_tags": first_row["category_tags"],
    }

    # Build line_items array from joined results
    # Handle case where receipt exists but has no line items (line_number will be None)
    line_items = []
    for row in rows:
        if row["line_number"] is not None:  # line_number is NULL for receipts without line items
            line_items.append({
                "name": row["product_name"],
                "price_cents": row["line_total_cents"],
                "sku": row["sku"],
                "brand": row["brand"],
                "quantity": float(row["quantity"]) if row["quantity"] is not None else 0.0,
                "category": row["category_l1"]
            })

    receipt_dict["line_items"] = line_items

    # Store in cache for future requests (cache full object, filter on retrieval)
    receipt_cache.set(transaction_id, receipt_dict)

    # Apply field filtering before returning
    return optimize_receipt_response(receipt_dict, fields=fields, include_line_items=include_line_items)


@router.get("/customer/{customer_id}")
async def get_customer_receipts(
    customer_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    limit: int = 20,
    offset: int = 0,
    fields: str | None = None,
):
    """
    Fetch recent receipts for a customer. Paginated.

    Optimization: Uses LRU cache (5 min TTL) for frequently accessed customer lists.
    Cache hit: ~1-2ms (in-memory)
    Cache miss: ~5-8ms (database via connection pool)

    Query parameters:
    - limit: Max receipts to return (default: 20)
    - offset: Pagination offset (default: 0)
    - fields: Comma-separated field names (e.g., "transaction_id,total_cents,transaction_ts")
              If not provided, returns all fields

    Payload optimization examples:
    - Full receipt summaries: GET /receipt/customer/{id}  (~1-3KB per receipt)
    - ID + total + date only: GET /receipt/customer/{id}?fields=transaction_id,total_cents,transaction_ts  (~150 bytes per receipt)

    Returns total_cents in cents (BIGINT).
    Note: Full receipt details (with line items) require separate /receipt/{id} call.
    """
    # Build cache key from customer_id + pagination params (not fields - filter applied post-cache)
    cache_key = f"{customer_id}:{limit}:{offset}"

    # Check cache first
    cached_results = customer_receipts_cache.get(cache_key)
    if cached_results is not None:
        # Apply field filtering to cached results
        return filter_fields(cached_results, fields)

    # Cache miss - fetch from database
    async with get_lakebase_connection(request) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT transaction_id, store_id, store_name,
                       transaction_ts, total_cents, tender_type,
                       item_count, item_summary, category_tags
                FROM receipt_lookup
                WHERE customer_id = %s
                ORDER BY transaction_ts DESC
                LIMIT %s OFFSET %s
                """,
                (customer_id, limit, offset),
            )
            results = await cur.fetchall()
            receipt_list = [dict(r) for r in results]

    # Store in cache for future requests (5-minute TTL, cache full objects, filter on retrieval)
    customer_receipts_cache.set(cache_key, receipt_list)

    # Apply field filtering before returning
    return filter_fields(receipt_list, fields)


@router.post("/write")
async def write_receipt(
    receipt: ReceiptWriteRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Direct write to Lakebase native table (receipt_transactions).
    This is the instant path — receipt is queryable immediately.
    The Zerobus analytics path (Bronze→Silver→Gold) runs in parallel.

    Uses ON CONFLICT DO NOTHING — idempotent for retries.
    """
    conninfo = request.app.state.lakebase_conninfo

    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO receipt_transactions (
                    transaction_id, store_id, store_name, customer_id,
                    transaction_ts, subtotal_cents, tax_cents, total_cents,
                    tender_type, card_last4, item_count, item_summary, raw_items
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (transaction_id) DO NOTHING
                RETURNING transaction_id
                """,
                (
                    receipt.transaction_id,
                    receipt.store_id,
                    receipt.store_name,
                    receipt.customer_id,
                    receipt.transaction_ts,
                    receipt.subtotal_cents,
                    receipt.tax_cents,
                    receipt.total_cents,
                    receipt.tender_type,
                    receipt.card_last4,
                    len(receipt.items),
                    receipt.item_summary,
                    json.dumps(receipt.items),
                ),
            )
            result = await cur.fetchone()
        await conn.commit()

    if result:
        return {"status": "created", "transaction_id": receipt.transaction_id}
    return {"status": "exists", "transaction_id": receipt.transaction_id}
