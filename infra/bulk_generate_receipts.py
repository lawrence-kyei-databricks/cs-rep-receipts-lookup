"""
Bulk generate realistic receipt data for Giant Eagle CS demo.

This script generates:
- 500-1000 receipts across 60 days
- 50-100 customers (mix of frequent/occasional shoppers)
- 4 stores (East Liberty, Shadyside, Squirrel Hill, Monroeville)
- Realistic shopping patterns (more weekend traffic, common product combos)
- Both receipt_lookup and receipt_line_items tables

Uses correct SKU format (SKU-1001 to SKU-1015) matching product_embeddings.
"""

import psycopg
from psycopg.rows import dict_row
import subprocess
import json
import sys
from datetime import datetime, timedelta
import random
from decimal import Decimal

# Product catalog matching embeddings (SKU-1001 to SKU-1015)
PRODUCT_CATALOG = [
    {"sku": "SKU-1001", "name": "Organic Whole Milk 1 Gal", "brand": "Snowville Creamery", "category_l1": "DAIRY", "category_l2": "MILK", "price_cents": 599},
    {"sku": "SKU-1002", "name": "Organic Eggs Large", "brand": "Vital Farms", "category_l1": "DAIRY", "category_l2": "EGGS", "price_cents": 699},
    {"sku": "SKU-1003", "name": "Roquefort Cheese 8oz", "brand": "Papillon", "category_l1": "DELI", "category_l2": "CHEESE", "price_cents": 1299},
    {"sku": "SKU-1004", "name": "Brie Cheese 8oz", "brand": "President", "category_l1": "DELI", "category_l2": "CHEESE", "price_cents": 899},
    {"sku": "SKU-1005", "name": "Organic Bananas", "brand": None, "category_l1": "PRODUCE", "category_l2": "FRUIT", "price_cents": 79},
    {"sku": "SKU-1006", "name": "Honeycrisp Apples", "brand": None, "category_l1": "PRODUCE", "category_l2": "FRUIT", "price_cents": 299},
    {"sku": "SKU-1007", "name": "Roma Tomatoes", "brand": None, "category_l1": "PRODUCE", "category_l2": "VEGETABLE", "price_cents": 199},
    {"sku": "SKU-1008", "name": "Chicken Breast Boneless", "brand": "Bell & Evans", "category_l1": "MEAT", "category_l2": "POULTRY", "price_cents": 899},
    {"sku": "SKU-1009", "name": "Ground Beef 80/20", "brand": "Local Farm", "category_l1": "MEAT", "category_l2": "BEEF", "price_cents": 699},
    {"sku": "SKU-1010", "name": "Atlantic Salmon Fillet", "brand": "Wild Caught", "category_l1": "MEAT", "category_l2": "SEAFOOD", "price_cents": 1499},
    {"sku": "SKU-1011", "name": "Sourdough Bread Loaf", "brand": "Giant Eagle Bakery", "category_l1": "BAKERY", "category_l2": "BREAD", "price_cents": 399},
    {"sku": "SKU-1012", "name": "Penne Pasta 1lb", "brand": "Barilla", "category_l1": "PANTRY", "category_l2": "PASTA", "price_cents": 299},
    {"sku": "SKU-1013", "name": "Black Beans Can 15oz", "brand": "Goya", "category_l1": "PANTRY", "category_l2": "CANNED", "price_cents": 149},
    {"sku": "SKU-1014", "name": "Ribeye Steak 2lb", "brand": "Premium Choice", "category_l1": "MEAT", "category_l2": "BEEF", "price_cents": 2999},
    {"sku": "SKU-1015", "name": "Fancy Cheese Assortment", "brand": "Artisan Selection", "category_l1": "DELI", "category_l2": "CHEESE", "price_cents": 1799},
]

# Store names and addresses
STORES = [
    "East Liberty",
    "Shadyside",
    "Squirrel Hill",
    "Monroeville",
]

# Tender types
TENDER_TYPES = ["CREDIT", "DEBIT", "CASH", "EBT"]

# Common shopping patterns (products that are often bought together)
SHOPPING_PATTERNS = [
    ["SKU-1001", "SKU-1002", "SKU-1011"],  # Breakfast basics
    ["SKU-1008", "SKU-1007", "SKU-1012"],  # Chicken pasta dinner
    ["SKU-1009", "SKU-1005", "SKU-1013"],  # Ground beef meal
    ["SKU-1010", "SKU-1006", "SKU-1007"],  # Salmon with veggies
    ["SKU-1003", "SKU-1004", "SKU-1011"],  # Cheese plate
    ["SKU-1014"],  # Premium steak (usually alone)
    ["SKU-1015"],  # Fancy cheese (usually alone)
]


def get_oauth_token():
    """Get OAuth token for Lakebase authentication."""
    result = subprocess.run(
        ['env', '-u', 'DATABRICKS_CONFIG_PROFILE', 'databricks', 'auth', 'token',
         '--host', 'https://adb-984752964297111.11.azuredatabricks.net', '-o', 'json'],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f"Error getting token: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    token_data = json.loads(result.stdout.strip())
    return token_data['access_token']


def generate_customer_id(customer_num):
    """Generate customer ID in format cust-XXXX."""
    return f"cust-{5000 + customer_num}"


def generate_transaction_id(txn_num):
    """Generate transaction ID in format txn-XXXX."""
    return f"txn-{2000 + txn_num}"


def pick_shopping_basket():
    """
    Generate a realistic shopping basket.
    60% follow common patterns, 40% are random picks.
    """
    if random.random() < 0.6:
        # Use a common pattern
        pattern = random.choice(SHOPPING_PATTERNS)
        basket_skus = pattern.copy()

        # Maybe add 1-3 random items
        num_extra = random.choices([0, 1, 2, 3], weights=[0.3, 0.4, 0.2, 0.1])[0]
        for _ in range(num_extra):
            extra_sku = random.choice(PRODUCT_CATALOG)['sku']
            if extra_sku not in basket_skus:
                basket_skus.append(extra_sku)
    else:
        # Random basket (2-8 items)
        num_items = random.randint(2, 8)
        basket_skus = random.sample([p['sku'] for p in PRODUCT_CATALOG],
                                    min(num_items, len(PRODUCT_CATALOG)))

    return basket_skus


def generate_receipt(txn_id, customer_id, receipt_date):
    """Generate a single receipt with line items."""
    store_name = random.choice(STORES)
    tender_type = random.choices(TENDER_TYPES, weights=[0.5, 0.3, 0.15, 0.05])[0]

    # Generate card last 4 for credit/debit
    card_last4 = None
    if tender_type in ["CREDIT", "DEBIT"]:
        card_last4 = f"{random.randint(1000, 9999)}"

    # Pick items for this receipt
    basket_skus = pick_shopping_basket()

    # Generate line items
    line_items = []
    subtotal_cents = 0

    for line_num, sku in enumerate(basket_skus, 1):
        product = next(p for p in PRODUCT_CATALOG if p['sku'] == sku)

        # Quantity (most items qty=1, occasional qty=2-3 for produce/pantry)
        if product['category_l1'] in ['PRODUCE', 'PANTRY']:
            quantity = random.choices([1.0, 2.0, 3.0], weights=[0.7, 0.2, 0.1])[0]
        else:
            quantity = 1.0

        unit_price_cents = product['price_cents']
        line_total_cents = int(unit_price_cents * quantity)

        line_items.append({
            'line_number': line_num,
            'sku': sku,
            'product_name': product['name'],
            'brand': product['brand'],
            'category_l1': product['category_l1'],
            'category_l2': product['category_l2'],
            'quantity': quantity,
            'unit_price_cents': unit_price_cents,
            'line_total_cents': line_total_cents,
        })

        subtotal_cents += line_total_cents

    # Calculate tax (8% on taxable items)
    tax_cents = int(subtotal_cents * 0.08)
    total_cents = subtotal_cents + tax_cents

    # Create item summary for receipt_lookup
    item_summary = ", ".join([item['product_name'] for item in line_items[:5]])
    if len(line_items) > 5:
        item_summary += f", +{len(line_items) - 5} more"

    receipt = {
        'transaction_id': txn_id,
        'customer_id': customer_id,
        'store_name': store_name,
        'transaction_ts': receipt_date,
        'item_count': len(line_items),
        'item_summary': item_summary,
        'subtotal_cents': subtotal_cents,
        'tax_cents': tax_cents,
        'total_cents': total_cents,
        'tender_type': tender_type,
        'card_last4': card_last4,
    }

    return receipt, line_items


def main():
    # Configuration
    NUM_CUSTOMERS = 75  # 75 customers
    NUM_RECEIPTS = 750  # 750 receipts over 60 days
    DATE_RANGE_DAYS = 60

    # Connection details
    LAKEBASE_HOST = "instance-48e7b373-3240-4e42-a9f0-d7289706e1c6.database.azuredatabricks.net"
    LAKEBASE_PORT = 5432
    LAKEBASE_DATABASE = "giant_eagle"

    print(f"Generating {NUM_RECEIPTS} receipts for {NUM_CUSTOMERS} customers over {DATE_RANGE_DAYS} days...")

    # Get OAuth token
    password = get_oauth_token()

    # Build connection string
    conninfo = f"host={LAKEBASE_HOST} port={LAKEBASE_PORT} dbname={LAKEBASE_DATABASE} user=lawrence.kyei@databricks.com password={password} sslmode=require"

    print(f"Connecting to Lakebase...")
    conn = psycopg.connect(conninfo)
    cursor = conn.cursor(row_factory=dict_row)

    try:
        # Clear existing data
        print("\n1. Clearing existing data...")
        cursor.execute("DELETE FROM receipt_line_items")
        cursor.execute("DELETE FROM receipt_lookup")
        conn.commit()
        print("   ✓ Old data cleared")

        # Generate receipts
        print(f"\n2. Generating {NUM_RECEIPTS} receipts...")

        # Customer shopping frequency (some customers shop more often)
        customer_weights = []
        for i in range(NUM_CUSTOMERS):
            if i < 10:  # 10 very frequent shoppers
                customer_weights.append(3.0)
            elif i < 30:  # 20 regular shoppers
                customer_weights.append(2.0)
            else:  # 45 occasional shoppers
                customer_weights.append(1.0)

        # Generate receipts with realistic date distribution
        base_date = datetime.now() - timedelta(days=DATE_RANGE_DAYS)
        receipts_generated = 0
        line_items_generated = 0

        for i in range(NUM_RECEIPTS):
            # Pick customer (weighted by frequency)
            customer_num = random.choices(range(NUM_CUSTOMERS), weights=customer_weights)[0]
            customer_id = generate_customer_id(customer_num)

            # Generate random date within range (more weight on recent dates and weekends)
            days_offset = random.choices(
                range(DATE_RANGE_DAYS),
                weights=[1.0 + (0.02 * d) for d in range(DATE_RANGE_DAYS)]  # Linear increase toward recent
            )[0]
            receipt_date = base_date + timedelta(days=days_offset)

            # More traffic on weekends
            if receipt_date.weekday() >= 5:  # Saturday or Sunday
                if random.random() > 0.3:  # 70% chance to keep weekend receipt
                    pass
                else:
                    continue  # Skip this receipt 30% of the time

            # Add random time (store hours: 7am-10pm)
            hour = random.randint(7, 21)
            minute = random.randint(0, 59)
            receipt_date = receipt_date.replace(hour=hour, minute=minute, second=random.randint(0, 59))

            # Generate receipt and line items
            txn_id = generate_transaction_id(i)
            receipt, line_items = generate_receipt(txn_id, customer_id, receipt_date)

            # Insert receipt into receipt_lookup
            cursor.execute("""
                INSERT INTO receipt_lookup (
                    transaction_id, customer_id, store_name,
                    transaction_ts, item_count, item_summary,
                    subtotal_cents, tax_cents, total_cents,
                    tender_type, card_last4
                ) VALUES (
                    %(transaction_id)s, %(customer_id)s, %(store_name)s,
                    %(transaction_ts)s, %(item_count)s, %(item_summary)s,
                    %(subtotal_cents)s, %(tax_cents)s, %(total_cents)s,
                    %(tender_type)s, %(card_last4)s
                )
            """, receipt)

            # Insert line items
            for line_item in line_items:
                cursor.execute("""
                    INSERT INTO receipt_line_items (
                        transaction_id, line_number, sku, product_name,
                        brand, category_l1, category_l2, quantity,
                        unit_price_cents, line_total_cents, discount_cents, is_taxable
                    ) VALUES (
                        %(transaction_id)s, %(line_number)s, %(sku)s, %(product_name)s,
                        %(brand)s, %(category_l1)s, %(category_l2)s, %(quantity)s,
                        %(unit_price_cents)s, %(line_total_cents)s, 0, TRUE
                    )
                """, {
                    'transaction_id': txn_id,
                    **line_item
                })

            receipts_generated += 1
            line_items_generated += len(line_items)

            if receipts_generated % 100 == 0:
                print(f"   Generated {receipts_generated} receipts ({line_items_generated} line items)...")
                conn.commit()

        conn.commit()
        print(f"   ✓ Generated {receipts_generated} receipts with {line_items_generated} line items")

        # Verify data
        print("\n3. Verifying data...")
        cursor.execute("""
            SELECT
                COUNT(*) as total_receipts,
                COUNT(DISTINCT customer_id) as unique_customers,
                COUNT(DISTINCT store_name) as unique_stores,
                MIN(transaction_ts) as earliest,
                MAX(transaction_ts) as latest,
                MIN(total_cents)/100.0 as min_total,
                MAX(total_cents)/100.0 as max_total,
                AVG(total_cents)/100.0 as avg_total,
                SUM(total_cents)/100.0 as total_revenue
            FROM receipt_lookup
        """)
        stats = cursor.fetchone()

        print(f"   Total receipts: {stats['total_receipts']}")
        print(f"   Unique customers: {stats['unique_customers']}")
        print(f"   Unique stores: {stats['unique_stores']}")
        print(f"   Date range: {stats['earliest']} to {stats['latest']}")
        print(f"   Total range: ${stats['min_total']:.2f} - ${stats['max_total']:.2f}")
        print(f"   Average total: ${stats['avg_total']:.2f}")
        print(f"   Total revenue: ${stats['total_revenue']:.2f}")

        # Line items stats
        cursor.execute("""
            SELECT
                COUNT(*) as total_line_items,
                COUNT(DISTINCT sku) as unique_skus,
                COUNT(DISTINCT transaction_id) as receipts_with_items
            FROM receipt_line_items
        """)
        line_stats = cursor.fetchone()
        print(f"\n   Total line items: {line_stats['total_line_items']}")
        print(f"   Unique SKUs: {line_stats['unique_skus']}")
        print(f"   Receipts with items: {line_stats['receipts_with_items']}")

        # Show some sample receipts
        print("\n4. Sample receipts:")
        cursor.execute("""
            SELECT transaction_id, customer_id, store_name,
                   to_char(transaction_ts, 'YYYY-MM-DD HH24:MI') as ts,
                   total_cents/100.0 as total, item_summary
            FROM receipt_lookup
            ORDER BY transaction_ts DESC
            LIMIT 5
        """)
        for r in cursor.fetchall():
            print(f"   {r['transaction_id']}: {r['customer_id']} at {r['store_name']} on {r['ts']}, ${r['total']:.2f}")
            print(f"      Items: {r['item_summary']}")

        print("\n✅ Done! Realistic receipt data generated successfully.")
        print(f"\nThis simulates approximately {DATE_RANGE_DAYS} days of transactions across 4 Giant Eagle stores.")
        print(f"Data volume is sufficient for demonstrating:")
        print(f"  - Fuzzy search across multiple criteria")
        print(f"  - AI semantic search with meaningful results")
        print(f"  - Customer context and shopping patterns")
        print(f"  - Audit logging at realistic scale")

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
