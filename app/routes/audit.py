"""
Audit routes — query the audit trail.

Authorization is handled by Unity Catalog row filters:
  - Supervisors/fraud_team: see all audit logs
  - CS reps: see only their own audit logs

No role checks in code — UC enforces at query time.
"""

from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Depends, Request

from middleware.auth import get_current_user

router = APIRouter()


@router.get("/log")
async def query_audit_log(
    request: Request,
    user: dict = Depends(get_current_user),
    rep_id: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 100,
):
    """
    Query audit trail.

    Row filter automatically applies:
      - Supervisors/fraud_team see all logs
      - CS reps see only their own logs (WHERE rep_email = current_user())

    UC enforces permissions — no role check needed.
    """
    conninfo = request.app.state.lakebase_conninfo
    conditions = []
    params = []

    if rep_id:
        conditions.append("rep_email = %s")
        params.append(rep_id)
    if action:
        conditions.append("action = %s")
        params.append(action)
    if resource_type:
        conditions.append("resource_type = %s")
        params.append(resource_type)
    if date_from:
        conditions.append("timestamp >= %s::timestamptz")
        params.append(date_from)
    if date_to:
        conditions.append("timestamp <= %s::timestamptz")
        params.append(date_to)

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)

    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"""
                SELECT audit_id, rep_email, action,
                       resource_type, resource_id, query_params,
                       result_count, timestamp as created_at
                FROM audit_log
                WHERE {where}
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                params,
            )
            return await cur.fetchall()


@router.get("/log/rep/{rep_email}")
async def get_rep_audit_trail(
    rep_email: str,
    request: Request,
    user: dict = Depends(get_current_user),
    limit: int = 50,
):
    """
    Get audit trail for a specific CS rep.

    Row filter automatically applies:
      - Supervisors/fraud_team can query any rep's logs
      - CS reps can only query their own logs (UC blocks others)

    UC enforces permissions — no role check needed.
    """
    conninfo = request.app.state.lakebase_conninfo

    async with await psycopg.AsyncConnection.connect(conninfo) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT audit_id, action, resource_type, resource_id,
                       query_params, result_count, timestamp as created_at
                FROM audit_log
                WHERE rep_email = %s
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                (rep_email, limit),
            )
            return await cur.fetchall()
