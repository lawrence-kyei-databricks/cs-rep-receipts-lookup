-- ============================================================================
-- Receipt Line Items Table - Production Schema
-- Stores actual individual line items from each receipt with real SKUs and prices
-- ============================================================================

CREATE TABLE IF NOT EXISTS receipt_line_items (
    line_item_id        TEXT PRIMARY KEY DEFAULT gen_random_uuid()::TEXT,
    transaction_id      TEXT NOT NULL,                   -- FK to receipt_transactions/receipt_lookup
    line_number         INTEGER NOT NULL,                -- Line position on receipt (1, 2, 3...)
    sku                 TEXT NOT NULL,                   -- Product SKU
    product_name        TEXT NOT NULL,                   -- "Organic Whole Milk 1 Gal"
    brand               TEXT,                            -- "Snowville Creamery"
    category_l1         TEXT,                            -- "DAIRY"
    category_l2         TEXT,                            -- "MILK"
    quantity            DECIMAL(10,3) NOT NULL DEFAULT 1.0,  -- 1.0, 2.5 (for weighted items)
    unit_price_cents    BIGINT NOT NULL,                 -- Price per unit in cents
    line_total_cents    BIGINT NOT NULL,                 -- quantity * unit_price_cents
    discount_cents      BIGINT DEFAULT 0,                -- Promotional discount applied
    is_taxable          BOOLEAN DEFAULT true,
    weight_lb           DECIMAL(10,3),                   -- For weighted items (produce, deli)

    created_at          TIMESTAMPTZ DEFAULT NOW(),

    -- Ensure unique line items per transaction
    CONSTRAINT uq_line_item UNIQUE (transaction_id, line_number)
);

-- Indexes for fast lookups
CREATE INDEX idx_line_items_transaction ON receipt_line_items(transaction_id, line_number);
CREATE INDEX idx_line_items_sku ON receipt_line_items(sku);
CREATE INDEX idx_line_items_category ON receipt_line_items(category_l1, category_l2);

-- Add FK constraint (note: transaction_id exists in both receipt_transactions and receipt_lookup)
-- For production, you'd choose one as the authoritative source
COMMENT ON TABLE receipt_line_items IS 'Itemized line items for each receipt transaction. Each row represents one product line on a receipt with actual SKU, price, and quantity.';
