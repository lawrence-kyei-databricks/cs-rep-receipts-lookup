#!/usr/bin/env python3
"""Test app endpoints to see error details"""
import requests
import json

# Read token
with open("/tmp/token.txt") as f:
    token = f.read().strip()

app_url = "https://acme-retail-cs-receipt-lookup-984752964297111.11.azure.databricksapps.com"

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "X-Forwarded-Email": "lawrence.kyei@databricks.com"
}

print("=" * 80)
print("Testing /search/fuzzy endpoint (expecting 500 error)")
print("=" * 80)

fuzzy_payload = {
    "customer_id": "cust-5001",
    "store_name": "East Liberty",
    "limit": 10
}

try:
    resp = requests.post(f"{app_url}/search/fuzzy", json=fuzzy_payload, headers=headers, timeout=30)
    print(f"Status Code: {resp.status_code}")
    print(f"Response: {resp.text}")
except Exception as e:
    print(f"Error: {e}")

print("\n" + "=" * 80)
print("Testing /search endpoint (expecting 404 error)")
print("=" * 80)

search_payload = {
    "query": "chicken",
    "customer_id": "cust-5001"
}

try:
    resp = requests.post(f"{app_url}/search", json=search_payload, headers=headers, timeout=30)
    print(f"Status Code: {resp.status_code}")
    print(f"Response: {resp.text}")
except Exception as e:
    print(f"Error: {e}")

print("\n" + "=" * 80)
print("Testing /search/ endpoint (with trailing slash)")
print("=" * 80)

try:
    resp = requests.post(f"{app_url}/search/", json=search_payload, headers=headers, timeout=30)
    print(f"Status Code: {resp.status_code}")
    print(f"Response: {resp.text}")
except Exception as e:
    print(f"Error: {e}")

print("\nDone!")
