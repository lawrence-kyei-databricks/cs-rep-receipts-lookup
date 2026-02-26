#!/usr/bin/env python3
"""
Test script to diagnose user access issues with the Acme Retail CS Receipt Lookup app.

This script simulates what a user's browser does when accessing the app and helps
diagnose why data might not be returning.

Usage:
    python test_user_access.py --email user@company.com
"""
import sys
import argparse
import json
import os
import subprocess
import requests

# App configuration
APP_URL = "https://acme-retail-cs-receipt-lookup-984752964297111.11.azure.databricksapps.com"

# Known-good test cases from sample data
TEST_CASES = [
    {
        "name": "Customer cust-5001 at East Liberty",
        "payload": {
            "customer_id": "cust-5001",
            "store_name": "East Liberty",
            "limit": 5
        },
        "expected_count": 2
    },
    {
        "name": "Shadyside store receipts",
        "payload": {
            "store_name": "Shadyside",
            "limit": 10
        },
        "expected_count": "> 0"
    },
    {
        "name": "Customer cust-5001 only",
        "payload": {
            "customer_id": "cust-5001",
            "limit": 10
        },
        "expected_count": "> 0"
    },
]


def main():
    parser = argparse.ArgumentParser(description="Test user access to Acme Retail app")
    parser.add_argument(
        "--email",
        required=True,
        help="Email of the user to test (e.g., friend@company.com)"
    )
    args = parser.parse_args()

    print(f"Testing Acme Retail CS Receipt Lookup App Access")
    print(f"=" * 70)
    print(f"User: {args.email}")
    print(f"App URL: {APP_URL}")
    print()

    # Step 1: Get Databricks auth token
    print("Step 1: Obtaining Databricks authentication token...")
    try:
        # Try to get token from environment first
        token = os.environ.get("DATABRICKS_TOKEN")

        if not token:
            # Try reading from /tmp/token.txt if it exists
            token_file = "/tmp/token.txt"
            if os.path.exists(token_file):
                with open(token_file, 'r') as f:
                    token = f.read().strip()
                    print(f"  📄 Using token from {token_file}")

        if not token:
            # Try getting via databricks CLI
            print("  🔐 Generating new token via databricks CLI...")
            result = subprocess.run(
                ["env", "-u", "DATABRICKS_CONFIG_PROFILE", "databricks", "auth", "token",
                 "--host", "https://adb-984752964297111.11.azuredatabricks.net", "-o", "json"],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                token_data = json.loads(result.stdout)
                token = token_data.get("access_token")

        if not token:
            raise Exception("Could not obtain token from any source")

        print(f"✅ Token obtained (length: {len(token)})")
    except Exception as e:
        print(f"❌ Failed to obtain token: {e}")
        print(f"\n💡 Tip: Run this first to generate a token:")
        print(f"   env -u DATABRICKS_CONFIG_PROFILE databricks auth token \\")
        print(f"     --host https://adb-984752964297111.11.azuredatabricks.net > /tmp/token.txt")
        sys.exit(1)

    # Step 2: Test each endpoint
    print()
    print("Step 2: Testing API endpoints with known-good data...")
    print()

    for i, test in enumerate(TEST_CASES, 1):
        print(f"Test {i}: {test['name']}")
        print(f"  Payload: {json.dumps(test['payload'], indent=4)}")

        try:
            response = requests.post(
                f"{APP_URL}/search/fuzzy",
                json=test['payload'],
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Forwarded-Email": args.email,  # Simulate user identity
                    "Content-Type": "application/json"
                },
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                count = data.get("count", 0)
                searched_by = data.get("searched_by", "unknown")

                print(f"  ✅ Status: {response.status_code}")
                print(f"  📊 Results: {count} receipts found")
                print(f"  👤 Searched by: {searched_by}")

                if count > 0:
                    print(f"  📝 Sample result:")
                    sample = data["results"][0]
                    print(f"     Transaction: {sample.get('transaction_id')}")
                    print(f"     Store: {sample.get('store_name')}")
                    print(f"     Date: {sample.get('transaction_ts', 'N/A')[:10]}")
                    print(f"     Total: ${sample.get('total_cents', 0) / 100:.2f}")
                else:
                    print(f"  ⚠️  Expected {test['expected_count']} results but got 0")
                    print(f"  💡 This might indicate:")
                    print(f"     - Input case sensitivity issue")
                    print(f"     - Whitespace in inputs")
                    print(f"     - Data not available for this user")
            else:
                print(f"  ❌ Status: {response.status_code}")
                print(f"  Error: {response.text[:200]}")

        except requests.exceptions.RequestException as e:
            print(f"  ❌ Request failed: {e}")

        print()

    # Step 3: Test health endpoint
    print("Step 3: Testing app health endpoint...")
    try:
        response = requests.get(
            f"{APP_URL}/health",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        if response.status_code == 200:
            health = response.json()
            print(f"✅ Health check passed")
            print(f"   Lakebase: {health.get('lakebase', 'unknown')}")
            print(f"   Token age: {health.get('lakebase_token_age_seconds', 'N/A')}s")
        else:
            print(f"❌ Health check failed: {response.status_code}")
    except Exception as e:
        print(f"❌ Health check error: {e}")

    print()
    print("=" * 70)
    print("Diagnosis Summary:")
    print()
    print("If all tests passed and returned data:")
    print("  ➜ User permissions are working correctly")
    print("  ➜ Issue is likely with user inputs (case, typos, whitespace)")
    print()
    print("If tests failed with 401/403:")
    print("  ➜ User may not have app-level permissions")
    print("  ➜ Check Databricks Apps permissions tab")
    print()
    print("If tests returned 0 results:")
    print("  ➜ User may be entering incorrect search parameters")
    print("  ➜ Try exact values from test cases above")
    print()
    print("Common issues to check:")
    print("  • Store name case: 'East Liberty' not 'east liberty'")
    print("  • Customer ID format: 'cust-5001' not 'Cust-5001'")
    print("  • Whitespace: no leading/trailing spaces in inputs")
    print("  • Browser cache: try incognito/private mode")


if __name__ == "__main__":
    main()
