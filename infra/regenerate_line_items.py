"""
Regenerate receipt_line_items with corrected SKU format (SKU-1001 through SKU-1015).
This script clears existing data and repopulates from receipt_lookup.
"""

import psycopg
from psycopg.rows import dict_row
import subprocess
import sys

# Updated product catalog with SKU-#### format matching embeddings
PRODUCT_CATALOG = {
    # DAIRY
    "milk": {"sku": "SKU-1001", "name": "Organic Whole Milk 1 Gal", "brand": "Snowville Creamery", "category_l1": "DAIRY", "category_l2": "MILK", "price_cents": 599},
    "egg": {"sku": "SKU-1002", "name": "Organic Eggs Large", "brand": "Vital Farms", "category_l1": "DAIRY", "category_l2": "EGGS", "price_cents": 699},

    # CHEESE / DELI
    "roquefort": {"sku": "SKU-1003", "name": "Roquefort Cheese 8oz", "brand": "Papillon", "category_l1": "DELI", "category_l2": "CHEESE", "price_cents": 1299},
    "brie": {"sku": "SKU-1004", "name": "Brie Cheese 8oz", "brand": "President", "category_l1": "DELI", "category_l2": "CHEESE", "price_cents": 899},
    "fancy": {"sku": "SKU-1015", "name": "Fancy Cheese Assortment", "brand": "Artisan Selection", "category_l1": "DELI", "category_l2": "CHEESE", "price_cents": 1799},
    "cheese": {"sku": "SKU-1003", "name": "Roquefort Cheese 8oz", "brand": "Papillon", "category_l1": "DELI", "category_l2": "CHEESE", "price_cents": 1299},

    # PRODUCE
    "banana": {"sku": "SKU-1005", "name": "Organic Bananas", "brand": None, "category_l1": "PRODUCE", "category_l2": "FRUIT", "price_cents": 79},
    "apple": {"sku": "SKU-1006", "name": "Honeycrisp Apples", "brand": None, "category_l1": "PRODUCE", "category_l2": "FRUIT", "price_cents": 299},
    "tomato": {"sku": "SKU-1007", "name": "Roma Tomatoes", "brand": None, "category_l1": "PRODUCE", "category_l2": "VEGETABLE", "price_cents": 199},
    "lettuce": {"sku": "SKU-1007", "name": "Roma Tomatoes", "brand": None, "category_l1": "PRODUCE", "category_l2": "VEGETABLE", "price_cents": 249},

    # MEAT
    "chicken": {"sku": "SKU-1008", "name": "Chicken Breast Boneless", "brand": "Bell & Evans", "category_l1": "MEAT", "category_l2": "POULTRY", "price_cents": 899},
    "beef": {"sku": "SKU-1009", "name": "Ground Beef 80/20", "brand": "Local Farm", "category_l1": "MEAT", "category_l2": "BEEF", "price_cents": 699},
    "ribeye": {"sku": "SKU-1014", "name": "Ribeye Steak 2lb", "brand": "Premium Choice", "category_l1": "MEAT", "category_l2": "BEEF", "price_cents": 2999},
    "steak": {"sku": "SKU-1014", "name": "Ribeye Steak 2lb", "brand": "Premium Choice", "category_l1": "MEAT", "category_l2": "BEEF", "price_cents": 2999},
    "salmon": {"sku": "SKU-1010", "name": "Atlantic Salmon Fillet", "brand": "Wild Caught", "category_l1": "MEAT", "category_l2": "SEAFOOD", "price_cents": 1499},

    # BAKERY
    "bread": {"sku": "SKU-1011", "name": "Sourdough Bread Loaf", "brand": "Giant Eagle Bakery", "category_l1": "BAKERY", "category_l2": "BREAD", "price_cents": 399},
    "bagel": {"sku": "SKU-1011", "name": "Sourdough Bread Loaf", "brand": "Giant Eagle Bakery", "category_l1": "BAKERY", "category_l2": "BREAD", "price_cents": 499},

    # PANTRY
    "pasta": {"sku": "SKU-1012", "name": "Penne Pasta 1lb", "brand": "Barilla", "category_l1": "PANTRY", "category_l2": "PASTA", "price_cents": 299},
    "rice": {"sku": "SKU-1012", "name": "Penne Pasta 1lb", "brand": "Barilla", "category_l1": "PANTRY", "category_l2": "GRAINS", "price_cents": 899},
    "beans": {"sku": "SKU-1013", "name": "Black Beans Can 15oz", "brand": "Goya", "category_l1": "PANTRY", "category_l2": "CANNED", "price_cents": 149},
}

def match_product(item_name: str):
    """Match item name to product catalog."""
    item_lower = item_name.lower()
    for key, product in PRODUCT_CATALOG.items():
        if key in item_lower:
            return product
    return {
        "sku": "SKU-9999",
        "name": item_name.strip(),
        "brand": "Generic",
        "category_l1": "MISC",
        "category_l2": "OTHER",
        "price_cents": 599
    }

def get_oauth_token():
    """Get OAuth token for Lakebase authentication."""
    import json
    print("Getting OAuth token...")
    result = subprocess.run(
        ['env', '-u', 'DATABRICKS_CONFIG_PROFILE', 'databricks', 'auth', 'token', '--host', 'https://adb-984752964297111.11.azuredatabricks.net'],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f"Error getting token: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    # Parse JSON response and extract access_token
    token_data = json.loads(result.stdout.strip())
    return token_data['access_token']

def main():
    # Connection details
    LAKEBASE_HOST = "instance-48e7b373-3240-4e42-a9f0-d7289706e1c6.database.azuredatabricks.net"
    LAKEBASE_PORT = 5432
    LAKEBASE_DATABASE = "giant_eagle"

    # Get OAuth token
    password = get_oauth_token()

    # Build connection string
    conninfo = f"host={LAKEBASE_HOST} port={LAKEBASE_PORT} dbname={LAKEBASE_DATABASE} user=lawrence.kyei@databricks.com password={password} sslmode=require"

    print(f"Connecting to Lakebase...")
    conn = psycopg.connect(conninfo)
    cursor = conn.cursor(row_factory=dict_row)

    try:
        # Step 1: Clear existing data
        print("\n1. Clearing existing receipt_line_items...")
        cursor.execute("DELETE FROM receipt_line_items")
        conn.commit()
        print("   ✓ Old data cleared")

        # Step 2: Get all receipts
        print("\n2. Fetching receipts from receipt_lookup...")
        cursor.execute("""
            SELECT transaction_id, item_summary, subtotal_cents
            FROM receipt_lookup
            WHERE item_summary IS NOT NULL
            ORDER BY transaction_ts DESC
        """)
        receipts = cursor.fetchall()
        print(f"   ✓ Found {len(receipts)} receipts")

        # Step 3: Regenerate line items with correct SKUs
        print("\n3. Regenerating line items with SKU-#### format...")
        total_items = 0

        for receipt in receipts:
            transaction_id = receipt['transaction_id']
            item_summary = receipt['item_summary']
            subtotal_cents = receipt['subtotal_cents']

            if not item_summary:
                continue

            items = [item.strip() for item in item_summary.split(',')]
            remaining_cents = subtotal_cents

            for i, item_name in enumerate(items):
                is_last = (i == len(items) - 1)
                product = match_product(item_name)

                line_number = i + 1
                quantity = 1.0

                if is_last:
                    unit_price_cents = remaining_cents
                    line_total_cents = remaining_cents
                else:
                    unit_price_cents = min(product['price_cents'], remaining_cents - 100)
                    line_total_cents = unit_price_cents
                    remaining_cents -= line_total_cents

                cursor.execute("""
                    INSERT INTO receipt_line_items (
                        transaction_id, line_number, sku, product_name,
                        brand, category_l1, category_l2, quantity,
                        unit_price_cents, line_total_cents, discount_cents, is_taxable
                    ) VALUES (
                        %(transaction_id)s, %(line_number)s, %(sku)s, %(product_name)s,
                        %(brand)s, %(category_l1)s, %(category_l2)s, %(quantity)s,
                        %(unit_price_cents)s, %(line_total_cents)s, %(discount_cents)s, %(is_taxable)s
                    )
                """, {
                    'transaction_id': transaction_id,
                    'line_number': line_number,
                    'sku': product['sku'],
                    'product_name': product['name'],
                    'brand': product['brand'],
                    'category_l1': product['category_l1'],
                    'category_l2': product['category_l2'],
                    'quantity': quantity,
                    'unit_price_cents': unit_price_cents,
                    'line_total_cents': line_total_cents,
                    'discount_cents': 0,
                    'is_taxable': True,
                })
                total_items += 1

            if total_items % 20 == 0:
                print(f"   Processing... {total_items} items so far")

        conn.commit()
        print(f"   ✓ Inserted {total_items} line items for {len(receipts)} receipts")

        # Step 4: Verify
        print("\n4. Verifying data...")
        cursor.execute("""
            SELECT
                COUNT(*) as total_lines,
                COUNT(DISTINCT transaction_id) as total_receipts,
                COUNT(DISTINCT sku) as unique_skus
            FROM receipt_line_items
        """)
        stats = cursor.fetchone()
        print(f"   Total line items: {stats['total_lines']}")
        print(f"   Total receipts: {stats['total_receipts']}")
        print(f"   Unique SKUs: {stats['unique_skus']}")

        # Check for ribeye specifically
        cursor.execute("""
            SELECT COUNT(*) as ribeye_count
            FROM receipt_line_items
            WHERE sku = 'SKU-1014'
        """)
        ribeye = cursor.fetchone()
        print(f"   Ribeye receipts (SKU-1014): {ribeye['ribeye_count']}")

        print("\n✅ Done! Receipt line items regenerated with correct SKU format.")

    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()
