#!/usr/bin/env python3
"""Test Lakebase connection with service principal credentials"""
import os
from databricks.sdk import WorkspaceClient

# Get the service principal client ID
sp_client_id = "e1751c32-5a1b-4d6f-90c2-e71e10246366"
instance_name = "acme-retail-receipt-db"

print("Testing service principal Lakebase connection...")
print(f"SP Client ID: {sp_client_id}")
print(f"Instance: {instance_name}")
print()

# Try to generate a token for the service principal
try:
    w = WorkspaceClient()
    print("✓ WorkspaceClient initialized")

    # Generate database credential for the service principal
    cred = w.database.generate_database_credential(instance_names=[instance_name])
    print(f"✓ Generated database credential")
    print(f"  Token length: {len(cred.token) if cred.token else 0}")
    print()

    # Try to connect using the service principal client ID as username
    import psycopg
    from psycopg.rows import dict_row

    conninfo = (
        f"host=instance-48e7b373-3240-4e42-a9f0-d7289706e1c6.database.azuredatabricks.net "
        f"port=5432 dbname=acme_retail "
        f"user={sp_client_id} password={cred.token} sslmode=require"
    )

    print(f"Connecting with user: {sp_client_id}")
    with psycopg.connect(conninfo, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, version()")
            result = cur.fetchone()
            print(f"✓ Connected successfully!")
            print(f"  Current user: {result[0]}")
            print(f"  Database version: {result[1][:50]}...")

            # Test if we can query a table
            cur.execute("SELECT COUNT(*) FROM receipt_lookup")
            count = cur.fetchone()[0]
            print(f"  receipt_lookup row count: {count}")

except Exception as e:
    print(f"✗ Connection failed: {type(e).__name__}")
    print(f"  Error: {e}")
    import traceback
    traceback.print_exc()
