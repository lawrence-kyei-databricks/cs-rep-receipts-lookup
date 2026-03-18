# Test Data Successfully Loaded

## Status: ✅ Complete

**Date**: 2026-03-17
**Database**: giant-eagle-receipt-db-v2
**App URL**: https://giant-eagle-cs-receipt-lookup-984752964297111.11.azure.databricksapps.com

---

## What Was Loaded

### Receipt Data
- **100 receipts** across last 90 days
- **637 line items** (3-10 items per receipt)
- **10 unique customers**:
  - John Smith (cust-5001)
  - Sarah Johnson (cust-5002)
  - Mike Davis (cust-5003)
  - Emily Brown (cust-5004)
  - David Wilson (cust-5005)
  - Lisa Anderson (cust-5006)
  - Tom Martinez (cust-5007)
  - Anna Garcia (cust-5008)
  - Chris Lee (cust-5009)
  - Jessica Taylor (cust-5010)

- **20 store locations** (Giant Eagle Pittsburgh area):
  - Bethel Park, Bloomfield, Cranberry, Downtown, East Liberty
  - Greenfield, Highland Park, Homestead, Lawrenceville, Monroeville
  - Mt. Lebanon, North Hills, Oakland, Regent Square, Robinson
  - Ross Park, Shadyside, Southside, Squirrel Hill, Waterfront

### Product Mix
- 17 different products across categories:
  - Produce (bananas, apples, spinach, tomatoes)
  - Dairy (milk, yogurt, cheese)
  - Meat (steak, ground beef, chicken)
  - Seafood (salmon)
  - Bakery (bread, bagels)
  - Beverages (orange juice, coffee)
  - Pantry (pasta, olive oil)

### Payment Mix
- Credit card (with last 4 digits)
- Debit card (with last 4 digits)
- Cash
- EBT

---

## Sample Data (Verified Working)

```
TXN-00001 | Sarah Johnson | Greenfield    | $56.07  | 2026-03-15
TXN-00002 | Lisa Anderson | East Liberty  | $126.45 | 2026-03-14
TXN-00003 | Chris Lee     | Greenfield    | $30.54  | 2026-03-13
```

---

## How to Test the App

### 1. Fuzzy Search
Try searching by:
- **Store**: "Greenfield", "East Liberty", "Squirrel Hill"
- **Date range**: Last week, last month, last 3 months
- **Amount**: $30-$130 range
- **Card last 4**: Any 4-digit number from receipts

Example searches:
```
Store: Greenfield, Date: last week
Store: East Liberty, Amount: $50-$150
Customer: cust-5003, Date: last month
```

### 2. AI Search (Natural Language)
Try asking:
- "Show me receipts from last week at Greenfield"
- "Find purchases over $100"
- "What did customer cust-5003 buy?"
- "Show me all dairy purchases"
- "Find receipts with salmon"

### 3. Customer Context
Look up a specific customer:
- Customer ID: `cust-5001` through `cust-5010`
- Should show purchase history, spending patterns

---

## Tables Populated

| Table | Rows | Purpose |
|-------|------|---------|
| `receipt_lookup` | 100 | Main receipt data with summaries |
| `receipt_line_items` | 637 | Individual line items (avg 6-7 per receipt) |
| `audit_log` | 0 → will fill as you use app | CS activity tracking |
| `receipt_delivery_log` | 0 → will fill when you email receipts | Delivery tracking |

---

## Troubleshooting 500 Errors

If you see a 500 error:

### 1. Check app logs
```bash
databricks apps logs giant-eagle-cs-receipt-lookup --tail 50
```

### 2. Common issues:
- **OAuth token expired**: App auto-refreshes, wait 30 seconds and retry
- **Search query syntax**: Try simpler queries first (single store, no date range)
- **AI search timeout**: Complex queries can take 2-5 seconds

### 3. Test the health endpoint:
The app requires authentication, so you'll be redirected to login first. That's expected.

### 4. Try a simple search:
- Open the app
- Login with your Databricks credentials
- Go to "Fuzzy Search" tab
- Select store: "Greenfield"
- Click "Search"
- Should return multiple results

---

## Sample Queries That Should Work

### In Fuzzy Search UI:
```
Store: Greenfield → Returns ~5-8 receipts
Store: East Liberty, Amount Min: $50 → Returns receipts over $50
Date: Last 30 days → Returns all receipts from last month
```

### In AI Search UI:
```
"Show me purchases from Greenfield store" → Returns receipts
"Find transactions over $100" → Returns high-value receipts
"What did Sarah Johnson buy?" → Returns customer's receipts
"Show me dairy purchases" → Returns receipts with dairy items
```

---

## Next Steps

1. **Login to app**: https://giant-eagle-cs-receipt-lookup-984752964297111.11.azure.databricksapps.com
2. **Try fuzzy search** with store name "Greenfield"
3. **Try AI search** with "show me receipts from last week"
4. **View a receipt** by clicking on any result
5. **Check audit log** in admin panel to see your activity tracked

---

## Script for Re-generating Data

If you need to reload or add more data:

```bash
python3 /tmp/load_test_receipts.py
```

The script is idempotent (uses `ON CONFLICT DO NOTHING`), so you can run it multiple times without duplicates.

---

## Success Criteria Met ✅

- [x] Tables exist (audit_log, receipt_lookup, receipt_line_items, receipt_delivery_log)
- [x] App is running (state: RUNNING, compute: ACTIVE)
- [x] Test data loaded (100 receipts, 10 customers, 20 stores)
- [x] Data is queryable (verified with direct SQL queries)
- [x] Realistic data mix (dates, amounts, products, payment types)

**The "0 results" issue is now fixed!** 🎉

The app should now return search results when you try fuzzy search or AI search.
