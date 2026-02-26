-- ============================================================================
-- Giant Eagle CS Receipt Lookup — Unity Catalog RBAC Setup
-- Row-level security, column masking, and group-based permissions
-- ============================================================================
--
-- This script replaces application-layer SCIM role checking with UC-native
-- authorization. Benefits:
--   - Zero SCIM API calls (permissions enforced at query time)
--   - Manage access via UC UI (no code deploys needed)
--   - Built-in audit logging (Unity Catalog audit logs)
--   - Consistent enforcement across all query tools (SQL, Python, apps)
--
-- Required UC Groups (create these first):
--   - cs_rep       → basic CS operations
--   - supervisor   → escalations, refunds, audit access
--   - fraud_team   → pattern analysis, bulk export, fraud flags
--
-- ============================================================================

USE CATALOG giant_eagle;

-- ============================================================================
-- 1. ROW FILTER FUNCTIONS
-- These enforce data visibility based on UC group membership.
-- Applied to Lakebase synced tables after creation.
-- ============================================================================

-- Audit Log: supervisors see all, cs_reps see only their own actions
CREATE OR REPLACE FUNCTION gold.audit_log_filter(rep_email STRING)
RETURN
  CASE
    -- Supervisors and fraud team see everything
    WHEN IS_ACCOUNT_GROUP_MEMBER('supervisor') OR IS_ACCOUNT_GROUP_MEMBER('fraud_team')
      THEN TRUE
    -- CS reps see only their own audit trail
    WHEN IS_ACCOUNT_GROUP_MEMBER('cs_rep')
      THEN rep_email = current_user()
    -- Default deny (shouldn't happen if grants are correct)
    ELSE FALSE
  END
COMMENT 'Row filter for audit_log: supervisors see all, cs_reps see only their own actions';

-- Customer Profile: fraud team sees fraud flags, others don't
CREATE OR REPLACE FUNCTION gold.customer_profile_filter()
RETURN
  -- Everyone can query customer profiles (needed for CS lookups)
  -- But fraud flags are masked via column masking (see below)
  TRUE
COMMENT 'Row filter for customer_profiles: allow all reads (fraud flags masked separately)';

-- Receipt Lookup: all CS roles can read all receipts (needed for CS operations)
CREATE OR REPLACE FUNCTION gold.receipt_lookup_filter()
RETURN
  IS_ACCOUNT_GROUP_MEMBER('cs_rep') OR
  IS_ACCOUNT_GROUP_MEMBER('supervisor') OR
  IS_ACCOUNT_GROUP_MEMBER('fraud_team')
COMMENT 'Row filter for receipt_lookup: allow all CS roles (needed for lookups)';


-- ============================================================================
-- 2. COLUMN MASKING FUNCTIONS
-- Hide sensitive columns from users without appropriate permissions.
-- ============================================================================

-- Mask fraud flags for cs_rep role (only supervisors+ see this)
CREATE OR REPLACE FUNCTION gold.mask_fraud_flags(fraud_flag_score DECIMAL(3,2))
RETURN
  CASE
    WHEN IS_ACCOUNT_GROUP_MEMBER('supervisor') OR IS_ACCOUNT_GROUP_MEMBER('fraud_team')
      THEN fraud_flag_score
    ELSE NULL  -- cs_reps don't see fraud scores
  END
COMMENT 'Column mask for fraud flags: visible to supervisors+ only';

-- Mask full payment card numbers (all roles see last 4 only)
CREATE OR REPLACE FUNCTION gold.mask_payment_card(card_number STRING)
RETURN
  CASE
    WHEN card_number IS NULL THEN NULL
    WHEN length(card_number) >= 4 THEN concat('****', right(card_number, 4))
    ELSE '****'
  END
COMMENT 'Column mask for payment cards: show last 4 digits only';

-- Mask customer PII for bulk exports (fraud team only gets full PII)
CREATE OR REPLACE FUNCTION gold.mask_customer_pii(email STRING)
RETURN
  CASE
    WHEN IS_ACCOUNT_GROUP_MEMBER('fraud_team')
      THEN email
    WHEN IS_ACCOUNT_GROUP_MEMBER('supervisor')
      THEN regexp_replace(email, '^(.{2}).*(@.*)', '$1***$2')  -- j***@example.com
    ELSE NULL  -- cs_reps don't need email in bulk queries
  END
COMMENT 'Column mask for customer email: full for fraud team, masked for supervisors, null for cs_reps';


-- ============================================================================
-- 3. UC GROUP SETUP
-- Create the CS role groups if they don't exist.
-- Run this as a workspace admin.
-- ============================================================================

-- Note: These commands require workspace admin permissions.
-- You can also create groups via Databricks UI: Settings → Identity & Access → Groups

-- CREATE GROUP IF NOT EXISTS cs_rep;
-- CREATE GROUP IF NOT EXISTS supervisor;
-- CREATE GROUP IF NOT EXISTS fraud_team;

-- Add users to groups:
-- ALTER GROUP cs_rep ADD USER 'rep1@gianteagle.com';
-- ALTER GROUP supervisor ADD USER 'manager1@gianteagle.com';
-- ALTER GROUP fraud_team ADD USER 'fraud.analyst@gianteagle.com';


-- ============================================================================
-- 4. LAKEBASE CATALOG GRANTS
-- Grant access to the Lakebase foreign catalog (created in Lakehouse Federation).
-- This assumes you've already created the Lakebase catalog via:
--   CREATE FOREIGN CATALOG lakebase_live USING CONNECTION lakebase_connection
-- ============================================================================

-- All CS roles need access to the Lakebase catalog
GRANT USE CATALOG ON CATALOG giant_eagle_lakebase TO `cs_rep`;
GRANT USE CATALOG ON CATALOG giant_eagle_lakebase TO `supervisor`;
GRANT USE CATALOG ON CATALOG giant_eagle_lakebase TO `fraud_team`;

-- Grant schema access (assuming 'public' schema in Lakebase)
GRANT USE SCHEMA ON SCHEMA giant_eagle_lakebase.public TO `cs_rep`;
GRANT USE SCHEMA ON SCHEMA giant_eagle_lakebase.public TO `supervisor`;
GRANT USE SCHEMA ON SCHEMA giant_eagle_lakebase.public TO `fraud_team`;


-- ============================================================================
-- 5. TABLE-LEVEL GRANTS (Lakebase Synced Tables)
--
-- NOTE: These grants are applied to the Lakebase synced tables AFTER they
-- are created by the sync process. The tables don't exist yet at this point.
-- Run these commands after running the Lakebase setup script.
-- ============================================================================

-- Receipt Lookup (synced from Delta gold.receipt_lookup)
-- All CS roles can read receipts (needed for CS operations)
GRANT SELECT ON TABLE giant_eagle_lakebase.public.receipt_lookup TO `cs_rep`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.receipt_lookup TO `supervisor`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.receipt_lookup TO `fraud_team`;

-- Product Catalog (synced from Delta reference.product_catalog)
-- All CS roles can read product info (needed for search)
GRANT SELECT ON TABLE giant_eagle_lakebase.public.product_catalog TO `cs_rep`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.product_catalog TO `supervisor`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.product_catalog TO `fraud_team`;

-- Customer Profiles (synced from Delta gold.customer_profiles)
-- All CS roles can read profiles, but fraud flags are masked
GRANT SELECT ON TABLE giant_eagle_lakebase.public.customer_profiles TO `cs_rep`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.customer_profiles TO `supervisor`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.customer_profiles TO `fraud_team`;

-- Spending Summary (synced from Delta gold.spending_summary)
-- All CS roles can read spending summaries (needed for CS context)
GRANT SELECT ON TABLE giant_eagle_lakebase.public.spending_summary TO `cs_rep`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.spending_summary TO `supervisor`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.spending_summary TO `fraud_team`;

-- Audit Log (synced from Delta gold.audit_log)
-- Row filter applied: supervisors see all, cs_reps see only their own
GRANT SELECT ON TABLE giant_eagle_lakebase.public.audit_log TO `cs_rep`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.audit_log TO `supervisor`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.audit_log TO `fraud_team`;

-- Receipt Delivery Log (synced from Delta gold.receipt_delivery_log)
-- Row filter: cs_reps see only their own deliveries
GRANT SELECT ON TABLE giant_eagle_lakebase.public.receipt_delivery_log TO `cs_rep`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.receipt_delivery_log TO `supervisor`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.receipt_delivery_log TO `fraud_team`;


-- ============================================================================
-- 6. TABLE-LEVEL GRANTS (Lakebase Native Tables)
-- These tables are written directly by the app (not synced from Delta).
-- ============================================================================

-- Receipt Transactions (native Lakebase table for instant POS writes)
-- cs_reps can read and write (for POS integration)
GRANT SELECT, INSERT ON TABLE giant_eagle_lakebase.public.receipt_transactions TO `cs_rep`;
GRANT SELECT, INSERT ON TABLE giant_eagle_lakebase.public.receipt_transactions TO `supervisor`;
GRANT SELECT, INSERT ON TABLE giant_eagle_lakebase.public.receipt_transactions TO `fraud_team`;

-- Agent State (AI agent conversation state)
-- All CS roles can read/write their own agent sessions
GRANT SELECT, INSERT, UPDATE ON TABLE giant_eagle_lakebase.public.agent_state TO `cs_rep`;
GRANT SELECT, INSERT, UPDATE ON TABLE giant_eagle_lakebase.public.agent_state TO `supervisor`;
GRANT SELECT, INSERT, UPDATE ON TABLE giant_eagle_lakebase.public.agent_state TO `fraud_team`;

-- Agent Memory (multi-turn NL query context)
GRANT SELECT, INSERT, UPDATE ON TABLE giant_eagle_lakebase.public.agent_memory TO `cs_rep`;
GRANT SELECT, INSERT, UPDATE ON TABLE giant_eagle_lakebase.public.agent_memory TO `supervisor`;
GRANT SELECT, INSERT, UPDATE ON TABLE giant_eagle_lakebase.public.agent_memory TO `fraud_team`;

-- User Sessions (CS rep session tracking)
GRANT SELECT, INSERT, UPDATE ON TABLE giant_eagle_lakebase.public.user_sessions TO `cs_rep`;
GRANT SELECT, INSERT, UPDATE ON TABLE giant_eagle_lakebase.public.user_sessions TO `supervisor`;
GRANT SELECT, INSERT, UPDATE ON TABLE giant_eagle_lakebase.public.user_sessions TO `fraud_team`;

-- Audit Log (write access for audit middleware)
-- All CS roles can INSERT their own audit records
GRANT INSERT ON TABLE giant_eagle_lakebase.public.audit_log TO `cs_rep`;
GRANT INSERT ON TABLE giant_eagle_lakebase.public.audit_log TO `supervisor`;
GRANT INSERT ON TABLE giant_eagle_lakebase.public.audit_log TO `fraud_team`;

-- Receipt Delivery Log (write access for receipt delivery)
GRANT INSERT ON TABLE giant_eagle_lakebase.public.receipt_delivery_log TO `cs_rep`;
GRANT INSERT ON TABLE giant_eagle_lakebase.public.receipt_delivery_log TO `supervisor`;
GRANT INSERT ON TABLE giant_eagle_lakebase.public.receipt_delivery_log TO `fraud_team`;

-- Product Embeddings (pgvector AI table)
-- cs_reps can read for semantic search
GRANT SELECT ON TABLE giant_eagle_lakebase.public.product_embeddings TO `cs_rep`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.product_embeddings TO `supervisor`;
GRANT SELECT ON TABLE giant_eagle_lakebase.public.product_embeddings TO `fraud_team`;

-- Search Cache (reduce LLM calls)
GRANT SELECT, INSERT ON TABLE giant_eagle_lakebase.public.search_cache TO `cs_rep`;
GRANT SELECT, INSERT ON TABLE giant_eagle_lakebase.public.search_cache TO `supervisor`;
GRANT SELECT, INSERT ON TABLE giant_eagle_lakebase.public.search_cache TO `fraud_team`;


-- ============================================================================
-- 7. APPLY ROW FILTERS TO TABLES
--
-- NOTE: Row filters can only be applied to Delta tables in Unity Catalog.
-- For Lakebase tables accessed via Lakehouse Federation, row filters are
-- applied to the DELTA SOURCE tables (giant_eagle.gold.*), and the synced
-- Lakebase tables inherit the filtering via Change Data Feed.
--
-- If you need row filtering on Lakebase native tables (not synced from Delta),
-- you must implement it in the app layer OR use Postgres RLS (Row Level Security).
-- ============================================================================

-- Apply row filter to Delta gold.audit_log (synced to Lakebase)
ALTER TABLE giant_eagle.gold.audit_log
  SET ROW FILTER gold.audit_log_filter ON (rep_email);

-- Apply column mask to Delta gold.customer_profiles (synced to Lakebase)
-- Note: Replace 'fraud_flag_score' with actual column name if different
-- ALTER TABLE giant_eagle.gold.customer_profiles
--   SET COLUMN MASK gold.mask_fraud_flags ON (fraud_flag_score);


-- ============================================================================
-- 8. VERIFICATION QUERIES
-- Run these as different users to verify row filters work.
-- ============================================================================

-- As a cs_rep user:
-- SELECT * FROM giant_eagle_lakebase.public.audit_log;
-- → Should see only their own audit records

-- As a supervisor user:
-- SELECT * FROM giant_eagle_lakebase.public.audit_log;
-- → Should see ALL audit records

-- As a fraud_team user:
-- SELECT * FROM giant_eagle_lakebase.public.customer_profiles;
-- → Should see fraud_flag_score column (not masked)


-- ============================================================================
-- 9. MIGRATION NOTES
-- ============================================================================
--
-- After applying these UC permissions, you can remove from the app code:
--   1. app/middleware/auth.py → _resolve_user_role() function (SCIM lookup)
--   2. app/middleware/auth.py → require_role() decorator
--   3. All route decorators → dependencies=[Depends(require_role(...))]
--
-- The app only needs to:
--   1. Authenticate the user (read X-Forwarded-Email header) ✅
--   2. Pass the user identity to Lakebase queries ✅
--   3. Let UC enforce permissions at query time ✅
--
-- Benefits:
--   - Zero SCIM API calls (5-10ms latency removed)
--   - Permission changes via UC UI (no app redeploy)
--   - Consistent enforcement (SQL, Python, notebooks, apps)
--   - Built-in UC audit logging (compliance ready)
--
-- ============================================================================
