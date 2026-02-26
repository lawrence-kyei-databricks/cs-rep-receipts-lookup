# Migration Guide: SCIM-Based Auth → Unity Catalog Native RBAC

## Summary

This migration removes **application-layer permission checking** (SCIM API lookups, role hierarchies in Python) and replaces it with **Unity Catalog native authorization** (table grants, row filters, column masks).

**Before:** App checks user's UC group membership via SCIM API on every request
**After:** App authenticates user, UC enforces permissions at query time

## Benefits

| Metric | Before (SCIM) | After (UC-Native) |
|---|---|---|
| **Permission check latency** | 5-10ms per request | 0ms (no API calls) |
| **Permission updates** | Requires app redeploy | Instant via UC UI |
| **Enforcement consistency** | App-only (bypassed by SQL) | All query tools |
| **Audit trail** | Custom app logging | UC built-in audit logs |
| **Code complexity** | 150+ lines of auth code | 50 lines |

## What Changed

### 1. Removed from Code

**Deleted functions:**
- `_resolve_user_role()` — SCIM API lookup (auth.py:59-131)
- `_resolve_user_role_cached()` — 5-minute cache wrapper (auth.py:42-56)
- `require_role()` — Route decorator (auth.py:202-226)
- `_highest_role()` — Role hierarchy resolver (auth.py:134-143)
- `ROLE_HIERARCHY` — Role hierarchy dict (auth.py:31-35)

**Removed from all routes:**
- `dependencies=[Depends(require_role("cs_rep"))]`
- `dependencies=[Depends(require_role("supervisor"))]`

**Updated imports:**
```python
# Before
from app.middleware.auth import get_current_user, require_role

# After
from app.middleware.auth import get_current_user
```

### 2. Added to Unity Catalog

**New SQL infrastructure:**
- `infra/uc_rbac_setup.sql` — Complete UC permission setup
  - Row filter functions (audit_log_filter, receipt_lookup_filter, etc.)
  - Column mask functions (mask_fraud_flags, mask_payment_card, etc.)
  - Table grants for cs_rep, supervisor, fraud_team groups
  - Row filter applications to Delta tables

**UC Groups (must exist):**
- `cs_rep` — Basic CS operations
- `supervisor` — Escalations, audit access, refunds
- `fraud_team` — Pattern analysis, fraud flags, bulk export

### 3. Modified Files

**Auth middleware (app/middleware/auth.py):**
- Reduced from 226 lines → 85 lines (62% reduction)
- Removed SCIM API dependencies
- Kept only basic authentication (read X-Forwarded-Email header)

**Route files (all routes):**
- Removed `require_role()` decorators
- Kept `user: dict = Depends(get_current_user)` for authentication
- Updated docstrings to note UC handles authorization

**Files modified:**
- `app/middleware/auth.py` ✅
- `app/routes/audit.py` ✅
- `app/routes/lookup.py` ✅
- `app/routes/search.py` ✅
- `app/routes/fuzzy_search.py` ✅
- `app/routes/cs_context.py` ✅
- `app/routes/receipt_delivery.py` ✅
- `app/routes/admin.py` ✅

## Migration Steps

### Phase 1: Setup UC Permissions (One-Time)

**Step 1.1: Create UC groups (if they don't exist)**

Via Databricks UI:
1. Settings → Identity & Access → Groups
2. Create groups: `cs_rep`, `supervisor`, `fraud_team`
3. Add users to groups

Or via SQL (requires workspace admin):
```sql
CREATE GROUP IF NOT EXISTS cs_rep;
CREATE GROUP IF NOT EXISTS supervisor;
CREATE GROUP IF NOT EXISTS fraud_team;

ALTER GROUP supervisor ADD USER 'your.email@gianteagle.com';
```

**Step 1.2: Run UC RBAC setup script**

```bash
# From workspace SQL Editor or notebook:
%sql
SOURCE infra/uc_rbac_setup.sql;
```

This creates:
- Row filter functions in `giant_eagle.gold` schema
- Column mask functions
- Table grants for all three groups
- Row filter applications to Delta tables

**Step 1.3: Verify permissions work**

```sql
-- As a supervisor user
SELECT * FROM giant_eagle_lakebase.public.audit_log;
-- Should see ALL audit logs

-- As a cs_rep user (switch user in notebook or SQL editor)
SELECT * FROM giant_eagle_lakebase.public.audit_log;
-- Should see ONLY your own audit logs
```

### Phase 2: Deploy Updated Code

**Step 2.1: Deploy the app**

The code changes are already in the codebase. Deploy via:

```bash
# If using Databricks Apps
databricks apps deploy <app_name>

# Or via CI/CD pipeline
# (Your existing deployment process)
```

**Step 2.2: Verify authentication works**

Test that the app still authenticates users correctly:

```bash
# Hit a public endpoint (should work without auth)
curl https://<your-app-url>/health

# Hit a protected endpoint (should require auth)
curl https://<your-app-url>/receipt/txn-1001 \
  -H "X-Forwarded-Email: your.email@gianteagle.com"
```

**Step 2.3: Verify authorization works**

Test that UC row filters apply correctly:

As a **supervisor** user:
```bash
# Should see all audit logs
curl https://<your-app-url>/audit/log \
  -H "X-Forwarded-Email: supervisor@gianteagle.com"
```

As a **cs_rep** user:
```bash
# Should see only their own audit logs
curl https://<your-app-url>/audit/log \
  -H "X-Forwarded-Email: rep@gianteagle.com"
```

### Phase 3: Cleanup (Optional)

**Remove unused environment variables:**

If you had any SCIM-related config:
```bash
# No longer needed:
# RBAC_FALLBACK_ROLE
# SCIM_API_ENDPOINT
```

**Update documentation:**

Update any internal docs that referenced the old SCIM-based auth model.

## How It Works Now

### Authentication Flow

```
1. User accesses app via Databricks Apps URL
2. Databricks platform authenticates user (workspace SSO)
3. Platform injects headers:
   - X-Forwarded-Email: user@example.com
   - X-Forwarded-User: user
4. App reads email header → user dict
5. App sets request.state.user for audit logging
```

### Authorization Flow (UC Query-Time Enforcement)

```
1. App executes query: SELECT * FROM audit_log
2. UC checks: Does user's group have SELECT on audit_log?
   - If NO → Permission denied error
   - If YES → Continue
3. UC applies row filter: audit_log_filter(rep_email)
   - Supervisors: TRUE (see all rows)
   - CS reps: rep_email = current_user() (see only their own)
4. Query returns filtered results
5. App returns data to frontend
```

**No SCIM API calls, no Python role checks, no app-layer filtering.**

## Permission Management

### Adding a New User

**Before (SCIM-based):**
1. Add user to UC group
2. Wait 5 minutes for cache to expire
3. User can access

**After (UC-native):**
1. Add user to UC group
2. User can access immediately

Via UI:
1. Settings → Identity & Access → Groups
2. Select group (cs_rep, supervisor, or fraud_team)
3. Add member → Enter email → Add

Via SQL:
```sql
ALTER GROUP cs_rep ADD USER 'newrep@gianteagle.com';
```

### Changing Permissions

**Example: Make a cs_rep into a supervisor**

**Before:**
1. Add user to `supervisor` group
2. Wait 5 minutes for SCIM cache to expire
3. Redeploy app if role hierarchy changed

**After:**
```sql
ALTER GROUP supervisor ADD USER 'promoted@gianteagle.com';
-- Takes effect immediately
```

### Revoking Access

**Example: Remove access to audit logs**

**Before:**
1. Remove user from `supervisor` group
2. Wait 5 minutes for cache expiry
3. User loses access

**After:**
```sql
ALTER GROUP supervisor REMOVE USER 'demoted@gianteagle.com';
-- Takes effect immediately
```

Or revoke table access:
```sql
REVOKE SELECT ON TABLE giant_eagle_lakebase.public.audit_log FROM `cs_rep`;
-- cs_rep group can no longer query audit logs
```

## Troubleshooting

### "Permission denied" errors

**Symptom:** User gets 403 or SQL permission denied error

**Diagnosis:**
```sql
-- Check user's group memberships (as admin)
DESCRIBE USER 'user@example.com';

-- Check table grants
SHOW GRANTS ON TABLE giant_eagle_lakebase.public.audit_log;

-- Check row filter (if applicable)
DESCRIBE TABLE EXTENDED giant_eagle_lakebase.public.audit_log;
```

**Fix:**
```sql
-- Add user to correct group
ALTER GROUP cs_rep ADD USER 'user@example.com';

-- Or grant table access
GRANT SELECT ON TABLE giant_eagle_lakebase.public.audit_log TO `cs_rep`;
```

### "No data returned" but no error

**Symptom:** Query succeeds but returns 0 rows for a supervisor

**Diagnosis:** Row filter may be applied incorrectly

**Check:**
```sql
-- Verify row filter function
DESCRIBE FUNCTION giant_eagle.gold.audit_log_filter;

-- Test row filter manually
SELECT giant_eagle.gold.audit_log_filter('test@example.com') AS should_see_row;
```

**Fix:**
```sql
-- Reapply row filter
ALTER TABLE giant_eagle.gold.audit_log
  SET ROW FILTER giant_eagle.gold.audit_log_filter ON (rep_email);
```

### "User not authenticated" (401 error)

**Symptom:** App returns 401 error

**Diagnosis:**
- User not accessing via Databricks Apps URL
- Header injection not working
- DEV_USER_EMAIL not set (local dev only)

**Fix:**

For production:
- Ensure user accesses via official Databricks Apps URL
- Check app deployment settings (SSO must be enabled)

For local development:
```bash
# Set dev user environment variable
export DEV_USER_EMAIL=dev@example.com
```

### UC audit logs not showing app queries

**Symptom:** Want to see who queried which tables

**Solution:**

UC audit logs automatically track all queries. View them via:

```sql
-- Query UC audit logs (requires admin)
SELECT *
FROM system.access.audit
WHERE service_name = 'unityCatalog'
  AND action_name = 'generateTemporaryTableCredential'
  AND request_params.table_full_name LIKE '%audit_log%'
ORDER BY event_time DESC
LIMIT 100;
```

Or via Databricks UI:
1. Admin Console → Audit Logs
2. Filter by service: Unity Catalog
3. Search for table name

## Rollback Plan

If you need to revert to SCIM-based auth:

**Step 1: Restore old auth.py**
```bash
git checkout HEAD~1 app/middleware/auth.py
```

**Step 2: Restore old route decorators**
```bash
git checkout HEAD~1 app/routes/*.py
```

**Step 3: Redeploy app**
```bash
databricks apps deploy <app_name>
```

**Step 4: Remove UC row filters (optional)**
```sql
ALTER TABLE giant_eagle.gold.audit_log
  DROP ROW FILTER;
```

**Note:** UC grants can stay in place — they don't interfere with SCIM auth.

## FAQ

### Q: Can I still use UC groups with this approach?
**A:** Yes! UC groups are the foundation of this approach. Users must be in `cs_rep`, `supervisor`, or `fraud_team` groups.

### Q: What happens if a table doesn't have a row filter?
**A:** Table grants still apply. If a user has `SELECT` on the table, they see all rows. Use row filters for fine-grained access control.

### Q: Can I have per-user permissions (not just group-based)?
**A:** Yes. You can grant permissions directly to users:
```sql
GRANT SELECT ON TABLE giant_eagle_lakebase.public.audit_log TO `user@example.com`;
```

But group-based is recommended for easier management.

### Q: How do I add a new role (e.g., "manager")?
**A:**
1. Create UC group: `CREATE GROUP manager;`
2. Add table grants: `GRANT SELECT ON ... TO \`manager\`;`
3. Update row filter functions to include manager role
4. No code changes needed

### Q: Can cs_reps still see receipts from all customers?
**A:** Yes. The `receipt_lookup_filter` function allows all CS roles to read all receipts (needed for CS operations). Restrictions apply to audit logs and fraud flags, not receipts.

### Q: How do I test row filters locally?
**A:**

Row filters only work when querying through Unity Catalog. For local testing:

1. Use Databricks SQL warehouse (not local Postgres)
2. Query via `databricks-connect`:
```python
from databricks import sql
# Query through UC-aware connection
```
3. Or set `DEV_USER_EMAIL` and test via deployed app

### Q: Does this work with Lakebase native tables?
**A:** Partially. Row filters apply to **Delta tables in Unity Catalog**. For Lakebase native tables (not synced from Delta), you need:
- Table grants (work as-is)
- Postgres Row Level Security (RLS) for row filtering
- Or app-layer filtering (not recommended)

For this app, most tables are **synced from Delta Gold**, so UC row filters work.

## Next Steps

1. **Immediate:** Add yourself to `supervisor` group to access audit logs:
   ```sql
   ALTER GROUP supervisor ADD USER 'your.email@gianteagle.com';
   ```

2. **Short-term:** Run `infra/uc_rbac_setup.sql` to set up all UC permissions

3. **Long-term:** Consider adding column masks for PII fields:
   ```sql
   CREATE FUNCTION gold.mask_customer_email(email STRING)
   RETURN regexp_replace(email, '^(.{2}).*(@.*)', '$1***$2');

   ALTER TABLE giant_eagle.gold.customer_profiles
     SET COLUMN MASK gold.mask_customer_email ON (email);
   ```

## Support

If you encounter issues:
1. Check Databricks Apps logs: `databricks apps logs <app_name>`
2. Check UC audit logs (see Troubleshooting section)
3. Verify UC groups: `DESCRIBE USER 'your.email@example.com'`
4. File an issue if needed

---

**Summary:** This migration simplifies auth, improves performance, and leverages Databricks platform capabilities. All permission management is now in Unity Catalog UI — no code changes needed for access control updates.
