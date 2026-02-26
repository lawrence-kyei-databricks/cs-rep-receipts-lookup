"""
Fuzzy search routes — multi-field approximate matching.
This is the most-used CS endpoint. Customers call with vague info:
"I was at the East Liberty store last Tuesday, spent about $40"

Supports: date range, store name/ID, amount range (in dollars → converted to cents),
last 4 of card, customer ID.

All monetary Lakebase columns are BIGINT in cents.
amount_min/amount_max in the request are accepted as dollars (float)
and multiplied by 100 before the SQL WHERE clause.
"""

from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, model_validator

from middleware.auth import get_current_user
from db_utils import get_lakebase_connection
from response_utils import filter_fields

router = APIRouter()


class FuzzySearchRequest(BaseModel):
    """
    All fields optional — CS rep fills in whatever the customer provides.
    amount_min/amount_max are in dollars (e.g. 40.00 = $40.00).
    """
    customer_id: Optional[str] = None
    store_id: Optional[str] = None
    store_name: Optional[str] = None        # "East Liberty", "Market District Robinson"
    date_from: Optional[str] = None         # ISO date string
    date_to: Optional[str] = None
    amount_min: Optional[float] = None      # dollars (converted to cents in query)
    amount_max: Optional[float] = None      # dollars (converted to cents in query)
    card_last4: Optional[str] = None        # last 4 digits of card
    limit: int = 25
    offset: int = 0

    @model_validator(mode="after")
    def validate_amounts(self):
        """Validate amount ranges to prevent overflow and nonsensical queries."""
        # Normalize empty strings to None (frontend sends "" for empty fields)
        if self.customer_id == "":
            self.customer_id = None
        if self.store_id == "":
            self.store_id = None
        if self.store_name == "":
            self.store_name = None
        if self.date_from == "":
            self.date_from = None
        if self.date_to == "":
            self.date_to = None
        if self.card_last4 == "":
            self.card_last4 = None

        # Prevent negative amounts
        if self.amount_min is not None and self.amount_min < 0:
            raise ValueError("amount_min must be non-negative")
        if self.amount_max is not None and self.amount_max < 0:
            raise ValueError("amount_max must be non-negative")

        # Prevent unreasonably large amounts (prevent BIGINT overflow)
        # PostgreSQL BIGINT max is ~92 quadrillion cents = ~$92 trillion
        # Set a reasonable upper limit of $10 million per receipt
        MAX_AMOUNT = 10_000_000  # $10 million
        if self.amount_min is not None and self.amount_min > MAX_AMOUNT:
            raise ValueError(f"amount_min must be <= ${MAX_AMOUNT:,.0f}")
        if self.amount_max is not None and self.amount_max > MAX_AMOUNT:
            raise ValueError(f"amount_max must be <= ${MAX_AMOUNT:,.0f}")

        # Ensure min <= max when both provided
        if (
            self.amount_min is not None
            and self.amount_max is not None
            and self.amount_min > self.amount_max
        ):
            raise ValueError("amount_min must be <= amount_max")

        # Validate limit range
        if self.limit < 1 or self.limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

        # Validate offset (prevent negative offset)
        if self.offset < 0:
            raise ValueError("offset must be non-negative")

        # Validate card_last4 format (must be exactly 4 digits)
        if self.card_last4 is not None:
            if not self.card_last4.isdigit() or len(self.card_last4) != 4:
                raise ValueError("card_last4 must be exactly 4 digits")

        return self


@router.post("/fuzzy")
async def fuzzy_search(
    req: FuzzySearchRequest,
    request: Request,
    user: dict = Depends(get_current_user),
    fields: str | None = None,
):
    """
    Multi-field fuzzy receipt search for CS reps.

    Strategy:
    - Customer ID: exact match
    - Store name: ILIKE match against store_name column in receipt_lookup
    - Amount: range match (dollars converted to cents, ±10% if single bound)
    - Date: range match with +1 day buffer on upper bound
    - Card last4: exact match on card_last4 in receipt_lookup

    All amount comparisons are against total_cents (BIGINT).

    Query parameters:
    - fields: Comma-separated field names to include in results (e.g., "transaction_id,total_cents,store_name")
              If not provided, returns all fields (default behavior)
    - limit: Max results to return (1-1000, default: 25)

    Payload optimization examples:
    - Full results: POST /search/fuzzy with JSON body  (~1-3KB per result)
    - ID + total + date only: POST /search/fuzzy?fields=transaction_id,total_cents,transaction_ts  (~150 bytes per result)

    Security: Requires at least ONE specific parameter when using date ranges
    to prevent enumeration attacks. Date range alone is too broad.

    Authorization: Data-level permissions (who can see which receipts) are enforced
    by Unity Catalog row filters and table grants, not by this app code.
    """
    # Identify specific search parameters (not just date range)
    specific_params = [
        req.customer_id,
        req.store_id,
        req.store_name,
        req.card_last4,
        req.amount_min is not None,
        req.amount_max is not None,
    ]

    # Count how many specific params are provided
    specific_param_count = sum(bool(p) for p in specific_params)
    has_date_range = bool(req.date_from or req.date_to)

    # Require at least one param
    if specific_param_count == 0 and not has_date_range:
        raise HTTPException(
            status_code=400,
            detail="At least one search parameter is required (customer_id, store_id, store_name, amount, date, or card_last4)"
        )

    # If only date range provided, require at least ONE other specific param
    # to prevent bulk enumeration (e.g., "all receipts from last year")
    if has_date_range and specific_param_count == 0:
        raise HTTPException(
            status_code=400,
            detail="Date range must be combined with at least one other specific parameter (customer_id, store_id, store_name, amount, or card_last4) to prevent broad searches"
        )

    conditions = []
    params = []

    # Customer ID (exact)
    if req.customer_id:
        conditions.append("rl.customer_id = %s")
        params.append(req.customer_id)

    # Store: exact ID match or fuzzy name match directly on receipt_lookup
    if req.store_id:
        conditions.append("rl.store_id = %s")
        params.append(req.store_id)
    elif req.store_name:
        conditions.append("rl.store_name ILIKE %s")
        params.append(f"%{req.store_name}%")

    # Date range (with +1 day buffer on upper bound for "last Tuesday" approximation)
    if req.date_from:
        conditions.append("rl.transaction_ts >= %s::timestamptz")
        params.append(req.date_from)
    if req.date_to:
        conditions.append("rl.transaction_ts <= %s::timestamptz + interval '1 day'")
        params.append(req.date_to)

    # Amount range — request is in dollars, column is in cents (BIGINT)
    # If only one bound given, use ±10% of that value with overflow protection
    if req.amount_min is not None and req.amount_max is not None:
        conditions.append("rl.total_cents BETWEEN %s AND %s")
        params.extend([int(req.amount_min * 100), int(req.amount_max * 100)])
    elif req.amount_min is not None:
        min_cents = int(req.amount_min * 100)
        # Calculate ±10% margin safely (use integer division to avoid float errors)
        margin = min_cents // 10  # 10% as integer division
        lower_bound = max(0, min_cents - margin)  # Prevent negative
        upper_bound = min_cents + margin
        conditions.append("rl.total_cents BETWEEN %s AND %s")
        params.extend([lower_bound, upper_bound])
    elif req.amount_max is not None:
        max_cents = int(req.amount_max * 100)
        # Calculate ±10% margin safely
        margin = max_cents // 10
        lower_bound = max(0, max_cents - margin)  # Prevent negative
        upper_bound = max_cents + margin
        conditions.append("rl.total_cents BETWEEN %s AND %s")
        params.extend([lower_bound, upper_bound])

    # Last 4 of card — exact match on card_last4 in receipt_lookup
    if req.card_last4:
        conditions.append("rl.card_last4 = %s")
        params.append(req.card_last4)

    # Build query
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    params.append(req.limit)
    params.append(req.offset)

    query = f"""
        SELECT rl.transaction_id, rl.store_id, rl.store_name, rl.customer_id,
               rl.customer_name, rl.transaction_ts, rl.total_cents, rl.tender_type,
               rl.card_last4, rl.item_count, rl.item_summary, rl.category_tags
        FROM receipt_lookup rl
        WHERE {where_clause}
        ORDER BY rl.transaction_ts DESC
        LIMIT %s OFFSET %s
    """

    # Use connection pool for better performance
    async with get_lakebase_connection(request) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(query, params)
            results = [dict(r) for r in await cur.fetchall()]

    # Apply field filtering to results if requested
    filtered_results = filter_fields(results, fields)

    return {
        "results": filtered_results,
        "count": len(filtered_results),
        "limit": req.limit,
        "offset": req.offset,
        "search_params": req.model_dump(exclude_none=True),
        "searched_by": user.get("preferred_username"),
        "note": "total_cents is in cents — divide by 100 for dollar display",
    }
