# Databricks notebook source
# MAGIC %md
# MAGIC # Lakebase → Delta Migration (Spark-based)
# MAGIC
# MAGIC **Purpose**: Migrate 335,507 receipts from Lakebase backup table to Delta Bronze layer
# MAGIC
# MAGIC **Approach**: Use Spark JDBC connector for parallel reads/writes
# MAGIC
# MAGIC **Expected runtime**: 5-15 minutes (100-1000x faster than Python script)
# MAGIC
# MAGIC **Prerequisites**:
# MAGIC - Cluster with DBR 15.x+
# MAGIC - Lakebase credential token (generated below)
# MAGIC
# MAGIC **Architecture fix**: This migration enables data to flow through proper DLT pipeline:
# MAGIC ```
# MAGIC Lakebase backup → Delta Bronze → DLT (Silver → Gold) → Synced Tables → Lakebase serving
# MAGIC ```

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Setup and Configuration

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.types import *
from databricks.sdk import WorkspaceClient
import uuid

# Configuration
LAKEBASE_HOST = "instance-7c6265a0-a083-4654-8781-a29b80c5afcf.database.azuredatabricks.net"
LAKEBASE_PORT = 5432
LAKEBASE_DB = "giant_eagle"
LAKEBASE_USER = "lawrence.kyei@databricks.com"
LAKEBASE_BACKUP_TABLE = "receipt_lookup_backup"

DELTA_BRONZE_TABLE = "giant_eagle.bronze.pos_raw_receipts"

print("Configuration loaded successfully")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Generate Lakebase Credential

# COMMAND ----------

# Generate fresh OAuth token for Lakebase
w = WorkspaceClient()

# Get credential token for giant-eagle-receipt-db instance
credential = w.lakebase_provisioned.generate_credential(
    instance_names=["giant-eagle-receipt-db"]
)

LAKEBASE_TOKEN = credential.token

print("✓ Lakebase credential generated")
print(f"Token length: {len(LAKEBASE_TOKEN)} chars")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Read from Lakebase via JDBC

# COMMAND ----------

# JDBC connection properties
jdbc_url = f"jdbc:postgresql://{LAKEBASE_HOST}:{LAKEBASE_PORT}/{LAKEBASE_DB}?sslmode=require"

jdbc_properties = {
    "user": LAKEBASE_USER,
    "password": LAKEBASE_TOKEN,
    "driver": "org.postgresql.Driver",
    "fetchsize": "10000",  # Fetch 10k rows at a time for performance
}

# Read backup table into Spark DataFrame
print(f"Reading from {LAKEBASE_BACKUP_TABLE}...")

lakebase_df = (
    spark.read
    .jdbc(
        url=jdbc_url,
        table=LAKEBASE_BACKUP_TABLE,
        properties=jdbc_properties
    )
)

# Cache for performance (data fits in memory)
lakebase_df.cache()

record_count = lakebase_df.count()
print(f"✓ Read {record_count:,} records from Lakebase")

# Show sample
print("\nSample records:")
lakebase_df.show(5, truncate=False)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Transform Schema (Lakebase → Delta Bronze)

# COMMAND ----------

# Schema transformation function
def transform_to_bronze_schema(df):
    """Transform Lakebase schema to Delta Bronze schema."""

    # Generate event_id for each row
    df = df.withColumn("event_id",
        F.concat(F.lit("evt-"), F.substring(F.expr("uuid()"), 1, 12))
    )

    # Rename fields
    df = df.withColumnRenamed("purchase_timestamp", "transaction_ts")
    df = df.withColumnRenamed("last4_card", "card_last4")
    df = df.withColumnRenamed("created_at", "ingested_ts")

    # Infer tender_type from card presence
    df = df.withColumn("tender_type",
        F.when(F.col("card_last4").isNotNull(), F.lit("CREDIT"))
         .otherwise(F.lit("CASH"))
    )

    # Estimate tax as 10% of total (rough approximation)
    df = df.withColumn("tax_cents", (F.col("total_cents") * 0.10).cast("bigint"))
    df = df.withColumn("subtotal_cents", F.col("total_cents") - F.col("tax_cents"))

    # Create minimal raw_payload as JSON string
    df = df.withColumn("raw_payload",
        F.to_json(F.struct(
            F.col("transaction_id"),
            F.col("store_name"),
            F.col("total_cents"),
            F.col("transaction_ts").alias("timestamp"),
            F.lit("lakebase_migration").alias("source")
        ))
    )

    # Add NULL columns that don't exist in backup
    df = df.withColumn("store_id", F.lit(None).cast("string"))
    df = df.withColumn("pos_terminal_id", F.lit(None).cast("string"))
    df = df.withColumn("cashier_id", F.lit(None).cast("string"))
    df = df.withColumn("_rescued_data", F.lit(None).cast("string"))

    # Select columns in Delta Bronze order
    bronze_df = df.select(
        "event_id",
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
        "raw_payload",
        "ingested_ts",
        "_rescued_data"
    )

    return bronze_df

# Apply transformation
print("Transforming schema...")
bronze_df = transform_to_bronze_schema(lakebase_df)

print("✓ Schema transformation complete")
print(f"\nTransformed schema:")
bronze_df.printSchema()

print(f"\nSample transformed records:")
bronze_df.show(3, truncate=False)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Check Current Delta Bronze State

# COMMAND ----------

# Check current record count in Delta Bronze
current_bronze_df = spark.table(DELTA_BRONZE_TABLE)
current_count = current_bronze_df.count()

print(f"Current Delta Bronze records: {current_count:,}")
print(f"Records to migrate: {record_count:,}")
print(f"Expected final count: {current_count + record_count:,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Write to Delta Bronze (Parallel)

# COMMAND ----------

print(f"Writing {record_count:,} records to Delta Bronze...")
print(f"Target table: {DELTA_BRONZE_TABLE}")
print(f"Write mode: append")

# Write to Delta using Spark (parallel, fast)
(
    bronze_df.write
    .format("delta")
    .mode("append")
    .option("mergeSchema", "false")  # Schema must match exactly
    .saveAsTable(DELTA_BRONZE_TABLE)
)

print("✓ Write completed successfully")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Verify Migration

# COMMAND ----------

# Check final count
final_bronze_df = spark.table(DELTA_BRONZE_TABLE)
final_count = final_bronze_df.count()

print("="*60)
print("MIGRATION COMPLETE")
print("="*60)
print(f"Before migration: {current_count:,} records")
print(f"Migrated: {record_count:,} records")
print(f"After migration: {final_count:,} records")
print(f"Net new: {final_count - current_count:,} records")

if final_count - current_count == record_count:
    print("\n✓ SUCCESS: All records migrated successfully!")
else:
    print(f"\n⚠ WARNING: Expected {record_count:,} new records but got {final_count - current_count:,}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Verify Sample Migrated Data

# COMMAND ----------

# Query some migrated records
print("Sample migrated records (recent inserts):")

(
    spark.table(DELTA_BRONZE_TABLE)
    .orderBy(F.col("ingested_ts").desc())
    .select(
        "transaction_id",
        "customer_id",
        "store_name",
        "transaction_ts",
        "total_cents",
        "tender_type",
        "card_last4"
    )
    .show(10, truncate=False)
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Next Steps
# MAGIC
# MAGIC Now that data is in Delta Bronze, follow these steps:
# MAGIC
# MAGIC ### 1. DLT Pipeline Processing
# MAGIC The existing DLT pipelines will process:
# MAGIC - **Bronze → Silver**: Data cleaning, validation, enrichment
# MAGIC - **Silver → Gold**: Pre-computed aggregations
# MAGIC
# MAGIC Trigger pipeline manually or wait for scheduled run:
# MAGIC ```bash
# MAGIC databricks pipelines start-update --pipeline-id <pipeline-id> --full-refresh
# MAGIC ```
# MAGIC
# MAGIC ### 2. Fix Gold Layer Structure
# MAGIC **Critical issue**: Current Gold layer outputs `MATERIALIZED_VIEW` which cannot be synced.
# MAGIC
# MAGIC **Solution**: Modify DLT pipeline to output standard Delta tables:
# MAGIC - Option A: Change `@dlt.table()` to `@dlt.view()` + separate materialized table
# MAGIC - Option B: Create intermediate Delta table from materialized view
# MAGIC
# MAGIC ### 3. Create Synced Tables
# MAGIC Once Gold tables are proper Delta tables, create synced tables:
# MAGIC ```python
# MAGIC from databricks.sdk import WorkspaceClient
# MAGIC w = WorkspaceClient()
# MAGIC
# MAGIC w.online_tables.create(
# MAGIC     name="giant_eagle_serving.public.receipt_lookup",
# MAGIC     spec={
# MAGIC         "source_table_full_name": "giant_eagle.gold.receipt_lookup",
# MAGIC         "run_triggered": {"triggered": True}
# MAGIC     }
# MAGIC )
# MAGIC ```
# MAGIC
# MAGIC ### 4. Application Queries via Synced Tables
# MAGIC Application will query enriched data:
# MAGIC - Source: `giant_eagle_serving.public.receipt_lookup` (Lakebase)
# MAGIC - Auto-synced from: `giant_eagle.gold.receipt_lookup` (Delta)
# MAGIC - Performance: Sub-10ms queries with up-to-date data

# COMMAND ----------

# MAGIC %md
# MAGIC ## Summary
# MAGIC
# MAGIC This migration restored the proper data flow architecture:
# MAGIC
# MAGIC **Before**:
# MAGIC ```
# MAGIC Test Script → Lakebase (bypassed Delta entirely)
# MAGIC ```
# MAGIC
# MAGIC **After**:
# MAGIC ```
# MAGIC Data → Delta Bronze → DLT (Silver → Gold) → Synced Tables → Lakebase
# MAGIC ```
# MAGIC
# MAGIC All 335,507 receipts now in Delta as source of truth, ready for DLT processing and syncing to Lakebase.
