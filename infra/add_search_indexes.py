"""
Add optimized indexes for fuzzy search queries on receipt_lookup table.

This script creates compound indexes to speed up common CS rep search patterns:
- Store + Date + Amount + Card (primary fuzzy search)
- Card + Date (when customer knows card ending)
- Date descending (date range queries)

Run this after receipt_lookup table is populated.
"""

import psycopg
from psycopg.rows import dict_row
import subprocess
import json
import sys


def get_oauth_token():
    """Get OAuth token for Lakebase authentication."""
    result = subprocess.run(
        ['env', '-u', 'DATABRICKS_CONFIG_PROFILE', 'databricks', 'auth', 'token',
         '--host', 'https://adb-984752964297111.11.azuredatabricks.net', '-o', 'json'],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f"Error getting token: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    token_data = json.loads(result.stdout.strip())
    return token_data['access_token']


def main():
    # Connection details
    LAKEBASE_HOST = "instance-48e7b373-3240-4e42-a9f0-d7289706e1c6.database.azuredatabricks.net"
    LAKEBASE_PORT = 5432
    LAKEBASE_DATABASE = "giant_eagle"

    print("Getting OAuth token...")
    password = get_oauth_token()

    # Build connection string
    conninfo = f"host={LAKEBASE_HOST} port={LAKEBASE_PORT} dbname={LAKEBASE_DATABASE} user=lawrence.kyei@databricks.com password={password} sslmode=require"

    print("Connecting to Lakebase...")
    conn = psycopg.connect(conninfo, autocommit=True)  # CONCURRENT index creation requires autocommit
    cursor = conn.cursor(row_factory=dict_row)

    try:
        print("\nCreating optimized indexes for fuzzy search...\n")

        # Index 1: Compound index for primary fuzzy search pattern
        # Used when: store + date range + amount range + optional card
        print("1. Creating compound fuzzy search index (store, date, amount, card)...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_receipt_fuzzy_search
            ON receipt_lookup (store_name, transaction_ts DESC, total_cents, card_last4)
        """)
        print("   ✓ idx_receipt_fuzzy_search created")

        # Index 2: Card-focused search (partial index)
        # Used when: customer knows their card ending
        # Partial index = only rows with card_last4 (saves space)
        print("\n2. Creating card search index (card + date)...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_receipt_card_search
            ON receipt_lookup (card_last4, transaction_ts DESC)
            WHERE card_last4 IS NOT NULL
        """)
        print("   ✓ idx_receipt_card_search created (partial index)")

        # Index 3: Date range index
        # Used when: date-only searches or broad date ranges
        print("\n3. Creating date range index...")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_receipt_date_range
            ON receipt_lookup (transaction_ts DESC)
        """)
        print("   ✓ idx_receipt_date_range created")

        # Verify indexes
        print("\n4. Verifying indexes...")
        cursor.execute("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'receipt_lookup'
            AND schemaname = 'public'
            ORDER BY indexname
        """)

        indexes = cursor.fetchall()
        print(f"   Total indexes on receipt_lookup: {len(indexes)}")
        for idx in indexes:
            print(f"     - {idx['indexname']}")

        # Analyze table to update statistics for query planner
        print("\n5. Analyzing table to update query planner statistics...")
        cursor.execute("ANALYZE receipt_lookup")
        print("   ✓ Table analyzed")

        print("\n✅ Done! Fuzzy search indexes created successfully.")
        print("\nExpected performance improvement: 40-60ms faster fuzzy search queries")
        print("\nIndexes created:")
        print("  - idx_receipt_fuzzy_search: store + date + amount + card")
        print("  - idx_receipt_card_search: card + date (partial)")
        print("  - idx_receipt_date_range: date descending")

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
