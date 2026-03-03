"""
CS Receipt Lookup Platform — Bronze Quality Gate
Lakeflow Declarative Pipeline — Part of the Silver pipeline

Customer-agnostic DLT pipeline that reads from Zerobus-managed Delta tables
({catalog}.bronze.*) and applies schema validation, dropping rows that fail
critical quality expectations before Silver processes them.

Zerobus provides at-least-once delivery, so Bronze does NOT dedup — that's
Silver's job. Bronze only enforces:
  - Required fields are non-null
  - Numeric values are in valid range
  - Every item line has a valid identifier (upc or sku)

Source tables (managed by Zerobus, not DLT):
  {catalog}.bronze.pos_raw_receipts  — one row per POS transaction header
  {catalog}.bronze.pos_raw_items     — one row per line item (item_seq is PK within txn)

Schema (pos_raw_receipts):
  event_id, transaction_id, store_id, store_name, pos_terminal_id,
  cashier_id, customer_id, transaction_ts (timestamp), subtotal_cents (bigint),
  tax_cents (bigint), total_cents (bigint), tender_type, card_last4,
  raw_payload, ingested_ts (timestamp)

Schema (pos_raw_items):
  event_id, transaction_id, item_seq (int), upc, sku, product_desc,
  quantity (decimal(10,3)), unit_price_cents (bigint), extended_cents (bigint),
  discount_cents (bigint), department_code, ingested_ts (timestamp)
"""

import dlt
from pyspark.sql import functions as F


# ── Configuration ──────────────────────────────────────────────────────────────
# Read catalog name from DLT pipeline configuration (set in databricks.yml)
# DLT automatically sets this based on the pipeline's catalog parameter
def get_catalog_name():
    """Get the catalog name from Spark configuration.

    DLT pipelines inject the catalog name via spark.databricks.delta.catalog
    or we can read it from the current catalog setting. This makes the pipeline
    customer-agnostic while still referencing the correct catalog tables.
    """
    from pyspark.sql import SparkSession
    spark = SparkSession.getActiveSession()

    # Try to get from DLT configuration first
    catalog = spark.conf.get("spark.databricks.delta.catalog", None)

    # Fall back to current catalog
    if not catalog:
        catalog = spark.catalog.currentCatalog()

    return catalog


CATALOG = get_catalog_name()


# ── POS Receipt Headers ───────────────────────────────────────────────────────

@dlt.table(
    name="pos_receipts_validated",
    comment="Quality-gated POS receipt headers from Zerobus. Drops malformed rows.",
    table_properties={
        "quality": "bronze",
        "delta.enableChangeDataFeed": "true",
        "pipelines.autoOptimize.zOrderCols": "store_id,transaction_ts",
    },
)
@dlt.expect_or_drop("valid_transaction_id", "transaction_id IS NOT NULL")
@dlt.expect_or_drop("valid_store_id", "store_id IS NOT NULL")
@dlt.expect_or_drop("valid_timestamp", "transaction_ts IS NOT NULL")
@dlt.expect_or_drop("non_negative_total", "total_cents >= 0")
@dlt.expect("has_event_id", "event_id IS NOT NULL")  # warn: Zerobus always sets this
def pos_receipts_validated():
    """
    Streaming read from {catalog}.bronze.pos_raw_receipts (Zerobus-managed).

    Adds _bronze_ts (pipeline processing time) to distinguish from:
      - transaction_ts: when the POS transaction occurred
      - ingested_ts:    when Zerobus received the record
      - _bronze_ts:     when DLT processed it through this quality gate
    """
    from pyspark.sql import SparkSession
    spark = SparkSession.getActiveSession()

    return (
        spark.readStream.format("delta")
        .table(f"{CATALOG}.bronze.pos_raw_receipts")
        .withColumn("_bronze_ts", F.current_timestamp())
    )


# ── POS Line Items ─────────────────────────────────────────────────────────────

@dlt.table(
    name="pos_items_validated",
    comment="Quality-gated POS line items from Zerobus. Drops rows missing both upc and sku.",
    table_properties={
        "quality": "bronze",
        "delta.enableChangeDataFeed": "true",
        "pipelines.autoOptimize.zOrderCols": "transaction_id",
    },
)
@dlt.expect_or_drop("valid_transaction_id", "transaction_id IS NOT NULL")
@dlt.expect_or_drop("valid_item_identifier", "upc IS NOT NULL OR sku IS NOT NULL")
@dlt.expect_or_drop("positive_quantity", "quantity > 0")
@dlt.expect_or_drop("non_negative_extended", "extended_cents >= 0")
@dlt.expect("has_product_desc", "product_desc IS NOT NULL")  # warn: always set by POS
def pos_items_validated():
    """
    Streaming read from {catalog}.bronze.pos_raw_items (Zerobus-managed).

    Items are written as a separate stream from receipts — Zerobus sends the
    header record to pos_raw_receipts and each line item to pos_raw_items
    individually. Silver joins them by transaction_id.

    item_seq is the line-item sequence number on the physical receipt (1-based).
    """
    from pyspark.sql import SparkSession
    spark = SparkSession.getActiveSession()

    return (
        spark.readStream.format("delta")
        .table(f"{CATALOG}.bronze.pos_raw_items")
        .withColumn("_bronze_ts", F.current_timestamp())
    )
