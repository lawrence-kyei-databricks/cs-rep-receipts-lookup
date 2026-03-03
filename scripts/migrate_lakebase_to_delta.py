#!/usr/bin/env python3
"""
Migrate receipt data from Lakebase backup table to Delta Bronze layer.

This script reads the 335,507 receipts from receipt_lookup_backup (Lakebase)
and inserts them into giant_eagle.bronze.pos_raw_receipts (Delta) so they can
flow through the DLT pipeline (Bronze → Silver → Gold) and eventually sync
back to Lakebase via synced tables.

Schema mapping:
  Lakebase                    → Delta Bronze
  ------------------------------------------------
  transaction_id              → transaction_id
  customer_id                 → customer_id
  store_name                  → store_name
  purchase_timestamp          → transaction_ts
  total_cents                 → total_cents
  last4_card                  → card_last4
  created_at                  → ingested_ts
  (generated)                 → event_id
  (inferred from card_last4)  → tender_type
  (estimated 10%)             → tax_cents
  (total - tax)               → subtotal_cents
  (minimal JSON)              → raw_payload
  (NULL)                      → store_id, pos_terminal_id, cashier_id
"""

import os
import psycopg2
from databricks.sdk import WorkspaceClient
from datetime import datetime
import uuid
import json

# Lakebase connection details
LAKEBASE_HOST = "instance-7c6265a0-a083-4654-8781-a29b80c5afcf.database.azuredatabricks.net"
LAKEBASE_PORT = 5432
LAKEBASE_DB = "giant_eagle"
LAKEBASE_USER = "lawrence.kyei@databricks.com"
TOKEN = os.environ.get('TOKEN')

# Delta target
DELTA_BRONZE_TABLE = "giant_eagle.bronze.pos_raw_receipts"

def generate_lakebase_token():
    """Generate fresh Lakebase OAuth token."""
    print("Generating Lakebase credential token...")
    w = WorkspaceClient()

    # Get Lakebase instance details
    from databricks.sdk.service.provisioning import GetProvisioningInfoRequest
    instances = w.lakebase_provisioned.list()
    instance = None
    for inst in instances:
        if inst.name == "giant-eagle-receipt-db":
            instance = inst
            break

    if not instance:
        raise Exception("Lakebase instance 'giant-eagle-receipt-db' not found")

    # Generate credential for this instance
    credential = w.lakebase_provisioned.generate_credential(
        instance_names=["giant-eagle-receipt-db"]
    )

    return credential.token


def estimate_tax_split(total_cents):
    """Estimate tax as 10% of total (rough approximation)."""
    tax_cents = int(total_cents * 0.10)
    subtotal_cents = total_cents - tax_cents
    return subtotal_cents, tax_cents


def infer_tender_type(last4_card):
    """Infer tender type from card presence."""
    if last4_card and last4_card.strip():
        return "CREDIT"  # Assume credit if card present
    return "CASH"


def create_minimal_payload(transaction_id, store_name, total_cents, timestamp):
    """Create minimal JSON payload for raw_payload field."""
    return json.dumps({
        "transaction_id": transaction_id,
        "store": store_name,
        "total": total_cents,
        "timestamp": timestamp.isoformat() if timestamp else None,
        "source": "lakebase_migration"
    })


def read_lakebase_receipts(conn, batch_size=1000):
    """Read receipts from Lakebase backup table in batches."""
    cursor = conn.cursor()

    # Get total count
    cursor.execute("SELECT COUNT(*) FROM receipt_lookup_backup")
    total = cursor.fetchone()[0]
    print(f"Total receipts to migrate: {total:,}")

    # Read in batches
    offset = 0
    while offset < total:
        cursor.execute(f"""
            SELECT
                transaction_id,
                customer_id,
                store_name,
                purchase_timestamp,
                total_cents,
                last4_card,
                created_at
            FROM receipt_lookup_backup
            ORDER BY transaction_id
            LIMIT {batch_size} OFFSET {offset}
        """)

        batch = cursor.fetchall()
        if not batch:
            break

        yield batch
        offset += batch_size

        if offset % 10000 == 0:
            print(f"  Read {offset:,} / {total:,} receipts...")

    cursor.close()


def insert_to_delta(w, batch):
    """Insert batch of receipts into Delta Bronze table via individual SQL statements."""

    success_count = 0
    error_count = 0

    for row in batch:
        (transaction_id, customer_id, store_name, purchase_timestamp,
         total_cents, last4_card, created_at) = row

        # Generate/transform fields
        event_id = f"evt-{uuid.uuid4().hex[:12]}"
        tender_type = infer_tender_type(last4_card)
        subtotal_cents, tax_cents = estimate_tax_split(total_cents)
        raw_payload = create_minimal_payload(transaction_id, store_name, total_cents, purchase_timestamp)

        # Handle NULLs - use proper NULL in SQL
        customer_id_val = f"'{customer_id}'" if customer_id else "NULL"
        last4_val = f"'{last4_card}'" if last4_card else "NULL"

        # Escape single quotes in strings
        transaction_id_esc = transaction_id.replace("'", "''") if transaction_id else ""
        store_name_esc = store_name.replace("'", "''") if store_name else ""
        raw_payload_esc = raw_payload.replace("'", "''")

        # Build single-row INSERT statement
        insert_sql = f"""
            INSERT INTO {DELTA_BRONZE_TABLE}
            (event_id, transaction_id, store_id, store_name, pos_terminal_id, cashier_id,
             customer_id, transaction_ts, subtotal_cents, tax_cents, total_cents,
             tender_type, card_last4, raw_payload, ingested_ts, _rescued_data)
            VALUES (
                '{event_id}',
                '{transaction_id_esc}',
                NULL,
                '{store_name_esc}',
                NULL,
                NULL,
                {customer_id_val},
                TIMESTAMP '{purchase_timestamp}',
                {subtotal_cents},
                {tax_cents},
                {total_cents},
                '{tender_type}',
                {last4_val},
                '{raw_payload_esc}',
                TIMESTAMP '{created_at}',
                NULL
            )
        """

        try:
            w.statement_execution.execute_statement(
                warehouse_id=None,  # Auto-select warehouse
                statement=insert_sql,
                catalog="giant_eagle",
                schema="bronze",
                wait_timeout="30s"
            )
            success_count += 1
        except Exception as e:
            error_count += 1
            if error_count <= 5:  # Only print first 5 errors
                print(f"  Error inserting transaction {transaction_id}: {e}")

    if error_count > 0:
        print(f"  Batch complete: {success_count} succeeded, {error_count} failed")

    return success_count, error_count


def main():
    """Main migration workflow."""

    # Generate token if not provided
    token = TOKEN
    if not token:
        print("TOKEN not in environment, generating new one...")
        token = generate_lakebase_token()

    print(f"\n{'='*60}")
    print("LAKEBASE → DELTA MIGRATION")
    print(f"{'='*60}\n")

    print(f"Source: Lakebase receipt_lookup_backup table")
    print(f"Target: {DELTA_BRONZE_TABLE}")
    print(f"Instance: {LAKEBASE_HOST}\n")

    # Connect to Databricks
    print("Connecting to Databricks...")
    w = WorkspaceClient()

    # Connect to Lakebase
    print("Connecting to Lakebase...")
    conn = psycopg2.connect(
        host=LAKEBASE_HOST,
        port=LAKEBASE_PORT,
        dbname=LAKEBASE_DB,
        user=LAKEBASE_USER,
        password=token,
        sslmode='require'
    )

    try:
        # Check current Delta count
        print("\nChecking current Delta Bronze record count...")
        result = w.statement_execution.execute_statement(
            warehouse_id=None,
            statement=f"SELECT COUNT(*) as count FROM {DELTA_BRONZE_TABLE}",
            catalog="giant_eagle",
            schema="bronze"
        )

        current_count = 0
        if result.result and result.result.data_array:
            current_count = result.result.data_array[0][0]

        print(f"Current Delta Bronze records: {current_count:,}")

        # Migrate in batches
        print(f"\nStarting migration (batch size: 1000)...\n")

        total_migrated = 0
        total_errors = 0
        batch_num = 0

        for batch in read_lakebase_receipts(conn, batch_size=100):  # Smaller batches
            batch_num += 1
            success, errors = insert_to_delta(w, batch)
            total_migrated += success
            total_errors += errors

            if batch_num % 10 == 0:
                print(f"  Progress: {total_migrated:,} succeeded, {total_errors:,} failed ({batch_num} batches)...")

        print(f"\n✓ Migration complete!")
        print(f"  Total migrated: {total_migrated:,}")
        print(f"  Total errors: {total_errors:,}")

        # Verify final count
        print("\nVerifying final Delta Bronze count...")
        result = w.statement_execution.execute_statement(
            warehouse_id=None,
            statement=f"SELECT COUNT(*) as count FROM {DELTA_BRONZE_TABLE}",
            catalog="giant_eagle",
            schema="bronze"
        )

        final_count = 0
        if result.result and result.result.data_array:
            final_count = result.result.data_array[0][0]

        print(f"Final Delta Bronze records: {final_count:,}")
        print(f"Net new records: {final_count - current_count:,}")

        print(f"\n{'='*60}")
        print("NEXT STEPS")
        print(f"{'='*60}")
        print("1. DLT pipelines will process Bronze → Silver → Gold")
        print("2. Once Gold tables exist as Delta tables (not MATERIALIZED_VIEWs),")
        print("   create synced tables to sync Gold → Lakebase")
        print("3. Application will then query enriched data via synced tables")

    finally:
        conn.close()
        print("\nLakebase connection closed.")

    return 0


if __name__ == "__main__":
    exit(main())
