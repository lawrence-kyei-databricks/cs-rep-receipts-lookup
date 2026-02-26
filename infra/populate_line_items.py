"""
Populate receipt_line_items with realistic retail product data.

This script:
1. Connects to Lakebase
2. Finds existing receipts from receipt_lookup
3. Parses item_summary and creates real line items with actual prices
4. Inserts data into receipt_line_items table

Run this after creating the receipt_line_items table.
"""

import psycopg
from psycopg.rows import dict_row
import os
import json

# Realistic retail product catalog with actual SKUs and prices
# SKU format matches product_embeddings table (SKU-1001 through SKU-1015)
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
    "banana": {"sku": "SKU-1005", "name": "Organic Bananas", "brand": None, "category_l1": "PRODUCE", "category_l2": "FRUIT", "price_cents": 79},  # per lb
    "apple": {"sku": "SKU-1006", "name": "Honeycrisp Apples", "brand": None, "category_l1": "PRODUCE", "category_l2": "FRUIT", "price_cents": 299},  # per lb
    "tomato": {"sku": "SKU-1007", "name": "Roma Tomatoes", "brand": None, "category_l1": "PRODUCE", "category_l2": "VEGETABLE", "price_cents": 199},  # per lb
    "lettuce": {"sku": "SKU-1007", "name": "Roma Tomatoes", "brand": None, "category_l1": "PRODUCE", "category_l2": "VEGETABLE", "price_cents": 249},

    # MEAT
    "chicken": {"sku": "SKU-1008", "name": "Chicken Breast Boneless", "brand": "Bell & Evans", "category_l1": "MEAT", "category_l2": "POULTRY", "price_cents": 899},  # per lb
    "beef": {"sku": "SKU-1009", "name": "Ground Beef 80/20", "brand": "Local Farm", "category_l1": "MEAT", "category_l2": "BEEF", "price_cents": 699},  # per lb
    "ribeye": {"sku": "SKU-1014", "name": "Ribeye Steak 2lb", "brand": "Premium Choice", "category_l1": "MEAT", "category_l2": "BEEF", "price_cents": 2999},
    "steak": {"sku": "SKU-1014", "name": "Ribeye Steak 2lb", "brand": "Premium Choice", "category_l1": "MEAT", "category_l2": "BEEF", "price_cents": 2999},
    "salmon": {"sku": "SKU-1010", "name": "Atlantic Salmon Fillet", "brand": "Wild Caught", "category_l1": "MEAT", "category_l2": "SEAFOOD", "price_cents": 1499},  # per lb

    # BAKERY
    "bread": {"sku": "SKU-1011", "name": "Sourdough Bread Loaf", "brand": "Giant Eagle Bakery", "category_l1": "BAKERY", "category_l2": "BREAD", "price_cents": 399},
    "bagel": {"sku": "SKU-1011", "name": "Sourdough Bread Loaf", "brand": "Giant Eagle Bakery", "category_l1": "BAKERY", "category_l2": "BREAD", "price_cents": 499},

    # PANTRY
    "pasta": {"sku": "SKU-1012", "name": "Penne Pasta 1lb", "brand": "Barilla", "category_l1": "PANTRY", "category_l2": "PASTA", "price_cents": 299},
    "rice": {"sku": "SKU-1012", "name": "Penne Pasta 1lb", "brand": "Barilla", "category_l1": "PANTRY", "category_l2": "GRAINS", "price_cents": 899},
    "beans": {"sku": "SKU-1013", "name": "Black Beans Can 15oz", "brand": "Goya", "category_l1": "PANTRY", "category_l2": "CANNED", "price_cents": 149},
}

def match_product(item_name: str):
    """
    Match item name from receipt to a product in catalog.
    Uses fuzzy matching on key words.
    """
    item_lower = item_name.lower()

    # Try exact substring matches first
    for key, product in PRODUCT_CATALOG.items():
        if key in item_lower:
            return product

    # Fallback: generic item
    return {
        "sku": "GE-MISC-9999",
        "name": item_name.strip(),
        "brand": "Generic",
        "category_l1": "MISC",
        "category_l2": "OTHER",
        "price_cents": 599  # Default $5.99
    }

def populate_line_items(conninfo: str):
    """
    Populate receipt_line_items from existing receipt_lookup data.
    """
    conn = psycopg.connect(conninfo)
    cursor = conn.cursor(row_factory=dict_row)

    try:
        # First, create the table if it doesn't exist
        print("Creating receipt_line_items table...")
        with open('/Users/lawrence.kyei/Desktop/dbx-demos/receipts_lookup/infra/add_line_items_table.sql', 'r') as f:
            cursor.execute(f.read())
        conn.commit()
        print("✓ Table created/verified")

        # Get all receipts that have item_summary
        print("\nFetching receipts from receipt_lookup...")
        cursor.execute("""
            SELECT transaction_id, item_summary, subtotal_cents, item_count
            FROM receipt_lookup
            WHERE item_summary IS NOT NULL
            ORDER BY transaction_ts DESC
        """)
        receipts = cursor.fetchall()
        print(f"✓ Found {len(receipts)} receipts to process")

        # Process each receipt
        total_items_inserted = 0
        for receipt in receipts:
            transaction_id = receipt['transaction_id']
            item_summary = receipt['item_summary']
            subtotal_cents = receipt['subtotal_cents']

            if not item_summary:
                continue

            # Parse items from comma-separated summary
            items = [item.strip() for item in item_summary.split(',')]

            # Match each item to products and calculate prices
            line_items = []
            remaining_cents = subtotal_cents

            for i, item_name in enumerate(items):
                is_last = (i == len(items) - 1)

                # Match to product catalog
                product = match_product(item_name)

                line_number = i + 1
                quantity = 1.0

                if is_last:
                    # Last item gets exact remaining amount
                    unit_price_cents = remaining_cents
                    line_total_cents = remaining_cents
                else:
                    # Use catalog price, but ensure we don't exceed remaining
                    unit_price_cents = min(product['price_cents'], remaining_cents - 100)
                    line_total_cents = unit_price_cents
                    remaining_cents -= line_total_cents

                line_items.append({
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

            # Insert line items for this receipt
            if line_items:
                for item in line_items:
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
                        ON CONFLICT (transaction_id, line_number) DO UPDATE SET
                            sku = EXCLUDED.sku,
                            product_name = EXCLUDED.product_name,
                            unit_price_cents = EXCLUDED.unit_price_cents,
                            line_total_cents = EXCLUDED.line_total_cents
                    """, item)

                total_items_inserted += len(line_items)
                print(f"  ✓ {transaction_id}: {len(line_items)} items")

        conn.commit()
        print(f"\n✓ Successfully populated {total_items_inserted} line items for {len(receipts)} receipts")

        # Verify the data
        print("\nVerifying data...")
        cursor.execute("""
            SELECT
                COUNT(*) as total_lines,
                COUNT(DISTINCT transaction_id) as total_receipts,
                SUM(line_total_cents)/100.0 as total_revenue
            FROM receipt_line_items
        """)
        stats = cursor.fetchone()
        print(f"  Total line items: {stats['total_lines']}")
        print(f"  Total receipts: {stats['total_receipts']}")
        print(f"  Total revenue: ${stats['total_revenue']:.2f}")

    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    # Get Lakebase connection from environment (same as app)
    LAKEBASE_HOST = "instance-48e7b373-3240-4e42-a9f0-d7289706e1c6.database.azuredatabricks.net"
    LAKEBASE_PORT = 5432
    LAKEBASE_DATABASE = "giant_eagle"

    # Get OAuth token for authentication
    import subprocess
    result = subprocess.run(
        [
            'env', '-u', 'DATABRICKS_CONFIG_PROFILE',
            'databricks', 'lakebase', 'generate-temporary-password',
            '--instance-name', 'instance-48e7b373-3240-4e42-a9f0-d7289706e1c6'
        ],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(f"Error getting Lakebase password: {result.stderr}")
        exit(1)

    password = result.stdout.strip()

    # Build connection string
    conninfo = f"host={LAKEBASE_HOST} port={LAKEBASE_PORT} dbname={LAKEBASE_DATABASE} user=lawrence.kyei@databricks.com password={password} sslmode=require"

    print("Connecting to Lakebase...")
    populate_line_items(conninfo)
    print("\n✅ Done!")
