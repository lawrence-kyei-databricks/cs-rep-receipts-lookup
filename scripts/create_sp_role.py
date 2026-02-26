#!/usr/bin/env python3
"""Create service principal role in Lakebase and grant permissions"""
import psycopg

# Connection details (using my user credentials)
host = "instance-48e7b373-3240-4e42-a9f0-d7289706e1c6.database.azuredatabricks.net"
port = "5432"
dbname = "acme_retail"
user = "lawrence.kyei@databricks.com"

# Generate fresh token
from databricks.sdk import WorkspaceClient
w = WorkspaceClient()
cred = w.database.generate_database_credential(instance_names=["acme-retail-receipt-db"])
password = cred.token

# Service principal that needs access
sp_client_id = "e1751c32-5a1b-4d6f-90c2-e71e10246366"

conninfo = f"host={host} port={port} dbname={dbname} user={user} password={password} sslmode=require"

print(f"Creating role for service principal: {sp_client_id}")
print("Connecting to Lakebase...")

with psycopg.connect(conninfo) as conn:
    with conn.cursor() as cur:
        # Check if role exists
        cur.execute(
            "SELECT COUNT(*) FROM pg_roles WHERE rolname = %s",
            [sp_client_id]
        )
        role_exists = cur.fetchone()[0] > 0

        if role_exists:
            print(f"✓ Role {sp_client_id} already exists")
        else:
            # Create the role with LOGIN privilege
            print(f"Creating role {sp_client_id}...")
            # Use quoted identifier for the UUID-based role name
            cur.execute(f'CREATE ROLE "{sp_client_id}" WITH LOGIN')
            print(f"✓ Role {sp_client_id} created")

        # Grant USAGE on schema to the service principal
        print("Granting USAGE on public schema...")
        cur.execute(f'GRANT USAGE ON SCHEMA public TO "{sp_client_id}"')

        # Grant permissions on all tables
        print("Granting table permissions...")
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        tables = [row[0] for row in cur.fetchall()]

        for table in tables:
            if table == 'audit_log':
                # Read-write access to audit_log
                print(f"  Granting SELECT, INSERT on {table}...")
                cur.execute(f'GRANT SELECT, INSERT ON TABLE "{table}" TO "{sp_client_id}"')
            else:
                # Read-only access to other tables
                print(f"  Granting SELECT on {table}...")
                cur.execute(f'GRANT SELECT ON TABLE "{table}" TO "{sp_client_id}"')

        # Grant USAGE on sequences (for INSERT to work with SERIAL)
        cur.execute("""
            SELECT sequence_name FROM information_schema.sequences
            WHERE sequence_schema = 'public'
        """)
        sequences = [row[0] for row in cur.fetchall()]

        for seq in sequences:
            print(f"  Granting USAGE on sequence {seq}...")
            cur.execute(f'GRANT USAGE ON SEQUENCE "{seq}" TO "{sp_client_id}"')

        conn.commit()
        print("\n✅ All permissions granted successfully!")
        print(f"\nGranted to service principal: {sp_client_id}")
        print("- USAGE on schema public")
        print(f"- SELECT, INSERT on audit_log")
        print(f"- SELECT on {len(tables)-1} other tables")
        print(f"- USAGE on {len(sequences)} sequences")

print("\nDone!")
