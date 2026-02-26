#!/usr/bin/env python3
"""Check app health endpoint"""
import requests

with open("/tmp/token.txt") as f:
    token = f.read().strip()

app_url = "https://acme-retail-cs-receipt-lookup-984752964297111.11.azure.databricksapps.com"

headers = {
    "Authorization": f"Bearer {token}",
}

print("Checking app health...")
try:
    resp = requests.get(f"{app_url}/health", headers=headers, timeout=10)
    print(f"Status Code: {resp.status_code}")
    print(f"Response:\n{resp.text}")
except Exception as e:
    print(f"Error: {e}")
