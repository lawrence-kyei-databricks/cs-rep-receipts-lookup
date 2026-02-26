# Giant Eagle — Receipt Lookup on Azure Databricks

## Project Overview
AI-powered receipt lookup application for Giant Eagle **Customer Service** team. CS reps use this to find customer receipts when customers call in — often with vague descriptions like "I bought something at the East Liberty store two weeks ago." Built entirely on the Databricks platform: Zerobus for ingestion, Delta Lakehouse as source of truth, Lakebase (managed Postgres) for serving, and Mosaic AI for intelligent search.

**This is an internal CS tool, NOT a consumer-facing app.**

## Who Uses This
- **Tier 1 CS Reps:** Basic receipt lookup, reprint/email receipts
- **Supervisors:** Dispute resolution, refund approvals, escalation review
- **Fraud Team:** Pattern investigation, multi-receipt analysis
- All users authenticated via Azure AD / Entra ID (internal SSO, not customer OAuth)

## Architecture (Delta-Anchored, Lakebase-Served)
```
POS → Dual Path:
  1. gRPC → ZEROBUS → DELTA (Bronze→Silver→Gold) → Synced Tables → LAKEBASE
  2. JDBC → LAKEBASE (native tables: receipt_transactions, agent_state)

LAKEBASE → Databricks App (CS Receipt Lookup)
DELTA GOLD → Mosaic AI → Embeddings → LAKEBASE pgvector

CS Rep → App → Lakebase (fast lookup) + Mosaic AI (semantic search, NL query)
                ↓
         Audit Log (every lookup tracked for compliance)
```

**Source of Truth:** Delta on ADLS (only layer with cross-region DR via RA-GRS)
**Serving Layer:** Lakebase (sub-10ms reads, pgvector, multi-zone HA)
**AI Layer:** Mosaic AI (semantic search, NL→SQL, customer context — NO reorder agent)

## Key Design Decisions
- Delta is SOT (not Lakebase) because ADLS RA-GRS provides cross-region DR
- Dual write from POS: Zerobus for analytics path + JDBC for instant Lakebase writes
- Three Lakebase table types: synced (read-only from Delta), native (read-write), AI (pgvector)
- Nightly embedding pipeline (product catalog doesn't change hourly)
- Pre-computed Gold insights repurposed as CS customer context (not consumer dashboards)
- **Every CS lookup is audit-logged** (compliance requirement)
- **Fuzzy search is critical** — customers call with vague info (approx date, store, amount)

## What Changed from Consumer Version
- ❌ REMOVED: Smart Reorder agent, reorder_suggestions table, /reorder endpoints
- ✅ ADDED: Fuzzy multi-field search (date + store + amount + partial card + name)
- ✅ ADDED: Audit trail (who looked up what, when, why)
- ✅ ADDED: Receipt delivery (email/print receipt to customer)
- ✅ CHANGED: Auth from customer OAuth → internal employee SSO (Azure AD RBAC)
- ✅ CHANGED: Spending insights → CS customer context card (quick profile for the rep)

## Project Structure
```
infra/              — Database setup, Unity Catalog, infrastructure
pos_integration/    — POS system integration (dual write handler)
pipelines/          — Lakeflow Declarative Pipelines (medallion)
ai/                 — Mosaic AI agents and embedding pipelines
  embedding_pipeline.py     — Product embeddings → pgvector (nightly)
  nl_search_agent.py        — "chicken from East Liberty last Tuesday" → results
  cs_context_agent.py       — Quick customer profile card for CS reps
  (NO reorder_agent.py)
app/                — Databricks App (FastAPI)
  routes/
    lookup.py               — Receipt by ID, customer history
    search.py               — Semantic + NL search
    fuzzy_search.py         — Multi-field fuzzy lookup (date/store/amount/card)
    cs_context.py           — Customer context card for reps
    receipt_delivery.py     — Email/print receipt to customer
    audit.py                — Audit log endpoints
  middleware/
    audit_middleware.py      — Auto-log every request
    auth.py                 — Azure AD SSO + RBAC
dr/terraform/       — Disaster recovery infrastructure
tests/              — Unit and integration tests
config/             — Environment configs
```

## Tech Stack
- **Runtime:** Databricks Runtime 15.x+, Python 3.11+
- **Ingestion:** Zerobus gRPC client
- **Pipelines:** Lakeflow Declarative Pipelines (DLT)
- **Database:** Lakebase (Postgres 16+ w/ pgvector)
- **AI:** Mosaic AI Agent Framework, Foundation Model Serving, Vector Search
- **App:** Databricks Apps with FastAPI
- **Auth:** Azure AD / Entra ID SSO with RBAC roles (cs_rep, supervisor, fraud_team)
- **Network:** Azure Private Link
- **DR:** ADLS RA-GRS + secondary workspace (warm standby)

## Development Conventions
- Use `databricks-sdk` for all Databricks API interactions
- Use `psycopg[binary]` for Lakebase connections (standard Postgres wire protocol)
- All Delta tables under Unity Catalog: `giant_eagle.{bronze|silver|gold}.table_name`
- Lakebase instance referenced via `LAKEBASE_INSTANCE_NAME` env var
- Tests use pytest with databricks-connect for integration tests
- Type hints on all functions, docstrings on public APIs
- **Every route must pass through audit middleware** — no exceptions

## Lakebase Tables

### Synced Tables (read-only, from Delta Gold via CDF)
- `receipt_lookup` — enriched receipts with product names, categories
- `product_catalog` — full product reference
- `customer_profiles` — customer 360 (repurposed as CS context)
- `spending_summary` — pre-computed spending by category/month

### Native Tables (read-write, app writes directly)
- `receipt_transactions` — instant POS receipt capture
- `agent_state` — AI agent conversation state
- `agent_memory` — multi-turn NL query memory
- `user_sessions` — CS rep session tracking
- `audit_log` — **NEW** every CS lookup/action logged
- `receipt_delivery_log` — **NEW** tracks emailed/printed receipts

### AI Tables (read-write, written by AI pipelines)
- `product_embeddings` — pgvector for semantic search
- `search_cache` — reduce redundant LLM calls

## RBAC Model
```
cs_rep:       SELECT on receipt_lookup, spending_summary, customer_profiles
              INSERT on audit_log, receipt_delivery_log
              Can: lookup, search, email receipt
              Cannot: bulk export, view fraud flags

supervisor:   All cs_rep permissions +
              Can: approve refunds, view escalation history, bulk search

fraud_team:   All supervisor permissions +
              Can: cross-customer pattern search, view fraud flags, bulk export
```

## Environment Variables
```
DATABRICKS_HOST          — Workspace URL
DATABRICKS_TOKEN         — PAT or OAuth token
LAKEBASE_INSTANCE_NAME   — Lakebase instance identifier
LAKEBASE_HOST            — Lakebase connection host
LAKEBASE_PORT            — Default 5432
LAKEBASE_DATABASE        — giant_eagle
AZURE_TENANT_ID          — Entra ID tenant
AZURE_CLIENT_ID          — App registration client ID (internal SSO app)
AZURE_CLIENT_SECRET      — App registration secret
SMTP_HOST                — For receipt email delivery
SMTP_FROM                — e.g. receipts@gianteagle.com
```

## Build Order (Phases)
1. **Phase 1 — Foundation:** infra/ → Lakebase tables (including NEW audit_log, receipt_delivery_log), Unity Catalog, Zerobus target tables
2. **Phase 2 — Ingestion:** pos_integration/ → dual write handler (unchanged from consumer version)
3. **Phase 3 — Pipelines:** pipelines/ → Bronze → Silver → Gold medallion + Synced Tables (unchanged)
4. **Phase 4 — AI:** ai/ → embeddings, NL→SQL agent, CS context agent (NO reorder agent)
5. **Phase 5 — App:** app/ → FastAPI with CS-specific routes: fuzzy search, audit middleware, receipt delivery, Azure AD RBAC
6. **Phase 6 — DR:** dr/ → Terraform for secondary region (unchanged)

## API Endpoints
```
GET  /receipt/{transaction_id}      → Receipt by ID (sub-10ms, Lakebase synced)
GET  /receipt/customer/{id}         → Customer receipt history (paginated)
POST /receipt/write                 → Direct POS receipt write (instant)

POST /search/                       → AI semantic + NL search ("that cheese from last week")
POST /search/fuzzy                  → Multi-field fuzzy: date range + store + amount range + partial card

GET  /cs/context/{customer_id}      → Customer context card (profile, top categories, visit frequency)

POST /receipt/deliver               → Email or print receipt to customer
GET  /receipt/deliver/log/{cust}    → Delivery history for a customer

GET  /audit/log                     → Query audit trail (supervisor+)
GET  /audit/log/rep/{rep_id}        → Audit trail for a specific CS rep

GET  /health                        → Health check
```

## CS-Specific Search Scenarios
These are the real-world queries CS reps need to handle:

1. **"I was at Giant Eagle East Liberty last Tuesday, spent about $40"**
   → Fuzzy search: store=East Liberty, date=last Tuesday ±1 day, amount=$35-$45

2. **"I bought some kind of fancy cheese there"**
   → Semantic search via pgvector: "fancy cheese" matches Roquefort, Brie, etc.

3. **"My card ending in 4532, purchase around January 15th"**
   → Fuzzy search: last4_card=4532, date=Jan 13-17

4. **"How much did customer X spend last month? They're disputing a charge"**
   → CS context agent pulls spending_summary + receipt history

5. **"Find all receipts over $200 from Store 247 this week"**
   → Direct SQL via NL agent or structured fuzzy search
