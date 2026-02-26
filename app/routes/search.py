"""Search routes — semantic search (pgvector) and natural language queries."""

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from nl_search_agent import NLSearchAgent
from middleware.auth import get_current_user

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    customer_id: str | None = None
    conversation_history: list[dict] | None = None
    limit: int = 25
    offset: int = 0


async def _search_receipts_impl(req: SearchRequest, request: Request):
    """
    AI-powered receipt search implementation.
    Handles both semantic ("that blue cheese") and structured
    ("how much chicken last month") queries via the NL Search Agent.

    Latency: ~200-400ms for semantic, ~1-3s for NL→SQL.
    """
    try:
        agent = NLSearchAgent(
            lakebase_conninfo=request.app.state.lakebase_conninfo,
            lakebase_pool=request.app.state.lakebase_pool
        )
        result = await agent.search(
            query=req.query,
            customer_id=req.customer_id,
            conversation_history=req.conversation_history,
        )
        return result
    except Exception as e:
        import logging
        logging.error(f"AI search failed: {e}")
        # Return a helpful error message instead of 500
        return {
            "answer": f"AI search is currently unavailable. Error: {str(e)[:200]}. Please use the Fuzzy Search or direct receipt lookup instead.",
            "customer_id": req.customer_id,
            "query": req.query,
            "error": str(e)[:500]
        }


@router.post("/")
async def search_receipts_with_slash(
    req: SearchRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Route handler for /search/ (with trailing slash)."""
    return await _search_receipts_impl(req, request)


@router.post("")
async def search_receipts(
    req: SearchRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """Route handler for /search (without trailing slash)."""
    return await _search_receipts_impl(req, request)
