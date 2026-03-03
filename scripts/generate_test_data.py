#!/usr/bin/env python3
"""
Generate synthetic receipt data to test Lakebase performance.

Generates:
- 10,000 receipts across 6 months
- 50 unique customers
- 20 store locations
- Realistic product purchases
"""

import os
import random
import psycopg2
from datetime import datetime, timedelta
from faker import Faker

fake = Faker()

# Connection details
LAKEBASE_HOST = "instance-7c6265a0-a083-4654-8781-a29b80c5afcf.database.azuredatabricks.net"
LAKEBASE_PORT = 5432
LAKEBASE_DB = "giant_eagle"
LAKEBASE_USER = "lawrence.kyei@databricks.com"
# Token from environment
TOKEN = os.environ.get('TOKEN')

# Store names (Pittsburgh area)
STORES = [
    "East Liberty", "Squirrel Hill", "Shadyside", "Oakland", "Lawrenceville",
    "Bloomfield", "Highland Park", "Regent Square", "Greenfield", "Southside",
    "Mt. Lebanon", "Bethel Park", "Cranberry", "Monroeville", "Robinson",
    "Waterfront", "Homestead", "Downtown", "North Hills", "Ross Park"
]

# Products with SKUs and prices
PRODUCTS = [
    ("SKU-1001", "Organic Bananas", 299, "produce"),
    ("SKU-1002", "Red Delicious Apples", 349, "produce"),
    ("SKU-1003", "Baby Spinach", 399, "produce"),
    ("SKU-1004", "Romaine Lettuce", 279, "produce"),
    ("SKU-1005", "Cherry Tomatoes", 429, "produce"),

    ("SKU-1010", "2% Milk Gallon", 449, "dairy"),
    ("SKU-1011", "Whole Milk Gallon", 459, "dairy"),
    ("SKU-1012", "Greek Yogurt", 599, "dairy"),
    ("SKU-1013", "Cheddar Cheese Block", 749, "dairy"),
    ("SKU-1014", "Ribeye Steak", 1899, "meat"),

    ("SKU-1020", "Ground Beef 80/20", 899, "meat"),
    ("SKU-1021", "Chicken Breast", 699, "meat"),
    ("SKU-1022", "Pork Chops", 799, "meat"),
    ("SKU-1023", "Salmon Fillet", 1299, "seafood"),
    ("SKU-1024", "Shrimp Raw", 1499, "seafood"),

    ("SKU-1030", "White Bread", 349, "bakery"),
    ("SKU-1031", "Whole Wheat Bread", 399, "bakery"),
    ("SKU-1032", "Bagels Pack", 499, "bakery"),
    ("SKU-1033", "Croissants 6pk", 699, "bakery"),
    ("SKU-1034", "Sourdough Loaf", 549, "bakery"),

    ("SKU-1040", "Orange Juice 64oz", 549, "beverages"),
    ("SKU-1041", "Coca-Cola 12pk", 599, "beverages"),
    ("SKU-1042", "Bottled Water 24pk", 699, "beverages"),
    ("SKU-1043", "Coffee Beans 12oz", 1299, "beverages"),
    ("SKU-1044", "Green Tea Box", 449, "beverages"),

    ("SKU-1050", "Pasta Box", 199, "pantry"),
    ("SKU-1051", "Pasta Sauce Jar", 349, "pantry"),
    ("SKU-1052", "Rice 5lb Bag", 899, "pantry"),
    ("SKU-1053", "Olive Oil 16oz", 1099, "pantry"),
    ("SKU-1054", "Black Beans Can", 149, "pantry"),
]

def generate_transaction_id(timestamp):
    """Generate a unique transaction ID."""
    return f"TXN-{timestamp.strftime('%Y%m%d')}-{random.randint(100000, 999999)}"

def generate_customer_id():
    """Generate a customer ID."""
    return f"CUST-{random.randint(10000, 99999)}"

def generate_receipt_items(num_items=None):
    """Generate random items for a receipt."""
    if num_items is None:
        num_items = random.randint(3, 15)  # 3 to 15 items per receipt

    items = random.sample(PRODUCTS, min(num_items, len(PRODUCTS)))
    item_list = []
    item_summary = []
    total = 0

    for sku, name, price_cents, category in items:
        quantity = random.randint(1, 3)
        item_total = price_cents * quantity
        total += item_total
        item_list.append({
            'sku': sku,
            'name': name,
            'quantity': quantity,
            'price': price_cents,
            'total': item_total
        })
        item_summary.append(f"{quantity}x {name}")

    return item_list, ", ".join(item_summary), total

def generate_receipts(conn, num_receipts=10000):
    """Generate synthetic receipt data."""
    cursor = conn.cursor()

    # Generate stable set of customers
    customers = [generate_customer_id() for _ in range(50)]

    # Generate receipts over last 6 months
    end_date = datetime.now()
    start_date = end_date - timedelta(days=180)

    print(f"Generating {num_receipts} receipts...")
    print(f"Date range: {start_date.date()} to {end_date.date()}")
    print(f"Customers: {len(customers)}")
    print(f"Stores: {len(STORES)}")

    batch_size = 100
    for i in range(0, num_receipts, batch_size):
        values = []

        for j in range(batch_size):
            if i + j >= num_receipts:
                break

            # Random timestamp within range
            days_offset = random.randint(0, 180)
            hours_offset = random.randint(8, 22)  # Store hours 8am-10pm
            minutes_offset = random.randint(0, 59)

            receipt_time = start_date + timedelta(
                days=days_offset,
                hours=hours_offset,
                minutes=minutes_offset
            )

            # Random customer (some shop more frequently)
            customer_id = random.choices(
                customers,
                weights=[random.randint(1, 10) for _ in customers],
                k=1
            )[0]

            # Random store
            store_name = random.choice(STORES)

            # Generate items
            items, item_summary, total_cents = generate_receipt_items()

            # Transaction ID
            transaction_id = generate_transaction_id(receipt_time)

            # Last 4 digits of card (for fuzzy search testing)
            last4_card = f"{random.randint(1000, 9999)}"

            values.append((
                transaction_id,
                customer_id,
                store_name,
                receipt_time,
                total_cents,
                item_summary,
                last4_card
            ))

        # Batch insert
        insert_query = """
            INSERT INTO receipt_lookup
            (transaction_id, customer_id, store_name, purchase_timestamp, total_cents, item_summary, last4_card)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (transaction_id) DO NOTHING
        """

        cursor.executemany(insert_query, values)
        conn.commit()

        if (i + batch_size) % 1000 == 0:
            print(f"  Inserted {i + batch_size} receipts...")

    print(f"\n✓ Successfully generated {num_receipts} receipts!")

    # Print statistics
    cursor.execute("SELECT COUNT(*) FROM receipt_lookup")
    total_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT customer_id) FROM receipt_lookup")
    unique_customers = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(DISTINCT store_name) FROM receipt_lookup")
    unique_stores = cursor.fetchone()[0]

    cursor.execute("SELECT MIN(purchase_timestamp), MAX(purchase_timestamp) FROM receipt_lookup")
    date_range = cursor.fetchone()

    cursor.execute("SELECT AVG(total_cents)/100.0 FROM receipt_lookup")
    avg_total = cursor.fetchone()[0]

    print(f"\nDatabase Statistics:")
    print(f"  Total receipts: {total_count:,}")
    print(f"  Unique customers: {unique_customers}")
    print(f"  Unique stores: {unique_stores}")
    print(f"  Date range: {date_range[0]} to {date_range[1]}")
    print(f"  Average receipt: ${avg_total:.2f}")

    cursor.close()

def main():
    """Main entry point."""
    if not TOKEN:
        print("ERROR: TOKEN environment variable not set")
        print("Run: export TOKEN='your-lakebase-token'")
        return 1

    print("Connecting to Lakebase...")
    conn = psycopg2.connect(
        host=LAKEBASE_HOST,
        port=LAKEBASE_PORT,
        dbname=LAKEBASE_DB,
        user=LAKEBASE_USER,
        password=TOKEN,
        sslmode='require'
    )

    try:
        # Create table if it doesn't exist
        cursor = conn.cursor()
        print("Creating receipt_lookup table with indexes...")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS receipt_lookup (
                transaction_id VARCHAR(50) PRIMARY KEY,
                customer_id VARCHAR(20),
                store_name VARCHAR(100),
                purchase_timestamp TIMESTAMP,
                total_cents INTEGER,
                item_summary TEXT,
                last4_card VARCHAR(4),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create indexes for sub-10ms queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_customer_id ON receipt_lookup(customer_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_store_name ON receipt_lookup(store_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_purchase_timestamp ON receipt_lookup(purchase_timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_last4_card ON receipt_lookup(last4_card)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_combined_lookup ON receipt_lookup(store_name, purchase_timestamp, total_cents)")
        conn.commit()
        cursor.close()
        print("✓ Table and indexes created successfully\n")

        # Generate 10 million receipts for performance testing
        generate_receipts(conn, num_receipts=10000000)
    finally:
        conn.close()
        print("\nConnection closed.")

    return 0

if __name__ == "__main__":
    exit(main())
