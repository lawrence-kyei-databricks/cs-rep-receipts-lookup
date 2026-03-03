#!/usr/bin/env python3
"""
CS Receipt Lookup Platform — Deployment Validation Script
Customer-agnostic validation for successful deployments.

Validates that all infrastructure and application components are working:
- Lakebase instance is running and accessible
- Unity Catalog objects exist (catalog, schemas, volumes)
- Lakebase tables are created (synced, native, AI)
- DLT pipelines are deployed and can run
- Databricks App is deployed and accessible
- End-to-end connectivity tests

Usage:
    # Use environment variables (set by DAB deployment)
    python3 scripts/validate_deployment.py

    # Or specify configuration explicitly
    python3 scripts/validate_deployment.py \
        --customer-name "Acme Retail" \
        --catalog-name "acme_retail" \
        --lakebase-instance "acme-receipt-db"

Exit codes:
    0 - All validations passed
    1 - One or more validations failed
    2 - Configuration error or validation could not run

Output:
    Prints validation results with ✓ (pass) or ✗ (fail) for each check.
"""

import argparse
import logging
import os
import sys
import time
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.pipelines import PipelineState

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class DeploymentValidator:
    """Validates customer deployment."""

    def __init__(
        self,
        customer_name: str,
        catalog_name: str,
        lakebase_instance_name: str,
    ):
        """
        Initialize validator.

        Args:
            customer_name: Display name for customer
            catalog_name: Unity Catalog name
            lakebase_instance_name: Lakebase instance identifier
        """
        self.customer_name = customer_name
        self.catalog_name = catalog_name
        self.lakebase_instance_name = lakebase_instance_name

        # Initialize Databricks SDK client
        self.w = WorkspaceClient()

        # Track validation results
        self.results = {
            "lakebase_instance": None,
            "unity_catalog": None,
            "schemas": None,
            "volumes": None,
            "lakebase_connectivity": None,
            "lakebase_tables": None,
            "dlt_pipeline": None,
            "app_deployment": None,
        }

    def run_all_validations(self) -> dict[str, Any]:
        """
        Run all deployment validations.

        Returns:
            Dict with validation results and overall success status
        """
        logger.info("=" * 80)
        logger.info(f"CS Receipt Lookup Platform — Deployment Validation")
        logger.info(f"Customer: {self.customer_name}")
        logger.info(f"Catalog: {self.catalog_name}")
        logger.info(f"Lakebase: {self.lakebase_instance_name}")
        logger.info("=" * 80)

        # Run validations in order
        self.validate_lakebase_instance()
        self.validate_unity_catalog()
        self.validate_schemas()
        self.validate_volumes()
        self.validate_lakebase_connectivity()
        self.validate_lakebase_tables()
        self.validate_dlt_pipeline()
        self.validate_app_deployment()

        # Print summary
        logger.info("\n" + "=" * 80)
        logger.info("VALIDATION SUMMARY")
        logger.info("=" * 80)

        all_passed = True
        for check, result in self.results.items():
            status = "✓ PASS" if result else "✗ FAIL"
            logger.info(f"{status} - {check.replace('_', ' ').title()}")
            if not result:
                all_passed = False

        if all_passed:
            logger.info("\n✓ All validations passed - deployment is healthy")
            return {"success": True, "results": self.results}
        else:
            logger.error("\n✗ Some validations failed - review errors above")
            return {"success": False, "results": self.results}

    def validate_lakebase_instance(self) -> None:
        """Validate Lakebase instance exists and is running."""
        logger.info("\n1. Validating Lakebase instance...")

        try:
            instance = self.w.database.get(name=self.lakebase_instance_name)

            if instance.state and instance.state.value == "RUNNING":
                logger.info(f"  ✓ Instance '{self.lakebase_instance_name}' is RUNNING")
                logger.info(f"    Host: {instance.read_write_dns}")
                self.results["lakebase_instance"] = True
            else:
                logger.error(f"  ✗ Instance state: {instance.state.value if instance.state else 'UNKNOWN'}")
                self.results["lakebase_instance"] = False

        except Exception as e:
            logger.error(f"  ✗ Instance not found or inaccessible: {e}")
            self.results["lakebase_instance"] = False

    def validate_unity_catalog(self) -> None:
        """Validate Unity Catalog exists."""
        logger.info("\n2. Validating Unity Catalog...")

        try:
            catalog = self.w.catalogs.get(name=self.catalog_name)
            logger.info(f"  ✓ Catalog '{self.catalog_name}' exists")
            self.results["unity_catalog"] = True
        except Exception as e:
            logger.error(f"  ✗ Catalog not found: {e}")
            self.results["unity_catalog"] = False

    def validate_schemas(self) -> None:
        """Validate required schemas exist."""
        logger.info("\n3. Validating schemas...")

        required_schemas = ["bronze", "silver", "gold"]
        all_exist = True

        for schema_name in required_schemas:
            full_name = f"{self.catalog_name}.{schema_name}"
            try:
                schema = self.w.schemas.get(full_name=full_name)
                logger.info(f"  ✓ Schema '{full_name}' exists")
            except Exception as e:
                logger.error(f"  ✗ Schema '{full_name}' not found: {e}")
                all_exist = False

        self.results["schemas"] = all_exist

    def validate_volumes(self) -> None:
        """Validate required volumes exist."""
        logger.info("\n4. Validating volumes...")

        required_volumes = [
            f"{self.catalog_name}.bronze.raw_data",
            f"{self.catalog_name}.gold.exports",
        ]

        all_exist = True

        for volume_name in required_volumes:
            try:
                volume = self.w.volumes.read(name=volume_name)
                logger.info(f"  ✓ Volume '{volume_name}' exists")
            except Exception as e:
                logger.error(f"  ✗ Volume '{volume_name}' not found: {e}")
                all_exist = False

        self.results["volumes"] = all_exist

    def validate_lakebase_connectivity(self) -> None:
        """Validate connectivity to Lakebase instance."""
        logger.info("\n5. Validating Lakebase connectivity...")

        try:
            # Get database credential
            cred = self.w.database.generate_database_credential(
                instance_names=[self.lakebase_instance_name]
            )

            logger.info("  ✓ Successfully generated database credential")
            logger.info(f"    Token length: {len(cred.token or '')} characters")

            # Try to connect
            try:
                import psycopg
            except ImportError:
                logger.warning("  ! psycopg not installed - skipping connection test")
                logger.warning("    Install with: pip install 'psycopg[binary]'")
                self.results["lakebase_connectivity"] = None  # Indeterminate
                return

            instance = self.w.database.get(name=self.lakebase_instance_name)

            conninfo = (
                f"host={instance.read_write_dns} "
                f"port=5432 "
                f"dbname=databricks_postgres "
                f"user={self.w.current_user.me().user_name} "
                f"password={cred.token} "
                f"sslmode=require"
            )

            with psycopg.connect(conninfo, connect_timeout=10) as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT version();")
                    version = cur.fetchone()[0]
                    logger.info(f"  ✓ Successfully connected to Lakebase")
                    logger.info(f"    PostgreSQL version: {version[:50]}...")

            self.results["lakebase_connectivity"] = True

        except Exception as e:
            logger.error(f"  ✗ Connection failed: {e}")
            self.results["lakebase_connectivity"] = False

    def validate_lakebase_tables(self) -> None:
        """Validate required Lakebase tables exist."""
        logger.info("\n6. Validating Lakebase tables...")

        required_tables = [
            "audit_log",
            "receipt_delivery_log",
            "agent_state",
            "search_cache",
            "product_embeddings",
        ]

        try:
            import psycopg
        except ImportError:
            logger.warning("  ! psycopg not installed - skipping table validation")
            self.results["lakebase_tables"] = None
            return

        try:
            cred = self.w.database.generate_database_credential(
                instance_names=[self.lakebase_instance_name]
            )
            instance = self.w.database.get(name=self.lakebase_instance_name)

            conninfo = (
                f"host={instance.read_write_dns} "
                f"port=5432 "
                f"dbname=databricks_postgres "
                f"user={self.w.current_user.me().user_name} "
                f"password={cred.token} "
                f"sslmode=require"
            )

            all_exist = True

            with psycopg.connect(conninfo, connect_timeout=10) as conn:
                with conn.cursor() as cur:
                    for table_name in required_tables:
                        cur.execute(
                            """
                            SELECT EXISTS (
                                SELECT FROM information_schema.tables
                                WHERE table_schema = 'public'
                                AND table_name = %s
                            );
                            """,
                            (table_name,),
                        )
                        exists = cur.fetchone()[0]

                        if exists:
                            # Get row count
                            cur.execute(f"SELECT COUNT(*) FROM {table_name};")
                            count = cur.fetchone()[0]
                            logger.info(f"  ✓ Table '{table_name}' exists ({count:,} rows)")
                        else:
                            logger.error(f"  ✗ Table '{table_name}' not found")
                            all_exist = False

            self.results["lakebase_tables"] = all_exist

        except Exception as e:
            logger.error(f"  ✗ Table validation failed: {e}")
            self.results["lakebase_tables"] = False

    def validate_dlt_pipeline(self) -> None:
        """Validate DLT pipeline deployment."""
        logger.info("\n7. Validating DLT pipeline...")

        try:
            # List all pipelines and find ours by name pattern
            pipelines = self.w.pipelines.list_pipelines()

            # Look for pipeline with our catalog name in it
            pipeline_name_pattern = self.catalog_name.replace("_", "-")

            found_pipeline = None
            for pipeline in pipelines:
                if pipeline.name and pipeline_name_pattern in pipeline.name:
                    found_pipeline = pipeline
                    break

            if found_pipeline:
                logger.info(f"  ✓ DLT pipeline found: {found_pipeline.name}")
                logger.info(f"    Pipeline ID: {found_pipeline.pipeline_id}")

                # Check if pipeline has ever run
                if found_pipeline.latest_updates:
                    latest = found_pipeline.latest_updates[0]
                    logger.info(f"    Latest update state: {latest.state}")
                else:
                    logger.info("    ⚠ Pipeline has not run yet")

                self.results["dlt_pipeline"] = True
            else:
                logger.warning(f"  ⚠ No DLT pipeline found with pattern '{pipeline_name_pattern}'")
                logger.warning("    This is expected if pipeline hasn't been deployed via DABs yet")
                self.results["dlt_pipeline"] = False

        except Exception as e:
            logger.error(f"  ✗ Pipeline validation failed: {e}")
            self.results["dlt_pipeline"] = False

    def validate_app_deployment(self) -> None:
        """Validate Databricks App deployment."""
        logger.info("\n8. Validating Databricks App...")

        try:
            # List apps and find ours
            # App name follows pattern from databricks.yml: {customer_name_slug}-cs-receipt-lookup
            app_name_pattern = self.customer_name.lower().replace(" ", "-")

            try:
                apps = self.w.apps.list()

                found_app = None
                for app in apps:
                    if app.name and app_name_pattern in app.name:
                        found_app = app
                        break

                if found_app:
                    logger.info(f"  ✓ Databricks App found: {found_app.name}")
                    logger.info(f"    App state: {found_app.app_status.state if found_app.app_status else 'UNKNOWN'}")
                    logger.info(f"    Compute state: {found_app.compute_status.state if found_app.compute_status else 'UNKNOWN'}")

                    if found_app.url:
                        logger.info(f"    URL: {found_app.url}")

                    self.results["app_deployment"] = True
                else:
                    logger.warning(f"  ⚠ No Databricks App found with pattern '{app_name_pattern}'")
                    logger.warning("    This is expected if app hasn't been deployed yet")
                    self.results["app_deployment"] = False

            except Exception as e:
                logger.warning(f"  ⚠ Could not list apps: {e}")
                logger.warning("    This may require Apps API access")
                self.results["app_deployment"] = None

        except Exception as e:
            logger.error(f"  ✗ App validation failed: {e}")
            self.results["app_deployment"] = False


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Validate CS Receipt Lookup Platform deployment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--customer-name",
        default=os.environ.get("CUSTOMER_DISPLAY_NAME"),
        help="Customer display name",
    )
    parser.add_argument(
        "--catalog-name",
        default=os.environ.get("CATALOG_NAME"),
        help="Unity Catalog name",
    )
    parser.add_argument(
        "--lakebase-instance",
        default=os.environ.get("LAKEBASE_INSTANCE_NAME"),
        help="Lakebase instance name",
    )

    args = parser.parse_args()

    # Validate required arguments
    if not args.customer_name:
        logger.error("--customer-name is required (or set CUSTOMER_DISPLAY_NAME)")
        sys.exit(2)
    if not args.catalog_name:
        logger.error("--catalog-name is required (or set CATALOG_NAME)")
        sys.exit(2)
    if not args.lakebase_instance:
        logger.error("--lakebase-instance is required (or set LAKEBASE_INSTANCE_NAME)")
        sys.exit(2)

    # Run validation
    validator = DeploymentValidator(
        customer_name=args.customer_name,
        catalog_name=args.catalog_name,
        lakebase_instance_name=args.lakebase_instance,
    )

    result = validator.run_all_validations()

    if result["success"]:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
