-- ============================================================================
-- Giant Eagle Receipt Lookup — Unity Catalog Setup
-- Catalog structure, schemas, permissions, Lakehouse Federation
-- ============================================================================

-- ============================================================================
-- 1. CATALOG + SCHEMAS (Medallion Architecture)
-- ============================================================================

CREATE CATALOG IF NOT EXISTS giant_eagle;
USE CATALOG giant_eagle;

-- Bronze: raw POS data from Zerobus
CREATE SCHEMA IF NOT EXISTS bronze
COMMENT 'Raw POS receipt data ingested via Zerobus. No transformations.';

-- Silver: cleaned, deduped, enriched
CREATE SCHEMA IF NOT EXISTS silver
COMMENT 'Cleaned and enriched receipt data. Product catalog joined.';

-- Gold: business-ready aggregations and AI features
CREATE SCHEMA IF NOT EXISTS gold
COMMENT 'Spending metrics, customer 360, reorder signals, AI feature tables.';

-- Reference: slowly changing dimensions
CREATE SCHEMA IF NOT EXISTS reference
COMMENT 'Product catalog, store directory, category taxonomy.';

-- AI: model artifacts, feature tables
CREATE SCHEMA IF NOT EXISTS ai
COMMENT 'Embedding tables, vector search indexes, model artifacts.';

-- ============================================================================
-- 2. ZEROBUS TARGET TABLE (Bronze)
-- This is where Zerobus writes raw POS records via gRPC direct write.
-- ============================================================================

CREATE TABLE IF NOT EXISTS giant_eagle.bronze.pos_receipts (
    transaction_id      STRING NOT NULL,
    store_id            STRING NOT NULL,
    register_id         STRING,
    customer_id         STRING,
    transaction_ts      TIMESTAMP NOT NULL,
    total_amount        DECIMAL(10,2) NOT NULL,
    payment_method      STRING,
    items               ARRAY<STRUCT<
        item_id:        STRING,
        product_id:     STRING,
        product_name:   STRING,
        quantity:        DECIMAL(10,3),
        unit_price:     DECIMAL(10,2),
        total_price:    DECIMAL(10,2),
        department:     STRING,
        upc:            STRING
    >>,
    raw_json            STRING,
    _ingest_ts          TIMESTAMP DEFAULT current_timestamp(),
    _source             STRING DEFAULT 'zerobus'
)
USING DELTA
COMMENT 'Raw POS receipt data from Zerobus. Append-only, no dedup at this layer.'
TBLPROPERTIES (
    'delta.enableChangeDataFeed' = 'true',
    'delta.autoOptimize.optimizeWrite' = 'true',
    'delta.autoOptimize.autoCompact' = 'true'
);

-- ============================================================================
-- 3. REFERENCE TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS giant_eagle.reference.product_catalog (
    product_id          STRING NOT NULL,
    product_name        STRING NOT NULL,
    description         STRING,
    category_l1         STRING,     -- e.g. "Grocery"
    category_l2         STRING,     -- e.g. "Dairy"
    category_l3         STRING,     -- e.g. "Milk & Alternatives"
    brand               STRING,
    upc                 STRING,
    unit_size           STRING,
    unit_of_measure     STRING,
    is_private_label    BOOLEAN DEFAULT false,
    updated_at          TIMESTAMP DEFAULT current_timestamp()
)
USING DELTA
TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true');

CREATE TABLE IF NOT EXISTS giant_eagle.reference.stores (
    store_id            STRING NOT NULL,
    store_name          STRING NOT NULL,
    store_type          STRING,     -- 'Giant Eagle', 'Market District', 'GetGo'
    address             STRING,
    city                STRING,
    state               STRING,
    zip                 STRING,
    region              STRING,
    latitude            DECIMAL(9,6),
    longitude           DECIMAL(9,6),
    is_active           BOOLEAN DEFAULT true
)
USING DELTA;

-- ============================================================================
-- 4. LAKEHOUSE FEDERATION — Register Lakebase as UC Catalog
-- This lets Spark query Lakebase tables directly alongside Delta tables.
-- ============================================================================

-- First, create a connection to Lakebase
-- (Run this via Databricks SQL or API — connection string uses Private Link)
CREATE CONNECTION IF NOT EXISTS lakebase_connection
TYPE POSTGRESQL
OPTIONS (
    host '${LAKEBASE_HOST}',
    port '${LAKEBASE_PORT}',
    user '${LAKEBASE_USER}',
    password '${LAKEBASE_PASSWORD}'
);

-- Register Lakebase as a foreign catalog in Unity Catalog
CREATE FOREIGN CATALOG IF NOT EXISTS lakebase_live
USING CONNECTION lakebase_connection
OPTIONS (database '${LAKEBASE_DATABASE}');

-- Now Spark can query: SELECT * FROM lakebase_live.public.agent_state

-- ============================================================================
-- 5. PERMISSIONS
-- ============================================================================

-- Data engineers: full access to all schemas
GRANT USE CATALOG ON CATALOG giant_eagle TO `data-engineers`;
GRANT USE SCHEMA ON SCHEMA giant_eagle.bronze TO `data-engineers`;
GRANT USE SCHEMA ON SCHEMA giant_eagle.silver TO `data-engineers`;
GRANT USE SCHEMA ON SCHEMA giant_eagle.gold TO `data-engineers`;
GRANT ALL PRIVILEGES ON SCHEMA giant_eagle.bronze TO `data-engineers`;
GRANT ALL PRIVILEGES ON SCHEMA giant_eagle.silver TO `data-engineers`;
GRANT ALL PRIVILEGES ON SCHEMA giant_eagle.gold TO `data-engineers`;

-- AI/ML engineers: read bronze/silver, read-write gold and ai
GRANT USE CATALOG ON CATALOG giant_eagle TO `ml-engineers`;
GRANT SELECT ON SCHEMA giant_eagle.silver TO `ml-engineers`;
GRANT SELECT ON SCHEMA giant_eagle.gold TO `ml-engineers`;
GRANT ALL PRIVILEGES ON SCHEMA giant_eagle.ai TO `ml-engineers`;

-- App service principal: read-only on gold (for synced tables source)
GRANT USE CATALOG ON CATALOG giant_eagle TO `receipt-app-sp`;
GRANT SELECT ON SCHEMA giant_eagle.gold TO `receipt-app-sp`;
GRANT SELECT ON SCHEMA giant_eagle.reference TO `receipt-app-sp`;

-- Analysts: read gold only
GRANT USE CATALOG ON CATALOG giant_eagle TO `analysts`;
GRANT USE SCHEMA ON SCHEMA giant_eagle.gold TO `analysts`;
GRANT SELECT ON SCHEMA giant_eagle.gold TO `analysts`;
