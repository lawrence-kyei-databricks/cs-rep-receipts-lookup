#!/usr/bin/env bash
# ============================================================================
# Quick Schema Setup for Lakebase
# ============================================================================
# This script creates the minimum required tables to unblock app development.
# Run this immediately after creating a Lakebase instance.
#
# Usage:
#   ./scripts/quick_schema_setup.sh <instance-name>
#
# Example:
#   ./scripts/quick_schema_setup.sh giant-eagle-receipt-db-v2
#
# What this creates:
#   - audit_log (required for all CS activity tracking)
#   - receipt_lookup (mock table for testing, replace with synced table later)
#   - receipt_line_items (optional, for line item details)
#
# For full schema (including AI tables, indexes, functions):
#   Use infra/lakebase_setup.sql instead
# ============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check arguments
if [ $# -eq 0 ]; then
    echo -e "${RED}ERROR: Instance name required${NC}"
    echo "Usage: $0 <instance-name>"
    echo "Example: $0 giant-eagle-receipt-db-v2"
    exit 1
fi

INSTANCE_NAME=$1
DATABASE_NAME=${2:-databricks_postgres}

echo -e "${GREEN}=== Quick Schema Setup ===${NC}"
echo "Instance: $INSTANCE_NAME"
echo "Database: $DATABASE_NAME"
echo ""

# Step 1: Get OAuth token
echo -e "${YELLOW}[1/5] Getting OAuth token...${NC}"
TOKEN=$(databricks database generate-database-credential \
    --instance-names "$INSTANCE_NAME" 2>/dev/null | jq -r .token)

if [ -z "$TOKEN" ] || [ "$TOKEN" = "null" ]; then
    echo -e "${RED}ERROR: Failed to get OAuth token${NC}"
    echo "Check that:"
    echo "  - Databricks CLI is configured (databricks auth login)"
    echo "  - Instance name is correct"
    echo "  - You have permissions to access the instance"
    exit 1
fi
echo -e "${GREEN}✓ Token acquired${NC}"

# Step 2: Get instance DNS
echo -e "${YELLOW}[2/5] Getting instance DNS...${NC}"
INSTANCE_DNS=$(databricks database get-database-instance "$INSTANCE_NAME" 2>/dev/null | jq -r .read_write_dns)

if [ -z "$INSTANCE_DNS" ] || [ "$INSTANCE_DNS" = "null" ]; then
    echo -e "${RED}ERROR: Failed to get instance DNS${NC}"
    echo "Check that the instance exists and is running:"
    echo "  databricks database list-database-instances"
    exit 1
fi
echo -e "${GREEN}✓ DNS: $INSTANCE_DNS${NC}"

# Step 3: Create SQL script
echo -e "${YELLOW}[3/5] Creating SQL schema...${NC}"

TEMP_SQL=$(mktemp /tmp/lakebase_quick_setup.XXXXXX.sql)
cat > "$TEMP_SQL" << 'EOF'
-- ============================================================================
-- Quick Schema Setup — Minimum required tables
-- ============================================================================

-- 1. Audit log (REQUIRED — all CS actions are logged)
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

CREATE INDEX IF NOT EXISTS idx_audit_rep ON audit_log(rep_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action, created_at DESC);

-- 2. Receipt lookup (TEMPORARY — replace with synced table from Delta later)
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

CREATE INDEX IF NOT EXISTS idx_receipt_lookup_customer ON receipt_lookup(customer_id, transaction_ts DESC);
CREATE INDEX IF NOT EXISTS idx_receipt_lookup_ts ON receipt_lookup(transaction_ts DESC);
CREATE INDEX IF NOT EXISTS idx_receipt_lookup_store ON receipt_lookup(store_id, transaction_ts DESC);
CREATE INDEX IF NOT EXISTS idx_receipt_lookup_card ON receipt_lookup(card_last4, transaction_ts DESC) WHERE card_last4 IS NOT NULL;

-- 3. Receipt line items (OPTIONAL — for detailed item breakdown)
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

CREATE INDEX IF NOT EXISTS idx_line_items_txn ON receipt_line_items(transaction_id);

-- 4. Receipt delivery log (tracks emailed/printed receipts)
CREATE TABLE IF NOT EXISTS receipt_delivery_log (
    delivery_id         TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    transaction_id      TEXT NOT NULL,
    customer_id         TEXT,
    delivery_method     TEXT NOT NULL,
    delivery_target     TEXT NOT NULL,
    delivered_by_rep    TEXT NOT NULL,
    status              TEXT DEFAULT 'sent',
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_delivery_customer ON receipt_delivery_log(customer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_delivery_receipt ON receipt_delivery_log(transaction_id);

EOF

# Step 4: Run SQL script
echo -e "${YELLOW}[4/5] Executing SQL script...${NC}"
export PGPASSWORD="$TOKEN"
psql "host=$INSTANCE_DNS port=5432 dbname=$DATABASE_NAME sslmode=require" \
    -f "$TEMP_SQL" 2>&1 | grep -v "NOTICE:" || true

echo -e "${GREEN}✓ Tables created${NC}"

# Step 5: Verify tables
echo -e "${YELLOW}[5/5] Verifying tables...${NC}"
TABLES=$(PGPASSWORD="$TOKEN" psql "host=$INSTANCE_DNS port=5432 dbname=$DATABASE_NAME sslmode=require" \
    -t -c "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name" 2>/dev/null)

if echo "$TABLES" | grep -q "audit_log"; then
    echo -e "${GREEN}✓ audit_log exists${NC}"
else
    echo -e "${RED}✗ audit_log NOT found${NC}"
fi

if echo "$TABLES" | grep -q "receipt_lookup"; then
    echo -e "${GREEN}✓ receipt_lookup exists${NC}"
else
    echo -e "${RED}✗ receipt_lookup NOT found${NC}"
fi

# Cleanup
rm -f "$TEMP_SQL"

echo ""
echo -e "${GREEN}=== Setup Complete ===${NC}"
echo ""
echo "Tables created:"
echo "  - audit_log          (CS activity tracking)"
echo "  - receipt_lookup     (receipt data)"
echo "  - receipt_line_items (line item details)"
echo "  - receipt_delivery_log (email/print tracking)"
echo ""
echo -e "${YELLOW}IMPORTANT:${NC}"
echo "  - receipt_lookup is a TEMPORARY table for testing"
echo "  - For production, replace with Synced Table from Delta gold layer"
echo "  - Run infra/lakebase_setup.sql for full schema (AI tables, functions, etc.)"
echo ""
echo "Next steps:"
echo "  1. Deploy app: databricks bundle deploy"
echo "  2. Load test data: python3 scripts/generate_test_data.py"
echo "  3. Access app: https://<workspace>.databricksapps.com"
