#!/usr/bin/env python3
"""Check debug endpoint"""
import requests

with open("/tmp/token.txt") as f:
    token = f.read().strip()

app_url = "https://acme-retail-cs-receipt-lookup-984752964297111.11.azure.databricksapps.com"

headers = {
    "Authorization": f"Bearer {token}",
}

print("Checking debug endpoint...")
try:
    resp = requests.get(f"{app_url}/debug/lakebase-config", headers=headers, timeout=10)
    print(f"Status Code: {resp.status_code}")
    if resp.status_code == 200:
        import json
        print(json.dumps(resp.json(), indent=2))
    else:
        print(f"Response: {resp.text}")
except Exception as e:
    print(f"Error: {e}")
