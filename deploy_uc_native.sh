#!/bin/bash
# ============================================================================
# Deploy UC-Native Auth Version
# Removes SCIM-based permission checking, enables Unity Catalog native RBAC
# ============================================================================

set -e  # Exit on error

echo "🚀 Deploying Giant Eagle CS Receipt Lookup with UC-native auth..."
echo ""

# Step 1: Verify we're in the right directory
if [ ! -f "databricks.yml" ]; then
    echo "❌ Error: databricks.yml not found. Are you in the project root?"
    exit 1
fi

echo "✅ Project root found"
echo ""

# Step 2: Check if databricks CLI is installed
if ! command -v databricks &> /dev/null; then
    echo "❌ Error: databricks CLI not installed"
    echo "Install: https://docs.databricks.com/dev-tools/cli/install.html"
    exit 1
fi

echo "✅ Databricks CLI found"
echo ""

# Step 3: Validate the app
echo "📋 Validating app bundle..."
databricks bundle validate

echo "✅ Bundle validation passed"
echo ""

# Step 4: Deploy the app
echo "📦 Deploying app..."
echo ""

# Unset DATABRICKS_CONFIG_PROFILE to use default workspace
unset DATABRICKS_CONFIG_PROFILE

databricks bundle deploy --target dev

echo ""
echo "✅ App deployed successfully!"
echo ""

# Step 5: Get app status
echo "📊 Checking app status..."
APP_NAME="giant-eagle-cs-receipt-lookup"

# Wait a few seconds for deployment to register
sleep 3

# Get app status
APP_STATUS=$(env -u DATABRICKS_CONFIG_PROFILE databricks apps get $APP_NAME --json 2>/dev/null || echo "{}")

if [ -z "$APP_STATUS" ] || [ "$APP_STATUS" = "{}" ]; then
    echo "⚠️  App status not available yet (this is normal for first deployment)"
    echo ""
    echo "Run this to check status:"
    echo "  databricks apps get $APP_NAME"
else
    echo "$APP_STATUS" | python3 -c "
import json, sys
data = json.load(sys.stdin)
state = data.get('status', {}).get('state', 'UNKNOWN')
url = data.get('url', 'N/A')
print(f'State: {state}')
print(f'URL: {url}')
"
fi

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "✅ DEPLOYMENT COMPLETE"
echo "════════════════════════════════════════════════════════════════════"
echo ""
echo "Next steps:"
echo ""
echo "1. Check app logs:"
echo "   databricks apps logs $APP_NAME"
echo ""
echo "2. Test audit log access (should work now with UC groups):"
echo "   curl https://<your-app-url>/audit/log \\"
echo "     -H 'X-Forwarded-Email: lawrence.kyei@databricks.com'"
echo ""
echo "3. If you get permission errors, run UC setup:"
echo "   # In a SQL notebook or SQL Editor:"
echo "   SOURCE infra/uc_quick_setup.sql;"
echo ""
echo "════════════════════════════════════════════════════════════════════"
