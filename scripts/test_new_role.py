#!/usr/bin/env python3
"""Test Lakebase connection with the UI-created role"""
import psycopg
from databricks.sdk import WorkspaceClient

# The role name created by the Lakebase UI
lakebase_role = "26560c6b-d6f8-4d23-804d-c4eecb62ce5b"
instance_name = "acme-retail-receipt-db"

print(f"Testing connection with Lakebase role: {lakebase_role}")
print()

try:
    w = WorkspaceClient()

    # Generate token for the service principal
    cred = w.database.generate_database_credential(instance_names=[instance_name])
    print(f"✓ Generated token (length: {len(cred.token)})")

    # Try to connect using the Lakebase UI role name
    conninfo = (
        f"host=instance-48e7b373-3240-4e42-a9f0-d7289706e1c6.database.azuredatabricks.net "
        f"port=5432 dbname=acme_retail "
        f"user={lakebase_role} password={cred.token} sslmode=require"
    )

    print(f"Connecting with user: {lakebase_role}")
    with psycopg.connect(conninfo, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, version()")
            result = cur.fetchone()
            print(f"✅ Connected successfully!")
            print(f"  Current user: {result[0]}")
            print(f"  Database: {result[1][:80]}...")

            # Test querying a table
            cur.execute("SELECT COUNT(*) FROM receipt_lookup")
            count = cur.fetchone()[0]
            print(f"  receipt_lookup row count: {count}")

except Exception as e:
    print(f"✗ Connection failed: {type(e).__name__}")
    print(f"  Error: {e}")
