"""
Giant Eagle POS Integration — Event Models

Pydantic models for the dual-write pipeline. Column names intentionally
match the Lakebase `receipt_transactions` schema and the Bronze Delta
`pos_raw_receipts` / `pos_raw_items` schemas from Phase 1.

All monetary values are in **cents** (integer) to avoid floating-point issues.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class POSLineItem(BaseModel):
    """A single line item on a receipt. Maps to bronze.pos_raw_items columns."""

    upc: Optional[str] = None
    sku: Optional[str] = None
    product_desc: str
    quantity: float = Field(default=1.0, ge=0)
    unit_price_cents: int = Field(ge=0, description="Unit price in cents")
    extended_cents: int = Field(ge=0, description="Line total in cents (qty × unit_price)")
    discount_cents: int = Field(default=0, ge=0)
    department_code: Optional[str] = None

    @model_validator(mode="after")
    def upc_or_sku_required(self) -> "POSLineItem":
        if not self.upc and not self.sku:
            raise ValueError("Each line item must have at least a upc or sku")
        return self

    def item_summary_fragment(self) -> str:
        """Short human-readable label for receipt summary field."""
        qty = f"{self.quantity:.0f}x " if self.quantity != 1.0 else ""
        return f"{qty}{self.product_desc}"


class POSReceiptEvent(BaseModel):
    """
    Complete POS receipt event from the point-of-sale system.

    This is the canonical input to DualWriteHandler. Column names match
    the Lakebase receipt_transactions table and the bronze Delta schemas
    (pos_raw_receipts + pos_raw_items).
    """

    # ── Identity ─────────────────────────────────────────────────────────────
    transaction_id: str = Field(
        description="Unique POS transaction ID — idempotency key across both paths"
    )
    store_id: str = Field(description="Store number, e.g. 'STORE-247'")
    store_name: str = Field(description="Human-readable store name, e.g. 'East Liberty'")

    # ── Terminal ─────────────────────────────────────────────────────────────
    pos_terminal_id: Optional[str] = Field(
        default=None, description="POS terminal/lane number"
    )
    cashier_id: Optional[str] = Field(default=None, description="Cashier employee ID")

    # ── Customer (loyalty) ───────────────────────────────────────────────────
    customer_id: Optional[str] = Field(
        default=None, description="Loyalty card customer ID — null for non-loyalty"
    )

    # ── Timing ───────────────────────────────────────────────────────────────
    transaction_ts: datetime = Field(description="POS transaction timestamp (UTC)")

    # ── Amounts (all in cents) ───────────────────────────────────────────────
    subtotal_cents: Optional[int] = Field(default=None, ge=0)
    tax_cents: Optional[int] = Field(default=None, ge=0)
    total_cents: int = Field(ge=0, description="Total amount in cents (required)")

    # ── Payment ──────────────────────────────────────────────────────────────
    tender_type: Optional[Literal["CREDIT", "DEBIT", "CASH", "EBT", "CHECK", "GIFT"]] = (
        Field(default=None, description="Payment tender type")
    )
    card_last4: Optional[str] = Field(
        default=None,
        min_length=4,
        max_length=4,
        pattern=r"^\d{4}$",
        description="Last 4 digits of card (digits only)",
    )

    # ── Line items ───────────────────────────────────────────────────────────
    items: list[POSLineItem] = Field(default_factory=list)

    @field_validator("card_last4", mode="before")
    @classmethod
    def strip_card_last4(cls, v: object) -> Optional[str]:
        """Accept '4532' or '****4532' — extract last 4 digits."""
        if v is None:
            return None
        s = str(v).strip()
        # Take last 4 digits if longer string passed
        digits = "".join(c for c in s if c.isdigit())
        return digits[-4:] if len(digits) >= 4 else None

    @model_validator(mode="after")
    def validate_total(self) -> "POSReceiptEvent":
        """Subtotal + tax should ≈ total when all three are provided."""
        if (
            self.subtotal_cents is not None
            and self.tax_cents is not None
            and abs((self.subtotal_cents + self.tax_cents) - self.total_cents) > 5
        ):
            raise ValueError(
                f"subtotal_cents ({self.subtotal_cents}) + tax_cents ({self.tax_cents}) "
                f"does not equal total_cents ({self.total_cents}) within ±5 cents"
            )
        return self

    # ── Derived helpers ───────────────────────────────────────────────────────

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def item_summary(self) -> str:
        """Top-3 item names joined — used for the quick-display column."""
        top = self.items[:3]
        summary = ", ".join(i.item_summary_fragment() for i in top)
        if len(self.items) > 3:
            summary += f" + {len(self.items) - 3} more"
        return summary

    def raw_items_json(self) -> list[dict]:
        """Serialize items as JSON-serializable dicts for the JSONB column."""
        return [item.model_dump() for item in self.items]


class DualWriteResult(BaseModel):
    """Result of a dual-write operation returned to the POS caller."""

    transaction_id: str
    lakebase: dict
    zerobus: dict
    overall_status: Literal["success", "partial", "failed"]

    @property
    def lakebase_ok(self) -> bool:
        return self.lakebase.get("status") == "success"

    @property
    def zerobus_ok(self) -> bool:
        return self.zerobus.get("status") == "success"
