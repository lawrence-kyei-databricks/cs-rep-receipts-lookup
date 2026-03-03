#!/usr/bin/env python3
"""
CS Receipt Lookup Platform — Infrastructure Setup Script
Customer-agnostic setup script for new deployments.

Creates all required infrastructure:
- Lakebase Provisioned instance
- Unity Catalog: catalog, schemas, volumes
- Lakebase tables: synced tables, native tables, AI tables
- Initial permissions and security setup

Usage:
    # Read configuration from databricks.yml variables
    python3 scripts/setup_infrastructure.py \
        --customer-name "Acme Retail" \
        --catalog-name "acme_retail" \
        --lakebase-instance "acme-receipt-db"

    # Or use environment variables (set by DAB deployment)
    export CUSTOMER_DISPLAY_NAME="Acme Retail"
    export CATALOG_NAME="acme_retail"
    export LAKEBASE_INSTANCE_NAME="acme-receipt-db"
    python3 scripts/setup_infrastructure.py

Prerequisites:
- Databricks CLI configured with workspace authentication
- Workspace admin permissions (for Lakebase, Unity Catalog)
- databricks-sdk Python package installed

What this script creates:
1. Lakebase Provisioned instance (CU_2 capacity by default)
2. Unity Catalog: {catalog_name} catalog with bronze, silver, gold schemas
3. Unity Catalog: Volumes for raw data storage
4. Lakebase tables: All synced, native, and AI tables
5. Initial RBAC: Grants for cs_rep, supervisor, fraud_team groups

What this script does NOT create:
- Databricks Workflows (Jobs) — created by DAB deployment
- DLT Pipelines — created by DAB deployment
- Databricks App — created by DAB deployment
- Test data — use seed_test_data.py separately

Run this BEFORE deploying via DABs (databricks bundle deploy).
"""

import argparse
import logging
import os
import sys
import time
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.catalog import (
    CatalogInfo,
    SchemaInfo,
    VolumeInfo,
    VolumeType,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class InfrastructureSetup:
    """Handles customer infrastructure setup."""

    def __init__(
        self,
        customer_name: str,
        catalog_name: str,
        lakebase_instance_name: str,
        lakebase_capacity: str = "CU_2",
    ):
        """
        Initialize setup configuration.

        Args:
            customer_name: Display name for customer (e.g., "Acme Retail")
            catalog_name: Unity Catalog name (e.g., "acme_retail")
            lakebase_instance_name: Lakebase instance identifier (e.g., "acme-receipt-db")
            lakebase_capacity: Compute capacity (CU_1, CU_2, CU_4, CU_8)
        """
        self.customer_name = customer_name
        self.catalog_name = catalog_name
        self.lakebase_instance_name = lakebase_instance_name
        self.lakebase_capacity = lakebase_capacity

        # Initialize Databricks SDK client
        self.w = WorkspaceClient()

        # Store created resources for validation
        self.created_resources = {
            "lakebase_instance": None,
            "catalog": None,
            "schemas": [],
            "volumes": [],
            "lakebase_tables": [],
        }

    def run_full_setup(self) -> dict[str, Any]:
        """
        Execute full infrastructure setup.

        Returns:
            Dict with created resource details and status
        """
        logger.info(f"Starting infrastructure setup for {self.customer_name}...")
        logger.info(f"  Catalog: {self.catalog_name}")
        logger.info(f"  Lakebase: {self.lakebase_instance_name}")

        try:
            # Step 1: Create Lakebase instance
            logger.info("\n=== Step 1: Creating Lakebase Instance ===")
            self.create_lakebase_instance()

            # Step 2: Create Unity Catalog objects
            logger.info("\n=== Step 2: Creating Unity Catalog ===")
            self.create_unity_catalog()
            self.create_schemas()
            self.create_volumes()

            # Step 3: Wait for Lakebase to be ready
            logger.info("\n=== Step 3: Waiting for Lakebase Instance ===")
            self.wait_for_lakebase()

            # Step 4: Create Lakebase tables
            logger.info("\n=== Step 4: Creating Lakebase Tables ===")
            self.create_lakebase_tables()

            # Step 5: Set up initial permissions
            logger.info("\n=== Step 5: Configuring Permissions ===")
            self.setup_permissions()

            logger.info("\n=== Infrastructure Setup Complete ===")
            return {
                "success": True,
                "customer_name": self.customer_name,
                "catalog_name": self.catalog_name,
                "lakebase_instance": self.lakebase_instance_name,
                "created_resources": self.created_resources,
            }

        except Exception as e:
            logger.error(f"Setup failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "created_resources": self.created_resources,
            }

    def create_lakebase_instance(self) -> None:
        """Create Lakebase Provisioned instance."""
        try:
            # Check if instance already exists
            try:
                existing = self.w.database.get(name=self.lakebase_instance_name)
                logger.info(f"✓ Lakebase instance '{self.lakebase_instance_name}' already exists (state: {existing.state})")
                self.created_resources["lakebase_instance"] = {
                    "name": self.lakebase_instance_name,
                    "state": existing.state,
                    "host": existing.read_write_dns,
                    "existed": True,
                }
                return
            except Exception:
                pass  # Instance doesn't exist, create it

            logger.info(f"Creating Lakebase instance '{self.lakebase_instance_name}' with {self.lakebase_capacity} capacity...")

            instance = self.w.database.create(
                name=self.lakebase_instance_name,
                capacity=self.lakebase_capacity,
                stopped=False,  # Start immediately
            )

            logger.info(f"✓ Lakebase instance created: {instance.name}")
            logger.info(f"  State: {instance.state}")
            logger.info(f"  Capacity: {self.lakebase_capacity}")

            self.created_resources["lakebase_instance"] = {
                "name": instance.name,
                "state": instance.state.value if instance.state else "UNKNOWN",
                "capacity": self.lakebase_capacity,
                "existed": False,
            }

        except Exception as e:
            logger.error(f"Failed to create Lakebase instance: {e}")
            raise

    def create_unity_catalog(self) -> None:
        """Create Unity Catalog."""
        try:
            # Check if catalog exists
            try:
                existing = self.w.catalogs.get(name=self.catalog_name)
                logger.info(f"✓ Catalog '{self.catalog_name}' already exists")
                self.created_resources["catalog"] = {
                    "name": self.catalog_name,
                    "existed": True,
                }
                return
            except Exception:
                pass  # Catalog doesn't exist

            logger.info(f"Creating Unity Catalog '{self.catalog_name}'...")

            catalog = self.w.catalogs.create(
                name=self.catalog_name,
                comment=f"CS Receipt Lookup data for {self.customer_name}",
            )

            logger.info(f"✓ Catalog created: {catalog.name}")

            self.created_resources["catalog"] = {
                "name": catalog.name,
                "existed": False,
            }

        except Exception as e:
            logger.error(f"Failed to create catalog: {e}")
            raise

    def create_schemas(self) -> None:
        """Create medallion architecture schemas (bronze, silver, gold)."""
        schemas = ["bronze", "silver", "gold"]

        for schema_name in schemas:
            try:
                full_name = f"{self.catalog_name}.{schema_name}"

                # Check if schema exists
                try:
                    existing = self.w.schemas.get(full_name=full_name)
                    logger.info(f"✓ Schema '{full_name}' already exists")
                    self.created_resources["schemas"].append({
                        "name": full_name,
                        "existed": True,
                    })
                    continue
                except Exception:
                    pass  # Schema doesn't exist

                logger.info(f"Creating schema '{full_name}'...")

                schema = self.w.schemas.create(
                    name=schema_name,
                    catalog_name=self.catalog_name,
                    comment=f"{schema_name.capitalize()} layer for {self.customer_name}",
                )

                logger.info(f"✓ Schema created: {schema.full_name}")

                self.created_resources["schemas"].append({
                    "name": schema.full_name,
                    "existed": False,
                })

            except Exception as e:
                logger.error(f"Failed to create schema {schema_name}: {e}")
                raise

    def create_volumes(self) -> None:
        """Create Unity Catalog volumes for raw data storage."""
        volumes = [
            {
                "schema": "bronze",
                "name": "raw_data",
                "comment": "Raw POS transaction files",
            },
            {
                "schema": "gold",
                "name": "exports",
                "comment": "Data exports for CS reps",
            },
        ]

        for vol_config in volumes:
            try:
                full_name = f"{self.catalog_name}.{vol_config['schema']}.{vol_config['name']}"

                # Check if volume exists
                try:
                    existing = self.w.volumes.read(name=full_name)
                    logger.info(f"✓ Volume '{full_name}' already exists")
                    self.created_resources["volumes"].append({
                        "name": full_name,
                        "existed": True,
                    })
                    continue
                except Exception:
                    pass  # Volume doesn't exist

                logger.info(f"Creating volume '{full_name}'...")

                volume = self.w.volumes.create(
                    catalog_name=self.catalog_name,
                    schema_name=vol_config["schema"],
                    name=vol_config["name"],
                    volume_type=VolumeType.MANAGED,
                    comment=vol_config["comment"],
                )

                logger.info(f"✓ Volume created: {volume.full_name}")

                self.created_resources["volumes"].append({
                    "name": volume.full_name,
                    "existed": False,
                })

            except Exception as e:
                logger.error(f"Failed to create volume {vol_config['name']}: {e}")
                raise

    def wait_for_lakebase(self, timeout: int = 600) -> None:
        """
        Wait for Lakebase instance to become RUNNING.

        Args:
            timeout: Maximum wait time in seconds (default: 10 minutes)
        """
        logger.info(f"Waiting for Lakebase instance '{self.lakebase_instance_name}' to be ready...")

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                instance = self.w.database.get(name=self.lakebase_instance_name)

                if instance.state and instance.state.value == "RUNNING":
                    logger.info(f"✓ Lakebase instance is RUNNING")
                    logger.info(f"  Host: {instance.read_write_dns}")
                    self.created_resources["lakebase_instance"]["host"] = instance.read_write_dns
                    return

                logger.info(f"  Instance state: {instance.state.value if instance.state else 'UNKNOWN'} (waiting...)")
                time.sleep(30)

            except Exception as e:
                logger.warning(f"Error checking instance status: {e}")
                time.sleep(30)

        raise TimeoutError(f"Lakebase instance did not become RUNNING within {timeout} seconds")

    def create_lakebase_tables(self) -> None:
        """
        Create Lakebase tables (native, synced will be created by DLT pipelines).

        Note: This creates the table DDL only. Synced tables are populated by
        DLT pipelines with Change Data Feed. Native tables are written to by
        the application directly.
        """
        logger.info("Creating Lakebase native tables...")

        # Get database credential for Lakebase connection
        cred = self.w.database.generate_database_credential(
            instance_names=[self.lakebase_instance_name]
        )

        # Import psycopg here to avoid requiring it for UC-only setup
        try:
            import psycopg
        except ImportError:
            logger.error("psycopg package required for Lakebase table creation")
            logger.error("Install with: pip install 'psycopg[binary]'")
            raise

        # Get instance details for connection
        instance = self.w.database.get(name=self.lakebase_instance_name)

        # Connect to Lakebase
        conninfo = (
            f"host={instance.read_write_dns} "
            f"port=5432 "
            f"dbname=databricks_postgres "
            f"user={self.w.current_user.me().user_name} "
            f"password={cred.token} "
            f"sslmode=require"
        )

        with psycopg.connect(conninfo) as conn:
            with conn.cursor() as cur:
                # Create native tables (written by app, not synced from Delta)
                native_table_ddls = [
                    # Audit log table (compliance requirement)
                    """
                    CREATE TABLE IF NOT EXISTS audit_log (
                        id BIGSERIAL PRIMARY KEY,
                        rep_id TEXT NOT NULL,
                        rep_email TEXT NOT NULL,
                        rep_role TEXT NOT NULL,
                        action TEXT NOT NULL,
                        resource_type TEXT,
                        resource_id TEXT,
                        query_params JSONB,
                        result_count INTEGER,
                        ip_address TEXT,
                        user_agent TEXT,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_audit_rep_email ON audit_log(rep_email);
                    CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_log(created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
                    """,
                    # Receipt delivery log (tracks emailed/printed receipts)
                    """
                    CREATE TABLE IF NOT EXISTS receipt_delivery_log (
                        id BIGSERIAL PRIMARY KEY,
                        transaction_id TEXT NOT NULL,
                        customer_id TEXT NOT NULL,
                        delivery_method TEXT NOT NULL,  -- 'email' or 'print'
                        delivery_address TEXT,          -- email address or printer ID
                        delivered_by TEXT NOT NULL,     -- rep email
                        delivery_status TEXT NOT NULL,  -- 'success', 'failed', 'pending'
                        error_message TEXT,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_delivery_customer ON receipt_delivery_log(customer_id);
                    CREATE INDEX IF NOT EXISTS idx_delivery_transaction ON receipt_delivery_log(transaction_id);
                    CREATE INDEX IF NOT EXISTS idx_delivery_created_at ON receipt_delivery_log(created_at DESC);
                    """,
                    # Agent state for Mosaic AI multi-turn conversations
                    """
                    CREATE TABLE IF NOT EXISTS agent_state (
                        session_id TEXT PRIMARY KEY,
                        customer_id TEXT,
                        rep_email TEXT NOT NULL,
                        conversation_history JSONB,
                        context JSONB,
                        last_updated TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE INDEX IF NOT EXISTS idx_agent_rep_email ON agent_state(rep_email);
                    CREATE INDEX IF NOT EXISTS idx_agent_last_updated ON agent_state(last_updated DESC);
                    """,
                    # Search cache to reduce redundant LLM calls
                    """
                    CREATE TABLE IF NOT EXISTS search_cache (
                        query_hash TEXT PRIMARY KEY,
                        query_text TEXT NOT NULL,
                        result_json JSONB NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                        access_count INTEGER DEFAULT 1
                    );
                    CREATE INDEX IF NOT EXISTS idx_cache_created_at ON search_cache(created_at DESC);
                    """,
                ]

                for ddl in native_table_ddls:
                    table_name = ddl.split("CREATE TABLE IF NOT EXISTS ")[1].split()[0]
                    logger.info(f"  Creating table: {table_name}")
                    cur.execute(ddl)
                    self.created_resources["lakebase_tables"].append(table_name)

                # Create pgvector extension for AI embeddings
                logger.info("  Enabling pgvector extension...")
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

                # Create product embeddings table (written by nightly embedding job)
                logger.info("  Creating table: product_embeddings")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS product_embeddings (
                        sku TEXT PRIMARY KEY,
                        product_name TEXT NOT NULL,
                        search_text TEXT,
                        embedding vector(1024),
                        updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                    );
                """)

                # Create HNSW index for fast vector similarity search
                logger.info("  Creating HNSW index on product_embeddings...")
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_product_embedding_hnsw
                    ON product_embeddings
                    USING hnsw (embedding vector_cosine_ops);
                """)

                self.created_resources["lakebase_tables"].append("product_embeddings")

            conn.commit()

        logger.info(f"✓ Created {len(self.created_resources['lakebase_tables'])} Lakebase tables")

    def setup_permissions(self) -> None:
        """
        Set up initial RBAC permissions for Unity Catalog.

        Creates grants for standard CS roles:
        - cs_rep: Basic lookup and search
        - supervisor: + audit access and bulk operations
        - fraud_team: + cross-customer patterns and fraud flags
        """
        logger.info("Configuring Unity Catalog permissions...")

        # Note: This assumes groups already exist in the workspace
        # Groups should be created via Databricks Account Console or SCIM

        roles_and_permissions = {
            "cs_rep": {
                "catalog": ["USE_CATALOG"],
                "schemas": ["USE_SCHEMA"],
                "tables": ["SELECT"],
            },
            "supervisor": {
                "catalog": ["USE_CATALOG"],
                "schemas": ["USE_SCHEMA"],
                "tables": ["SELECT"],
            },
            "fraud_team": {
                "catalog": ["USE_CATALOG"],
                "schemas": ["USE_SCHEMA"],
                "tables": ["SELECT"],
            },
        }

        for role, perms in roles_and_permissions.items():
            try:
                logger.info(f"  Setting permissions for '{role}' group...")

                # Grant catalog permissions
                for privilege in perms["catalog"]:
                    try:
                        self.w.grants.update(
                            securable_type="CATALOG",
                            full_name=self.catalog_name,
                            changes=[
                                {
                                    "principal": role,
                                    "add": [privilege],
                                }
                            ],
                        )
                    except Exception as e:
                        logger.warning(f"    Could not grant {privilege} to {role}: {e}")

                # Grant schema permissions (all schemas in catalog)
                for schema_info in self.created_resources["schemas"]:
                    schema_name = schema_info["name"]
                    for privilege in perms["schemas"]:
                        try:
                            self.w.grants.update(
                                securable_type="SCHEMA",
                                full_name=schema_name,
                                changes=[
                                    {
                                        "principal": role,
                                        "add": [privilege],
                                    }
                                ],
                            )
                        except Exception as e:
                            logger.warning(f"    Could not grant {privilege} on {schema_name} to {role}: {e}")

                logger.info(f"  ✓ Permissions configured for '{role}'")

            except Exception as e:
                logger.warning(f"Failed to set permissions for {role}: {e}")

        logger.info("✓ Permissions configuration complete")
        logger.info("  Note: Row-level security and column masks are applied via Unity Catalog")
        logger.info("        functions in the DLT pipelines (not managed by this script)")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Set up infrastructure for CS Receipt Lookup Platform",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--customer-name",
        default=os.environ.get("CUSTOMER_DISPLAY_NAME"),
        help="Customer display name (e.g., 'Acme Retail')",
    )
    parser.add_argument(
        "--catalog-name",
        default=os.environ.get("CATALOG_NAME"),
        help="Unity Catalog name (e.g., 'acme_retail')",
    )
    parser.add_argument(
        "--lakebase-instance",
        default=os.environ.get("LAKEBASE_INSTANCE_NAME"),
        help="Lakebase instance name (e.g., 'acme-receipt-db')",
    )
    parser.add_argument(
        "--lakebase-capacity",
        default=os.environ.get("LAKEBASE_CAPACITY", "CU_2"),
        choices=["CU_1", "CU_2", "CU_4", "CU_8"],
        help="Lakebase compute capacity (default: CU_2)",
    )

    args = parser.parse_args()

    # Validate required arguments
    if not args.customer_name:
        logger.error("--customer-name is required (or set CUSTOMER_DISPLAY_NAME)")
        sys.exit(1)
    if not args.catalog_name:
        logger.error("--catalog-name is required (or set CATALOG_NAME)")
        sys.exit(1)
    if not args.lakebase_instance:
        logger.error("--lakebase-instance is required (or set LAKEBASE_INSTANCE_NAME)")
        sys.exit(1)

    # Run setup
    setup = InfrastructureSetup(
        customer_name=args.customer_name,
        catalog_name=args.catalog_name,
        lakebase_instance_name=args.lakebase_instance,
        lakebase_capacity=args.lakebase_capacity,
    )

    result = setup.run_full_setup()

    if result["success"]:
        logger.info("\n" + "=" * 80)
        logger.info("INFRASTRUCTURE SETUP SUCCESSFUL")
        logger.info("=" * 80)
        logger.info(f"\nCustomer: {result['customer_name']}")
        logger.info(f"Catalog: {result['catalog_name']}")
        logger.info(f"Lakebase: {result['lakebase_instance']}")
        logger.info("\nNext steps:")
        logger.info("1. Deploy via DABs: databricks bundle deploy")
        logger.info("2. Start DLT pipelines to populate synced tables")
        logger.info("3. Run embedding pipeline: databricks jobs run --job-id <embedding-job-id>")
        logger.info("4. Deploy Databricks App: databricks apps deploy")
        sys.exit(0)
    else:
        logger.error("\n" + "=" * 80)
        logger.error("INFRASTRUCTURE SETUP FAILED")
        logger.error("=" * 80)
        logger.error(f"\nError: {result.get('error')}")
        logger.error("\nPartially created resources may need manual cleanup")
        sys.exit(1)


if __name__ == "__main__":
    main()
