"""
Giant Eagle — Silver Receipt Transform
Lakeflow Declarative Pipeline — Part of the Silver pipeline

Silver responsibilities:
  1. Dedup receipts and items using dlt.apply_changes() (UPSERT on transaction_id)
     Zerobus is at-least-once, so the same event_id may arrive multiple times.
     For receipts: keys = [transaction_id], latest ingested_ts wins.
     For items:    keys = [transaction_id, item_seq], latest ingested_ts wins.

  2. Aggregate items per receipt to produce a denormalized receipt_lookup_silver:
     - item_count (total line items on receipt)
     - items_detail (sorted struct array by item_seq for Gold's item_summary)
     - departments (set of department codes on this receipt)
     - items_extended_cents (sum of extended_cents across all items)

  3. Produce receipt_lookup_silver — the input to the Gold pipeline.

This file is part of the Silver pipeline together with bronze_receipt_ingest.py.
DLT resolves references between the two files within the same pipeline.

Target schema: giant_eagle.silver (set at pipeline level)
"""

import dlt
from pyspark.sql import functions as F


# ── receipts_silver: deduplicated receipt headers ─────────────────────────────

dlt.create_streaming_table(
    name="receipts_silver",
    comment="Deduplicated receipt headers. One row per transaction_id (latest wins).",
    table_properties={
        "quality": "silver",
        "delta.enableChangeDataFeed": "true",
        "pipelines.autoOptimize.zOrderCols": "customer_id,transaction_ts",
    },
    expect_all_or_drop={
        "positive_total_cents": "total_cents > 0",
    },
)

dlt.apply_changes(
    target="receipts_silver",
    source="pos_receipts_validated",
    keys=["transaction_id"],
    sequence_by=F.col("ingested_ts"),   # ingested_ts is TIMESTAMP — latest wins
    stored_as_scd_type=1,               # UPSERT: overwrite on retry/correction
)


# ── receipt_items_silver: deduplicated line items ─────────────────────────────

dlt.create_streaming_table(
    name="receipt_items_silver",
    comment="Deduplicated line items. One row per (transaction_id, item_seq).",
    table_properties={
        "quality": "silver",
        "delta.enableChangeDataFeed": "true",
    },
    expect_all_or_drop={
        "positive_extended_cents": "extended_cents >= 0",
    },
)

dlt.apply_changes(
    target="receipt_items_silver",
    source="pos_items_validated",
    keys=["transaction_id", "item_seq"],
    sequence_by=F.col("ingested_ts"),   # same ingested_ts as receipt header
    stored_as_scd_type=1,
)


# ── receipt_lookup_silver: denormalized receipts + item aggregates ─────────────

@dlt.table(
    name="receipt_lookup_silver",
    comment="Denormalized receipt headers + item aggregates. Feeds Gold pipeline.",
    table_properties={
        "quality": "silver",
        "delta.enableChangeDataFeed": "true",
        "pipelines.autoOptimize.zOrderCols": "customer_id,transaction_ts",
    },
)
def receipt_lookup_silver():
    """
    Joins clean receipt headers with aggregated item details.

    Items are aggregated per transaction to produce:
      item_count         — total number of line items
      items_extended_cents — sum of all extended_cents (should ≈ subtotal_cents)
      items_detail       — sorted struct array; Gold uses this for item_summary text
      departments        — set of department codes (for CS filtering/context)

    The items_detail struct is sorted by item_seq (ascending) so Gold can take
    the top-N product names in receipt order for the item_summary field.

    Struct fields in items_detail (matches pos_raw_items schema):
      item_seq (int), product_desc (string), upc (string), sku (string),
      quantity (decimal), unit_price_cents (bigint), extended_cents (bigint),
      discount_cents (bigint), department_code (string)
    """
    receipts = dlt.read("receipts_silver")
    items = dlt.read("receipt_items_silver")

    items_agg = (
        items
        .groupBy("transaction_id")
        .agg(
            F.count("*").alias("item_count"),
            F.sum("extended_cents").alias("items_extended_cents"),
            # Collect item structs — sort_array in Gold will sort by item_seq
            F.collect_list(
                F.struct(
                    F.col("item_seq").cast("int"),       # int: sort_array sorts by first field
                    F.col("product_desc"),
                    F.col("upc"),
                    F.col("sku"),
                    F.col("quantity"),
                    F.col("unit_price_cents"),
                    F.col("extended_cents"),
                    F.col("discount_cents"),
                    F.col("department_code"),
                )
            ).alias("items_detail"),
            F.collect_set("department_code").alias("departments"),
        )
    )

    return (
        receipts
        .join(items_agg, on="transaction_id", how="left")
        .withColumn("_silver_ts", F.current_timestamp())
    )
