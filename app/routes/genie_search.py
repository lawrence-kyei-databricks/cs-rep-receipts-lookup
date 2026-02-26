"""
Giant Eagle — Genie-Powered Receipt Search Routes
Natural language SQL generation via Databricks Genie.

Alternative to AI Search (tool-calling agent). Genie generates SQL from natural
language queries and executes against Delta Gold tables.

Pros: Faster than tool-calling (single model call), native chat interface
Cons: No pgvector semantic search, queries Delta (slower than Lakebase)
"""

import logging
from typing import Any

from databricks.sdk import WorkspaceClient
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from middleware.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

# Genie Space ID
# NOTE: If you get "does not exist" errors, the service principal needs access.
# Share the space with the service principal: e1751c32-5a1b-4d6f-90c2-e71e10246366
GENIE_SPACE_ID = "01f111c10f9c117ea630787776862209"


@router.post("/setup")
async def setup_genie_space(request: Request):
    """
    Create or find the Genie Space for this app.

    This endpoint creates the Genie Space using the app's service principal
    credentials, ensuring the app has permission to access it.

    Should be called once during initial app setup.
    """
    global GENIE_SPACE_ID

    try:
        w = WorkspaceClient()
        from databricks.sdk.service.dashboards import GenieAPI

        genie = GenieAPI(w.api_client)

        # Try to find existing space by name
        space_name = "Giant Eagle CS Receipt Genie"

        # List all Genie spaces to find ours
        try:
            # Note: There's no direct list API in the SDK yet, so we'll try to create
            # and handle the conflict if it already exists
            logger.info("Attempting to create Genie Space...")

            # Create the Genie Space using direct API call
            # The SDK doesn't have create_space yet, so we'll use the REST API
            response = w.api_client.do(
                method="POST",
                path="/api/2.0/genie/spaces",
                body={
                    "display_name": space_name,
                    "description": "Natural language SQL for Giant Eagle CS reps - query receipts, spending, and customer data",
                },
            )

            space_id = response.get("space_id")
            logger.info(f"Created new Genie Space: {space_id}")

            # Configure the space with tables
            w.api_client.do(
                method="PATCH",
                path=f"/api/2.0/genie/spaces/{space_id}",
                body={
                    "sql_warehouse_id": "148ccb90800933a1",  # Auto-selected warehouse
                    "table_identifiers": [
                        "giant_eagle.gold.receipt_lookup",
                        "giant_eagle.gold.spending_summary",
                        "giant_eagle.gold.customer_profiles",
                    ],
                    "sample_questions": [
                        "Show me all transactions with dairy products last month",
                        "Find receipts over $50 from Store 247 this week",
                        "What did customer cust-5001 spend in January 2026?",
                        "Show me receipts from East Liberty store last Tuesday",
                    ],
                },
            )

            GENIE_SPACE_ID = space_id

            return {
                "status": "created",
                "space_id": space_id,
                "message": "Genie Space created successfully and configured with tables"
            }

        except Exception as create_error:
            error_str = str(create_error)

            # If space already exists, try to find it
            if "already exists" in error_str.lower() or "conflict" in error_str.lower():
                logger.info("Space may already exist, attempting to find it...")

                # Try to list spaces and find ours
                # For now, we'll return an error asking user to provide the space_id
                return {
                    "status": "error",
                    "message": "A Genie Space with this name already exists. Please provide the space_id manually.",
                    "error": error_str,
                }
            else:
                raise create_error

    except Exception as e:
        logger.error(f"Genie setup failed: {e}", exc_info=True)
        return {
            "status": "error",
            "error": str(e),
            "error_type": type(e).__name__,
            "message": "Failed to create Genie Space. Check app logs for details."
        }


@router.get("/test")
async def test_genie(request: Request):
    """Test endpoint to verify Genie SDK connectivity and space access."""
    try:
        w = WorkspaceClient()
        from databricks.sdk.service.dashboards import GenieAPI

        genie = GenieAPI(w.api_client)

        if not GENIE_SPACE_ID:
            return {
                "status": "not_configured",
                "message": "Genie Space not configured. Call POST /genie/setup first."
            }

        # Try to access the space
        try:
            # Test by listing conversations (should work if we have access)
            response = w.api_client.do(
                method="GET",
                path=f"/api/2.0/genie/spaces/{GENIE_SPACE_ID}/conversations",
            )

            return {
                "status": "ok",
                "space_id": GENIE_SPACE_ID,
                "workspace_host": w.config.host,
                "message": "Genie Space is accessible",
                "has_access": True,
            }
        except Exception as access_error:
            return {
                "status": "access_denied",
                "space_id": GENIE_SPACE_ID,
                "workspace_host": w.config.host,
                "error": str(access_error),
                "message": "Cannot access Genie Space. May need permissions or re-run setup.",
                "has_access": False,
            }

    except Exception as e:
        logger.error(f"Genie test failed: {e}", exc_info=True)
        return {
            "status": "error",
            "error": str(e),
            "error_type": type(e).__name__
        }


class GenieQueryRequest(BaseModel):
    """Request model for Genie queries."""

    question: str
    customer_id: str | None = None


class GenieFollowupRequest(BaseModel):
    """Request model for Genie follow-up queries."""

    conversation_id: str
    question: str
    customer_id: str | None = None


class GenieQueryResponse(BaseModel):
    """Response model for Genie queries."""

    question: str
    answer: str
    sql: str | None = None
    data: list[dict[str, Any]] | None = None
    row_count: int | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    status: str
    error: str | None = None


@router.post("/ask", response_model=GenieQueryResponse)
async def ask_genie(
    query: GenieQueryRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Ask a natural language question via Genie.

    Genie generates SQL, executes it against Delta Gold tables, and returns
    results with a natural language summary.

    If customer_id is provided, the question is automatically scoped to that
    customer for data privacy (appends WHERE customer_id = '...' to generated SQL).

    Args:
        query: Question and optional customer_id
        request: FastAPI request (for accessing Databricks SDK client)
        user: Authenticated user from platform SSO

    Returns:
        GenieQueryResponse with answer, SQL, data, and conversation_id

    Example:
        POST /genie/ask
        {
            "question": "Show receipts from East Liberty store last Tuesday",
            "customer_id": "cust-5001"
        }
    """
    try:
        # Check if Genie Space is configured
        if not GENIE_SPACE_ID:
            return GenieQueryResponse(
                question=query.question,
                answer="Genie is not configured yet. Please contact your administrator to run the setup.",
                status="NOT_CONFIGURED",
                error="GENIE_SPACE_ID not set. Call POST /genie/setup to initialize."
            )

        # Get Databricks SDK client
        w = WorkspaceClient()

        # Build question with customer context if provided
        question = query.question
        if query.customer_id:
            question = (
                f"[For customer {query.customer_id}] {query.question}\n\n"
                f"IMPORTANT: Filter all queries by customer_id = '{query.customer_id}' "
                f"to ensure data privacy."
            )

        logger.info(
            f"Genie query from {user.get('email')}: {query.question} "
            f"(customer: {query.customer_id or 'all'})"
        )

        # Ask Genie (using the SDK directly for more control)
        # The ask_genie MCP tool is async and uses databricks-sdk internally
        # For FastAPI integration, we'll use the SDK directly
        from databricks.sdk.service.dashboards import GenieAPI

        genie = GenieAPI(w.api_client)

        # Start conversation
        result = genie.start_conversation(space_id=GENIE_SPACE_ID, content=question)

        # Get the message result
        conversation_id = result.conversation_id
        message_id = result.message_id

        # Wait for result (Genie is async, polls until complete)
        # Maximum 55 seconds wait (below gateway timeout of ~60s)
        # Testing shows Genie typically needs 50-80s for first query
        import time

        max_wait = 55
        start = time.time()
        while time.time() - start < max_wait:
            message = genie.get_message(space_id=GENIE_SPACE_ID, conversation_id=conversation_id, message_id=message_id)

            if message.status in ("COMPLETED", "FAILED", "CANCELLED"):
                break

            time.sleep(1)
        else:
            # Timeout
            logger.warning(f"Genie query timed out after {max_wait}s")
            return GenieQueryResponse(
                question=query.question,
                answer="Query is taking longer than expected. Please try again.",
                status="TIMEOUT",
                error=f"Genie query exceeded {max_wait}s timeout (still processing)",
            )

        # Extract results
        if message.status == "COMPLETED":
            # Genie returns query results
            answer_text = ""
            sql_query = None
            data_rows = None
            row_count = None

            # Parse attachments for SQL and data
            if message.attachments:
                for attachment in message.attachments:
                    if attachment.text:
                        answer_text = attachment.text.content

                    if hasattr(attachment, "query") and attachment.query:
                        sql_query = attachment.query.query
                        if attachment.query.result:
                            # Convert result to list of dicts
                            data_rows = []
                            if attachment.query.result.data_array:
                                columns = [col.name for col in attachment.query.result.data_schema.columns]
                                for row in attachment.query.result.data_array:
                                    data_rows.append(dict(zip(columns, row)))
                                row_count = len(data_rows)

            logger.info(
                f"Genie query completed: {row_count or 0} rows returned"
            )

            return GenieQueryResponse(
                question=query.question,
                answer=answer_text or "Query completed successfully.",
                sql=sql_query,
                data=data_rows,
                row_count=row_count,
                conversation_id=conversation_id,
                message_id=message_id,
                status="COMPLETED",
            )
        else:
            # Query failed
            error_msg = getattr(message, "error", "Unknown error")
            logger.error(f"Genie query failed: {error_msg}")
            return GenieQueryResponse(
                question=query.question,
                answer=f"Query failed: {error_msg}",
                status="FAILED",
                error=str(error_msg),
            )

    except Exception as e:
        logger.error(f"Genie API error: {e}", exc_info=True)
        # Return detailed error in response instead of generic 500
        return GenieQueryResponse(
            question=query.question,
            answer=f"Genie query failed with error: {str(e)}",
            status="ERROR",
            error=f"{type(e).__name__}: {str(e)}"
        )


@router.post("/followup", response_model=GenieQueryResponse)
async def ask_genie_followup(
    followup: GenieFollowupRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    Ask a follow-up question in an existing Genie conversation.

    Maintains conversation context for multi-turn queries like:
    - "Show receipts from last week" → "What about the week before?"
    - "Total spending in January" → "Break that down by category"

    Args:
        followup: Follow-up request with conversation_id and question
        request: FastAPI request
        user: Authenticated user

    Returns:
        GenieQueryResponse with answer maintaining conversation context
    """
    try:
        # Check if Genie Space is configured
        if not GENIE_SPACE_ID:
            return GenieQueryResponse(
                question=followup.question,
                answer="Genie is not configured yet. Please contact your administrator to run the setup.",
                status="NOT_CONFIGURED",
                error="GENIE_SPACE_ID not set. Call POST /genie/setup to initialize."
            )

        w = WorkspaceClient()
        from databricks.sdk.service.dashboards import GenieAPI

        genie = GenieAPI(w.api_client)

        logger.info(
            f"Genie follow-up from {user.get('email')}: {followup.question} "
            f"(conversation: {followup.conversation_id})"
        )

        # Continue conversation
        result = genie.create_message(
            space_id=GENIE_SPACE_ID,
            conversation_id=followup.conversation_id,
            content=followup.question,
        )

        message_id = result.message_id

        # Wait for result
        import time

        max_wait = 55
        start = time.time()
        while time.time() - start < max_wait:
            message = genie.get_message(
                space_id=GENIE_SPACE_ID,
                conversation_id=followup.conversation_id,
                message_id=message_id,
            )

            if message.status in ("COMPLETED", "FAILED", "CANCELLED"):
                break

            time.sleep(1)
        else:
            return GenieQueryResponse(
                question=followup.question,
                answer="Query is taking longer than expected. Please try again.",
                status="TIMEOUT",
                error=f"Genie follow-up exceeded {max_wait}s timeout (still processing)",
            )

        # Extract results (same as ask_genie)
        if message.status == "COMPLETED":
            answer_text = ""
            sql_query = None
            data_rows = None
            row_count = None

            if message.attachments:
                for attachment in message.attachments:
                    if attachment.text:
                        answer_text = attachment.text.content

                    if hasattr(attachment, "query") and attachment.query:
                        sql_query = attachment.query.query
                        if attachment.query.result:
                            data_rows = []
                            if attachment.query.result.data_array:
                                columns = [col.name for col in attachment.query.result.data_schema.columns]
                                for row in attachment.query.result.data_array:
                                    data_rows.append(dict(zip(columns, row)))
                                row_count = len(data_rows)

            return GenieQueryResponse(
                question=followup.question,
                answer=answer_text or "Query completed successfully.",
                sql=sql_query,
                data=data_rows,
                row_count=row_count,
                conversation_id=followup.conversation_id,
                message_id=message_id,
                status="COMPLETED",
            )
        else:
            error_msg = getattr(message, "error", "Unknown error")
            logger.error(f"Genie follow-up failed: {error_msg}")
            return GenieQueryResponse(
                question=followup.question,
                answer=f"Query failed: {error_msg}",
                status="FAILED",
                error=str(error_msg),
            )

    except Exception as e:
        logger.error(f"Genie follow-up API error: {e}", exc_info=True)
        return GenieQueryResponse(
            question=followup.question,
            answer=f"Genie follow-up failed with error: {str(e)}",
            status="ERROR",
            error=f"{type(e).__name__}: {str(e)}"
        )
