#!/usr/bin/env python3
"""Set security label for service principal OAuth authentication in Lakebase"""
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

# Service principal that needs OAuth mapping
sp_client_id = "e1751c32-5a1b-4d6f-90c2-e71e10246366"

conninfo = f"host={host} port={port} dbname={dbname} user={user} password={password} sslmode=require"

print(f"Setting security label for service principal: {sp_client_id}")
print("Connecting to Lakebase...")

with psycopg.connect(conninfo) as conn:
    with conn.cursor() as cur:
        # Set security label to map the role to Databricks OAuth
        # The label format is: databricks:client_id=<sp_client_id>
        print(f"Setting security label on role {sp_client_id}...")

        label_value = f"databricks:client_id={sp_client_id}"
        # SECURITY LABEL doesn't support parameterized queries, must use string literal
        cur.execute(
            f"SECURITY LABEL FOR databricks ON ROLE \"{sp_client_id}\" IS '{label_value}'"
        )

        conn.commit()
        print(f"✅ Security label set successfully!")
        print(f"   Label: {label_value}")

print("\nDone! Service principal should now be able to authenticate with OAuth tokens.")
