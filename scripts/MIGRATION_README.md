# Lakebase → Delta Migration

## Overview

This directory contains scripts to migrate the 335,507 receipts from Lakebase `receipt_lookup_backup` table back into the Delta Bronze layer, so they can flow through the proper DLT pipeline (Bronze → Silver → Gold) and eventually be synced to Lakebase via synced tables.

## The Problem

The test data generation script (`generate_test_data.py`) bypassed the Delta layer entirely and wrote 335,507 receipts directly to Lakebase PostgreSQL. According to the documented architecture, data should flow:

```
POS → gRPC → Zerobus → Delta (Bronze→Silver→Gold) → Synced Tables → Lakebase
```

But the actual reality was:

```
Test Script → Lakebase (bypass Delta completely)
```

This broke the data sync mechanism because synced tables require Delta as the source of truth.

## Migration Approaches

### Option 1: Python Script (Simple but Slow)

**Script**: `migrate_lakebase_to_delta.py`

**Approach**:
- Reads from Lakebase backup table in batches
- Maps schema from Lakebase format to Bronze format
- Inserts into Delta via SQL Warehouse API (one INSERT per record)

**Pros**:
- Simple, self-contained Python script
- Uses standard Databricks SDK
- Handles schema mapping automatically
- Can run from local machine

**Cons**:
- **VERY SLOW**: ~1-2 seconds per record = 4-8 days for 335k records
- May hit API rate limits
- SQL Warehouse compute costs for hours of runtime
- Not recommended for production

**Usage**:
```bash
# Generate fresh Lakebase credential
python3 migrate_lakebase_to_delta.py

# Or provide existing token
export TOKEN="your-lakebase-token"
python3 migrate_lakebase_to_delta.py
```

**Estimated time**: 4-8 days (not practical)

---

### Option 2: Databricks Notebook (Recommended)

**Script**: `migrate_via_notebook.py` (to be created)

**Approach**:
- Run as Databricks notebook on cluster
- Read from Lakebase via JDBC in Spark
- Transform data using Spark DataFrame API
- Write directly to Delta Bronze table (parallel writes)

**Pros**:
- **MUCH FASTER**: 100-1000x faster than Option 1
- Leverages Spark parallelism
- Efficient JDBC connector
- Native Delta write optimizations
- Estimated time: 5-15 minutes for 335k records

**Cons**:
- Requires Databricks cluster
- More complex setup

**Usage**:
```python
# Upload to Databricks workspace as notebook
# Configure cluster with:
# - Runtime: DBR 15.x+
# - Libraries: psycopg2-binary
# Run the notebook
```

**Estimated time**: 5-15 minutes

---

## Schema Mapping

The migration handles these transformations:

| Lakebase Backup | Delta Bronze | Notes |
|-----------------|--------------|-------|
| `transaction_id` | `transaction_id` | Direct copy |
| `customer_id` | `customer_id` | Direct copy |
| `store_name` | `store_name` | Direct copy |
| `purchase_timestamp` | `transaction_ts` | Renamed field |
| `total_cents` | `total_cents` | Direct copy |
| `last4_card` | `card_last4` | Renamed field |
| `created_at` | `ingested_ts` | Renamed field |
| (generated) | `event_id` | UUID generated |
| (inferred) | `tender_type` | "CREDIT" if card present, else "CASH" |
| (estimated) | `tax_cents` | 10% of total (rough estimate) |
| (calculated) | `subtotal_cents` | total - tax |
| (minimal JSON) | `raw_payload` | Constructed minimal payload |
| (NULL) | `store_id` | Not in backup |
| (NULL) | `pos_terminal_id` | Not in backup |
| (NULL) | `cashier_id` | Not in backup |

## Next Steps After Migration

Once the 335,507 records are in Delta Bronze:

1. **DLT Pipeline Processing**
   - Bronze → Silver → Gold transformations will run
   - Materialized views need to be converted to Delta tables
   - Current Gold layer uses `MATERIALIZED_VIEW` which can't be synced

2. **Fix Gold Layer Structure**
   - Modify DLT pipelines to output standard Delta tables instead of materialized views
   - Or create intermediate Delta tables that can be synced

3. **Create Synced Tables**
   - Once Gold tables are proper Delta tables, create synced tables:
   ```python
   from databricks.sdk import WorkspaceClient
   w = WorkspaceClient()

   w.online_tables.create(
       name="giant_eagle_serving.public.receipt_lookup",
       spec={
           "source_table_full_name": "giant_eagle.gold.receipt_lookup",
           "run_triggered": {"triggered": True}
       }
   )
   ```

4. **Application Will Query Synced Data**
   - App queries `giant_eagle_serving.public.receipt_lookup`
   - Data automatically syncs from Delta Gold via CDF
   - Sub-10ms Lakebase queries with up-to-date Delta data

## Current State Summary

```
Delta Bronze: 10 records (manual test inserts)
Lakebase backup: 335,507 records (bypassed Delta)
Synced tables: 1 active (pos_raw_receipts_synced with 10 records)
```

**Goal**: Get all 335,507 records into Delta so they can flow through the proper architecture.

## Recommendation

**Use Option 2 (Databricks Notebook)** for the actual migration. The Python script (Option 1) is useful for small datasets or testing, but not practical for 335k records.

I can create the Databricks notebook version if needed.
