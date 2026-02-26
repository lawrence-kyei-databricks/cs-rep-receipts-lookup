"""
Admin routes — one-time seeder for demo/dev environments.
POST /admin/seed populates Lakebase tables with the Bronze/Gold test data.
Requires supervisor role (not intended for regular CS rep access).
"""

import json
import logging
from datetime import datetime, timezone

import psycopg
from fastapi import APIRouter, Depends, Request

from middleware.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/debug/lakebase")
async def debug_lakebase(request: Request):
    """Temporary: show Lakebase env vars and test connection (no sensitive data)."""
    import os, psycopg
    # Show ALL env vars that might be lakebase/PG related
    all_env = {k: (v[:30] + "..." if len(v) > 30 else v) for k, v in os.environ.items()}
    lakebase_env = {k: v for k, v in all_env.items()
                    if any(x in k.upper() for x in ["PG", "LAKE", "DATABRICKS", "DB_", "DATABASE"])}
    env_info = {
        "all_relevant_env": lakebase_env,
        "PGHOST": os.environ.get("PGHOST", "NOT SET"),
        "PGPORT": os.environ.get("PGPORT", "NOT SET"),
        "PGDATABASE": os.environ.get("PGDATABASE", "NOT SET"),
        "PGUSER": os.environ.get("PGUSER", "NOT SET"),
        "PGSSLMODE": os.environ.get("PGSSLMODE", "NOT SET"),
        "LAKEBASE_INSTANCE_NAME": os.environ.get("LAKEBASE_INSTANCE_NAME", "NOT SET"),
        "DATABRICKS_CLIENT_ID": (os.environ.get("DATABRICKS_CLIENT_ID") or "NOT SET")[:8] + "...",
        "has_client_secret": bool(os.environ.get("DATABRICKS_CLIENT_SECRET")),
        "DATABRICKS_HOST": os.environ.get("DATABRICKS_HOST", "NOT SET"),
    }
    conninfo = request.app.state.lakebase_conninfo
    # Mask password in conninfo for display
    safe_conninfo = " ".join(
        p if not p.startswith("password=") else "password=***"
        for p in conninfo.split()
    )
    env_info["conninfo"] = safe_conninfo
    env_info["conninfo_has_password"] = "password=" in conninfo and len(conninfo.split("password=")[-1].split()[0]) > 5

    # Explicitly test generate_database_credential
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        cred = w.database.generate_database_credential(
            instance_names=[os.environ.get("LAKEBASE_INSTANCE_NAME", "giant-eagle-receipt-db")]
        )
        env_info["generate_credential_success"] = True
        env_info["credential_token_length"] = len(cred.token or "")
        env_info["credential_expiration"] = str(cred.expiration_time)
    except Exception as e:
        env_info["generate_credential_success"] = False
        env_info["generate_credential_error"] = str(e)

    # Try to connect
    try:
        with psycopg.connect(conninfo, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_user, version()")
                row = cur.fetchone()
                env_info["db_user"] = row[0]
                env_info["db_version"] = row[1][:50]
                cur.execute("SELECT COUNT(*) FROM receipt_lookup")
                env_info["receipt_count"] = cur.fetchone()[0]
    except Exception as e:
        env_info["db_error"] = str(e)

    return env_info


# ── Seed data matching what was inserted into Bronze/Gold ──────────────────────

RECEIPTS = [
    ("txn-1001", "cust-5001", None, "247", "East Liberty",  "2026-02-10T14:32:11Z", 3250, 260, 3510,  "CREDIT", "4532", 2, "Whole Milk 1gal, Wonder Bread 20oz",                               ["DAIRY","BAKERY"]),
    ("txn-1002", "cust-5001", None, "247", "East Liberty",  "2026-02-12T09:15:44Z", 5140, 412, 5552,  "CREDIT", "4532", 3, "Roquefort Cheese 8oz, Brie Cheese 8oz, OJ 52oz",                    ["DELI","BEVERAGE"]),
    ("txn-1003", "cust-5002", None, "112", "Shadyside",     "2026-02-11T18:22:05Z", 4200, 336, 4536,  "DEBIT",  "7821", 2, "Chicken Breast 2lb, Pasta Sauce 24oz",                              ["MEAT","GROCERY"]),
    ("txn-1004", "cust-5002", None, "112", "Shadyside",     "2026-02-14T11:05:30Z", 6800, 544, 7344,  "DEBIT",  "7821", 3, "Greek Yogurt 32oz, Cheerios 18oz, Roma Tomatoes 2lb",               ["DAIRY","CEREAL","PRODUCE"]),
    ("txn-1005", "cust-5003", None, "312", "Squirrel Hill", "2026-02-13T16:45:22Z", 9800, 784, 10584, "CREDIT", "2211", 1, "Ribeye Steak 1.5lb",                                                ["MEAT"]),
    ("txn-1006", "cust-5003", None, "312", "Squirrel Hill", "2026-02-15T12:30:18Z", 7200, 576, 7776,  "CREDIT", "2211", 3, "Fancy Cheese Assortment 12oz, Roquefort Cheese 8oz, Brie Cheese 8oz",["DELI"]),
    ("txn-1007", "cust-5004", None, "501", "Monroeville",   "2026-02-16T10:20:55Z", 5600, 448, 6048,  "CASH",   None,  2, "Whole Milk 1gal, Bananas 3lb",                                      ["DAIRY","PRODUCE"]),
    ("txn-1008", "cust-5005", None, "247", "East Liberty",  "2026-02-17T14:55:10Z", 3800, 304, 4104,  "EBT",    None,  2, "Wonder Bread 20oz, Whole Milk 1gal",                                ["BAKERY","DAIRY"]),
    ("txn-1009", "cust-5005", None, "112", "Shadyside",     "2026-02-18T09:30:22Z", 6100, 488, 6588,  "EBT",    None,  3, "Greek Yogurt 32oz, Cheerios 18oz, OJ 52oz",                         ["DAIRY","CEREAL","BEVERAGE"]),
    ("txn-1010", "cust-5006", None, "501", "Monroeville",   "2026-02-15T17:10:45Z", 8900, 712, 9612,  "CREDIT", "9988",1, "Ribeye Steak 1.5lb",                                                ["MEAT"]),
]

PROFILES = [
    # (customer_id, first_name, last_name, email, phone_last4, preferred_store_id, preferred_store_name, loyalty_tier, member_since, lifetime_spend, visit_freq_days, top_categories, avg_basket, has_pharmacy, fraud_flag)
    ("cust-5001", "Maria",  "Santos",    "maria.santos@example.com",   "4221", "247", "East Liberty",  "GOLD",   "2021-03-15", 3510 + 5552,  2.0, ["DELI","DAIRY","BAKERY"],   (3510+5552)//2,  False, False),
    ("cust-5002", "James",  "Chen",      "james.chen@example.com",     "8834", "112", "Shadyside",     "SILVER", "2022-07-01", 4536 + 7344,  3.0, ["MEAT","DAIRY","PRODUCE"],  (4536+7344)//2,  False, False),
    ("cust-5003", "Sarah",  "Williams",  "sarah.w@example.com",        "6612", "312", "Squirrel Hill", "GOLD",   "2020-11-20", 10584 + 7776, 2.0, ["MEAT","DELI"],             (10584+7776)//2, False, False),
    ("cust-5004", "Robert", "Johnson",   "rjohnson@example.com",       "3301", "501", "Monroeville",   "BASIC",  "2023-02-10", 6048,         7.0, ["DAIRY","PRODUCE"],         6048,            False, False),
    ("cust-5005", "Lisa",   "Washington","lisa.w@example.com",         "7723", "247", "East Liberty",  "BASIC",  "2023-09-05", 4104 + 6588,  2.0, ["DAIRY","BAKERY","CEREAL"], (4104+6588)//2,  False, False),
    ("cust-5006", "David",  "Thompson",  "d.thompson@example.com",     "5544", "501", "Monroeville",   "SILVER", "2022-04-18", 9612,        14.0, ["MEAT"],                    9612,            False, False),
]

SPENDING = [
    # (customer_id, summary_month, category_l1, total_cents, visit_count)
    ("cust-5001", "2026-02-01", "DAIRY",    3510, 1),
    ("cust-5001", "2026-02-01", "DELI",     5552, 1),
    ("cust-5001", "2026-02-01", "BAKERY",   3510, 1),
    ("cust-5002", "2026-02-01", "MEAT",     4536, 1),
    ("cust-5002", "2026-02-01", "DAIRY",    7344, 1),
    ("cust-5002", "2026-02-01", "PRODUCE",  4536, 1),
    ("cust-5003", "2026-02-01", "MEAT",    10584, 1),
    ("cust-5003", "2026-02-01", "DELI",     7776, 1),
    ("cust-5004", "2026-02-01", "DAIRY",    6048, 1),
    ("cust-5004", "2026-02-01", "PRODUCE",  6048, 1),
    ("cust-5005", "2026-02-01", "DAIRY",    4104, 1),
    ("cust-5005", "2026-02-01", "BAKERY",   4104, 1),
    ("cust-5005", "2026-02-01", "CEREAL",   6588, 1),
    ("cust-5006", "2026-02-01", "MEAT",     9612, 1),
]


@router.post("/seed")
async def seed_lakebase(
    request: Request,
    user: dict = Depends(get_current_user),
):
    """
    One-time seeder: populates Lakebase tables with demo test data.
    Idempotent — truncates tables before inserting.

    Authorization: UC table grants control who can write to tables.
    cs_reps without INSERT privilege will get a permission denied error.
    """
    conninfo = request.app.state.lakebase_conninfo
    now = datetime.now(timezone.utc)

    try:
        async with await psycopg.AsyncConnection.connect(conninfo) as conn:
            async with conn.cursor() as cur:

                # ── receipt_lookup ─────────────────────────────────────────
                await cur.execute("DELETE FROM public.receipt_lookup WHERE transaction_id LIKE 'txn-1%'")
                for row in RECEIPTS:
                    (txn_id, cust_id, cust_name, store_id, store_name, ts_str,
                     subtotal, tax, total, tender, card4, item_cnt, item_sum, cats) = row
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    await cur.execute(
                        """
                        INSERT INTO public.receipt_lookup (
                            transaction_id, customer_id, customer_name, store_id, store_name,
                            transaction_ts, transaction_date, subtotal_cents, tax_cents, total_cents,
                            tender_type, card_last4, item_count, item_summary, category_tags,
                            has_pharmacy, has_fuel_points, fuel_points_earned, updated_ts
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                        """,
                        (txn_id, cust_id, cust_name, store_id, store_name,
                         ts, ts.date(), subtotal, tax, total, tender, card4,
                         item_cnt, item_sum, json.dumps(cats), False, False, 0, now)
                    )
                logger.info("Seeded %d receipt_lookup rows", len(RECEIPTS))

                # ── customer_profiles ──────────────────────────────────────
                await cur.execute("DELETE FROM public.customer_profiles WHERE customer_id LIKE 'cust-5%'")
                for row in PROFILES:
                    (cust_id, fname, lname, email, phone4, pref_store_id, pref_store,
                     tier, since_str, lifetime, visit_freq, top_cats, avg_basket,
                     pharm, fraud) = row
                    since = datetime.strptime(since_str, "%Y-%m-%d").date()
                    await cur.execute(
                        """
                        INSERT INTO public.customer_profiles (
                            customer_id, first_name, last_name, email, phone_last4,
                            preferred_store_id, preferred_store_name, loyalty_tier,
                            member_since_date, lifetime_spend_cents, visit_frequency_days,
                            top_categories, avg_basket_cents, has_pharmacy, fraud_flag, updated_ts
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                        """,
                        (cust_id, fname, lname, email, phone4, pref_store_id, pref_store,
                         tier, since, lifetime, visit_freq, json.dumps(top_cats), avg_basket,
                         pharm, fraud, now)
                    )
                logger.info("Seeded %d customer_profiles rows", len(PROFILES))

                # ── spending_summary ───────────────────────────────────────
                await cur.execute("DELETE FROM public.spending_summary WHERE customer_id LIKE 'cust-5%'")
                for row in SPENDING:
                    cust_id, month_str, cat, total_c, visit_cnt = row
                    month_date = datetime.strptime(month_str, "%Y-%m-%d").date()
                    await cur.execute(
                        """
                        INSERT INTO public.spending_summary (
                            customer_id, summary_month, category_l1, total_cents, visit_count, updated_ts
                        ) VALUES (%s,%s,%s,%s,%s,%s)
                        """,
                        (cust_id, month_date, cat, total_c, visit_cnt, now)
                    )
                logger.info("Seeded %d spending_summary rows", len(SPENDING))

                await conn.commit()

        return {
            "status": "seeded",
            "receipt_lookup": len(RECEIPTS),
            "customer_profiles": len(PROFILES),
            "spending_summary": len(SPENDING),
            "timestamp": now.isoformat(),
        }

    except Exception as e:
        logger.error("Seed failed: %s", e)
        raise


@router.get("/debug/my-role")
async def debug_my_role(request: Request):
    """
    Debug endpoint: show current user's resolved role and groups.
    No auth required - useful for debugging role issues.
    """
    from middleware.auth import get_current_user
    import time

    # Get the user object (this will resolve role from UC)
    user = await get_current_user(request)

    return {
        "email": user.get("email"),
        "role": user.get("role"),
        "groups": user.get("groups"),
        "cache_key": int(time.time() / 300),
        "timestamp": time.time(),
    }


@router.post("/clear-role-cache")
async def clear_role_cache():
    """
    Clear the role resolution cache.

    Use this when a user's group membership changes and you need
    the role to update immediately instead of waiting 5 minutes.

    No auth required - clearing the cache is not a security risk.
    """
    from middleware.auth import _resolve_user_role_cached

    # Clear the LRU cache
    _resolve_user_role_cached.cache_clear()

    return {
        "status": "cleared",
        "message": "Role cache cleared. Next request will fetch fresh group memberships from Unity Catalog.",
        "cache_info": str(_resolve_user_role_cached.cache_info())
    }
