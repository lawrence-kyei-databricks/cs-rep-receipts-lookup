# Giant Eagle Receipt Lookup - Optimization & Implementation Plan

**Created:** 2026-02-26
**Status:** Ready for Implementation
**Est. Performance Impact:** 50% reduction in p95 response time (800ms → 400ms)

---

## Executive Summary

This document outlines performance optimizations for the Giant Eagle CS Receipt Lookup application. All optimizations have been identified through codebase analysis and are prioritized by impact vs. effort.

**Current State:**
- ✅ Cache files cleaned (Python `__pycache__`, `.pyc`, `.DS_Store`)
- ✅ AI semantic search fixed (correct embedding model: `databricks-gte-large-en`)
- ✅ Application deployed and functional
- ⚠️ 30+ background databricks app processes running (non-critical)

**Performance Baseline:**
- Current p95 response time: ~800ms
- Target p95 response time: ~400ms
- Primary bottlenecks: Connection overhead, embedding generation, synchronous audit logging

---

## Priority 1: HIGH IMPACT (Implement First)

### 1.1 Lakebase Connection Pooling

**Problem:**
File: `app/ai/nl_search_agent.py:182`
Every API request creates a new PostgreSQL connection, adding 20-50ms overhead per request.

**Solution:**
Implement `psycopg_pool.ConnectionPool` for connection reuse.

**Implementation Steps:**
```python
# 1. Update requirements.txt
# Add: psycopg[pool]>=3.1.0

# 2. Create new file: app/db/pool.py
from psycopg_pool import ConnectionPool
import os

# Initialize pool at module level (singleton pattern)
_pool = None

def get_pool():
    global _pool
    if _pool is None:
        conninfo = os.environ.get("LAKEBASE_CONNINFO")
        _pool = ConnectionPool(
            conninfo,
            min_size=5,      # Always keep 5 connections warm
            max_size=20,     # Max 20 concurrent connections
            timeout=30.0,    # Connection acquisition timeout
            max_lifetime=3600  # Recycle connections after 1 hour
        )
    return _pool

# 3. Update app/ai/nl_search_agent.py
# Replace direct psycopg.connect() calls with:
from app.db.pool import get_pool

def search_receipts(self, query: str):
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cursor:
            # ... existing query logic
```

**Files to Modify:**
- `app/ai/nl_search_agent.py` - Replace `psycopg.connect()` with pool
- `app/routes/lookup.py` - Use pooled connections
- `app/routes/search.py` - Use pooled connections
- `requirements.txt` - Add `psycopg[pool]>=3.1.0`

**Expected Impact:** 30-50ms per request
**Effort:** 2-3 hours
**Risk:** Low (backward compatible)

---

### 1.2 Embedding Query Cache

**Problem:**
File: `infra/regenerate_embeddings.py:141`
Every semantic search generates a new embedding via API call (200-300ms), even for common queries like "ribeye steak", "fancy cheese", etc.

**Solution:**
Pre-compute embeddings for common queries at app startup, cache in memory.

**Implementation Steps:**
```python
# 1. Create new file: app/ai/embedding_cache.py
import requests
from typing import Dict, List
import os

COMMON_QUERIES = [
    "ribeye steak", "fancy cheese", "chicken breast", "salmon",
    "organic milk", "eggs", "bread", "pasta", "ground beef",
    "roquefort cheese", "brie cheese", "bananas", "apples",
    "tomatoes", "black beans", "sourdough", "cheese assortment"
]

_cache: Dict[str, List[float]] = {}

def generate_embedding(text: str, token: str) -> List[float]:
    """Generate embedding using Databricks endpoint."""
    url = "https://adb-984752964297111.11.azuredatabricks.net/serving-endpoints/databricks-gte-large-en/invocations"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"input": [text]}
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()['data'][0]['embedding']

def initialize_cache(token: str):
    """Pre-compute embeddings for common queries at startup."""
    global _cache
    print(f"Initializing embedding cache with {len(COMMON_QUERIES)} queries...")
    for query in COMMON_QUERIES:
        _cache[query.lower()] = generate_embedding(query, token)
    print(f"✓ Embedding cache initialized with {len(_cache)} entries")

def get_cached_embedding(query: str, token: str) -> List[float]:
    """Get embedding from cache or generate on demand."""
    query_lower = query.lower().strip()

    # Exact match
    if query_lower in _cache:
        return _cache[query_lower]

    # Partial match (e.g., "ribeye" matches "ribeye steak")
    for cached_query, embedding in _cache.items():
        if query_lower in cached_query or cached_query in query_lower:
            return embedding

    # Cache miss - generate and cache for future
    embedding = generate_embedding(query, token)
    _cache[query_lower] = embedding
    return embedding

# 2. Update app/main.py (FastAPI startup)
from app.ai.embedding_cache import initialize_cache
import subprocess
import json

@app.on_event("startup")
async def startup_event():
    # Get token for embedding service
    result = subprocess.run(
        ['env', '-u', 'DATABRICKS_CONFIG_PROFILE', 'databricks', 'auth', 'token',
         '--host', 'https://adb-984752964297111.11.azuredatabricks.net', '-o', 'json'],
        capture_output=True, text=True
    )
    token = json.loads(result.stdout)['access_token']
    initialize_cache(token)

# 3. Update app/ai/nl_search_agent.py
from app.ai.embedding_cache import get_cached_embedding

def search(self, query: str):
    # Replace direct embedding generation with:
    query_embedding = get_cached_embedding(query, self.token)
    # ... rest of search logic
```

**Files to Modify:**
- New: `app/ai/embedding_cache.py`
- `app/main.py` - Add startup event handler
- `app/ai/nl_search_agent.py` - Use cached embeddings

**Expected Impact:** 200-300ms for ~70% of queries (cache hits)
**Effort:** 3-4 hours
**Risk:** Low (fallback to on-demand generation)

---

### 1.3 pgvector Index Tuning

**Problem:**
File: `infra/regenerate_embeddings.py:100`
HNSW index uses default parameters optimized for large datasets, but product catalog only has 15 products.

**Solution:**
Tune HNSW parameters for small-scale vector search.

**Implementation Steps:**
```sql
-- 1. Drop existing index
DROP INDEX IF EXISTS product_embeddings_hnsw_idx;

-- 2. Create optimized index for small dataset
CREATE INDEX product_embeddings_hnsw_idx
ON product_embeddings
USING hnsw (embedding vector_cosine_ops)
WITH (
    m = 8,              -- Max connections per layer (lower for small datasets)
    ef_construction = 32 -- Construction time neighbors (lower = faster build)
);

-- 3. For query time, configure ef_search (in queries):
SET hnsw.ef_search = 16;  -- Search time accuracy vs speed tradeoff
```

**Implementation:**
```python
# Update infra/regenerate_embeddings.py line 100
cursor.execute("""
    CREATE INDEX product_embeddings_hnsw_idx
    ON product_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 8, ef_construction = 32)
""")

# Update app/ai/nl_search_agent.py queries
# Add before vector search:
cursor.execute("SET hnsw.ef_search = 16")
```

**Files to Modify:**
- `infra/regenerate_embeddings.py:100` - Update index creation
- `app/ai/nl_search_agent.py` - Add `SET hnsw.ef_search` before queries

**Expected Impact:** 10-20% faster vector similarity search
**Effort:** 30 minutes
**Risk:** Very low (can revert index parameters)

---

## Priority 2: MEDIUM IMPACT

### 2.1 Async Audit Logging

**Problem:**
Every API call writes synchronously to `audit_log` table, adding 15-25ms to response time.

**Solution:**
Use FastAPI `BackgroundTasks` to write audit logs asynchronously after response is sent.

**Implementation Steps:**
```python
# Update app/middleware/audit_middleware.py
from fastapi import BackgroundTasks

async def write_audit_log_async(request_data: dict):
    """Write audit log in background."""
    # ... existing audit log write logic

@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    # ... collect request data

    # Send response immediately
    response = await call_next(request)

    # Write audit log in background (non-blocking)
    background_tasks = BackgroundTasks()
    background_tasks.add_task(write_audit_log_async, request_data)

    return response
```

**Files to Modify:**
- `app/middleware/audit_middleware.py` - Implement async logging

**Expected Impact:** 15-25ms per request
**Effort:** 2 hours
**Risk:** Medium (ensure no audit logs are lost, add retry logic)

---

### 2.2 Receipt Search Pagination

**Problem:**
Queries that return 100+ receipts fetch all rows from database, causing unnecessary overhead.

**Solution:**
Implement cursor-based pagination with configurable page size.

**Implementation Steps:**
```python
# Update app/routes/search.py
@router.post("/search/")
async def search(
    query: str,
    customer_id: str = None,
    limit: int = 20,        # NEW: page size
    offset: int = 0         # NEW: cursor position
):
    # Modify SQL queries to include LIMIT and OFFSET
    cursor.execute("""
        SELECT * FROM receipt_lookup
        WHERE ...
        ORDER BY transaction_ts DESC
        LIMIT %s OFFSET %s
    """, (limit, offset))

    results = cursor.fetchall()

    return {
        "results": results,
        "has_more": len(results) == limit,  # Indicates more pages exist
        "next_offset": offset + limit if len(results) == limit else None
    }
```

**Files to Modify:**
- `app/routes/search.py` - Add pagination parameters
- `app/routes/lookup.py` - Add pagination to customer history

**Expected Impact:** 50-100ms for large result sets
**Effort:** 2-3 hours
**Risk:** Low (backward compatible with default values)

---

### 2.3 Fuzzy Search Query Optimization

**Problem:**
Fuzzy search queries scan multiple columns without indexes (date, amount, store, card).

**Solution:**
Create compound indexes in Lakebase for common search patterns.

**Implementation Steps:**
```sql
-- Create compound index for fuzzy search
CREATE INDEX idx_receipt_fuzzy_search
ON receipt_lookup (store_name, transaction_ts DESC, total_cents, card_last4);

-- Create partial index for card searches (only rows with card_last4)
CREATE INDEX idx_receipt_card_search
ON receipt_lookup (card_last4, transaction_ts DESC)
WHERE card_last4 IS NOT NULL;

-- Create index for date range queries
CREATE INDEX idx_receipt_date_range
ON receipt_lookup (transaction_ts DESC);
```

**Implementation:**
```python
# Add new file: infra/add_search_indexes.py
"""
Add optimized indexes for fuzzy search queries.
Run after initial table creation.
"""
import psycopg
# ... connection logic

indexes = [
    """CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_receipt_fuzzy_search
       ON receipt_lookup (store_name, transaction_ts DESC, total_cents, card_last4)""",

    """CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_receipt_card_search
       ON receipt_lookup (card_last4, transaction_ts DESC)
       WHERE card_last4 IS NOT NULL""",

    """CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_receipt_date_range
       ON receipt_lookup (transaction_ts DESC)"""
]

for idx in indexes:
    print(f"Creating index: {idx[:60]}...")
    cursor.execute(idx)
```

**Files to Modify:**
- New: `infra/add_search_indexes.py`

**Expected Impact:** 40-60ms faster fuzzy search
**Effort:** 1 hour
**Risk:** Low (indexes improve reads, minimal write overhead for 15 products)

---

## Priority 3: LOW IMPACT (Nice to Have)

### 3.1 Deployment Bundle Optimization

**Problem:**
Current deployment duplicates `ai/` directory into `app/ai/` due to `databricks.yml` limitation.

**Solution:**
Modify `databricks.yml` to deploy both directories without duplication.

**Implementation:**
```yaml
# Update databricks.yml
bundle:
  name: giant-eagle-cs-receipt-lookup

resources:
  apps:
    giant-eagle-cs-receipt-lookup:
      name: giant-eagle-cs-receipt-lookup
      resources:
        - name: giant-eagle-cs-receipt-lookup-job
          description: Receipt lookup serverless compute
      source_code_path:
        - ./app
        - ./ai
```

**Files to Modify:**
- `databricks.yml`
- Remove duplicated `app/ai/` directory

**Expected Impact:** Faster deployments (smaller bundle)
**Effort:** 30 minutes
**Risk:** Medium (test deployment thoroughly)

---

### 3.2 Genie Space Integration

**Opportunity:**
Offload simple analytics queries to managed Genie service instead of custom NL→SQL agent.

**Examples:**
- "How much did customer X spend last month?" → Genie
- "Top 5 products by revenue" → Genie
- "Average transaction value by store" → Genie

**Implementation:**
```python
# Update app/ai/cs_context_agent.py
# For simple aggregation queries, route to Genie instead of custom agent

def should_use_genie(query: str) -> bool:
    """Determine if query should be handled by Genie."""
    genie_keywords = ["total", "sum", "average", "count", "top", "most", "least"]
    return any(kw in query.lower() for kw in genie_keywords)

async def handle_query(query: str):
    if should_use_genie(query):
        return await query_genie_space(query)
    else:
        return await custom_nl_agent(query)
```

**Files to Modify:**
- `app/ai/cs_context_agent.py` - Add Genie routing logic

**Expected Impact:** Reduced agent complexity, leverage managed service
**Effort:** 4-6 hours
**Risk:** Medium (requires Genie space setup and testing)

---

## Implementation Roadmap

### Phase 1: Quick Wins (Week 1)
1. ✅ Cleanup cache files (DONE)
2. ⬜ pgvector index tuning (30 min)
3. ⬜ Fuzzy search indexes (1 hour)

**Expected Gain:** 50-70ms per request

---

### Phase 2: Connection & Caching (Week 2)
1. ⬜ Lakebase connection pooling (2-3 hours)
2. ⬜ Embedding query cache (3-4 hours)

**Expected Gain:** 250-350ms per request (cache hits)

---

### Phase 3: Async & Pagination (Week 3)
1. ⬜ Async audit logging (2 hours)
2. ⬜ Receipt search pagination (2-3 hours)

**Expected Gain:** 50-100ms per request

---

### Phase 4: Polish (Week 4)
1. ⬜ Deployment bundle optimization (30 min)
2. ⬜ Genie space integration exploration (4-6 hours)

**Expected Gain:** Development velocity improvements

---

## Testing & Validation

### Performance Testing Checklist
```bash
# 1. Baseline performance (before optimizations)
python /tmp/test_ai_search.py  # Measure response time

# 2. After each optimization
# - Re-run test
# - Compare response times
# - Check for regressions

# 3. Load testing (optional)
# Use locust or k6 to simulate 100 concurrent users
```

### Validation Queries
```python
# Test embedding cache hit rate
cache_stats = {
    "ribeye steak": "CACHE HIT",      # Common query
    "fancy cheese": "CACHE HIT",       # Common query
    "xyz123": "CACHE MISS"             # Rare query
}

# Test connection pooling
# Check pool stats: active, idle, total connections
pool.get_stats()

# Test fuzzy search performance
# Before: ~120ms, After: ~60ms
```

---

## Rollback Plan

Each optimization is independent and can be rolled back individually:

1. **Connection Pooling:** Revert to `psycopg.connect()` in agent files
2. **Embedding Cache:** Remove startup event, use direct API calls
3. **pgvector Tuning:** Drop index, recreate with default parameters
4. **Async Logging:** Remove `BackgroundTasks`, use sync writes
5. **Indexes:** Drop indexes with `DROP INDEX idx_name`

---

## Monitoring & Observability

### Metrics to Track (Post-Implementation)
```python
# Add to app/main.py
from prometheus_client import Histogram, Counter

request_duration = Histogram('request_duration_seconds', 'Request duration')
embedding_cache_hits = Counter('embedding_cache_hits', 'Embedding cache hits')
embedding_cache_misses = Counter('embedding_cache_misses', 'Embedding cache misses')
db_connection_pool_size = Gauge('db_pool_active_connections', 'Active DB connections')
```

### Key Metrics
- p50, p95, p99 response times
- Embedding cache hit rate (target: >70%)
- Database connection pool utilization
- Audit log write success rate
- Error rates per endpoint

---

## Notes & Context

**Project:** Giant Eagle CS Receipt Lookup
**Stack:** Databricks Apps, Lakebase (PostgreSQL + pgvector), Mosaic AI
**Product Catalog:** 15 products (SKU-1001 to SKU-1015)
**Current Performance:** ~800ms p95 response time
**Target Performance:** ~400ms p95 response time (50% improvement)

**Recent Fixes:**
- ✅ Fixed embedding model: `databricks-bge-large-en` → `databricks-gte-large-en`
- ✅ Fixed AI agent deployment: Copied `ai/` into `app/ai/` for bundle deployment
- ✅ Verified semantic search working: "ribeye steak" returns 20+ receipts

**Known Issues:**
- 30+ background databricks app processes (non-critical, can be left running)
- No current connection pooling (new connection per request)
- No embedding caching (API call on every search)

---

## Contact & References

**Last Updated:** 2026-02-26
**Next Review:** After Phase 1 completion
**Owner:** Engineering Team

**Key Files:**
- `app/ai/nl_search_agent.py` - Main AI search agent
- `infra/regenerate_embeddings.py` - Embedding generation script
- `infra/bulk_generate_receipts.py` - Test data generation
- `databricks.yml` - Deployment configuration
- `CLAUDE.md` - Project documentation

---

## Success Criteria

**Phase 1 Complete When:**
- ✅ pgvector index tuned (HNSW parameters optimized)
- ✅ Fuzzy search indexes created
- ✅ Performance tests show 50-70ms improvement

**Phase 2 Complete When:**
- ✅ Connection pooling implemented
- ✅ Embedding cache hit rate >70%
- ✅ Performance tests show 250-350ms improvement on cache hits

**Phase 3 Complete When:**
- ✅ Audit logging is async
- ✅ Pagination implemented on search endpoints
- ✅ Performance tests show cumulative 400-500ms improvement

**Overall Success:**
- 🎯 p95 response time <450ms (50% improvement from baseline 800ms)
- 🎯 Zero regressions in functionality
- 🎯 All tests passing
- 🎯 Monitoring dashboard shows consistent improvements
