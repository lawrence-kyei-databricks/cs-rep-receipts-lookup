# Example Deployment: Acme Retail

This document shows a concrete example of deploying the CS Receipt Lookup Platform for a fictional customer: **Acme Retail**.

## Deployment Configuration

### databricks.yml Configuration

```yaml
bundle:
  name: acme-retail-receipt-lookup

variables:
  # Customer Identity
  customer_display_name:
    description: Customer display name for UI/logs
    default: "Acme Retail"

  customer_name_slug:
    description: URL-safe customer name
    default: "acme-retail"

  # Data Layer
  catalog_name:
    description: Unity Catalog name
    default: "acme_retail"

  # Serving Layer
  lakebase_instance_name:
    description: Lakebase instance identifier
    default: "acme-retail-receipt-db"

  lakebase_catalog_name:
    description: UC catalog for Lakebase connection
    default: "acme_retail_serving"

  # AI Layer
  embedding_model_endpoint:
    description: Foundation Model for embeddings
    default: "databricks-bge-large-en"

  llm_model_endpoint:
    description: LLM endpoint for NL search
    default: "databricks-claude-opus-4-6"

  # Compute
  dlt_compute_cluster:
    description: DLT pipeline cluster size
    default: "Small"

  embedding_job_cluster:
    description: Cluster size for embedding job
    default: "Medium"

  app_compute_spec:
    description: App compute specification
    default: "SMALL"

  # Network/Storage
  workspace_storage_path:
    description: DBFS/Unity Catalog path for workspace files
    default: "/Workspace/Users/${workspace.current_user.userName}/acme-retail-receipts"
```

### Environment Variables for Scripts

```bash
# Infrastructure setup
export CUSTOMER_DISPLAY_NAME="Acme Retail"
export CATALOG_NAME="acme_retail"
export LAKEBASE_INSTANCE_NAME="acme-retail-receipt-db"
export LAKEBASE_CAPACITY="CU_2"

# Application runtime (set in app.yaml, shown here for reference)
export CUSTOMER_DISPLAY_NAME="Acme Retail"
export CATALOG_NAME="acme_retail"
export LAKEBASE_INSTANCE_NAME="acme-retail-receipt-db"
export LAKEBASE_CATALOG_NAME="acme_retail_serving"
export EMBEDDING_MODEL_ENDPOINT="databricks-bge-large-en"
export LLM_MODEL_ENDPOINT="databricks-claude-opus-4-6"
```

## Step-by-Step Deployment

### Step 1: Infrastructure Setup

```bash
# Run infrastructure setup script
python3 scripts/setup_infrastructure.py \
    --customer-name "Acme Retail" \
    --catalog-name "acme_retail" \
    --lakebase-instance "acme-retail-receipt-db" \
    --lakebase-capacity "CU_2"
```

**Expected Resources Created**:
- Lakebase instance: `acme-retail-receipt-db` (CU_2, ~10 min to provision)
- Unity Catalog: `acme_retail`
- Schemas: `acme_retail.bronze`, `acme_retail.silver`, `acme_retail.gold`
- Volumes: `acme_retail.bronze.raw_data`, `acme_retail.gold.exports`
- Lakebase tables:
  - Native: `audit_log`, `receipt_delivery_log`, `agent_state`, `search_cache`
  - AI: `product_embeddings` (with pgvector + HNSW index)

### Step 2: Deploy DAB Resources

```bash
# Ensure you're in the project directory
cd /path/to/receipts_lookup

# Deploy all DAB resources
databricks bundle deploy --target prod
```

**Expected Resources Created**:
- DLT Pipeline: `acme-retail-receipt-lookup-pipeline`
  - Bronze tables: `acme_retail.bronze.raw_receipts`, `acme_retail.bronze.raw_products`
  - Silver tables: `acme_retail.silver.receipts_enriched`, `acme_retail.silver.products_cleaned`
  - Gold tables: `acme_retail.gold.receipt_lookup`, `acme_retail.gold.customer_profiles`, `acme_retail.gold.spending_summary`, `acme_retail.gold.product_catalog`

- Synced Tables (Lakebase):
  - `acme_retail_serving.public.receipt_lookup` (from `acme_retail.gold.receipt_lookup`)
  - `acme_retail_serving.public.customer_profiles` (from `acme_retail.gold.customer_profiles`)
  - `acme_retail_serving.public.spending_summary` (from `acme_retail.gold.spending_summary`)

- Databricks Workflow: `acme-retail-embedding-job` (nightly product embeddings)

- Databricks App: `acme-retail-cs-receipt-lookup`

### Step 3: Validate Deployment

```bash
# Run validation script
python3 scripts/validate_deployment.py \
    --customer-name "Acme Retail" \
    --catalog-name "acme_retail" \
    --lakebase-instance "acme-retail-receipt-db"
```

**Expected Validation Output**:
```
================================================================================
CS Receipt Lookup Platform — Deployment Validation
Customer: Acme Retail
Catalog: acme_retail
Lakebase: acme-retail-receipt-db
================================================================================

1. Validating Lakebase instance...
  ✓ Instance 'acme-retail-receipt-db' is RUNNING
    Host: instance-abc123.database.azuredatabricks.net

2. Validating Unity Catalog...
  ✓ Catalog 'acme_retail' exists

3. Validating schemas...
  ✓ Schema 'acme_retail.bronze' exists
  ✓ Schema 'acme_retail.silver' exists
  ✓ Schema 'acme_retail.gold' exists

4. Validating volumes...
  ✓ Volume 'acme_retail.bronze.raw_data' exists
  ✓ Volume 'acme_retail.gold.exports' exists

5. Validating Lakebase connectivity...
  ✓ Successfully generated database credential
  ✓ Successfully connected to Lakebase

6. Validating Lakebase tables...
  ✓ Table 'audit_log' exists (0 rows)
  ✓ Table 'receipt_delivery_log' exists (0 rows)
  ✓ Table 'agent_state' exists (0 rows)
  ✓ Table 'search_cache' exists (0 rows)
  ✓ Table 'product_embeddings' exists (0 rows)

7. Validating DLT pipeline...
  ✓ DLT pipeline found: acme-retail-receipt-lookup-pipeline

8. Validating Databricks App...
  ✓ Databricks App found: acme-retail-cs-receipt-lookup
    App state: RUNNING

================================================================================
VALIDATION SUMMARY
================================================================================
✓ PASS - Lakebase Instance
✓ PASS - Unity Catalog
✓ PASS - Schemas
✓ PASS - Volumes
✓ PASS - Lakebase Connectivity
✓ PASS - Lakebase Tables
✓ PASS - Dlt Pipeline
✓ PASS - App Deployment

✓ All validations passed - deployment is healthy
```

## Application Behavior Verification

### UI/UX Customization

Once deployed, verify that the application correctly displays "Acme Retail" branding:

1. **App Home Page**: Should show "Acme Retail CS Receipt Lookup" as the title
2. **Search Interface**: Placeholders and help text reference "Acme Retail"
3. **Customer Context Cards**: AI summaries say "Acme Retail customer service rep"
4. **Genie Space**: Created as "Acme Retail CS Receipt Genie"
5. **Audit Logs**: All log entries show `customer="Acme Retail"`

### Database Naming

1. **Unity Catalog Tables**:
   - `acme_retail.bronze.raw_receipts`
   - `acme_retail.silver.receipts_enriched`
   - `acme_retail.gold.receipt_lookup`

2. **Lakebase Tables**:
   - `acme_retail_serving.public.receipt_lookup` (synced)
   - `public.audit_log` (native)
   - `public.product_embeddings` (AI/pgvector)

3. **DLT Pipeline Name**: `acme-retail-receipt-lookup-pipeline`

4. **App Name**: `acme-retail-cs-receipt-lookup`

5. **Embedding Job Name**: `acme-retail-embedding-job`

### AI Agent Customization

Test that AI agents use customer-specific context:

**NL Search Agent System Prompt** (inspect via logs):
```
You are an internal CS (customer service) search assistant for Acme Retail.
A CS rep is trying to find a customer's receipt based on what the customer
described over the phone.
```

**CS Context Agent Briefing** (test with customer lookup):
```
You are a CS support tool. Generate a 2-3 sentence customer briefing for an
Acme Retail customer service rep who just pulled up this customer's profile.
```

## Data Flow Verification

### 1. POS Receipt Ingestion
```
POS System → Dual Write:
  1. Zerobus → acme_retail.bronze.raw_receipts
  2. JDBC → acme_retail_serving.public.receipt_transactions
```

### 2. DLT Pipeline Processing
```
Bronze (acme_retail.bronze.raw_receipts)
  ↓
Silver (acme_retail.silver.receipts_enriched)
  ↓
Gold (acme_retail.gold.receipt_lookup)
  ↓
Synced Table (acme_retail_serving.public.receipt_lookup)
```

### 3. Embedding Pipeline
```
acme_retail.gold.product_catalog
  ↓ (nightly job: acme-retail-embedding-job)
Foundation Model API (databricks-bge-large-en)
  ↓
acme_retail_serving.public.product_embeddings (pgvector)
```

### 4. CS Rep Lookup Flow
```
CS Rep → Databricks App (acme-retail-cs-receipt-lookup)
  ↓
FastAPI routes read from:
  - acme_retail_serving.public.receipt_lookup (sub-10ms)
  - acme_retail_serving.public.customer_profiles (sub-10ms)
  - acme_retail_serving.public.product_embeddings (semantic search)
  ↓
Results returned + audit logged to public.audit_log
```

## Configuration Differences from Giant Eagle

| Component | Giant Eagle | Acme Retail |
|-----------|-------------|-------------|
| Display Name | "Giant Eagle" | "Acme Retail" |
| Catalog | `giant_eagle` | `acme_retail` |
| Lakebase Instance | `giant-eagle-receipt-db` | `acme-retail-receipt-db` |
| Pipeline Name | `giant-eagle-receipt-lookup-pipeline` | `acme-retail-receipt-lookup-pipeline` |
| App Name | `giant-eagle-cs-receipt-lookup` | `acme-retail-cs-receipt-lookup` |
| Genie Space | "Giant Eagle CS Receipt Genie" | "Acme Retail CS Receipt Genie" |

**Code Changes Required**: **ZERO** — All differences handled via configuration variables.

## Testing Checklist

- [ ] Infrastructure setup completes without errors
- [ ] All resources created with correct naming (acme_retail, acme-retail prefixes)
- [ ] Validation script passes all 8 checks
- [ ] DLT pipeline runs successfully
- [ ] Synced tables populate from Gold layer
- [ ] Embedding job generates product_embeddings
- [ ] App starts and shows "Acme Retail" branding
- [ ] Receipt lookup works (test with sample data)
- [ ] Semantic search works via product_embeddings
- [ ] NL search agent responds with "Acme Retail" context
- [ ] CS context agent generates "Acme Retail customer service rep" briefings
- [ ] Genie space created as "Acme Retail CS Receipt Genie"
- [ ] Audit logs capture all actions with correct customer name
- [ ] RBAC enforced (cs_rep, supervisor, fraud_team roles)

## Rollback Procedure

If deployment fails or needs to be removed:

```bash
# 1. Delete Databricks App
databricks apps delete acme-retail-cs-receipt-lookup

# 2. Delete DLT Pipeline
databricks pipelines delete <pipeline-id>

# 3. Delete Databricks Workflow
databricks jobs delete <job-id>

# 4. Delete Synced Tables (via Unity Catalog)
# USE SQL or SDK to drop synced tables

# 5. Delete Lakebase Instance
databricks database delete acme-retail-receipt-db --purge

# 6. Delete Unity Catalog (CAUTION: deletes all data)
databricks catalogs delete acme_retail --force
```

## Cost Estimate (Azure East US 2)

**Monthly recurring costs for Acme Retail deployment:**

- Lakebase CU_2 instance: ~$1,200/month (24/7 running)
- DLT pipeline (Small cluster, daily runs): ~$150/month
- Embedding job (Medium cluster, nightly): ~$100/month
- Databricks App (SMALL compute): ~$200/month
- Foundation Model API calls:
  - BGE embeddings: ~$50/month (1M tokens)
  - Claude Opus NL search: ~$300/month (100K req/month)
- ADLS storage (1TB): ~$20/month

**Total: ~$2,020/month** for full production deployment

**Cost optimization options:**
- Stop Lakebase when not in use (nights/weekends): Save ~50%
- Use smaller app compute: Save ~$100/month
- Reduce embedding frequency (weekly vs nightly): Save ~$75/month
- Use Claude Sonnet instead of Opus: Save ~70% on LLM costs

## Production Readiness

Before going live with Acme Retail:

1. **Security**:
   - [ ] Configure Azure AD groups (cs_rep, supervisor, fraud_team)
   - [ ] Apply Unity Catalog row filters and column masks
   - [ ] Enable Private Link for Lakebase
   - [ ] Configure audit log retention policy

2. **Performance**:
   - [ ] Run load test (simulate 50 concurrent CS reps)
   - [ ] Verify sub-10ms Lakebase query latency
   - [ ] Test semantic search with 100K+ products

3. **Reliability**:
   - [ ] Set up secondary region DR (see dr/terraform/)
   - [ ] Configure ADLS RA-GRS replication
   - [ ] Test failover procedure

4. **Operations**:
   - [ ] Set up monitoring dashboards
   - [ ] Configure alert rules (pipeline failures, app downtime)
   - [ ] Document runbook for CS team
   - [ ] Train CS reps on new interface

## Support Contacts

- **Deployment Issues**: Reference DEPLOYMENT.md and scripts/README.md
- **Validation Failures**: Run `python3 scripts/validate_deployment.py` for diagnostics
- **Configuration Questions**: See databricks.yml variable descriptions
