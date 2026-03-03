# CS Receipt Lookup Platform — Deployment Guide

Production-ready, customer-agnostic receipt lookup solution for retail Customer Service teams, built on Databricks.

## Table of Contents
- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Deployment Steps](#deployment-steps)
- [Post-Deployment Setup](#post-deployment-setup)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)
- [Customization](#customization)

---

## Overview

This solution provides an AI-powered receipt lookup application for Customer Service representatives. It enables fuzzy search, semantic search, and customer context lookups with sub-10ms response times.

**Key Features:**
- Delta Lakehouse as source of truth with cross-region disaster recovery
- Lakebase (managed PostgreSQL) for sub-10ms serving
- Mosaic AI for semantic search and natural language queries
- Automated data synchronization via DLT pipelines and synced tables
- Production-grade security with Azure AD/Entra ID RBAC
- Full audit trail for compliance

**Designed For:** Retail companies needing Customer Service receipt lookup capabilities

---

## Architecture

```
POS Systems → Dual Path:
  1. gRPC → Zerobus → Delta (Bronze → Silver → Gold) → Synced Tables → Lakebase
  2. JDBC → Lakebase (native tables for instant writes)

Lakebase → Databricks App → CS Reps
Delta Gold → Mosaic AI → Embeddings → Lakebase pgvector
```

**Data Flow:**
- **Source of Truth:** Delta on ADLS (cross-region DR via RA-GRS)
- **Serving Layer:** Lakebase (sub-10ms reads, multi-zone HA)
- **AI Layer:** Mosaic AI (semantic search, NL→SQL queries)

---

## Prerequisites

### Databricks Workspace Requirements
- **Databricks Runtime:** 15.x or higher
- **Required Features:**
  - Unity Catalog enabled
  - Lakebase Provisioned access (public preview)
  - Delta Live Tables enabled
  - Mosaic AI Foundation Models enabled
  - Databricks Apps enabled

### Azure/Cloud Requirements
- **Azure:** Azure AD/Entra ID tenant (for SSO)
- **Storage:** ADLS Gen2 with RA-GRS (for cross-region DR)
- **Network:** Optional - Azure Private Link for production

### Databricks CLI & SDK
```bash
# Install Databricks CLI
pip install databricks-cli

# Configure authentication
databricks configure --token

# Install Databricks SDK (for custom scripts)
pip install databricks-sdk
```

### Permissions Required
- Workspace admin (for initial setup)
- Unity Catalog admin (for catalog/schema creation)
- Lakebase instance creator
- Cluster create permissions

---

## Quick Start

### 1. Clone Repository
```bash
git clone <your-repo-url>
cd receipts_lookup
```

### 2. Configure Customer Variables
Edit `databricks.yml` and set your customer-specific values:

```yaml
variables:
  customer_name:
    default: "your_company"  # e.g., "kroger", "target_stores"

  customer_display_name:
    default: "Your Company"  # e.g., "Kroger", "Target Stores"

  # Optional: Override other defaults as needed
  lakebase_capacity:
    default: "CU_4"  # CU_1, CU_2, CU_4, or CU_8
```

### 3. Deploy Bundle
```bash
# Validate configuration
databricks bundle validate

# Deploy to dev environment (default)
databricks bundle deploy

# Deploy to production
databricks bundle deploy -t prod
```

### 4. Initialize Infrastructure
```bash
# Run one-time setup script (to be created)
python3 scripts/init_deployment.py --customer your_company
```

---

## Configuration

### Environment Variables (databricks.yml)

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `customer_name` | Customer identifier (lowercase, underscores only) | `acme_retail` | Yes |
| `customer_display_name` | Display name for UI | `Acme Retail` | Yes |
| `catalog` | Unity Catalog name | `${var.customer_name}` | Yes |
| `lakebase_catalog` | Lakebase serving catalog | `${var.customer_name}_serving` | Yes |
| `lakebase_instance_name` | Lakebase instance name | `${var.customer_name}-receipt-db` | Yes |
| `lakebase_capacity` | Compute capacity | `CU_2` | No |
| `embedding_endpoint` | Embedding model endpoint | `databricks-bge-large-en` | No |
| `llm_endpoint` | LLM endpoint | `databricks-claude-sonnet-4` | No |
| `smtp_from_address` | Email sender address | `receipts@${var.customer_name}.com` | No |
| `deployment_env` | Environment (dev/staging/prod) | `dev` | No |
| `cost_center` | Cost center for billing | `cs-operations` | No |

### Target Environments

Three deployment targets are pre-configured:

**Development (default):**
```bash
databricks bundle deploy
# or explicitly:
databricks bundle deploy -t dev
```
- User-scoped workspace path
- Development mode enabled
- Instance name: `${customer_name}-receipt-db-dev`

**Staging:**
```bash
databricks bundle deploy -t staging
```
- Shared workspace path: `/Workspace/${customer_name}/cs-receipt-lookup/staging`
- Production mode
- Instance name: `${customer_name}-receipt-db-stg`

**Production:**
```bash
databricks bundle deploy -t prod
```
- Shared workspace path: `/Workspace/${customer_name}/cs-receipt-lookup/prod`
- Production mode
- Instance name: `${customer_name}-receipt-db-prod`

---

## Deployment Steps

### Step 1: Pre-Deployment Validation

Check that all prerequisites are met:
```bash
# Verify Databricks CLI is configured
databricks workspace ls /

# Verify Unity Catalog access
databricks catalogs list

# Verify bundle configuration
databricks bundle validate
```

### Step 2: Deploy Infrastructure Resources

```bash
# Deploy Databricks Asset Bundle
databricks bundle deploy -t dev
```

This creates:
- ✅ Unity Catalog schemas (bronze, silver, gold)
- ✅ DLT pipelines (medallion architecture)
- ✅ Scheduled jobs (embedding pipeline)
- ✅ Databricks App (CS receipt lookup UI)
- ✅ Workspace directories

### Step 3: Create Lakebase Instance

The Lakebase instance must be created separately (not currently supported in DABs):

```python
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

# Create Lakebase Provisioned instance
w.lakebase_provisioned.create(
    name="your_company-receipt-db-dev",
    capacity="CU_2",
    stopped=False
)

# Monitor creation status (takes 10-15 minutes)
instance = w.lakebase_provisioned.get(name="your_company-receipt-db-dev")
print(f"Status: {instance.state}")  # Wait for RUNNING
```

Or use the MCP Databricks tool:
```bash
# Create instance
mcp databricks create_lakebase_instance \
  --name your_company-receipt-db-dev \
  --capacity CU_2

# Check status
mcp databricks get_lakebase_instance \
  --name your_company-receipt-db-dev
```

### Step 4: Initialize Lakebase Schema

Once the instance is RUNNING, create the database schema:

```bash
# Generate Lakebase credential (valid 1 hour)
python3 scripts/init_lakebase_schema.py --customer your_company
```

This creates:
- `receipt_lookup` table (synced from Delta)
- `product_embeddings` table (pgvector for semantic search)
- `audit_log` table (compliance tracking)
- `user_sessions` table (CS rep session tracking)
- Required indexes for sub-10ms queries

### Step 5: Create Unity Catalog Registration

Register the Lakebase instance as a Unity Catalog foreign catalog:

```python
w.lakebase_provisioned.create_catalog(
    name="your_company_serving",
    instance_name="your_company-receipt-db-dev",
    database_name="databricks_postgres"
)
```

### Step 6: Configure Synced Tables

Create synced tables to automatically sync Delta Gold → Lakebase:

```python
# Wait for Gold tables to be created by DLT pipelines
# Then create synced tables

w.online_tables.create(
    name=f"{customer_name}_serving.public.receipt_lookup",
    spec={
        "source_table_full_name": f"{customer_name}.gold.receipt_lookup",
        "run_triggered": {"triggered": True}
    }
)
```

### Step 7: Run DLT Pipelines

Trigger initial pipeline runs to populate data:

```bash
# Start bronze pipeline
databricks pipelines start-update \
  --pipeline-name your_company-receipt-bronze-dev \
  --full-refresh

# Start silver pipeline (after bronze completes)
databricks pipelines start-update \
  --pipeline-name your_company-receipt-silver-dev \
  --full-refresh

# Start gold pipeline (after silver completes)
databricks pipelines start-update \
  --pipeline-name your_company-receipt-gold-dev \
  --full-refresh
```

### Step 8: Start Databricks App

```bash
databricks apps start your_company-cs-receipt-lookup
```

Wait 2-3 minutes for app to become RUNNING, then access via the Databricks UI.

---

## Post-Deployment Setup

### Configure Azure AD SSO

1. Create Azure AD App Registration
2. Configure redirect URIs for Databricks App
3. Set environment variables in app:
   - `AZURE_TENANT_ID`
   - `AZURE_CLIENT_ID`
   - `AZURE_CLIENT_SECRET`

### Set Up SMTP for Receipt Delivery

Configure email settings:
```bash
# Set in app environment variables
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_FROM=receipts@your-company.com
```

### Configure RBAC Roles

Assign Unity Catalog permissions:
```sql
-- CS reps
GRANT SELECT ON CATALOG your_company_serving TO `cs_reps_group`;
GRANT INSERT ON TABLE your_company_serving.public.audit_log TO `cs_reps_group`;

-- Supervisors
GRANT ALL PRIVILEGES ON CATALOG your_company_serving TO `supervisors_group`;

-- Fraud team
GRANT ALL PRIVILEGES ON CATALOG your_company TO `fraud_team_group`;
```

---

## Verification

### Verify Data Flow

```sql
-- Check Bronze layer
SELECT COUNT(*) FROM your_company.bronze.pos_raw_receipts;

-- Check Silver layer
SELECT COUNT(*) FROM your_company.silver.receipts_clean;

-- Check Gold layer
SELECT COUNT(*) FROM your_company.gold.receipt_lookup;

-- Check Lakebase synced table
SELECT COUNT(*) FROM your_company_serving.public.receipt_lookup;
```

### Verify App Status

```bash
databricks apps get your_company-cs-receipt-lookup
```

Expected output:
```json
{
  "name": "your_company-cs-receipt-lookup",
  "app_status": {
    "state": "RUNNING"
  },
  "compute_status": {
    "state": "ACTIVE"
  }
}
```

### Test Receipt Lookup

```bash
curl -X GET \
  "https://<workspace-url>/apps/your_company-cs-receipt-lookup/receipt/TXN-001" \
  -H "Authorization: Bearer <token>"
```

---

## Troubleshooting

### Lakebase Instance Stuck in CREATING

**Symptom:** Instance status shows CREATING for >20 minutes

**Solution:**
```bash
# Check instance details for error messages
databricks lakebase get --name your_company-receipt-db-dev
```

Common causes:
- Insufficient capacity in region (try different capacity tier)
- Quota limits (contact Databricks support)

### DLT Pipeline Failures

**Symptom:** Pipeline fails during data processing

**Solution:**
```bash
# Get pipeline events
databricks pipelines get-events \
  --pipeline-name your_company-receipt-bronze-dev
```

Common causes:
- Schema mismatch (check source data format)
- Missing Unity Catalog permissions
- Invalid SQL in pipeline definitions

### App Won't Start

**Symptom:** App stuck in DEPLOYING or ERROR state

**Solution:**
```bash
# Check app deployment logs
databricks apps list-deployments your_company-cs-receipt-lookup

# Check app logs
databricks apps logs your_company-cs-receipt-lookup
```

Common causes:
- Missing environment variables
- Incorrect source code path
- Dependencies not installed (check app.yaml)

### Synced Table Not Syncing

**Symptom:** Data in Lakebase is stale or empty

**Solution:**
```python
# Check sync status
sync_status = w.online_tables.get(
    name="your_company_serving.public.receipt_lookup"
)
print(sync_status.status.provisioning_status.state)

# Trigger manual sync
w.online_tables.refresh(
    name="your_company_serving.public.receipt_lookup"
)
```

---

## Customization

### Change Lakebase Capacity

Update `databricks.yml`:
```yaml
variables:
  lakebase_capacity:
    default: "CU_4"  # Scale up
```

Then resize instance:
```python
w.lakebase_provisioned.update(
    name="your_company-receipt-db-dev",
    capacity="CU_4"
)
```

### Change Embedding Model

Update `databricks.yml`:
```yaml
variables:
  embedding_endpoint:
    default: "your-custom-embedding-model"
```

Redeploy:
```bash
databricks bundle deploy
```

### Add Custom Environment Variables

Edit `app/app.yaml`:
```yaml
env:
  - name: CUSTOM_FEATURE_FLAG
    value: "enabled"
  - name: MAX_SEARCH_RESULTS
    value: "100"
```

---

## Next Steps

After successful deployment:

1. **Load Historical Data:** Use the migration scripts in `scripts/` to load existing receipt data
2. **Configure Monitoring:** Set up Databricks SQL alerts for pipeline failures
3. **Train CS Reps:** Provide training on the receipt lookup UI
4. **Set Up DR:** Configure secondary workspace in alternate region

For more information, see:
- [Architecture Documentation](./docs/architecture.md)
- [API Reference](./docs/api.md)
- [Troubleshooting Guide](./docs/troubleshooting.md)

---

## Support

For issues or questions:
- Check existing GitHub Issues
- Contact Databricks Support for infrastructure issues
- Review Databricks documentation: https://docs.databricks.com/

**Deployment Status Checklist:**
- [ ] DAB deployed successfully
- [ ] Lakebase instance RUNNING
- [ ] Unity Catalog registration complete
- [ ] DLT pipelines completed initial runs
- [ ] Synced tables active and syncing
- [ ] Databricks App RUNNING
- [ ] Azure AD SSO configured
- [ ] RBAC permissions assigned
- [ ] Data verification passed
- [ ] End-to-end test successful
