"""
Giant Eagle — Gold Receipt Insights
Lakeflow Declarative Pipeline (Gold pipeline — separate from Silver)

Gold tables are the authoritative source for the CS Receipt Lookup app.
All four tables are synced to Lakebase (giant_eagle_serving.public.*) via
continuous Synced Tables provisioned in Phase 1.

Tables produced:
  receipt_lookup     — Denormalized receipts with item_summary. CS reps query
                       this at sub-10ms via Lakebase synced copy. Also the
                       input to the Phase 4 embedding pipeline.
  spending_summary   — Customer spend by department_code + month. Powers the
                       CS customer context card ("top categories this month").
  customer_profiles  — Customer 360: lifetime stats, top departments.
                       CS reps see this as the quick profile panel.
  product_catalog    — Distinct products seen across all POS transactions.
                       Input to Phase 4 pgvector embedding pipeline.

All monetary values in cents (BIGINT). No floating-point anywhere.
CDF is enabled on all tables — required for Synced Tables to push incremental
changes to Lakebase.

REMOVED vs consumer version:
  ❌ purchase_frequency — Smart Reorder agent feature, not needed for CS tool

Source: giant_eagle.silver.* (produced by the Silver pipeline)
Target: giant_eagle.gold.* (owned by this pipeline)
"""

import dlt
from pyspark.sql import functions as F
from pyspark.sql.window import Window


# ── receipt_lookup ─────────────────────────────────────────────────────────────

@dlt.table(
    name="receipt_lookup",
    comment="Final enriched receipts for CS rep lookup. Synced to Lakebase via CDF.",
    table_properties={
        "quality": "gold",
        "delta.enableChangeDataFeed": "true",
        "pipelines.autoOptimize.zOrderCols": "customer_id,transaction_ts",
    },
)
def receipt_lookup():
    """
    Primary table synced to Lakebase (giant_eagle_serving.public.receipt_lookup).
    CS reps query this at sub-10ms for full receipt details.

    item_summary is computed from items_detail (receipt order, top 3 + "N more"):
      Examples:
        "Oat Milk 32oz, Roquefort Wedge 8oz"
        "Oat Milk 32oz, Roquefort Wedge 8oz, Sourdough Loaf + 3 more"

    sort_array(items_detail) sorts by item_seq (int, first struct field) ascending,
    giving receipt-order item names for the summary.

    Note: The Lakebase native table (receipt_transactions) has its own item_summary
    written by the POS integration layer. This Gold table serves the analytics/synced
    path for historical queries and semantic search ingestion.
    """
    # Silver tables are DLT Materialized Views — streaming reads are not supported.
    # Use a batch read (triggered Gold pipeline runs on a schedule, batch is correct).
    src = spark.read.table("giant_eagle.silver.receipt_lookup_silver")

    # Sort items_detail by item_seq (first struct field) to get receipt order
    sorted_items = F.sort_array(F.col("items_detail"))

    # Extract product_desc strings in order
    item_descs = F.transform(sorted_items, lambda x: x.getField("product_desc"))

    # Top-3 product names (or all if <= 3 items)
    top3 = F.slice(item_descs, 1, 3)

    # How many items beyond the top 3
    n_more = F.greatest(
        F.coalesce(F.col("item_count"), F.lit(0)) - F.lit(3),
        F.lit(0),
    )

    item_summary_expr = F.when(
        F.col("item_count").isNull() | (F.col("item_count") <= F.lit(3)),
        F.array_join(top3, ", "),
    ).otherwise(
        F.concat(
            F.array_join(top3, ", "),
            F.lit(" + "),
            n_more.cast("string"),
            F.lit(" more"),
        )
    )

    return (
        src
        .withColumn("item_summary", item_summary_expr)
        .withColumn("month_key", F.date_format("transaction_ts", "yyyy-MM"))
        # "yyyy-'W'ww" is not valid in Spark 3.x DateTimeFormatter.
        # Use weekofyear() + lpad for a portable ISO week string (e.g. "2026-W08").
        .withColumn(
            "week_key",
            F.concat(
                F.date_format("transaction_ts", "yyyy"),
                F.lit("-W"),
                F.lpad(F.weekofyear("transaction_ts").cast("string"), 2, "0"),
            ),
        )
        .withColumn("_gold_ts", F.current_timestamp())
        .select(
            # Receipt header (matches Lakebase receipt_transactions schema)
            "transaction_id",
            "store_id",
            "store_name",
            "pos_terminal_id",
            "cashier_id",
            "customer_id",
            "transaction_ts",
            "subtotal_cents",
            "tax_cents",
            "total_cents",
            "tender_type",
            "card_last4",
            # Derived / aggregated
            "item_count",
            "item_summary",          # "Oat Milk 32oz, Roquefort Wedge 8oz + 1 more"
            "departments",           # set of department_code values
            "items_detail",          # full item struct array (for embedding pipeline)
            "items_extended_cents",  # cross-check vs subtotal_cents
            # Time partitioning helpers
            "month_key",             # "2026-02"
            "week_key",              # "2026-W08"
            "_gold_ts",
        )
    )


# ── spending_summary ───────────────────────────────────────────────────────────

@dlt.table(
    name="spending_summary",
    comment="Pre-computed spending by customer, department, and month. CS context card.",
    table_properties={
        "quality": "gold",
        "delta.enableChangeDataFeed": "true",
        "pipelines.autoOptimize.zOrderCols": "customer_id,month_key",
    },
)
def spending_summary():
    """
    Spending aggregations that power the CS customer context card.
    Pre-computing these means CS reps see the customer's spending profile
    instantly (sub-10ms from Lakebase) without LLM calls.

    Synced to Lakebase: giant_eagle_serving.public.spending_summary
    Primary key (for synced table): (customer_id, department_code, month_key)

    All spend values are in cents (BIGINT).
    discount_cents shows loyalty/promo savings per category per month.
    """
    items = spark.read.table("giant_eagle.silver.receipt_items_silver")
    receipts = spark.read.table("giant_eagle.silver.receipts_silver")

    return (
        items
        .join(
            receipts
            .filter(F.col("customer_id").isNotNull())
            .select("transaction_id", "customer_id", "transaction_ts"),
            on="transaction_id",
            how="inner",
        )
        .withColumn("month_key", F.date_format("transaction_ts", "yyyy-MM"))
        .groupBy("customer_id", "department_code", "month_key")
        .agg(
            F.sum("extended_cents").alias("total_spend_cents"),
            F.sum("discount_cents").alias("total_discount_cents"),
            F.count("*").alias("item_count"),
            F.countDistinct("transaction_id").alias("trip_count"),
            F.min("transaction_ts").alias("first_purchase"),
            F.max("transaction_ts").alias("last_purchase"),
        )
        .withColumn("_gold_ts", F.current_timestamp())
    )


# ── customer_profiles ──────────────────────────────────────────────────────────

@dlt.table(
    name="customer_profiles",
    comment="Customer 360 aggregation. CS rep sees this as the quick profile panel.",
    table_properties={
        "quality": "gold",
        "delta.enableChangeDataFeed": "true",
    },
)
def customer_profiles():
    """
    Customer-level lifetime stats and top departments.
    CS reps use this to quickly orient themselves before a call:
      - "This customer shops mostly in DAIRY and CHEESE"
      - "30 visits, $4,200 lifetime spend, last visit 3 days ago"

    Synced to Lakebase: giant_eagle_serving.public.customer_profiles
    Primary key: customer_id

    lifetime_spend_cents and avg_basket_cents are BIGINT (cents).
    top_departments: array of (department_code, dept_spend_cents) structs,
    sorted by spend descending, top 5 only.
    """
    receipts = spark.read.table("giant_eagle.silver.receipts_silver")
    items = spark.read.table("giant_eagle.silver.receipt_items_silver")

    loyal_receipts = receipts.filter(F.col("customer_id").isNotNull())

    # Receipt-level lifetime stats per customer
    receipt_stats = (
        loyal_receipts
        .groupBy("customer_id")
        .agg(
            F.count("*").alias("total_transactions"),
            F.sum("total_cents").alias("lifetime_spend_cents"),
            (F.sum("total_cents") / F.count("*")).cast("bigint").alias("avg_basket_cents"),
            F.min("transaction_ts").alias("first_transaction"),
            F.max("transaction_ts").alias("last_transaction"),
            F.countDistinct("store_id").alias("stores_visited"),
            F.countDistinct(
                F.date_format("transaction_ts", "yyyy-MM")
            ).alias("active_months"),
        )
    )

    # Top 5 departments by spend per customer
    dept_spend = (
        items
        .join(
            loyal_receipts.select("transaction_id", "customer_id"),
            on="transaction_id",
        )
        .groupBy("customer_id", "department_code")
        .agg(F.sum("extended_cents").alias("dept_spend_cents"))
        .withColumn(
            "rank",
            F.row_number().over(
                Window.partitionBy("customer_id").orderBy(F.desc("dept_spend_cents"))
            ),
        )
        .filter(F.col("rank") <= 5)
        .groupBy("customer_id")
        .agg(
            F.collect_list(
                F.struct("department_code", "dept_spend_cents")
            ).alias("top_departments")
        )
    )

    return (
        receipt_stats
        .join(dept_spend, on="customer_id", how="left")
        .withColumn("_gold_ts", F.current_timestamp())
    )


# ── product_catalog ────────────────────────────────────────────────────────────

@dlt.table(
    name="product_catalog",
    comment="Distinct products from POS history. Input to Phase 4 embedding pipeline.",
    table_properties={
        "quality": "gold",
        "delta.enableChangeDataFeed": "true",
    },
)
def product_catalog():
    """
    Aggregates distinct products seen across all POS transactions.

    Primary key for synced table: COALESCE(upc, sku) — all items pass Bronze's
    upc-or-sku requirement, so at least one is always non-null.

    purchase_count and last_seen are used by Phase 4 to prioritize which
    products get embeddings first (high-frequency products embedded first).

    department_code is the POS department — used as category for semantic search
    until a richer product taxonomy is available.

    Synced to Lakebase: giant_eagle_serving.public.product_catalog
    """
    items = spark.read.table("giant_eagle.silver.receipt_items_silver")

    return (
        items
        .withColumn("product_key", F.coalesce(F.col("upc"), F.col("sku")))
        .groupBy("product_key", "upc", "sku", "product_desc", "department_code")
        .agg(
            F.count("*").alias("purchase_count"),
            F.max("ingested_ts").alias("last_seen"),
        )
        .withColumn("_gold_ts", F.current_timestamp())
    )
