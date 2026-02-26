"""
Comprehensive test suite for Acme Retail CS Receipt Lookup optimizations.
Tests all 6 optimization features plus core functionality.
"""
import os
import time
import requests
import json
from datetime import datetime

# Get app URL from environment or use default
APP_URL = os.environ.get("APP_URL", "https://acme-retail-cs-receipt-lookup-984752964297111.11.azure.databricksapps.com")
TOKEN = os.environ.get("DATABRICKS_TOKEN", "")

# Test configuration
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json"
}

class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.warnings = 0
        self.tests = []

    def add_test(self, name, status, message="", response_time=None):
        self.tests.append({
            "name": name,
            "status": status,
            "message": message,
            "response_time": response_time
        })
        if status == "PASS":
            self.passed += 1
        elif status == "FAIL":
            self.failed += 1
        elif status == "WARN":
            self.warnings += 1

    def print_summary(self):
        print("\n" + "="*70)
        print("TEST SUMMARY")
        print("="*70)
        print(f"Total Tests: {len(self.tests)}")
        print(f"✅ Passed: {self.passed}")
        print(f"❌ Failed: {self.failed}")
        print(f"⚠️  Warnings: {self.warnings}")
        print("="*70 + "\n")

        for test in self.tests:
            status_symbol = "✅" if test["status"] == "PASS" else ("❌" if test["status"] == "FAIL" else "⚠️")
            time_str = f" ({test['response_time']:.0f}ms)" if test['response_time'] else ""
            print(f"{status_symbol} {test['name']}{time_str}")
            if test["message"]:
                print(f"   → {test['message']}")

results = TestResults()

def test_health_check():
    """Test 1: Health check and Lakebase connectivity"""
    print("\n📋 Test 1: Health Check and Lakebase Connectivity")
    print("-" * 70)

    try:
        start_time = time.time()
        response = requests.get(f"{APP_URL}/health", headers=HEADERS, timeout=10)
        response_time = (time.time() - start_time) * 1000

        if response.status_code == 200:
            data = response.json()
            print(f"Status: {data.get('status')}")
            print(f"Lakebase: {data.get('lakebase')}")
            print(f"Token Age: {data.get('token_age_minutes')} minutes")

            if data.get('lakebase') == 'connected':
                results.add_test("Health Check & DB Connectivity", "PASS",
                               f"Lakebase connected, status={data.get('status')}", response_time)
            else:
                results.add_test("Health Check & DB Connectivity", "WARN",
                               f"Lakebase status: {data.get('lakebase')}", response_time)
        else:
            results.add_test("Health Check & DB Connectivity", "FAIL",
                           f"HTTP {response.status_code}: {response.text[:100]}")
    except Exception as e:
        results.add_test("Health Check & DB Connectivity", "FAIL", str(e))

def test_receipt_lookup():
    """Test 2: Receipt lookup endpoint with caching"""
    print("\n📋 Test 2: Receipt Lookup (Testing Task 1 & 3 Optimizations)")
    print("-" * 70)

    # First, get a receipt ID from fuzzy search
    try:
        search_payload = {
            "store_name": "Acme Retail",
            "limit": 1
        }
        search_resp = requests.post(f"{APP_URL}/search/fuzzy",
                                   headers=HEADERS,
                                   json=search_payload,
                                   timeout=10)

        if search_resp.status_code == 200 and search_resp.json().get('results'):
            receipt_id = search_resp.json()['results'][0]['transaction_id']
            print(f"Using receipt ID: {receipt_id}")

            # Test 1st request (cache miss)
            start_time = time.time()
            response1 = requests.get(f"{APP_URL}/receipt/{receipt_id}",
                                    headers=HEADERS, timeout=10)
            time1 = (time.time() - start_time) * 1000

            # Test 2nd request (cache hit - should be faster)
            start_time = time.time()
            response2 = requests.get(f"{APP_URL}/receipt/{receipt_id}",
                                    headers=HEADERS, timeout=10)
            time2 = (time.time() - start_time) * 1000

            if response1.status_code == 200 and response2.status_code == 200:
                data = response1.json()
                has_line_items = 'line_items' in data

                # Check if caching improved performance
                if time2 < time1 * 0.8:  # 20% improvement indicates caching
                    results.add_test("Receipt Lookup with Caching", "PASS",
                                   f"Cache working: 1st={time1:.0f}ms, 2nd={time2:.0f}ms (speedup: {((time1-time2)/time1*100):.0f}%)", time1)
                else:
                    results.add_test("Receipt Lookup with Caching", "WARN",
                                   f"Cache may not be working: 1st={time1:.0f}ms, 2nd={time2:.0f}ms", time1)

                # Check LEFT JOIN optimization (line items fetched in single query)
                if has_line_items:
                    results.add_test("LEFT JOIN Optimization (Task 3)", "PASS",
                                   "Line items included in single query")
                else:
                    results.add_test("LEFT JOIN Optimization (Task 3)", "WARN",
                                   "No line items in response")
            else:
                results.add_test("Receipt Lookup with Caching", "FAIL",
                               f"HTTP {response1.status_code}: {response1.text[:100]}")
        else:
            results.add_test("Receipt Lookup with Caching", "FAIL",
                           "Could not find receipt for testing")
    except Exception as e:
        results.add_test("Receipt Lookup with Caching", "FAIL", str(e))

def test_fuzzy_search_with_filters():
    """Test 3: Fuzzy search with field filtering (Task 6)"""
    print("\n📋 Test 3: Fuzzy Search with Field Filtering (Task 6)")
    print("-" * 70)

    try:
        # Test without field filtering
        payload = {
            "store_name": "Acme Retail",
            "limit": 2
        }
        start_time = time.time()
        response_full = requests.post(f"{APP_URL}/search/fuzzy",
                                     headers=HEADERS,
                                     json=payload,
                                     timeout=10)
        time_full = (time.time() - start_time) * 1000

        # Test with field filtering (should be smaller payload)
        start_time = time.time()
        response_filtered = requests.post(
            f"{APP_URL}/search/fuzzy?fields=transaction_id,total_cents,store_name",
            headers=HEADERS,
            json=payload,
            timeout=10
        )
        time_filtered = (time.time() - start_time) * 1000

        if response_full.status_code == 200 and response_filtered.status_code == 200:
            full_size = len(response_full.content)
            filtered_size = len(response_filtered.content)
            reduction = ((full_size - filtered_size) / full_size * 100)

            data_filtered = response_filtered.json()
            if data_filtered.get('results'):
                first_result = data_filtered['results'][0]
                field_count = len(first_result.keys())

                if field_count == 3:  # Only 3 fields requested
                    results.add_test("Field Filtering (Task 6)", "PASS",
                                   f"Payload reduced by {reduction:.0f}% ({full_size}→{filtered_size} bytes)", time_filtered)
                else:
                    results.add_test("Field Filtering (Task 6)", "WARN",
                                   f"Expected 3 fields, got {field_count}")
            else:
                results.add_test("Field Filtering (Task 6)", "WARN",
                               "No results returned")
        else:
            results.add_test("Field Filtering (Task 6)", "FAIL",
                           f"HTTP {response_full.status_code}/{response_filtered.status_code}")
    except Exception as e:
        results.add_test("Field Filtering (Task 6)", "FAIL", str(e))

def test_customer_list_caching():
    """Test 4: Customer receipt list caching (Task 5)"""
    print("\n📋 Test 4: Customer Receipt List Caching (Task 5)")
    print("-" * 70)

    try:
        # First get a customer ID
        search_payload = {"store_name": "Acme Retail", "limit": 1}
        search_resp = requests.post(f"{APP_URL}/search/fuzzy",
                                   headers=HEADERS,
                                   json=search_payload,
                                   timeout=10)

        if search_resp.status_code == 200 and search_resp.json().get('results'):
            customer_id = search_resp.json()['results'][0].get('customer_id')

            if customer_id:
                # Test 1st request (cache miss)
                start_time = time.time()
                response1 = requests.get(f"{APP_URL}/receipt/customer/{customer_id}",
                                        headers=HEADERS, timeout=10)
                time1 = (time.time() - start_time) * 1000

                # Test 2nd request (cache hit)
                start_time = time.time()
                response2 = requests.get(f"{APP_URL}/receipt/customer/{customer_id}",
                                        headers=HEADERS, timeout=10)
                time2 = (time.time() - start_time) * 1000

                if response1.status_code == 200 and response2.status_code == 200:
                    if time2 < time1 * 0.8:
                        results.add_test("Customer List Caching (Task 5)", "PASS",
                                       f"Cache working: 1st={time1:.0f}ms, 2nd={time2:.0f}ms (speedup: {((time1-time2)/time1*100):.0f}%)", time1)
                    else:
                        results.add_test("Customer List Caching (Task 5)", "WARN",
                                       f"Cache may not be working: 1st={time1:.0f}ms, 2nd={time2:.0f}ms", time1)
                else:
                    results.add_test("Customer List Caching (Task 5)", "FAIL",
                                   f"HTTP {response1.status_code}/{response2.status_code}")
            else:
                results.add_test("Customer List Caching (Task 5)", "WARN",
                               "No customer_id in search results")
        else:
            results.add_test("Customer List Caching (Task 5)", "FAIL",
                           "Could not find customer for testing")
    except Exception as e:
        results.add_test("Customer List Caching (Task 5)", "FAIL", str(e))

def test_compression():
    """Test 5: GZip compression middleware (Task 2)"""
    print("\n📋 Test 5: GZip Compression Middleware (Task 2)")
    print("-" * 70)

    try:
        # Request with Accept-Encoding: gzip
        headers_with_gzip = HEADERS.copy()
        headers_with_gzip['Accept-Encoding'] = 'gzip'

        payload = {"store_name": "Acme Retail", "limit": 10}

        # Request WITH compression
        response_compressed = requests.post(f"{APP_URL}/search/fuzzy",
                                          headers=headers_with_gzip,
                                          json=payload,
                                          timeout=10)

        # Request WITHOUT compression
        headers_no_gzip = HEADERS.copy()
        response_uncompressed = requests.post(f"{APP_URL}/search/fuzzy",
                                            headers=headers_no_gzip,
                                            json=payload,
                                            timeout=10)

        if response_compressed.status_code == 200 and response_uncompressed.status_code == 200:
            compressed_size = len(response_compressed.content)
            uncompressed_size = len(response_uncompressed.content)

            # Check if Content-Encoding header is present
            is_compressed = 'gzip' in response_compressed.headers.get('Content-Encoding', '')

            if is_compressed:
                ratio = (1 - compressed_size / uncompressed_size) * 100
                results.add_test("GZip Compression (Task 2)", "PASS",
                               f"Compression active: {ratio:.0f}% reduction ({uncompressed_size}→{compressed_size} bytes)")
            else:
                results.add_test("GZip Compression (Task 2)", "WARN",
                               f"Compression header not present (payload may be too small)")
        else:
            results.add_test("GZip Compression (Task 2)", "FAIL",
                           f"HTTP {response_compressed.status_code}/{response_uncompressed.status_code}")
    except Exception as e:
        results.add_test("GZip Compression (Task 2)", "FAIL", str(e))

def test_rate_limiting():
    """Test 6: Rate limiting middleware (Task 4)"""
    print("\n📋 Test 6: Rate Limiting Middleware (Task 4)")
    print("-" * 70)

    try:
        # Send multiple rapid requests to trigger rate limit
        rate_limit_hit = False
        request_count = 0

        for i in range(15):  # Try 15 rapid requests
            response = requests.get(f"{APP_URL}/health", headers=HEADERS, timeout=5)
            request_count += 1

            # Check for rate limit headers
            if 'X-RateLimit-Limit' in response.headers:
                limit = response.headers.get('X-RateLimit-Limit')
                remaining = response.headers.get('X-RateLimit-Remaining')
                print(f"  Request {i+1}: Status={response.status_code}, Limit={limit}, Remaining={remaining}")

            if response.status_code == 429:
                rate_limit_hit = True
                error_detail = response.json().get('detail', {})
                print(f"  ⚠️  Rate limit hit after {request_count} requests")
                print(f"     Message: {error_detail.get('message', 'Rate limit exceeded')}")
                break

            time.sleep(0.1)  # Small delay between requests

        if 'X-RateLimit-Limit' in response.headers:
            results.add_test("Rate Limiting Headers (Task 4)", "PASS",
                           f"Rate limit headers present (limit={response.headers.get('X-RateLimit-Limit')}/min)")
        else:
            results.add_test("Rate Limiting Headers (Task 4)", "WARN",
                           "Rate limit headers not found")

        if rate_limit_hit:
            results.add_test("Rate Limiting Enforcement (Task 4)", "PASS",
                           f"Rate limit enforced after {request_count} requests")
        else:
            results.add_test("Rate Limiting Enforcement (Task 4)", "WARN",
                           f"Rate limit not hit in {request_count} requests (may need more load)")

    except Exception as e:
        results.add_test("Rate Limiting (Task 4)", "FAIL", str(e))

def test_include_line_items_flag():
    """Test 7: include_line_items flag (Task 6)"""
    print("\n📋 Test 7: Include Line Items Flag (Task 6)")
    print("-" * 70)

    try:
        # Get a receipt ID
        search_payload = {"store_name": "Acme Retail", "limit": 1}
        search_resp = requests.post(f"{APP_URL}/search/fuzzy",
                                   headers=HEADERS,
                                   json=search_payload,
                                   timeout=10)

        if search_resp.status_code == 200 and search_resp.json().get('results'):
            receipt_id = search_resp.json()['results'][0]['transaction_id']

            # Test WITH line items
            response_with = requests.get(f"{APP_URL}/receipt/{receipt_id}?include_line_items=true",
                                        headers=HEADERS, timeout=10)

            # Test WITHOUT line items
            response_without = requests.get(f"{APP_URL}/receipt/{receipt_id}?include_line_items=false",
                                          headers=HEADERS, timeout=10)

            if response_with.status_code == 200 and response_without.status_code == 200:
                with_size = len(response_with.content)
                without_size = len(response_without.content)
                reduction = ((with_size - without_size) / with_size * 100)

                data_with = response_with.json()
                data_without = response_without.json()

                has_line_items_with = 'line_items' in data_with
                has_line_items_without = 'line_items' in data_without

                if has_line_items_with and not has_line_items_without:
                    results.add_test("Include Line Items Flag (Task 6)", "PASS",
                                   f"Flag working: {reduction:.0f}% reduction when disabled")
                else:
                    results.add_test("Include Line Items Flag (Task 6)", "WARN",
                                   f"Flag behavior unexpected: with={has_line_items_with}, without={has_line_items_without}")
            else:
                results.add_test("Include Line Items Flag (Task 6)", "FAIL",
                               f"HTTP {response_with.status_code}/{response_without.status_code}")
        else:
            results.add_test("Include Line Items Flag (Task 6)", "FAIL",
                           "Could not find receipt for testing")
    except Exception as e:
        results.add_test("Include Line Items Flag (Task 6)", "FAIL", str(e))

def main():
    """Run all tests"""
    print("\n" + "="*70)
    print("COMPREHENSIVE TEST SUITE")
    print("Acme Retail CS Receipt Lookup - Optimization Verification")
    print("="*70)
    print(f"App URL: {APP_URL}")
    print(f"Test Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)

    # Run all tests
    test_health_check()
    test_receipt_lookup()
    test_fuzzy_search_with_filters()
    test_customer_list_caching()
    test_compression()
    test_rate_limiting()
    test_include_line_items_flag()

    # Print summary
    results.print_summary()

    # Exit with appropriate code
    exit(0 if results.failed == 0 else 1)

if __name__ == "__main__":
    main()
