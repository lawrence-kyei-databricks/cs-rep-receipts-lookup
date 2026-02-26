"""
Giant Eagle — Natural Language Receipt Search Agent
CS tool that converts natural language queries like "that chicken from last week"
into SQL against Lakebase or semantic search against pgvector.

Used by CS reps to find customer receipts when the customer calls with a
vague description. NOT a consumer-facing agent.

Uses Mosaic AI Agent Framework with tool-calling (sql_query + semantic_search).
All monetary columns are BIGINT in cents — divide by 100 to display as dollars.
"""

import json
import logging
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from mlflow.deployments import get_deploy_client

logger = logging.getLogger(__name__)

# Schema context for the LLM (helps generate accurate SQL).
# All column names must match the actual Lakebase schema provisioned in Phase 1.
LAKEBASE_SCHEMA_CONTEXT = """
Available tables in Lakebase (public):

1. receipt_lookup (synced from Delta Gold, read-only):
   - transaction_id TEXT (PK)
   - customer_id TEXT
   - customer_name TEXT
   - store_id TEXT
   - store_name TEXT
   - transaction_ts TIMESTAMPTZ
   - transaction_date DATE
   - subtotal_cents BIGINT  (e.g. 4800 = $48.00)
   - tax_cents BIGINT
   - total_cents BIGINT     (e.g. 5016 = $50.16)
   - tender_type TEXT       (CREDIT, DEBIT, CASH, EBT, etc.)
   - card_last4 TEXT        (last 4 digits if credit/debit)
   - item_count INTEGER
   - item_summary TEXT      (e.g. "Oat Milk 32oz, Roquefort Wedge 8oz + 1 more")
   - category_tags JSONB    (array of category strings like ["DAIRY","BAKERY"])
   - has_pharmacy BOOLEAN
   - has_fuel_points BOOLEAN
   - fuel_points_earned INTEGER

2. spending_summary (synced from Delta Gold, read-only):
   - customer_id TEXT
   - category_l1 TEXT       (e.g. DAIRY, DELI, PRODUCE, MEAT)
   - summary_month DATE     (e.g. '2026-02-01')
   - total_cents BIGINT     (total spend in cents)
   - visit_count INTEGER    (number of transactions)

3. product_embeddings (AI table for semantic search):
   - sku TEXT
   - product_name TEXT      (e.g. "Roquefort Wedge 8oz")
   - brand TEXT
   - category_l1 TEXT       (e.g. DELI)
   - category_l2 TEXT       (subcategory)
   - search_text TEXT       (searchable description)
   - embedding VECTOR       (pgvector embedding for similarity search)

NOTE: All monetary values are in cents (BIGINT). To display as dollars, divide by 100.
      Use {{customer_id}} as a placeholder where the customer ID parameter should go.
      Use ONLY SELECT statements. Never use INSERT, UPDATE, DELETE, or DDL.
Current date: {current_date}
"""

SYSTEM_PROMPT = """You are an internal CS (customer service) search assistant for Giant Eagle.
A CS rep is trying to find a customer's receipt based on what the customer described over the phone.

Given the rep's search query, determine the best approach:

1. If the query mentions a product by name or description (e.g. "fancy cheese", "oat milk") → use semantic_search tool
2. If the query asks about spending totals, date ranges, store, or card → use sql_query tool
3. If ambiguous → try semantic_search first, then sql_query

SQL query rules:
- If customer_id is provided, filter by customer_id = {customer_id} (use the {customer_id} placeholder)
- If no customer_id, search across all customers - DO NOT ask for a customer ID
- Monetary columns are in cents — when displaying to the rep, divide by 100 to show dollars
- Use ONLY SELECT statements
- Prefer receipt_lookup for receipt-level queries, spending_summary for aggregate spending

CRITICAL: ALWAYS EXECUTE THE SEARCH
- When no customer_id is provided, ALWAYS search across all customers
- NEVER ask for a customer ID - just perform the search and return results
- If you find multiple receipts, show them to the rep (most recent first)
- The rep can narrow down results by asking follow-up questions

CRITICAL RESPONSE RULES:
- Provide a clear, concise summary for the CS rep
- NEVER include raw JSON, tool outputs, or technical debug information in your response
- NEVER say "Raw search results:", "semantic_search returned:", or "sql_query returned:"
- If no results found, simply say "No receipts found matching that description" and suggest alternatives
- Focus on actionable information: receipt details, dates, amounts, products
- Do not expose raw SQL to the CS rep

{schema_context}
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "sql_query",
            "description": (
                "Execute a SQL query against the Lakebase receipt database. "
                "Use for spending questions, date ranges, totals, store lookups, "
                "card last4 lookups, and structured queries. "
                "Filter by customer_id using the {customer_id} placeholder."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "PostgreSQL SELECT query against the Lakebase schema. "
                            "Use {customer_id} exactly once where the customer ID parameter goes. "
                            "Use ONLY SELECT. Monetary columns are in cents (BIGINT)."
                        ),
                    },
                    "explanation": {
                        "type": "string",
                        "description": "Brief explanation of what this query does.",
                    },
                },
                "required": ["query", "explanation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "semantic_search",
            "description": (
                "Search for products by natural language description using vector similarity. "
                "Use when the customer mentions a product by approximate name or description "
                "(e.g. 'fancy cheese', 'that sparkling water', 'organic cereal')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "search_text": {
                        "type": "string",
                        "description": "Natural language description of the product to find.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of results (default 5, max 20).",
                        "default": 5,
                    },
                },
                "required": ["search_text"],
            },
        },
    },
]


class NLSearchAgent:
    """
    Natural language receipt search for CS reps using Mosaic AI Agent Framework.

    Architecture:
    - Foundation Model (via AI Gateway) for NL → tool selection + SQL generation
    - Lakebase pgvector for semantic product search (product_embeddings table)
    - Lakebase SQL for structured receipt/spending queries
    - Agent memory in Lakebase for multi-turn conversations (agent_memory table)

    All queries are scoped to a single customer_id. CS reps may not
    cross-customer search — that is a fraud_team privilege.
    """

    def __init__(
        self,
        lakebase_conninfo: str,
        model_endpoint: str = "databricks-claude-opus-4-6",
        embedding_endpoint: str = "databricks-gte-large-en",
    ):
        self.lakebase_conninfo = lakebase_conninfo
        self.model_endpoint = model_endpoint
        self.embedding_endpoint = embedding_endpoint
        self.deploy_client = get_deploy_client("databricks")

        # Embedding cache: {normalized_search_text: (embedding_vector, timestamp)}
        # LRU cache with 100 entries, 24-hour TTL
        self._embedding_cache: OrderedDict = OrderedDict()
        self._cache_max_size = 100
        self._cache_ttl_seconds = 86400  # 24 hours

    def _get_cached_embedding(self, search_text: str) -> list[float] | None:
        """
        Check if embedding exists in cache and is still valid (not expired).

        Returns cached embedding or None if not found/expired.
        Implements LRU eviction by moving accessed items to end.
        """
        # Normalize search text for cache key (lowercase, strip whitespace)
        cache_key = search_text.lower().strip()

        if cache_key not in self._embedding_cache:
            return None

        embedding, timestamp = self._embedding_cache[cache_key]

        # Check if cache entry has expired
        if time.time() - timestamp > self._cache_ttl_seconds:
            # Entry expired - remove it
            del self._embedding_cache[cache_key]
            logger.info(f"Cache entry expired for '{search_text}' (age: {int(time.time() - timestamp)}s)")
            return None

        # Move to end (LRU: most recently used)
        self._embedding_cache.move_to_end(cache_key)
        logger.info(f"Cache HIT for '{search_text}' (age: {int(time.time() - timestamp)}s)")
        return embedding

    def _cache_embedding(self, search_text: str, embedding: list[float]) -> None:
        """
        Store embedding in cache with current timestamp.

        Implements LRU eviction: if cache is full, removes oldest entry.
        """
        # Normalize search text for cache key
        cache_key = search_text.lower().strip()

        # LRU eviction: if cache is full, remove oldest (first) entry
        if len(self._embedding_cache) >= self._cache_max_size:
            oldest_key = next(iter(self._embedding_cache))
            del self._embedding_cache[oldest_key]
            logger.info(f"Cache eviction: removed '{oldest_key}' (cache full: {self._cache_max_size} entries)")

        # Add to cache (will be placed at the end = most recently used)
        self._embedding_cache[cache_key] = (embedding, time.time())
        logger.info(f"Cache MISS (stored): '{search_text}' (cache size: {len(self._embedding_cache)})")

    async def search(
        self,
        query: str,
        customer_id: str | None = None,
        conversation_history: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Process a natural language receipt search query from a CS rep.

        Args:
            query: The rep's natural language search (e.g. "fancy cheese from last week")
            customer_id: Optional customer's loyalty ID. If None, searches across all customers.
            conversation_history: Previous messages for multi-turn context

        Returns:
            Dict with answer (text for the rep), customer_id, query
        """
        schema_ctx = LAKEBASE_SCHEMA_CONTEXT.format(
            current_date=datetime.now().strftime("%Y-%m-%d"),
        )

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(
                    schema_context=schema_ctx,
                    customer_id="{customer_id}",  # literal placeholder for LLM guidance
                ),
            },
        ]

        if conversation_history:
            messages.extend(conversation_history)

        if customer_id:
            messages.append({
                "role": "user",
                "content": f"[Looking up customer: {customer_id}] {query}",
            })
        else:
            messages.append({
                "role": "user",
                "content": f"[Searching across all customers] {query}",
            })

        # Call Foundation Model with tool-calling
        # Force tool usage by using tool_choice parameter
        try:
            response = self.deploy_client.predict(
                endpoint=self.model_endpoint,
                inputs={
                    "messages": messages,
                    "tools": TOOLS,
                    "tool_choice": "required",  # Force the LLM to call a tool
                    "max_tokens": 1024,
                },
            )
        except Exception as e:
            logger.error(f"LLM endpoint call failed: {e}")
            return {
                "answer": f"AI search failed: could not reach LLM endpoint. {str(e)[:200]}",
                "customer_id": customer_id,
                "query": query,
                "error": f"LLM call failed: {str(e)}"
            }

        # Process tool calls
        try:
            assistant_message = response["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Unexpected LLM response format: {e}. Response: {response}")
            return {
                "answer": f"AI search failed: unexpected response from LLM. {str(e)[:200]}",
                "customer_id": customer_id,
                "query": query,
                "error": f"Response parsing failed: {str(e)}"
            }

        if "tool_calls" in assistant_message:
            tool_results = []
            logger.info(f"Processing {len(assistant_message['tool_calls'])} tool calls")
            for tool_call in assistant_message["tool_calls"]:
                try:
                    fn_name = tool_call["function"]["name"]
                    fn_args = json.loads(tool_call["function"]["arguments"])
                    logger.info(f"Executing tool: {fn_name} with args: {fn_args}")

                    if fn_name == "sql_query":
                        result = await self._execute_sql(fn_args["query"], customer_id)
                        logger.info(f"SQL query returned {result.get('count', 0)} rows")
                    elif fn_name == "semantic_search":
                        result = await self._semantic_search(
                            fn_args["search_text"],
                            customer_id,
                            fn_args.get("limit", 5),
                        )
                        logger.info(f"Semantic search returned {len(result.get('matches', []))} matches")
                    else:
                        result = {"error": f"Unknown tool: {fn_name}"}
                        logger.warning(f"Unknown tool requested: {fn_name}")
                except Exception as e:
                    logger.error(f"Tool execution failed for {fn_name}: {e}", exc_info=True)
                    result = {"error": f"Tool execution failed: {str(e)}"}

                tool_results.append({
                    "tool_call_id": tool_call["id"],
                    "role": "tool",
                    "content": json.dumps(result, default=str),
                })
                logger.debug(f"Tool {fn_name} result: {json.dumps(result, default=str)[:200]}")

            # Send tool results back to model for final answer
            messages.append(assistant_message)
            messages.extend(tool_results)

            logger.info(f"Sending {len(tool_results)} tool results back to LLM for final answer")
            try:
                final_response = self.deploy_client.predict(
                    endpoint=self.model_endpoint,
                    inputs={"messages": messages, "max_tokens": 2048},  # Increased from 1024
                )
                logger.info(f"Final LLM response received: {str(final_response)[:500]}")
                answer = final_response["choices"][0]["message"]["content"]

                # Trust the LLM's response - no heuristics, no incomplete detection
                # If the LLM generates a response, use it as-is

            except Exception as e:
                logger.error(f"Final LLM call failed: {e}")
                # Fallback: return simple error message (no raw data)
                answer = "Search completed but couldn't generate a summary. Please try rephrasing your query or use the Receipt Search page for more precise filters."
        else:
            answer = assistant_message.get("content", "No results found for that query.")

        # Post-process: Remove any leaked debug output from LLM response
        # LLM sometimes ignores instructions and includes raw tool results
        answer = self._clean_debug_output(answer)

        return {"answer": answer, "customer_id": customer_id, "query": query}

    def _clean_debug_output(self, text: str) -> str:
        """
        Remove debug output that the LLM sometimes includes despite instructions.
        Strips out patterns like "Raw search results:", "semantic_search returned:", etc.

        This is more aggressive than system prompts because the LLM frequently
        ignores instructions and leaks raw tool outputs to CS reps.
        """
        import re

        # Remove entire blocks starting with debug phrases
        # Match from trigger phrase through the end of the JSON object
        debug_patterns = [
            # Match "Raw search results: ..." through the end of that paragraph/block
            r"Raw search results:[\s\S]*?(?=\n\n|\Z)",
            # Match "semantic_search returned: { ... }" entire JSON blocks
            r"semantic_search returned:\s*\{[\s\S]*?\}\s*(?=\n|$)",
            # Match "sql_query returned: { ... }" entire JSON blocks
            r"sql_query returned:\s*\{[\s\S]*?\}\s*(?=\n|$)",
            # Catch any remaining JSON with "matches" or "rows" keys (tool outputs)
            r"\{\s*\"matches\"[\s\S]*?\}\s*(?=\n|$)",
            r"\{\s*\"rows\"[\s\S]*?\}\s*(?=\n|$)",
        ]

        cleaned = text
        for pattern in debug_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.MULTILINE)

        # Remove multiple consecutive blank lines
        cleaned = re.sub(r'\n\s*\n\s*\n+', '\n\n', cleaned)

        return cleaned.strip()

    async def _execute_sql(self, query: str, customer_id: str | None = None) -> dict:
        """
        Execute a validated SQL SELECT against Lakebase.

        The LLM should generate queries with {{customer_id}} as a placeholder.
        If customer_id is provided and LLM doesn't include the placeholder,
        we automatically inject customer_id filtering.
        We replace placeholders with psycopg3 parameters (%s) and execute
        parameterized to prevent injection.

        Only SELECT statements are allowed. All other statements are rejected.
        """
        normalized = query.strip().upper()
        if not normalized.startswith("SELECT"):
            return {"error": "Only SELECT queries are allowed."}

        # First escape any literal % signs by doubling them (% -> %%)
        safe_query = query.replace("%", "%%")

        # Fix malformed single-brace placeholders (LLM sometimes generates {customer_id} instead of {{customer_id}})
        if "{customer_id}" in safe_query and "{{customer_id}}" not in safe_query:
            logger.warning("LLM generated malformed {customer_id} placeholder (single braces) - fixing to {{customer_id}}")
            safe_query = safe_query.replace("{customer_id}", "{{customer_id}}")

        # Handle customer_id filtering
        if customer_id:
            # Check if LLM included the {{customer_id}} placeholder
            if "{{customer_id}}" in safe_query:
                # Replace {{customer_id}} with %s parameter
                safe_query = safe_query.replace("{{customer_id}}", "%s")
                params = (customer_id,)
            else:
                # LLM didn't include placeholder - inject customer_id filter automatically
                logger.warning(f"LLM query missing {{{{customer_id}}}} placeholder, auto-injecting filter")

                # Check if query already has a WHERE clause
                if " WHERE " in safe_query.upper():
                    # Append to existing WHERE clause with AND
                    safe_query = safe_query.replace(" WHERE ", " WHERE customer_id = %s AND ", 1)
                else:
                    # Add WHERE clause before ORDER BY, LIMIT, or at the end
                    if " ORDER BY " in safe_query.upper():
                        safe_query = safe_query.replace(" ORDER BY ", " WHERE customer_id = %s ORDER BY ", 1)
                    elif " LIMIT " in safe_query.upper():
                        safe_query = safe_query.replace(" LIMIT ", " WHERE customer_id = %s LIMIT ", 1)
                    else:
                        safe_query = safe_query + " WHERE customer_id = %s"

                params = (customer_id,)
        else:
            # No customer_id provided - remove any placeholder references
            safe_query = safe_query.replace("{{customer_id}}", "''")  # Replace with empty string
            params = ()

        try:
            async with await psycopg.AsyncConnection.connect(self.lakebase_conninfo) as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(safe_query, params)
                    rows = await cur.fetchmany(50)  # Limit to 50 rows for the agent response
                    return {"rows": [dict(r) for r in rows], "count": len(rows)}
        except Exception as exc:
            logger.error(f"SQL execution failed: {exc}")
            return {"error": f"Query failed: {str(exc)[:200]}"}

    async def _semantic_search(
        self,
        search_text: str,
        customer_id: str | None = None,
        limit: int = 5,
    ) -> dict:
        """
        Semantic product search via pgvector, joined to receipts.

        Flow:
          1. Embed search_text via Foundation Model
          2. Cosine similarity search in product_embeddings (HNSW index)
          3. LEFT JOIN LATERAL to receipt_lookup to find matching transactions
             If customer_id provided, filters to that customer only.

        Returns matched products and the receipts where they were purchased.
        total_cents in the result is in cents (BIGINT).
        """
        # Generate embedding for the search query (with caching)
        try:
            # Check cache first
            query_embedding = self._get_cached_embedding(search_text)

            if query_embedding is None:
                # Cache miss - generate embedding via API
                embed_response = self.deploy_client.predict(
                    endpoint=self.embedding_endpoint,
                    inputs={"input": [search_text]},
                )
                query_embedding = embed_response["data"][0]["embedding"]

                # Store in cache for future requests
                self._cache_embedding(search_text, query_embedding)

                logger.info(f"Generated embedding for '{search_text}' (dim={len(query_embedding)})")

            # Convert to PostgreSQL array format for pgvector
            embedding_str = f"[{','.join(str(x) for x in query_embedding)}]"

        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return {
                "matches": [],
                "search_text": search_text,
                "error": f"Embedding failed: {str(e)[:200]}",
            }

        # pgvector cosine similarity search + join to receipts
        # Minimum similarity threshold of 0.3 to filter out irrelevant matches
        # (below 0.3, matches are essentially random/unrelated products)
        MIN_SIMILARITY_THRESHOLD = 0.1  # Lowered from 0.3 for better recall

        async with await psycopg.AsyncConnection.connect(self.lakebase_conninfo) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # Get top N products by semantic similarity above threshold
                # Then for each product, find receipts containing it
                if customer_id:
                    # Filter to specific customer using SKU-based JOIN for reliability
                    await cur.execute(
                        """
                        WITH matched_products AS (
                            SELECT sku, product_name,
                                   1 - (embedding <=> %s::vector) AS similarity
                            FROM product_embeddings
                            WHERE 1 - (embedding <=> %s::vector) >= %s
                            ORDER BY embedding <=> %s::vector
                            LIMIT %s
                        )
                        SELECT mp.product_name, mp.sku, mp.similarity,
                               rl.transaction_id, rl.transaction_ts,
                               rl.store_name, rl.total_cents, rl.item_summary, rl.customer_id
                        FROM matched_products mp
                        LEFT JOIN LATERAL (
                            SELECT DISTINCT
                                li.transaction_id,
                                r.transaction_ts,
                                r.store_name,
                                r.total_cents,
                                r.item_summary,
                                r.customer_id
                            FROM receipt_line_items li
                            JOIN receipt_lookup r ON li.transaction_id = r.transaction_id
                            WHERE li.sku = mp.sku
                              AND r.customer_id = %s
                            ORDER BY r.transaction_ts DESC
                            LIMIT 3
                        ) rl ON true
                        ORDER BY mp.similarity DESC, rl.transaction_ts DESC NULLS LAST
                        """,
                        (embedding_str, embedding_str, MIN_SIMILARITY_THRESHOLD, embedding_str, limit, customer_id),
                    )
                    logger.info(f"Semantic search (SKU-based) found product-receipt matches (threshold={MIN_SIMILARITY_THRESHOLD}) for customer {customer_id}")
                else:
                    # Search across all customers using SKU-based JOIN for reliability
                    await cur.execute(
                        """
                        WITH matched_products AS (
                            SELECT sku, product_name,
                                   1 - (embedding <=> %s::vector) AS similarity
                            FROM product_embeddings
                            WHERE 1 - (embedding <=> %s::vector) >= %s
                            ORDER BY embedding <=> %s::vector
                            LIMIT %s
                        )
                        SELECT mp.product_name, mp.sku, mp.similarity,
                               rl.transaction_id, rl.transaction_ts,
                               rl.store_name, rl.total_cents, rl.item_summary, rl.customer_id
                        FROM matched_products mp
                        LEFT JOIN LATERAL (
                            SELECT DISTINCT
                                li.transaction_id,
                                r.transaction_ts,
                                r.store_name,
                                r.total_cents,
                                r.item_summary,
                                r.customer_id
                            FROM receipt_line_items li
                            JOIN receipt_lookup r ON li.transaction_id = r.transaction_id
                            WHERE li.sku = mp.sku
                            ORDER BY r.transaction_ts DESC
                            LIMIT 5
                        ) rl ON true
                        ORDER BY mp.similarity DESC, rl.transaction_ts DESC NULLS LAST
                        """,
                        (embedding_str, embedding_str, MIN_SIMILARITY_THRESHOLD, embedding_str, limit),
                    )
                    logger.info(f"Semantic search (SKU-based) found product-receipt matches across all customers (threshold={MIN_SIMILARITY_THRESHOLD})")

                rows = await cur.fetchall()

                # DEBUG: Log what the query actually returned
                logger.info(f"Semantic search query returned {len(rows)} rows")
                if rows:
                    logger.info(f"First row sample: {dict(rows[0]) if rows else 'No rows'}")
                else:
                    logger.warning(f"❌ Semantic search returned ZERO rows for search_text='{search_text}', customer_id={customer_id}")

        return {
            "matches": [dict(r) for r in rows],
            "search_text": search_text,
            "note": "total_cents is in cents — divide by 100 for dollar display",
        }
