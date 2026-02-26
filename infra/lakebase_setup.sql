-- ============================================================================
-- Giant Eagle Receipt Lookup — Lakebase Setup
-- Managed Postgres (Lakebase) with pgvector for semantic search
-- ============================================================================

-- Enable pgvector extension for semantic search
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================================
-- NATIVE TABLES (read-write, direct application writes)
-- These are NOT synced from Delta — the app writes to them directly.
-- DR: PITR 35-day window + multi-zone HA. On regional failure, reconcile
-- from Delta (Zerobus captures same receipts).
-- ============================================================================

-- Instant receipt capture from POS (JDBC direct write)
CREATE TABLE IF NOT EXISTS receipt_transactions (
    transaction_id      TEXT PRIMARY KEY,           -- POS-generated idempotency key
    store_id            TEXT NOT NULL,
    register_id         TEXT,
    customer_id         TEXT,                        -- loyalty card / Giant Eagle Advantage
    transaction_ts      TIMESTAMPTZ NOT NULL,
    total_amount        NUMERIC(10,2) NOT NULL,
    payment_method      TEXT,
    payment_last4       TEXT,                        -- last 4 digits of card (for CS fuzzy search)
    item_count          INTEGER,
    raw_payload         JSONB NOT NULL,              -- full POS receipt JSON
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    
    -- Dedup: transaction_id is the idempotency key from POS
    CONSTRAINT uq_transaction UNIQUE (transaction_id)
);

CREATE INDEX idx_receipt_txn_customer ON receipt_transactions(customer_id, transaction_ts DESC);
CREATE INDEX idx_receipt_txn_store ON receipt_transactions(store_id, transaction_ts DESC);
CREATE INDEX idx_receipt_txn_ts ON receipt_transactions(transaction_ts DESC);

-- CS fuzzy search indexes: reps search by approx date + store + amount + last4 card
CREATE INDEX idx_receipt_txn_fuzzy ON receipt_transactions(store_id, transaction_ts, total_amount);
CREATE INDEX idx_receipt_txn_payment ON receipt_transactions(payment_last4, transaction_ts DESC)
    WHERE payment_last4 IS NOT NULL;

-- AI agent state (stateful agents persist here)
CREATE TABLE IF NOT EXISTS agent_state (
    agent_id            TEXT NOT NULL,               -- e.g. 'reorder_agent', 'spending_agent'
    customer_id         TEXT NOT NULL,
    state_key           TEXT NOT NULL,               -- e.g. 'last_notification', 'purchase_frequency'
    state_value         JSONB NOT NULL,
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    
    PRIMARY KEY (agent_id, customer_id, state_key)
);

CREATE INDEX idx_agent_state_customer ON agent_state(customer_id);

-- Agent conversation memory (for multi-turn NL queries)
CREATE TABLE IF NOT EXISTS agent_memory (
    memory_id           TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    customer_id         TEXT NOT NULL,
    agent_id            TEXT NOT NULL,
    role                TEXT NOT NULL,               -- 'user' | 'assistant' | 'tool'
    content             TEXT NOT NULL,
    metadata            JSONB,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_agent_memory_session ON agent_memory(customer_id, agent_id, created_at DESC);

-- User sessions for the receipt lookup app
CREATE TABLE IF NOT EXISTS user_sessions (
    session_id          TEXT PRIMARY KEY,
    customer_id         TEXT NOT NULL,
    started_at          TIMESTAMPTZ DEFAULT NOW(),
    last_active_at      TIMESTAMPTZ DEFAULT NOW(),
    session_data        JSONB DEFAULT '{}'::JSONB
);

CREATE INDEX idx_session_customer ON user_sessions(customer_id);

-- ============================================================================
-- CS-SPECIFIC NATIVE TABLES
-- ============================================================================

-- Audit log: every CS rep action is tracked (compliance requirement)
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id            TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    rep_id              TEXT NOT NULL,               -- Azure AD user ID
    rep_email           TEXT NOT NULL,               -- e.g. jsmith@gianteagle.com
    rep_role            TEXT NOT NULL,               -- cs_rep | supervisor | fraud_team
    action              TEXT NOT NULL,               -- lookup | search | fuzzy_search | deliver | export
    resource_type       TEXT NOT NULL,               -- receipt | customer | audit
    resource_id         TEXT,                        -- transaction_id or customer_id accessed
    query_params        JSONB,                       -- search parameters used
    result_count        INTEGER,                     -- how many results returned
    ip_address          TEXT,
    user_agent          TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_rep ON audit_log(rep_id, created_at DESC);
CREATE INDEX idx_audit_resource ON audit_log(resource_type, resource_id, created_at DESC);
CREATE INDEX idx_audit_action ON audit_log(action, created_at DESC);
CREATE INDEX idx_audit_ts ON audit_log(created_at DESC);

-- Receipt delivery log: tracks emailed/printed receipts
CREATE TABLE IF NOT EXISTS receipt_delivery_log (
    delivery_id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    transaction_id      TEXT NOT NULL,               -- receipt delivered
    customer_id         TEXT,
    delivery_method     TEXT NOT NULL,               -- email | print | sms
    delivery_target     TEXT NOT NULL,               -- email address, printer ID, phone
    delivered_by_rep    TEXT NOT NULL,               -- CS rep who initiated
    status              TEXT DEFAULT 'sent',          -- sent | delivered | failed | bounced
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_delivery_customer ON receipt_delivery_log(customer_id, created_at DESC);
CREATE INDEX idx_delivery_receipt ON receipt_delivery_log(transaction_id);


-- ============================================================================
-- AI TABLES (read-write, written by AI pipelines)
-- These are regenerable — on DR failover, re-run the embedding pipeline.
-- ============================================================================

-- Product embeddings for semantic search (pgvector)
CREATE TABLE IF NOT EXISTS product_embeddings (
    product_id          TEXT PRIMARY KEY,
    product_name        TEXT NOT NULL,
    product_description TEXT,
    category            TEXT,
    embedding           vector(1024),               -- BGE-large or GTE dimension
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW index for fast approximate nearest neighbor search
CREATE INDEX idx_product_embedding_hnsw 
    ON product_embeddings 
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 256);

CREATE INDEX idx_product_embedding_category ON product_embeddings(category);

-- Search result cache (reduce redundant LLM calls)
CREATE TABLE IF NOT EXISTS search_cache (
    query_hash          TEXT PRIMARY KEY,            -- SHA256 of normalized query
    query_text          TEXT NOT NULL,
    result_json         JSONB NOT NULL,
    hit_count           INTEGER DEFAULT 1,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    expires_at          TIMESTAMPTZ DEFAULT NOW() + INTERVAL '24 hours'
);

CREATE INDEX idx_search_cache_expires ON search_cache(expires_at);


-- ============================================================================
-- SYNCED TABLES are created automatically by Databricks Synced Tables feature.
-- These are READ-ONLY in Lakebase — Delta Gold is the write source.
-- 
-- The following tables will appear in Lakebase after sync is configured:
--   - receipt_lookup        (enriched receipt with product names, categories)
--   - product_catalog       (full product reference data)
--   - customer_profiles     (customer 360 from loyalty program)
--   - spending_summary      (pre-computed spending aggregations)
--
-- Do NOT create these manually — Synced Tables manages the schema.
-- See pipelines/sync_to_lakebase.py for configuration.
-- ============================================================================


-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Semantic search function: find products by natural language description
CREATE OR REPLACE FUNCTION search_products_semantic(
    query_embedding vector(1024),
    result_limit INTEGER DEFAULT 10,
    similarity_threshold FLOAT DEFAULT 0.3
)
RETURNS TABLE (
    product_id TEXT,
    product_name TEXT,
    category TEXT,
    similarity FLOAT
) AS $$
    SELECT 
        product_id,
        product_name,
        category,
        1 - (embedding <=> query_embedding) AS similarity
    FROM product_embeddings
    WHERE 1 - (embedding <=> query_embedding) > similarity_threshold
    ORDER BY embedding <=> query_embedding
    LIMIT result_limit;
$$ LANGUAGE SQL;

-- Upsert agent state (idempotent)
CREATE OR REPLACE FUNCTION upsert_agent_state(
    p_agent_id TEXT,
    p_customer_id TEXT,
    p_state_key TEXT,
    p_state_value JSONB
) RETURNS VOID AS $$
    INSERT INTO agent_state (agent_id, customer_id, state_key, state_value, updated_at)
    VALUES (p_agent_id, p_customer_id, p_state_key, p_state_value, NOW())
    ON CONFLICT (agent_id, customer_id, state_key)
    DO UPDATE SET state_value = p_state_value, updated_at = NOW();
$$ LANGUAGE SQL;
