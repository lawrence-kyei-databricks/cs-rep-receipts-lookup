# Giant Eagle — Receipt Lookup on Azure Databricks

AI-powered receipt lookup application for Giant Eagle (400+ grocery stores) built entirely on the Databricks platform.

## Architecture

```
POS → gRPC → Zerobus → Delta (Bronze→Silver→Gold) → Synced Tables → Lakebase → App
POS → JDBC → Lakebase (instant writes)
Delta Gold → Mosaic AI → Embeddings → Lakebase pgvector
```

**Source of Truth:** Delta on ADLS (cross-region DR via RA-GRS)  
**Serving Layer:** Lakebase (sub-10ms reads, pgvector, multi-zone HA)  
**AI Layer:** Mosaic AI (semantic search, NL→SQL, spending insights, smart reorder)

## Quick Start

```bash
# 1. Set up environment
cp .env.example .env
# Fill in Databricks and Lakebase credentials

# 2. Create Lakebase tables
# Run infra/lakebase_setup.sql against your Lakebase instance

# 3. Set up Unity Catalog
# Run infra/unity_catalog_setup.sql in Databricks SQL

# 4. Deploy pipelines
# Import pipelines/*.py as Lakeflow Declarative Pipelines

# 5. Run embedding pipeline
# Schedule ai/embedding_pipeline.py as a Databricks Workflow (nightly)

# 6. Deploy the app
databricks apps deploy --manifest app/app.yaml

# 7. Test
pytest tests/ -v
```

## Project Structure

```
infra/              — Lakebase DDL, Unity Catalog setup, Zerobus client
pos_integration/    — Dual write handler (POS → Lakebase + Zerobus)
pipelines/          — Lakeflow Declarative Pipelines (Bronze → Silver → Gold)
ai/                 — Embedding pipeline, NL search, spending insights, reorder agent
app/                — Databricks App (FastAPI) with route handlers
dr/terraform/       — Secondary region infrastructure for disaster recovery
tests/              — Unit and integration tests
```

## API Endpoints

| Endpoint | Method | Description | Latency |
|----------|--------|-------------|---------|
| `/receipt/{id}` | GET | Lookup receipt by ID | <10ms |
| `/receipt/customer/{id}` | GET | Customer receipt history | <10ms |
| `/receipt/write` | POST | Direct receipt write | <10ms |
| `/search/` | POST | Semantic + NL search | 200ms-3s |
| `/insights/{customer}` | GET | AI spending insights | ~500ms |
| `/reorder/{customer}` | GET | Smart reorder suggestions | <10ms |
