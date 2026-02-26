"""
Giant Eagle — CS Context Agent
Generates a quick customer profile card for CS reps.

When a rep pulls up a customer, they need immediate context:
"Frequent shopper, $200/week avg, shops mostly Store 247, top: PRODUCE/DAIRY.
Last visit: 2 days ago. 47 transactions in the last 90 days."

This helps the rep have a better conversation and resolve issues faster.
Replaces the consumer-facing spending insights agent.

Data sources (all Lakebase, sub-10ms):
  - customer_profiles (synced from giant_eagle.gold.customer_profiles)
  - receipt_lookup    (synced from giant_eagle.gold.receipt_lookup)
  - spending_summary  (synced from giant_eagle.gold.spending_summary)

All monetary values are in cents (BIGINT). The AI briefing converts to dollars
for the rep (divides by 100).
"""

import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row
from mlflow.deployments import get_deploy_client

logger = logging.getLogger(__name__)


class CSContextAgent:
    """
    Generates customer context cards for CS reps.
    Reads from pre-computed Lakebase synced tables (sub-10ms for data fetch).
    Optional LLM summary generation (~500ms if enabled).
    """

    def __init__(
        self,
        lakebase_conninfo: str,
        model_endpoint: str = "databricks-claude-opus-4-6",
        enable_ai_summary: bool = True,
    ):
        self.lakebase_conninfo = lakebase_conninfo
        self.model_endpoint = model_endpoint
        self.enable_ai_summary = enable_ai_summary
        if enable_ai_summary:
            self.deploy_client = get_deploy_client("databricks")

    async def get_context(self, customer_id: str) -> dict[str, Any]:
        """
        Build a complete customer context card for a CS rep.

        Returns structured data (always fast, sub-10ms Lakebase reads)
        plus an optional AI summary (~500ms extra).

        Returns:
            Dict with customer_id, profile, recent_receipts,
            spending_by_category, and ai_summary.
        """
        profile = await self._get_profile(customer_id)
        recent_receipts = await self._get_recent_receipts(customer_id, limit=5)
        spending = await self._get_recent_spending(customer_id, months=3)

        context: dict[str, Any] = {
            "customer_id": customer_id,
            "profile": profile,
            "recent_receipts": recent_receipts,
            "spending_by_category": spending,
        }

        # Generate AI summary for the rep (optional, adds ~500ms)
        if self.enable_ai_summary and profile:
            context["ai_summary"] = self._generate_rep_briefing(
                profile, recent_receipts, spending
            )
        elif not profile:
            context["ai_summary"] = (
                "No customer profile found. "
                "This may be a guest or non-loyalty customer."
            )

        return context

    async def _get_profile(self, customer_id: str) -> dict | None:
        """
        Fetch customer 360 profile from Lakebase (synced from Delta Gold).

        Schema (customer_profiles):
          customer_id TEXT, first_name TEXT, last_name TEXT
          loyalty_tier TEXT             (BASIC / SILVER / GOLD)
          member_since_date DATE
          lifetime_spend_cents BIGINT
          visit_frequency_days FLOAT    (avg days between visits)
          top_categories JSONB          (array of top category strings)
          avg_basket_cents BIGINT
          preferred_store_id TEXT, preferred_store_name TEXT
        """
        async with await psycopg.AsyncConnection.connect(self.lakebase_conninfo) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT customer_id,
                           first_name,
                           last_name,
                           loyalty_tier,
                           member_since_date,
                           lifetime_spend_cents,
                           visit_frequency_days,
                           top_categories,
                           avg_basket_cents,
                           preferred_store_id,
                           preferred_store_name
                    FROM customer_profiles
                    WHERE customer_id = %s
                    """,
                    (customer_id,),
                )
                return await cur.fetchone()

    async def _get_recent_receipts(self, customer_id: str, limit: int = 5) -> list[dict]:
        """
        Fetch last N receipts from Lakebase for quick context.

        Schema (receipt_lookup relevant columns):
          transaction_id TEXT, store_id TEXT, store_name TEXT,
          transaction_ts TIMESTAMPTZ, total_cents BIGINT,
          item_count INT, category_tags JSONB
        """
        async with await psycopg.AsyncConnection.connect(self.lakebase_conninfo) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT transaction_id, store_id, store_name,
                           transaction_ts, total_cents,
                           item_count, category_tags
                    FROM receipt_lookup
                    WHERE customer_id = %s
                    ORDER BY transaction_ts DESC
                    LIMIT %s
                    """,
                    (customer_id, limit),
                )
                return [dict(r) for r in await cur.fetchall()]

    async def _get_recent_spending(self, customer_id: str, months: int = 3) -> list[dict]:
        """
        Fetch spending breakdown from the pre-computed spending_summary table.

        Schema (spending_summary relevant columns):
          category_l1 TEXT, summary_month DATE,
          total_cents BIGINT, visit_count INT
        """
        async with await psycopg.AsyncConnection.connect(self.lakebase_conninfo) as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT category_l1, summary_month,
                           total_cents, visit_count
                    FROM spending_summary
                    WHERE customer_id = %s
                      AND summary_month >= date_trunc(
                          'month', current_date - (interval '1 month' * %s)
                      )
                    ORDER BY summary_month DESC, total_cents DESC
                    """,
                    (customer_id, months),
                )
                return [dict(r) for r in await cur.fetchall()]

    def _generate_rep_briefing(
        self,
        profile: dict,
        receipts: list[dict],
        spending: list[dict],
    ) -> str:
        """
        Generate a quick AI briefing for the CS rep.
        Concise, factual, action-oriented — not a consumer-facing message.

        Monetary values in profile/receipts/spending are in cents.
        This method converts them to dollar strings before sending to the LLM
        so the briefing reads naturally to the rep.
        """

        def cents_to_dollars(cents: int | None) -> str:
            if cents is None:
                return "N/A"
            return f"${cents / 100:,.2f}"

        # Build a dollar-readable profile summary for the LLM context
        profile_summary = {
            "name": f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip(),
            "loyalty_tier": profile.get("loyalty_tier"),
            "member_since": str(profile.get("member_since_date", "")),
            "lifetime_spend": cents_to_dollars(profile.get("lifetime_spend_cents")),
            "avg_basket": cents_to_dollars(profile.get("avg_basket_cents")),
            "visit_frequency_days": profile.get("visit_frequency_days"),
            "top_categories": profile.get("top_categories"),
            "preferred_store": profile.get("preferred_store_name") or profile.get("preferred_store_id"),
        }

        # Readable recent receipts
        receipts_summary = [
            {
                "store": r.get("store_name") or r.get("store_id"),
                "date": str(r.get("transaction_ts", "")),
                "total": cents_to_dollars(r.get("total_cents")),
                "items": r.get("item_count"),
                "categories": r.get("category_tags"),
            }
            for r in receipts
        ]

        # Readable spending breakdown
        spending_summary = [
            {
                "category": r.get("category_l1"),
                "month": str(r.get("summary_month", "")),
                "spend": cents_to_dollars(r.get("total_cents")),
                "visits": r.get("visit_count"),
            }
            for r in spending[:10]
        ]

        prompt = (
            "You are a CS support tool. Generate a 2-3 sentence customer briefing "
            "for a Giant Eagle customer service rep who just pulled up this customer's profile. "
            "Be factual and concise. Include: visit frequency, spending level, "
            "preferred departments/stores, and anything that helps the rep on the call.\n\n"
            f"Profile: {json.dumps(profile_summary, default=str)}\n"
            f"Last 5 receipts: {json.dumps(receipts_summary, default=str)}\n"
            f"Recent spending (last 3 months): {json.dumps(spending_summary, default=str)}\n\n"
            "Format as a brief paragraph, not bullet points. "
            "Start with the most important context for the rep."
        )

        try:
            response = self.deploy_client.predict(
                endpoint=self.model_endpoint,
                inputs={
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a concise CS assistant. Give facts, not fluff.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 200,
                },
            )
            return response["choices"][0]["message"]["content"]
        except Exception as exc:
            logger.warning(f"AI summary generation failed: {exc}")
            return "AI summary unavailable. See structured data above."
