# Giant Eagle — CS Receipt Lookup: Implementation Plan (v2)
> Internal Customer Service Tool · Azure Databricks · Lakebase · Mosaic AI

---

## What Changed (v1 → v2)

The use case shifted from a **consumer-facing app** to an **internal CS (Customer Service) tool** used by store reps, supervisors, and fraud teams.

| | v1 (Consumer) | v2 (CS Tool) |
|---|---|---|
| **Users** | Customers (self-service) | CS reps, supervisors, fraud team |
| **Auth** | Consumer OAuth | Azure AD SSO + RBAC |
| **Most-used feature** | Receipt lookup | Fuzzy multi-field search |
| **AI features** | NL search, Spending insights, Smart Reorder | NL search, CS Context Card |
| **Removed** | — | Smart Reorder, /reorder endpoints |
| **Added** | — | Fuzzy search, Audit trail, Receipt delivery, CS Context card |
| **Compliance** | None | Every action logged to audit_log (non-negotiable) |
| **App version** | v1.0.0 | v2.0.0 "Giant Eagle CS Receipt Lookup" |

---

## What We're Building

An internal tool for Giant Eagle CS reps to look up customer receipts across 400+ stores. A rep gets a call from a customer who can't find their receipt — the rep can search by any combination of customer ID, store, date, amount, or card last 4 digits, pull up the full receipt, and email or print it directly from the tool.

**Core workflow:**
```
Customer calls CS → Rep logs in (Azure AD SSO)
                 → Fuzzy search by partial info (date + store + amount)
                 → Pull receipt details + customer context card
                 → Email/print receipt to customer
                 → Every action audited for compliance
```

---

## Architecture

```
                         GIANT EAGLE POS
              400+ Stores · Self-Checkout · Online · GetGo
                    /                          \
                gRPC                      JDBC / Data API
                  |                              |
            ┌─────▼──────┐              ┌────────▼────────────────┐
            │  ZEROBUS   │              │       LAKEBASE          │
            │  Analytics │              │  Operational Serving    │
            │  Ingestion │              │                         │
            │            │              │  Native (read-write):   │
            │  Buffered  │              │  · receipt_transactions │
            │  <50ms ack │              │  · agent_state          │
            │  No Kafka  │              │  · audit_log       ←NEW │
            │  At-least- │              │  · receipt_delivery_log │
            │  once      │              │                         │
            └─────┬──────┘              │  AI (pipeline-written): │
                  │                     │  · product_embeddings   │
                  │ writes to           │    (pgvector + HNSW)    │
                  │ Delta tables        │  · search_cache         │
                  │                     │                         │
                  │                     │  Synced (read-only):    │
                  │                     │  · receipt_lookup       │
                  │                     │  · product_catalog      │
                  │                     │  · customer_profiles    │
                  │                     │  · spending_summary     │
                  │                     └────────▲────────────────┘
                  │                              │ Synced Tables (CDF)
                  ▼                              │
       ┌──────────────────────────────────────────────────────────┐
       │         DELTA LAKEHOUSE ON ADLS — SOURCE OF TRUTH       │
       │                                                          │
       │  Bronze ──► Silver ──► Gold                             │
       │  (raw)     (cleaned   (insights                         │
       │             enriched)  features)                        │
       │                                                          │
       │  Lakeflow Declarative Pipelines · CDF · Unity Catalog   │
       │  ADLS RA-GRS cross-region DR · Time Travel              │
       │  RPO: minutes · RTO: <1 hour                            │
       └──────────────────────────────────────────────────────────┘
                  │
                  ▼
       ┌──────────────────────────────────────────────────────────┐
       │                AI LAYER — MOSAIC AI                      │
       │                                                          │
       │  NL Receipt Search         CS Context Card              │
       │  Claude Sonnet 4           Customer profile for rep     │
       │  tool-calling              Pre-computed from Gold       │
       │  sql_query +               Optional LLM summary         │
       │  semantic_search           (~500ms)                     │
       └──────────────────────────────────────────────────────────┘
                  │
                  ▼
       ┌──────────────────────────────────────────────────────────┐
       │         DATABRICKS APP — CS Receipt Lookup v2.0         │
       │                                                          │
       │  POST /receipt/fuzzy      → Fuzzy multi-field search    │
       │  GET  /receipt/{id}       → Single receipt lookup       │
       │  GET  /receipt/customer/  → Customer history            │
       │  POST /search/            → NL semantic search          │
       │  GET  /cs-context/{cust}  → Customer profile card       │
       │  POST /deliver            → Email / print receipt       │
       │  GET  /audit/log          → Compliance audit trail      │
       │                                                          │
       │  FastAPI · Azure AD SSO · RBAC · Private Link           │
       │  AuditMiddleware on EVERY request (compliance)          │
       └──────────────────────────────────────────────────────────┘
```

---

## Permission Model — Unity Catalog (single source of truth)

All permissions live in Unity Catalog. One system governs both **data access** (what Delta/Lakebase tables a principal can query) and **app-level access** (what endpoints a user can call). No separate RBAC layer.

### Existing UC Groups (from `infra/unity_catalog_setup.sql`)

| Group | Current Grants |
|---|---|
| `data-engineers` | ALL PRIVILEGES on bronze, silver, gold |
| `ml-engineers` | SELECT on silver/gold, ALL on ai schema |
| `receipt-app-sp` | SELECT on gold + reference (pipeline service principal) |
| `analysts` | SELECT on gold only |

### New CS Groups to add to `unity_catalog_setup.sql`

| UC Group | App Role | Data Access | App Endpoints |
|---|---|---|---|
| `cs-reps` | cs_rep | SELECT on gold + reference | fuzzy search, receipt lookup, NL search, CS context, deliver |
| `cs-supervisors` | supervisor | SELECT on gold + reference + lakebase_live.public.audit_log | all cs_rep + audit trail queries |
| `fraud-investigators` | fraud_team | SELECT on gold + reference + silver + audit_log | all supervisor access |

Role hierarchy: `fraud-investigators > cs-supervisors > cs-reps`

### How it works at runtime

```
Databricks Apps (handles login, injects token)
    │
    ▼  X-Forwarded-Access-Token header
get_current_user() via databricks-sdk
    w.current_user.me()         ← who is this user?
    me.groups                   ← what UC groups are they in?
    │
    ▼  map UC group → app role
    cs-reps          → cs_rep
    cs-supervisors   → supervisor
    fraud-investigators → fraud_team
    │
    ▼  require_role() FastAPI dependency enforcer
```

**Single source of truth:** Add a user to `cs-supervisors` in Unity Catalog and they immediately get supervisor-level data access (via UC grants) AND supervisor-level app access (via group membership check). No separate system to keep in sync.

---

## Phase 1 — Foundation (`infra/`)

**Goal:** Provision Unity Catalog and Lakebase tables, including the new CS-specific tables.

### 1a. Unity Catalog Setup
Run `infra/unity_catalog_setup.sql`:

- Create `giant_eagle` catalog with schemas: `bronze`, `silver`, `gold`, `reference`, `ai`
- Create `giant_eagle.bronze.pos_receipts` — Zerobus landing table
- Seed `reference.product_catalog` and `reference.stores`
- Set up Lakehouse Federation (`lakebase_live` foreign catalog)
- Existing groups: `data-engineers`, `ml-engineers`, `receipt-app-sp`, `analysts`

**Extend the SQL with CS group grants (new):**
```sql
-- CS Reps: read receipts and reference data
GRANT USE CATALOG ON CATALOG giant_eagle TO `cs-reps`;
GRANT SELECT ON SCHEMA giant_eagle.gold TO `cs-reps`;
GRANT SELECT ON SCHEMA giant_eagle.reference TO `cs-reps`;

-- CS Supervisors: same as reps + audit log access via Lakehouse Federation
GRANT USE CATALOG ON CATALOG giant_eagle TO `cs-supervisors`;
GRANT SELECT ON SCHEMA giant_eagle.gold TO `cs-supervisors`;
GRANT SELECT ON SCHEMA giant_eagle.reference TO `cs-supervisors`;
GRANT SELECT ON TABLE lakebase_live.public.audit_log TO `cs-supervisors`;

-- Fraud Investigators: full read including silver + audit
GRANT USE CATALOG ON CATALOG giant_eagle TO `fraud-investigators`;
GRANT SELECT ON SCHEMA giant_eagle.silver TO `fraud-investigators`;
GRANT SELECT ON SCHEMA giant_eagle.gold TO `fraud-investigators`;
GRANT SELECT ON SCHEMA giant_eagle.reference TO `fraud-investigators`;
GRANT SELECT ON TABLE lakebase_live.public.audit_log TO `fraud-investigators`;
```

### 1b. Lakebase Setup
Run `infra/lakebase_setup.sql` after creating the Lakebase instance:

**Native tables (read-write by app):**
```sql
receipt_transactions    -- POS instant write, indexed for fuzzy search
agent_state             -- AI agent state persistence (JSONB)
agent_memory            -- multi-turn NL search conversation history
user_sessions           -- CS rep session tracking
audit_log               -- NEW: every CS action logged (compliance)
receipt_delivery_log    -- NEW: email/print delivery tracking
```

**AI tables (pipeline-written, regenerable):**
```sql
product_embeddings      -- pgvector(1024) + HNSW index
search_cache            -- query result cache (24hr TTL)
```

**Synced tables** (provisioned in Phase 3, read-only from Delta):
- `receipt_lookup`, `product_catalog`, `customer_profiles`, `spending_summary`

**Helper functions:**
- `search_products_semantic()` — pgvector cosine similarity
- `upsert_agent_state()` — idempotent state writes

---

## Phase 2 — POS Integration (`pos_integration/`)

**Unchanged from v1** — dual-write strategy is identical.

```
POS Transaction
    ├──► [HOT PATH — must succeed]
    │    JDBC → Lakebase receipt_transactions
    │    ON CONFLICT (transaction_id) DO NOTHING
    │    Failure: raise → POS retries
    │
    └──► [ANALYTICS PATH — best effort]
         gRPC → Zerobus → Delta bronze
         Failure: logged, non-blocking
         Reconciliation job patches gaps
```

`DualWriteHandler`:
- `write_receipt()` — async dual-write
- `write_batch()` — `asyncio.gather()` for checkout rush periods

`ZerobusReceiptIngester`:
- Validates fields, formats payload, calls Zerobus REST endpoint via `databricks-sdk`

---

## Phase 3 — Medallion Pipelines (`pipelines/`)

**Unchanged from v1** — same Bronze → Silver → Gold → Synced Tables flow.

```
Zerobus (giant_eagle.bronze.pos_receipts)
    │
    ▼  bronze_receipt_ingest.py
pos_receipts_raw          ← schema validation, append-only, _bronze_ts added
pos_receipt_items_raw     ← items array exploded to one row per item
    │
    ▼  silver_receipt_transform.py
receipts_cleaned          ← dedup by transaction_id (window, keep latest)
receipt_items_enriched    ← LEFT JOIN reference.product_catalog
receipt_lookup_silver     ← denormalized: receipts + aggregated items
    │
    ▼  gold_receipt_insights.py
receipt_lookup            ← final enriched receipts → synced to Lakebase
spending_summary          ← customer/category/month aggregations
customer_profiles         ← customer 360 (lifetime stats, top categories)
purchase_frequency        ← product cadence (kept for future use)
    │
    ▼  sync_to_lakebase.py
Continuous CDF sync (OnlineTableSpec):
  receipt_lookup ──────────► Lakebase (read-only)
  spending_summary ─────────► Lakebase (read-only)
  customer_profiles ────────► Lakebase (read-only)
  product_catalog ──────────► Lakebase (read-only)
```

| Layer | Key Logic |
|---|---|
| Bronze | Append-only, schema validation, no dedup |
| Silver | Dedup by `transaction_id`, product catalog enrichment |
| Gold | Pre-computed summaries powering sub-10ms Lakebase reads |

---

## Phase 4 — AI Layer (`ai/`)

**Removed:** Smart Reorder agent entirely.
**Changed:** Spending Insights → CS Context Card (rep-focused, not customer-facing).

### 4a. Embedding Pipeline (`embedding_pipeline.py`)
**Unchanged** — nightly Databricks Workflow.

```
Delta reference.product_catalog
    │  UDF → databricks-bge-large-en (1024-dim, batch=100)
    │  text = "product_name | description | category_l2"
    ▼
Lakebase product_embeddings (pgvector + HNSW)
```

### 4b. NL Search Agent (`nl_search_agent.py`)
**Unchanged** — Claude Sonnet 4 with tool-calling, multi-turn via `agent_memory`.

Tools:
- `sql_query` — SELECT-only, parameterized, returns ≤50 rows
- `semantic_search` — embed query → pgvector cosine (threshold 0.3) → LATERAL join

Latency: 200–400ms semantic, 1–3s NL→SQL.

### 4c. CS Context Agent (`cs_context_agent.py`) ← NEW (replaces Spending Insights)
**Trigger:** Per `GET /cs-context/{customer_id}` request · **Latency:** <10ms (pre-computed), ~500ms (with LLM summary)

```
Lakebase customer_profiles (synced, <10ms)
    +
Lakebase recent receipts from receipt_lookup (synced, <10ms)
    +
Lakebase spending_summary (synced, <10ms)
    │
    ▼  Optional: Foundation Model summary for rep briefing
"This customer spends ~$180/month, shops mainly at store #42,
 top categories: Produce and Dairy. Last visit was 3 days ago."
```

Returns to CS rep:
- Visit frequency, lifetime spend, avg basket size
- Spending level classification, preferred stores
- Top 5 categories
- Recent 5 receipts
- AI-generated brief (optional, ~500ms)

---

## Phase 5 — Application (`app/`)

This is where v2 differs most significantly from v1.

### Middleware Stack (applied to every request)

```
Request
  │
  ▼ AuditMiddleware  ← logs EVERY request to audit_log (must be first)
  ▼ CORS            ← internal CS portal domains only
  ▼ Route handler
  ▼
Response (elapsed time + status code also logged to audit_log)
```

`AuditMiddleware` captures:
- **Who:** rep_id, email, role (from JWT)
- **What:** action (lookup/search/fuzzy_search/deliver/context_lookup), resource_type, resource_id
- **When:** timestamp
- **How:** HTTP method, path, elapsed_ms, status_code
- Redacts sensitive fields (password, token, secret) from body logs
- Non-blocking: audit failure does NOT break the app

### Authentication (`middleware/auth.py`) — NEEDS REWORK

**Current (wrong):** Manually fetches JWKS from `login.microsoftonline.com` and validates JWTs with PyJWT. This is a direct Azure AD integration outside Databricks.

**Correct (Databricks-native):** Databricks Apps authenticates users at the platform level and injects the user's Databricks OAuth token via the `X-Forwarded-Access-Token` header. The app validates this token using `databricks-sdk`:

```python
from databricks.sdk import WorkspaceClient

def get_current_user(request: Request) -> UserContext:
    token = request.headers.get("X-Forwarded-Access-Token")
    w = WorkspaceClient(host=os.environ["DATABRICKS_HOST"], token=token)
    me = w.current_user.me()
    groups = [g.display for g in me.groups]
    role = _extract_role_from_groups(groups)  # cs_reps, cs_supervisors, fraud_investigators
    return UserContext(id=me.id, email=me.user_name, role=role)
```

- No `AZURE_TENANT_ID`, no `AZURE_CLIENT_ID`, no PyJWT, no JWKS fetching
- Roles derived from Databricks workspace group membership (not Azure AD JWT claims)
- `require_role(min_role)` — FastAPI dependency enforcer (unchanged pattern, different source)
- Role hierarchy: `fraud_team > supervisor > cs_rep`

### API Endpoints

| Method | Path | Role | Backend | Latency |
|---|---|---|---|---|
| POST | `/receipt/fuzzy` | cs_rep | Dynamic SQL → Lakebase | <10ms |
| GET | `/receipt/{id}` | cs_rep | Lakebase `receipt_lookup` | <10ms |
| GET | `/receipt/customer/{id}` | cs_rep | Lakebase `receipt_lookup` | <10ms |
| POST | `/receipt/write` | cs_rep | Lakebase `receipt_transactions` | <10ms |
| POST | `/search/` | cs_rep | NL Search Agent | 200ms–3s |
| GET | `/cs-context/{id}` | cs_rep | CS Context Agent | <10ms–500ms |
| POST | `/deliver` | cs_rep | Email/print + delivery_log | ~200ms |
| GET | `/deliver/log/{id}` | cs_rep | Lakebase `receipt_delivery_log` | <10ms |
| GET | `/audit/log` | supervisor | Lakebase `audit_log` | <10ms |
| GET | `/audit/log/rep/{id}` | supervisor | Lakebase `audit_log` | <10ms |
| GET | `/health` | none | Static | <1ms |

### Fuzzy Search — Most Used Feature (`routes/fuzzy_search.py`)

CS reps rarely have a complete transaction ID. They work from partial info a customer provides over the phone. The fuzzy search accepts any combination of:

```python
class FuzzySearchRequest:
    customer_id: str | None     # exact match
    store_name: str | None      # ILIKE fuzzy match via stores reference table
    store_id: str | None        # exact match
    date: str | None            # ± 1 day buffer
    date_from: str | None       # range start
    date_to: str | None         # range end
    amount: float | None        # ± 10% range if only one bound
    amount_min: float | None
    amount_max: float | None
    payment_last4: str | None   # exact match on receipt_transactions
    product_name: str | None    # falls through to semantic search (TODO)
```

Dynamic SQL builder assembles WHERE clause from whichever fields the rep provides. All fields optional — at least one required.

### Receipt Delivery (`routes/receipt_delivery.py`)

```python
class DeliverRequest:
    transaction_id: str
    method: Literal["email", "print"]
    target: str   # email address or printer ID
```

Flow:
1. Fetch receipt from `receipt_lookup` (synced)
2. `_send_email()` or `_send_to_printer()` (stubs → TODO wire to email/print service)
3. Log to `receipt_delivery_log` (delivery_id, transaction_id, method, target, status, rep_id)

### Route Structure

```
app/
├── main.py                    — FastAPI v2.0.0, AuditMiddleware first
├── app.yaml                   — Databricks Apps manifest
├── middleware/
│   ├── auth.py                — Azure AD JWT + RBAC
│   └── audit_middleware.py    — Compliance logging (every request)
└── routes/
    ├── lookup.py              — Single receipt + customer history
    ├── fuzzy_search.py        — Multi-field approximate search ← MOST USED
    ├── search.py              — NL semantic search
    ├── cs_context.py          — Customer profile card for rep
    ├── receipt_delivery.py    — Email/print delivery + delivery log
    └── audit.py               — Audit trail queries (supervisor only)
```

### Deployment
```bash
databricks apps deploy --manifest app/app.yaml
```

---

## Phase 6 — Disaster Recovery (`dr/terraform/`)

**Unchanged from v1.**

**Strategy:** Warm standby in Central US. Delta data replicated automatically via ADLS RA-GRS. Lakebase has no cross-region DR — mitigated because Delta is SOT and Lakebase rebuilds from sync on failover.

**RTO: 30–60 min | RPO: minutes**

### Terraform Resources

| Resource | Notes |
|---|---|
| Resource Group | `rg-giant-eagle-dr`, Central US |
| Storage Account | LRS (secondary doesn't need GRS) |
| Databricks Workspace | Premium SKU, `dbw-giant-eagle-dr` |
| VNet + Private Link subnet | 10.1.0.0/16 |

### Failover Runbook (10 steps)

1. Verify primary is truly down
2. Delta data already in Central US via RA-GRS
3. `terraform apply` — activate standby workspace
4. Configure UC metastore in secondary workspace
5. Spin up Lakebase instance, run `infra/lakebase_setup.sql`
6. Run `pipelines/sync_to_lakebase.py` — restore synced tables
7. Run `ai/embedding_pipeline.py` — regenerate pgvector embeddings
8. `databricks apps deploy --manifest app/app.yaml`
9. Update DNS / load balancer to secondary
10. Verify `GET /health` → `{"status": "healthy"}`

---

## Environment Variables

```bash
# Databricks (auth handled by platform in Databricks Apps — token injected at runtime)
DATABRICKS_HOST=https://<workspace>.azuredatabricks.net

# Lakebase (auto-connected via app.yaml resource binding — no credentials in env)
LAKEBASE_INSTANCE_NAME=giant-eagle-receipt-db

# These are only needed for local development / testing outside Databricks Apps
LAKEBASE_HOST=<host>
LAKEBASE_PORT=5432
LAKEBASE_DATABASE=giant_eagle
```

**Removed:** `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` — auth is handled by Databricks Apps platform, not custom Azure AD integration.

---

## Testing

```bash
pytest tests/ -v

# Key test cases
pytest tests/test_lakebase_queries.py::TestReceiptWrite -v      # write + idempotency
pytest tests/test_lakebase_queries.py::TestAgentState -v        # upsert_agent_state fn
pytest tests/test_lakebase_queries.py::TestSemanticSearch -v    # skip until embeddings run
```

**TODO — test gaps to fill:**
- Fuzzy search with partial inputs (most used feature, needs coverage)
- Audit log write on every request (middleware test)
- RBAC enforcement (cs_rep cannot access /audit/log)
- Receipt delivery log entry created on deliver

---

## Outstanding TODOs in Code

| File | TODO | On Databricks? |
|---|---|---|
| `middleware/auth.py` | Replace Azure AD JWKS auth with Databricks Apps native auth (`databricks-sdk`) | Yes — use `w.current_user.me()` |
| `middleware/auth.py` | Replace Azure AD role claims with Databricks workspace group membership | Yes — use `me.groups` |
| `routes/fuzzy_search.py` | Wire product_name → semantic search fallback | Yes — pgvector on Lakebase |
| `routes/receipt_delivery.py` | Implement `_send_email()` | Minimal external — SMTP or email API. Accept as necessary. |
| `routes/receipt_delivery.py` | Implement `_send_to_printer()` | External — store print hardware. Accept as necessary. |
| `infra/zerobus_client.py` | Upgrade to native gRPC when Databricks SDK adds support | Yes — Databricks roadmap item |

**On email/print:** These two delivery methods are inherently external to Databricks (customer email infrastructure, store printer hardware). They are the only acceptable external dependencies in an otherwise fully Databricks-native solution. Everything else — storage, compute, serving, AI, auth, audit — runs on Databricks.

---

## Build Order

```
Phase 1 ── infra/            Unity Catalog + Lakebase DDL (incl. audit_log, delivery_log)
Phase 2 ── pos_integration/  Dual-write handler (unchanged)
Phase 3 ── pipelines/        Bronze→Silver→Gold DLT + Synced Tables (unchanged)
Phase 4 ── ai/               Embeddings (nightly) + NL Search + CS Context Agent
Phase 5 ── app/              FastAPI v2.0 with auth, audit middleware, all CS routes
Phase 6 ── dr/terraform/     Warm standby in Central US (unchanged)
```
