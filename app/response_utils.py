"""
Response utilities for field selection and payload optimization.

Provides field filtering to reduce response payload sizes for bandwidth-constrained
clients (mobile apps, slow networks) and improve parsing performance.
"""

from typing import Any, Optional


def filter_fields(data: Any, fields: Optional[str]) -> Any:
    """
    Filter response data to include only requested fields.

    Args:
        data: Response data (dict, list of dicts, or other)
        fields: Comma-separated field names (e.g., "transaction_id,total_cents,store_name")
                If None or empty, returns all fields (backward compatible)

    Returns:
        Filtered data with only requested fields

    Examples:
        >>> receipt = {"transaction_id": "T123", "total_cents": 4500, "store_name": "East Liberty"}
        >>> filter_fields(receipt, "transaction_id,total_cents")
        {"transaction_id": "T123", "total_cents": 4500}

        >>> receipts = [{"id": "T1", "total": 100}, {"id": "T2", "total": 200}]
        >>> filter_fields(receipts, "id")
        [{"id": "T1"}, {"id": "T2"}]
    """
    # No filtering if fields not specified (backward compatible)
    if not fields:
        return data

    # Parse field list
    field_set = {f.strip() for f in fields.split(",") if f.strip()}

    # No filtering if field list is empty after parsing
    if not field_set:
        return data

    # Apply filtering based on data type
    if isinstance(data, dict):
        return {k: v for k, v in data.items() if k in field_set}
    elif isinstance(data, list):
        return [
            {k: v for k, v in item.items() if k in field_set}
            if isinstance(item, dict) else item
            for item in data
        ]
    else:
        # Return as-is for non-dict/non-list types
        return data


def optimize_receipt_response(
    receipt: dict,
    fields: Optional[str] = None,
    include_line_items: bool = True
) -> dict:
    """
    Optimize receipt response by filtering fields and optionally excluding line items.

    This is a specialized version of filter_fields() for receipt responses that
    handles the nested line_items array specially.

    Args:
        receipt: Receipt dictionary from database
        fields: Comma-separated field names for top-level receipt fields
        include_line_items: If False, removes line_items array entirely (default: True)

    Returns:
        Optimized receipt dict

    Usage:
        # Get just transaction summary (no line items, no detailed fields)
        optimize_receipt_response(
            receipt,
            fields="transaction_id,total_cents,transaction_ts",
            include_line_items=False
        )
        # Result: {"transaction_id": "...", "total_cents": 4500, "transaction_ts": "..."}
        # (no line_items array, ~60-80% smaller payload)
    """
    # First, optionally remove line_items
    if not include_line_items and "line_items" in receipt:
        receipt = {k: v for k, v in receipt.items() if k != "line_items"}

    # Then apply field filtering if requested
    if fields:
        receipt = filter_fields(receipt, fields)

    return receipt
