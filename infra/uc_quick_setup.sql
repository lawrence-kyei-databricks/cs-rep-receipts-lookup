-- ============================================================================
-- QUICK UC Setup for Giant Eagle CS Receipt Lookup
-- Run this to enable UC-native auth immediately
-- ============================================================================

-- Step 1: Create UC groups (if they don't exist)
-- Note: You need to be a workspace admin to create groups
-- CREATE GROUP IF NOT EXISTS cs_rep;
-- CREATE GROUP IF NOT EXISTS supervisor;
-- CREATE GROUP IF NOT EXISTS fraud_team;

-- If you can't create groups via SQL, use the Databricks UI:
-- Settings → Identity & Access → Groups → Create group

-- ============================================================================
-- Step 2: Add yourself to supervisor group (IMPORTANT!)
-- Replace with your actual email
-- ============================================================================

-- ALTER GROUP supervisor ADD USER 'lawrence.kyei@databricks.com';


-- ============================================================================
-- Step 3: Create row filter function for audit logs
-- This enforces: supervisors see all, cs_reps see only their own logs
-- ============================================================================

USE CATALOG giant_eagle;

CREATE OR REPLACE FUNCTION gold.audit_log_filter(rep_email STRING)
RETURN
  CASE
    WHEN IS_ACCOUNT_GROUP_MEMBER('supervisor') OR IS_ACCOUNT_GROUP_MEMBER('fraud_team')
      THEN TRUE
    WHEN IS_ACCOUNT_GROUP_MEMBER('cs_rep')
      THEN rep_email = current_user()
    ELSE FALSE
  END
COMMENT 'Row filter: supervisors see all audit logs, cs_reps see only their own';


-- ============================================================================
-- Step 4: Grant permissions on the audit_log table
-- NOTE: These grants are for when the audit_log table exists in Lakebase.
-- If the table doesn't exist yet, these will fail but you can run them later.
-- ============================================================================

-- For Lakebase tables accessed via Lakehouse Federation:
-- (Assumes you've registered Lakebase as a foreign catalog)

-- Grant SELECT on audit_log to all CS groups
-- GRANT SELECT ON TABLE giant_eagle_lakebase.public.audit_log TO `cs_rep`;
-- GRANT SELECT ON TABLE giant_eagle_lakebase.public.audit_log TO `supervisor`;
-- GRANT SELECT ON TABLE giant_eagle_lakebase.public.audit_log TO `fraud_team`;

-- Grant INSERT on audit_log (for writing audit logs)
-- GRANT INSERT ON TABLE giant_eagle_lakebase.public.audit_log TO `cs_rep`;
-- GRANT INSERT ON TABLE giant_eagle_lakebase.public.audit_log TO `supervisor`;
-- GRANT INSERT ON TABLE giant_eagle_lakebase.public.audit_log TO `fraud_team`;


-- ============================================================================
-- Step 5: Apply row filter to the Delta source table (if it exists)
-- This ensures the filter propagates to synced Lakebase tables
-- ============================================================================

-- Apply row filter to Delta gold.audit_log (if table exists)
-- ALTER TABLE giant_eagle.gold.audit_log
--   SET ROW FILTER gold.audit_log_filter ON (rep_email);


-- ============================================================================
-- VERIFICATION
-- Run these queries to test row filter works
-- ============================================================================

-- Test the row filter function
SELECT gold.audit_log_filter('test@example.com') AS should_see_row;
-- Should return TRUE if you're a supervisor, FALSE if cs_rep

-- Check your group memberships
-- DESCRIBE USER 'lawrence.kyei@databricks.com';


-- ============================================================================
-- WHAT TO DO IF GROUPS DON'T EXIST
-- ============================================================================
--
-- Option 1: Via Databricks UI (recommended)
-- 1. Go to: Settings → Identity & Access → Groups
-- 2. Click "Create group"
-- 3. Create: cs_rep, supervisor, fraud_team
-- 4. Add yourself to supervisor group
--
-- Option 2: Via SQL (requires workspace admin)
-- Uncomment and run:
-- CREATE GROUP cs_rep;
-- CREATE GROUP supervisor;
-- CREATE GROUP fraud_team;
-- ALTER GROUP supervisor ADD USER 'lawrence.kyei@databricks.com';
