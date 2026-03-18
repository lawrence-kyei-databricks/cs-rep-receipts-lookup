# Troubleshooting Guide — CS Receipt Lookup Platform

## Overview
This document captures all critical issues encountered during deployment and their solutions. These fixes were validated on the production Giant Eagle deployment: https://giant-eagle-cs-receipt-lookup-984752964297111.11.azure.databricksapps.com

**Last Updated**: 2026-03-17
**Status**: Production-tested fixes

---

## Critical Issues & Fixes

### 0. Missing Database Schema (BLOCKER)

#### Issue
App fails on startup or during first request with:
```
relation "receipt_lookup" does not exist
relation "audit_log" does not exist
```

#### Root Cause
The Lakebase instance was created but the required tables were never created. The app expects:

**Native Tables** (created via SQL script):
- `audit_log` — CS rep activity tracking
- `receipt_transactions` — Direct POS writes
- `product_embeddings` — Vector search
- `agent_state`, `agent_memory` — AI agent persistence

**Synced Tables** (created via Databricks Synced Tables):
- `receipt_lookup` — Enriched receipts (from Delta gold layer)
- `receipt_line_items` — Line item details (from Delta gold layer)
- `customer_profiles` — Customer 360 data
- `product_catalog` — Product reference data

#### Solution

**Option A: Run the setup SQL script manually**

1. Connect to your Lakebase instance:
```bash
# Get OAuth token
TOKEN=$(databricks database generate-database-credential \
  --instance-names giant-eagle-receipt-db-v2 | jq -r .token)

# Get instance DNS
INSTANCE_DNS=$(databricks database get-database-instance \
  giant-eagle-receipt-db-v2 | jq -r .read_write_dns)

# Connect with psql
PGPASSWORD=$TOKEN psql \
  "host=$INSTANCE_DNS port=5432 dbname=databricks_postgres sslmode=require" \
  -f infra/lakebase_setup.sql
```

2. Verify tables were created:
```sql
-- List all tables
\dt

-- Should see: audit_log, receipt_transactions, product_embeddings, agent_state, etc.
```

**Option B: Use the Python setup script**

```bash
# From project root
python3 scripts/setup_infrastructure.py \
  --customer-name "Giant Eagle" \
  --catalog-name "giant_eagle" \
  --lakebase-instance "giant-eagle-receipt-db-v2"
```

This will:
- Create all native tables from `infra/lakebase_setup.sql`
- Set up synced tables (if Delta gold tables exist)
- Configure initial permissions

**Option C: Quick manual table creation**

If you just need the audit_log to unblock testing:

```sql
-- Connect to Lakebase first (see Option A)

-- Create audit_log table
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    rep_id              TEXT NOT NULL,
    rep_email           TEXT NOT NULL,
    rep_role            TEXT NOT NULL,
    action              TEXT NOT NULL,
    resource_type       TEXT NOT NULL,
    resource_id         TEXT,
    query_params        JSONB,
    result_count        INTEGER,
    ip_address          TEXT,
    user_agent          TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_rep ON audit_log(rep_id, created_at DESC);
CREATE INDEX idx_audit_ts ON audit_log(created_at DESC);
```

For testing without synced tables, create a mock `receipt_lookup` table:

```sql
-- TEMPORARY: For testing only, until Delta sync is configured
CREATE TABLE IF NOT EXISTS receipt_lookup (
    transaction_id      TEXT PRIMARY KEY,
    store_id            TEXT,
    store_name          TEXT,
    customer_id         TEXT,
    customer_name       TEXT,
    transaction_ts      TIMESTAMPTZ,
    transaction_date    DATE,
    subtotal_cents      BIGINT,
    tax_cents           BIGINT,
    total_cents         BIGINT,
    tender_type         TEXT,
    card_last4          TEXT,
    item_count          INTEGER,
    item_summary        TEXT,
    category_tags       TEXT[],
    items_detail        JSONB,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_receipt_lookup_customer ON receipt_lookup(customer_id, transaction_ts DESC);
CREATE INDEX idx_receipt_lookup_ts ON receipt_lookup(transaction_ts DESC);

-- Optional: Line items table
CREATE TABLE IF NOT EXISTS receipt_line_items (
    transaction_id      TEXT NOT NULL,
    line_number         INTEGER NOT NULL,
    sku                 TEXT,
    product_name        TEXT,
    brand               TEXT,
    category_l1         TEXT,
    category_l2         TEXT,
    quantity            NUMERIC,
    unit_price_cents    BIGINT,
    line_total_cents    BIGINT,
    discount_cents      BIGINT,
    PRIMARY KEY (transaction_id, line_number)
);

CREATE INDEX idx_line_items_txn ON receipt_line_items(transaction_id);
```

**Key Learnings**:
- Lakebase instance creation does NOT automatically create tables
- You must run SQL scripts or use setup tooling to create schema
- Native tables (audit_log) must be created before app starts
- Synced tables can be configured later, but app will fail without them
- Always verify schema exists before deploying the app
- Add table existence checks to app startup for better error messages

**Prevention for future deployments**:
1. Add schema validation to app startup (log missing tables)
2. Run `setup_infrastructure.py` as part of CI/CD pipeline
3. Document schema dependencies clearly in deployment guide
4. Consider adding a `/health/schema` endpoint that checks table existence

**CRITICAL**: After creating tables in an existing Lakebase instance, you MUST restart the Databricks App:
```bash
databricks apps stop <app-name>
databricks apps start <app-name>
```

The app's connection pool is created at startup and caches schema information. Without a restart, it won't see the newly created tables.

---

### 1. Lakebase Database Configuration

#### Issue
App failed to connect to Lakebase with error:
```
FATAL: database "acme_retail" does not exist
```

#### Root Cause
The PGDATABASE environment variable was set to a custom database name that didn't match the actual Lakebase database. Lakebase Provisioned creates a default database named `databricks_postgres`, not a customer-specific name.

#### Solution
**File**: `app/app.yaml`

```yaml
# WRONG - Don't use custom database names
env:
  - name: PGDATABASE
    value: "acme_retail"  # ❌ This database doesn't exist!

# CORRECT - Use the standard Lakebase database name
env:
  - name: PGDATABASE
    value: "databricks_postgres"  # ✅ Standard Lakebase database
```

**Key Learnings**:
- Lakebase Provisioned always creates `databricks_postgres` as the default database
- You can create additional databases, but must do so explicitly via SQL
- The catalog name (Unity Catalog) is separate from the PostgreSQL database name
- For most deployments, stick with `databricks_postgres`

---

### 2. psycopg Pool Installation Issues

#### Issue
App startup failed with:
```
ModuleNotFoundError: No module named 'psycopg_pool'
```

#### Root Cause
The package specifier `psycopg[binary,pool]` doesn't properly install the pool extra in all environments. The pool functionality is actually in a separate package.

#### Solution
**File**: `app/requirements.txt`

```python
# WRONG - Pool extra not reliably installed
psycopg[binary,pool]>=3.1.0

# CORRECT - Install packages separately
psycopg[binary]>=3.1.0
psycopg-pool>=3.1.0  # Separate package for connection pooling
```

**Key Learnings**:
- `psycopg-pool` is a separate package, not just an extra
- Always install `psycopg[binary]` for compiled performance
- The pool import is `from psycopg_pool import AsyncConnectionPool`

---

### 3. Python String Formatting Inconsistencies

#### Issue
NL Search Agent crashed with:
```
KeyError: 'customer_id'
```

#### Root Cause
Mixed use of f-strings and `.format()` caused Python to interpret `{customer_id}` as a format placeholder instead of a literal string in the prompt.

#### Solution
**File**: `app/nl_search_agent.py`

```python
# WRONG - f-string interprets {customer_id} immediately
SYSTEM_PROMPT = f"""You are a CS assistant for {CUSTOMER_DISPLAY_NAME}.
SQL query rules:
- If customer_id is provided, filter by customer_id = {customer_id}
"""
# This tries to substitute customer_id at module load time → KeyError!

# CORRECT - Use .format() with proper escaping
SYSTEM_PROMPT = """You are a CS assistant for {customer_display_name}.
SQL query rules:
- If customer_id is provided, filter by customer_id = {{customer_id}}
"""
# In code:
system_msg = SYSTEM_PROMPT.format(
    customer_display_name=CUSTOMER_DISPLAY_NAME,
    customer_id="{{customer_id}}"  # Escaped braces → literal {customer_id}
)
```

**Key Learnings**:
- Use `{{` and `}}` to escape braces in `.format()` strings
- Don't mix f-strings and `.format()` unless you understand evaluation order
- F-strings evaluate at definition time, `.format()` evaluates at call time
- For prompts with nested placeholders, use `.format()` exclusively

---

### 4. Database Column Name Mismatches

#### Issue
Audit log queries failed with:
```
column "timestamp" does not exist
HINT: Perhaps you meant to reference the column "audit_log.created_at"
```

#### Root Cause
Database schema uses `created_at` column, but code was querying `timestamp`.

#### Solution
**File**: `app/routes/audit.py`

```python
# WRONG - Column doesn't exist
WHERE timestamp >= %s::timestamptz
ORDER BY timestamp DESC

# CORRECT - Use actual column name
WHERE created_at >= %s::timestamptz
ORDER BY created_at DESC
```

**Key Learnings**:
- Always verify column names match the actual schema
- Use schema migration tools to keep code and DB in sync
- PostgreSQL error hints are helpful — read them carefully
- Test all query paths, not just the happy path

---

### 5. Receipt Line Items Fallback Logic

#### Issue
Receipts displayed without line items, showing only totals.

#### Root Cause
Some receipts had `items_detail` JSON but no rows in the `receipt_line_items` normalized table. The app only queried the normalized table and didn't fall back to JSON.

#### Solution
**File**: `app/routes/lookup.py`

```python
# Add fallback after primary query
if not line_items and first_row["items_detail"]:
    try:
        # Parse items_detail JSON as fallback
        items_json = json.loads(first_row["items_detail"]) if isinstance(first_row["items_detail"], str) else first_row["items_detail"]
        for item in items_json:
            line_items.append({
                "name": item.get("item", "Unknown Item"),
                "price_cents": item.get("total", 0),
                "quantity": float(item.get("qty", 1)),
            })
    except (json.JSONDecodeError, TypeError, KeyError):
        # Gracefully handle malformed JSON
        pass
```

**Key Learnings**:
- Always have fallback strategies for critical data
- JSON columns provide flexibility but need careful handling
- Graceful degradation > hard failures for UI components
- Log failures silently — don't crash the request

---

### 6. Resource Naming Consistency

#### Issue
Databricks App creation failed with:
```
Invalid app name: "giant_eagle-cs-receipt-lookup"
App names cannot contain underscores
```

#### Root Cause
Databricks Apps require hyphen-separated names (URL-safe), but we were using underscore-separated customer names from Unity Catalog conventions.

#### Solution
**File**: `databricks.yml`

```yaml
# Add separate variable for URL-safe names
variables:
  customer_name:
    default: "giant_eagle"  # For UC catalogs (underscores OK)

  customer_slug:
    default: "giant-eagle"  # For app names (hyphens only)

resources:
  apps:
    cs_receipt_lookup:
      name: ${var.customer_slug}-cs-receipt-lookup  # Use slug!
```

**Key Learnings**:
- Unity Catalog allows underscores, but URLs/app names need hyphens
- Create separate variables for different naming contexts
- Validate naming conventions before deployment
- Databricks Apps have strict naming requirements

---

### 7. Lakebase Instance Name Consistency

#### Issue
App failed to start with:
```
Lakebase instance "acme_retail-receipt-db" not found
```

#### Root Cause
Mismatch between `app.yaml` resource declaration and environment variable. The instance was created with one name but app.yaml referenced a different name.

#### Solution
**File**: `app/app.yaml`

```yaml
env:
  - name: LAKEBASE_INSTANCE_NAME
    value: "giant-eagle-receipt-db-v2"  # ✅ Must match resources!

resources:
  - name: lakebase-instance
    type: lakebase
    instance_name: "giant-eagle-receipt-db-v2"  # ✅ Must match env var!
```

**File**: `databricks.yml`

```yaml
variables:
  lakebase_instance_name:
    default: "${var.customer_name}-receipt-db"  # Template

targets:
  dev:
    variables:
      lakebase_instance_name: ${var.customer_name}-receipt-db-v2  # Actual
```

**Key Learnings**:
- All three locations must match: env var, resources, and databricks.yml
- Use variables to ensure consistency across files
- Add comments linking related values
- Consider using `-dev`, `-v2` suffixes for iteration

---

## UI/UX Fixes

### 8. Missing Quantity Display in Receipt Modal

#### Issue
Receipt items showed only name and price, making multi-quantity items unclear.

#### Solution
**Files**: `ui/src/components/ReceiptModal.jsx`, `ui/src/index.css`

```jsx
// Added quantity display
<div className="item-line">
  <span className="item-qty">{item.quantity || 1}x</span>
  <span className="item-name">{item.name}</span>
  <span className="item-price">{fmt$(item.price_cents)}</span>
</div>
```

---

### 9. Non-Clickable Transaction IDs in AI Search

#### Issue
AI agent returned transaction IDs (e.g., "TXN-2024-001") but users couldn't click them to view receipts.

#### Solution
**File**: `ui/src/pages/AISearch.jsx`

```jsx
// Added custom markdown renderer to make transaction IDs clickable
<ReactMarkdown
  components={{
    p: ({ children }) => {
      const processedChildren = React.Children.map(children, child => {
        if (typeof child === 'string') {
          const txnRegex = /(TXN?-[A-Za-z0-9\-]+)/g
          const parts = child.split(txnRegex)
          return parts.map((part, i) => {
            if (txnRegex.test(part)) {
              return (
                <button onClick={() => handleViewReceipt(part)}>
                  {part}
                </button>
              )
            }
            return part
          })
        }
        return child
      })
      return <p>{processedChildren}</p>
    }
  }}
>
  {result.answer}
</ReactMarkdown>
```

**Key Learnings**:
- ReactMarkdown supports custom component renderers
- Regex-based text transformation works well for inline links
- Always provide feedback loops (click → view receipt)

---

### 10. Incomplete Store List for Fuzzy Search

#### Issue
Giant Eagle has 20+ locations but dropdown only showed 4 test stores.

#### Solution
**File**: `ui/src/pages/FuzzySearch.jsx`

```jsx
// Added all actual store locations
const STORES = [
  '',
  'Bethel Park', 'Bloomfield', 'Cranberry', 'Downtown',
  'East Liberty', 'Greenfield', 'Highland Park', 'Homestead',
  'Lawrenceville', 'Monroeville', 'Mt. Lebanon', 'North Hills',
  'Oakland', 'Regent Square', 'Robinson', 'Ross Park',
  'Shadyside', 'Southside', 'Squirrel Hill', 'Waterfront'
]
```

---

## Deployment Checklist

Use this checklist for future customer deployments:

### Pre-Deployment Configuration

- [ ] Update `databricks.yml` variables:
  - [ ] `customer_name` (underscores, for Unity Catalog)
  - [ ] `customer_slug` (hyphens, for app name)
  - [ ] `customer_display_name` (spaces OK, for UI)
  - [ ] `lakebase_instance_name` (must match target environment)

- [ ] Update `app/app.yaml`:
  - [ ] `LAKEBASE_INSTANCE_NAME` matches `databricks.yml`
  - [ ] `PGDATABASE` set to `databricks_postgres`
  - [ ] `resources.instance_name` matches env var
  - [ ] `CUSTOMER_DISPLAY_NAME` matches `databricks.yml`

- [ ] Verify `app/requirements.txt`:
  - [ ] `psycopg[binary]>=3.1.0` (separate from pool)
  - [ ] `psycopg-pool>=3.1.0` (separate package)

### Deployment Validation

- [ ] Lakebase instance exists and is running
- [ ] Lakebase catalog created in Unity Catalog
- [ ] Database schema deployed (audit_log, receipt_lookup, etc.)
- [ ] App deployed successfully (`databricks bundle deploy`)
- [ ] App is accessible (check URL in deployment output)
- [ ] Connection pool initializes (check logs)
- [ ] All routes load (/, /lookup, /search, /admin)
- [ ] Fuzzy search works (queries database)
- [ ] AI search works (queries LLM + database)
- [ ] Receipt modal displays correctly
- [ ] Audit logging works

### Post-Deployment Testing

- [ ] Search for a known receipt (fuzzy search)
- [ ] Search using natural language (AI search)
- [ ] Click transaction ID in AI results (opens modal)
- [ ] View receipt details (items, totals, customer info)
- [ ] Check audit log (admin panel)
- [ ] Test with multiple users (RBAC)
- [ ] Monitor Lakebase connection pool health
- [ ] Check logs for any errors/warnings

---

## Common Error Patterns

### Database Connection Errors

```
FATAL: database "X" does not exist
```
→ Check `PGDATABASE` in app.yaml (should be `databricks_postgres`)

```
could not connect to server: Connection refused
```
→ Check Lakebase instance is running: `databricks database list-database-instances`

```
no pg_hba.conf entry for host
```
→ Check PGHOST is using OAuth token, not password auth

### Python Runtime Errors

```
ModuleNotFoundError: No module named 'psycopg_pool'
```
→ Add `psycopg-pool>=3.1.0` to requirements.txt

```
KeyError: 'customer_id' (in nl_search_agent.py)
```
→ Check string formatting (use `{{customer_id}}` for literal braces)

```
column "timestamp" does not exist
```
→ Use correct column name `created_at` in queries

### Deployment Errors

```
Invalid app name: cannot contain underscores
```
→ Use `customer_slug` (hyphens) in app name, not `customer_name`

```
Lakebase instance "X" not found
```
→ Ensure instance name matches across app.yaml, databricks.yml, and resources

---

## Performance Optimization Tips

### Connection Pool Tuning

Default settings work for most deployments:
```python
min_size=5,      # Minimum idle connections
max_size=20,     # Maximum total connections
max_idle=600.0,  # 10 minutes idle timeout
```

Adjust based on:
- **Low traffic** (< 10 concurrent users): `min_size=2, max_size=10`
- **High traffic** (50+ concurrent users): `min_size=10, max_size=50`
- **Burst traffic**: Increase `max_size`, keep `min_size` low

### Query Optimization

1. **Add indexes** on frequently queried columns:
   - `receipt_lookup(customer_id, transaction_date)`
   - `receipt_lookup(transaction_ts)` for time-based queries
   - `receipt_line_items(transaction_id)` for JOIN performance

2. **Use prepared statements** for repeated queries (psycopg does this automatically)

3. **Cache frequent queries** (receipts, customer context) in Redis/in-memory

### Lakebase Scaling

Monitor these metrics:
- **Connection count**: Should stay below 80% of max_size
- **Query latency**: p95 should be < 100ms for simple queries
- **CPU usage**: If consistently > 70%, upgrade capacity

Capacity guidelines:
- **CU_1**: Development/testing (< 5 users)
- **CU_2**: Small teams (5-20 users) ✅ Current setup
- **CU_4**: Medium teams (20-50 users)
- **CU_8**: Large teams (50+ users) or heavy queries

---

## Support & Debugging

### Viewing App Logs

```bash
# Real-time logs
databricks apps logs <app-name> --follow

# Last 100 lines
databricks apps logs <app-name> --tail 100
```

### Connecting to Lakebase for Debugging

```bash
# Get OAuth token
TOKEN=$(databricks database generate-database-credential \
  --instance-names giant-eagle-receipt-db-v2 | jq -r .token)

# Connect with psql
PGPASSWORD=$TOKEN psql \
  "host=<instance>.database.azuredatabricks.net \
   port=5432 dbname=databricks_postgres \
   user=<service-principal-id> sslmode=require"
```

### Health Check Endpoints

- `GET /health` — Basic health check
- `GET /debug/dbinfo` — Database connection status
- `GET /admin` — Admin panel (audit logs, stats)

---

## Related Documentation

- [ARCHITECTURE.md](./ARCHITECTURE.md) — System design and data flow
- [PRE_DEPLOYMENT_VERIFICATION.md](./PRE_DEPLOYMENT_VERIFICATION.md) — Deployment checklist
- [MIGRATION_UC_RBAC.md](./MIGRATION_UC_RBAC.md) — Unity Catalog setup

---

## Change Log

| Date | Issue | Fix | Validated |
|------|-------|-----|-----------|
| 2026-03-17 | PGDATABASE wrong | Changed to `databricks_postgres` | ✅ Giant Eagle prod |
| 2026-03-17 | psycopg-pool missing | Split into separate package | ✅ Giant Eagle prod |
| 2026-03-17 | String format KeyError | Fixed {{customer_id}} escaping | ✅ Giant Eagle prod |
| 2026-03-17 | timestamp column error | Changed to created_at | ✅ Giant Eagle prod |
| 2026-03-17 | Missing line items | Added items_detail fallback | ✅ Giant Eagle prod |
| 2026-03-17 | App name validation | Added customer_slug variable | ✅ Giant Eagle prod |
| 2026-03-17 | UI: No quantity | Added qty display in modal | ✅ Giant Eagle prod |
| 2026-03-17 | UI: TXN not clickable | Made transaction IDs clickable | ✅ Giant Eagle prod |

---

**Production Deployment**: https://giant-eagle-cs-receipt-lookup-984752964297111.11.azure.databricksapps.com
