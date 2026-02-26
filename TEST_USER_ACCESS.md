# Testing User Access to Giant Eagle CS Receipt Lookup App

## Problem
Your friend can access the app page but doesn't see any data when searching. This test script helps diagnose why.

## What the Test Does

The script simulates exactly what the browser does when a user searches for receipts:

1. **Authenticates** - Gets a Databricks token
2. **Simulates user identity** - Uses `X-Forwarded-Email` header (same as the app does)
3. **Makes API calls** - Calls `/search/fuzzy` with known-good test data
4. **Verifies responses** - Checks if data is returned

## How to Run

### Quick Test (recommended)
```bash
# From the project directory
cd /Users/lawrence.kyei/Desktop/dbx-demos/receipts_lookup

# Test with your friend's email
python3 test_user_access.py --email friend@company.com
```

The script will:
- ✅ Use the token from `/tmp/token.txt` (if available)
- ✅ Or generate a new one via databricks CLI
- ✅ Test 3 known-good scenarios with sample data
- ✅ Show exactly what data is returned

### Example Output

```
Testing Giant Eagle CS Receipt Lookup App Access
======================================================================
User: friend@company.com
App URL: https://giant-eagle-cs-receipt-lookup-...

Step 1: Obtaining Databricks authentication token...
✅ Token obtained (length: 857)

Step 2: Testing API endpoints with known-good data...

Test 1: Customer cust-5001 at East Liberty
  Payload: {
    "customer_id": "cust-5001",
    "store_name": "East Liberty",
    "limit": 5
  }
  ✅ Status: 200
  📊 Results: 2 receipts found
  👤 Searched by: friend@company.com
  📝 Sample result:
     Transaction: txn-1002
     Store: East Liberty
     Date: 2026-02-12
     Total: $55.52
```

## Interpreting Results

### ✅ All tests pass with data returned
**Diagnosis**: Your friend's permissions are working correctly. The issue is with their search inputs.

**Solution**: Tell them to try these exact values:
- **Customer ID**: `cust-5001` (case-sensitive!)
- **Store Name**: `East Liberty` (with space, capital E and L)
- Leave other fields blank

### ❌ Tests return 401/403 errors
**Diagnosis**: Permissions issue

**Solution**:
1. Go to Databricks Apps UI
2. Click on "giant-eagle-cs-receipt-lookup" app
3. Go to "Permissions" tab
4. Grant your friend "CAN USE" permission

### ⚠️ Tests return 0 results
**Diagnosis**: App works but search parameters don't match data

**Solution**: Use the exact test values shown above

## Known-Good Test Inputs

From the sample data, these are guaranteed to return results:

### Test 1: Customer at specific store
```
Customer ID: cust-5001
Store Name: East Liberty
(Leave date range and amount blank)
Expected: 2 receipts
```

### Test 2: Store only
```
Store Name: Shadyside
Expected: 3+ receipts
```

### Test 3: Customer only
```
Customer ID: cust-5001
Expected: 2 receipts
```

## Common Pitfalls

### Case Sensitivity
❌ **Wrong**: `east liberty` or `East liberty`
✅ **Correct**: `East Liberty`

❌ **Wrong**: `Cust-5001` or `CUST-5001`
✅ **Correct**: `cust-5001`

### Whitespace
❌ **Wrong**: ` cust-5001 ` (spaces before/after)
✅ **Correct**: `cust-5001`

❌ **Wrong**: `East  Liberty` (double space)
✅ **Correct**: `East Liberty`

### Store Name Typos
❌ **Wrong**: `EastLiberty` (no space)
❌ **Wrong**: `East Libery` (typo)
✅ **Correct**: `East Liberty`

## How the App's M2M Auth Works

**Important**: Users do NOT need database permissions!

```
User → App (browser) → Backend uses Service Principal → Lakebase
                         ↑
                    This step uses app's credentials,
                    NOT the user's credentials
```

- ✅ User only needs: App-level "CAN USE" permission
- ❌ User does NOT need: Lakebase role, Unity Catalog grants, etc.

The service principal (`e1751c32-5a1b-4d6f-90c2-e71e10246366`) has all the database permissions and makes all queries on behalf of users.

## Troubleshooting

### Script can't get token
**Error**: `Failed to obtain token`

**Solution**: Generate one manually:
```bash
env -u DATABRICKS_CONFIG_PROFILE databricks auth token \
  --host https://adb-984752964297111.11.azuredatabricks.net > /tmp/token.txt

# Then run the test again
python3 test_user_access.py --email friend@company.com
```

### App is down
**Error**: Connection timeout or 502 Bad Gateway

**Solution**: Check app status:
```bash
env -u DATABRICKS_CONFIG_PROFILE databricks apps get giant-eagle-cs-receipt-lookup
```

If state is not `RUNNING`, restart it:
```bash
env -u DATABRICKS_CONFIG_PROFILE databricks apps start giant-eagle-cs-receipt-lookup
```

## Next Steps

1. **Run the test** with your friend's email
2. **Share the results** - shows whether it's permissions or inputs
3. **If tests pass**: The issue is search input formatting
4. **If tests fail**: Check app permissions in Databricks UI

## Contact

If the test shows data returning but your friend still can't see it:
- Clear browser cache / try incognito mode
- Check browser console for JavaScript errors (F12 → Console tab)
- Verify they're using the same URL: `https://giant-eagle-cs-receipt-lookup-984752964297111.11.azure.databricksapps.com`
