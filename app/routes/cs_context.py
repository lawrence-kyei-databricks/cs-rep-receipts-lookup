"""
CS Context routes — quick customer profile card for CS reps.
When a rep pulls up a customer, they see a snapshot:
"Frequent shopper, $200/week avg, shops mostly at Store 247, top: produce, dairy"
"""

from fastapi import APIRouter, Depends, Request
from middleware.auth import get_current_user
from ai.cs_context_agent import CSContextAgent

router = APIRouter()


@router.get("/{customer_id}")
async def get_customer_context(
    customer_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
    enable_ai_summary: bool = False,
):
    """
    Quick customer context card for CS reps.
    Pulls from pre-computed Gold tables (sub-10ms).

    Query param:
    - enable_ai_summary: If true, generates an AI briefing (~500ms extra).
      Disabled by default for speed.

    Returns:
    - Profile stats (lifetime spend, avg basket, visit frequency)
    - Top categories
    - Recent receipt summary
    - AI-generated context blurb (only if enable_ai_summary=true)
    """
    agent = CSContextAgent(
        lakebase_conninfo=request.app.state.lakebase_conninfo,
        enable_ai_summary=enable_ai_summary,
    )
    return await agent.get_context(customer_id)
