"""
Receipt delivery routes — email or print receipts to customers.
CS rep finds the receipt, clicks "send to customer."
"""

import os
import logging
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from middleware.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


class DeliverRequest(BaseModel):
    transaction_id: str
    method: str             # "email" | "print"
    target: str             # email address or printer ID
    customer_id: Optional[str] = None
    note: Optional[str] = None  # optional note from CS rep


@router.post("/deliver")
async def deliver_receipt(
    req: DeliverRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Send a receipt to the customer via email or print.
    Logs the delivery for audit trail.
    """
    conninfo = request.app.state.lakebase_conninfo

    # 1. Fetch the receipt
    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT transaction_id, store_id, store_name, customer_id,
                       transaction_ts, total_cents, tender_type,
                       item_count, item_summary, items_detail
                FROM receipt_lookup
                WHERE transaction_id = %s
                """,
                (req.transaction_id,),
            )
            receipt = await cur.fetchone()

    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")

    # 2. Deliver based on method
    if req.method == "email":
        delivery_status = await _send_email(receipt, req.target, req.note)
    elif req.method == "print":
        delivery_status = await _send_to_printer(receipt, req.target)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown delivery method: {req.method}")

    # 3. Log the delivery
    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO receipt_delivery_log (
                    transaction_id, customer_id, delivery_method,
                    delivery_target, delivered_by_rep, status
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    req.transaction_id,
                    req.customer_id or receipt.get("customer_id"),
                    req.method,
                    req.target,
                    user.get("oid", "unknown"),
                    delivery_status,
                ),
            )
        await conn.commit()

    return {
        "status": delivery_status,
        "transaction_id": req.transaction_id,
        "method": req.method,
        "target": req.target,
    }


@router.get("/deliver/log/{customer_id}")
async def get_delivery_log(
    customer_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Get delivery history for a customer."""
    conninfo = request.app.state.lakebase_conninfo

    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT delivery_id, transaction_id, delivery_method,
                       delivery_target, status, created_at
                FROM receipt_delivery_log
                WHERE customer_id = %s
                ORDER BY created_at DESC
                LIMIT 50
                """,
                (customer_id,),
            )
            return await cur.fetchall()


async def _send_email(receipt: dict, email: str, note: str | None) -> str:
    """Send receipt via email. Returns delivery status."""
    # TODO: Implement with SMTP or SendGrid/Mailgun
    # For now, stub that returns 'sent'
    logger.info(f"Sending receipt {receipt['transaction_id']} to {email}")

    # In production:
    # 1. Render receipt as HTML template
    # 2. Send via SMTP_HOST configured in env
    # 3. Return actual delivery status

    return "sent"


async def _send_to_printer(receipt: dict, printer_id: str) -> str:
    """Send receipt to a store printer. Returns delivery status."""
    # TODO: Implement with store print service API
    logger.info(f"Printing receipt {receipt['transaction_id']} on {printer_id}")
    return "sent"
