"""
Regenerate product embeddings with correct products from bulk_generate_receipts.py.

This script:
1. Connects to Lakebase
2. Drops and recreates the product_embeddings table
3. Generates embeddings for the correct PRODUCT_CATALOG
4. Inserts them into Lakebase with pgvector
"""

import psycopg
from psycopg.rows import dict_row
import subprocess
import json
import sys
import requests

# Product catalog matching bulk_generate_receipts.py
PRODUCT_CATALOG = [
    {"sku": "SKU-1001", "name": "Organic Whole Milk 1 Gal", "brand": "Snowville Creamery", "category_l1": "DAIRY", "category_l2": "MILK"},
    {"sku": "SKU-1002", "name": "Organic Eggs Large", "brand": "Vital Farms", "category_l1": "DAIRY", "category_l2": "EGGS"},
    {"sku": "SKU-1003", "name": "Roquefort Cheese 8oz", "brand": "Papillon", "category_l1": "DELI", "category_l2": "CHEESE"},
    {"sku": "SKU-1004", "name": "Brie Cheese 8oz", "brand": "President", "category_l1": "DELI", "category_l2": "CHEESE"},
    {"sku": "SKU-1005", "name": "Organic Bananas", "brand": None, "category_l1": "PRODUCE", "category_l2": "FRUIT"},
    {"sku": "SKU-1006", "name": "Honeycrisp Apples", "brand": None, "category_l1": "PRODUCE", "category_l2": "FRUIT"},
    {"sku": "SKU-1007", "name": "Roma Tomatoes", "brand": None, "category_l1": "PRODUCE", "category_l2": "VEGETABLE"},
    {"sku": "SKU-1008", "name": "Chicken Breast Boneless", "brand": "Bell & Evans", "category_l1": "MEAT", "category_l2": "POULTRY"},
    {"sku": "SKU-1009", "name": "Ground Beef 80/20", "brand": "Local Farm", "category_l1": "MEAT", "category_l2": "BEEF"},
    {"sku": "SKU-1010", "name": "Atlantic Salmon Fillet", "brand": "Wild Caught", "category_l1": "MEAT", "category_l2": "SEAFOOD"},
    {"sku": "SKU-1011", "name": "Sourdough Bread Loaf", "brand": "Giant Eagle Bakery", "category_l1": "BAKERY", "category_l2": "BREAD"},
    {"sku": "SKU-1012", "name": "Penne Pasta 1lb", "brand": "Barilla", "category_l1": "PANTRY", "category_l2": "PASTA"},
    {"sku": "SKU-1013", "name": "Black Beans Can 15oz", "brand": "Goya", "category_l1": "PANTRY", "category_l2": "CANNED"},
    {"sku": "SKU-1014", "name": "Ribeye Steak 2lb", "brand": "Premium Choice", "category_l1": "MEAT", "category_l2": "BEEF"},
    {"sku": "SKU-1015", "name": "Fancy Cheese Assortment", "brand": "Artisan Selection", "category_l1": "DELI", "category_l2": "CHEESE"},
]


def get_oauth_token():
    """Get OAuth token for Databricks authentication."""
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


def generate_embedding(text: str, token: str):
    """Generate embedding using Databricks embedding endpoint."""
    url = "https://adb-984752964297111.11.azuredatabricks.net/serving-endpoints/databricks-gte-large-en/invocations"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {"input": [text]}

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()

    return response.json()['data'][0]['embedding']


def main():
    # Connection details
    LAKEBASE_HOST = "instance-48e7b373-3240-4e42-a9f0-d7289706e1c6.database.azuredatabricks.net"
    LAKEBASE_PORT = 5432
    LAKEBASE_DATABASE = "giant_eagle"

    print("Getting OAuth token...")
    password = get_oauth_token()

    # Build connection string
    conninfo = f"host={LAKEBASE_HOST} port={LAKEBASE_PORT} dbname={LAKEBASE_DATABASE} user=lawrence.kyei@databricks.com password={password} sslmode=require"

    print(f"Connecting to Lakebase...")
    conn = psycopg.connect(conninfo)
    cursor = conn.cursor(row_factory=dict_row)

    try:
        # Step 1: Drop and recreate the table
        print("\n1. Recreating product_embeddings table...")
        cursor.execute("DROP TABLE IF EXISTS product_embeddings CASCADE")
        cursor.execute("""
            CREATE TABLE product_embeddings (
                sku TEXT PRIMARY KEY,
                product_name TEXT NOT NULL,
                search_text TEXT,
                embedding vector(1024),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Create HNSW index optimized for small dataset (15 products)
        # m=8: max connections per layer (lower for small datasets)
        # ef_construction=32: construction time neighbors (lower = faster build)
        cursor.execute("""
            CREATE INDEX product_embeddings_hnsw_idx
            ON product_embeddings
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 8, ef_construction = 32)
        """)

        conn.commit()
        print("   ✓ Table recreated")

        # Step 2: Generate embeddings for each product
        print(f"\n2. Generating embeddings for {len(PRODUCT_CATALOG)} products...")

        for i, product in enumerate(PRODUCT_CATALOG, 1):
            # Create search text: "Product Name | Category"
            search_text = f"{product['name']} | {product['category_l1']}"

            # Generate embedding
            print(f"   {i}/{len(PRODUCT_CATALOG)} Generating embedding for: {search_text}")
            embedding = generate_embedding(search_text, password)

            # Convert embedding to PostgreSQL array format
            embedding_str = '[' + ','.join(map(str, embedding)) + ']'

            # Insert into database
            cursor.execute("""
                INSERT INTO product_embeddings (sku, product_name, search_text, embedding)
                VALUES (%s, %s, %s, %s::vector)
            """, (product['sku'], product['name'], search_text, embedding_str))

            conn.commit()

        print(f"   ✓ Generated {len(PRODUCT_CATALOG)} embeddings")

        # Step 3: Verify
        print("\n3. Verifying embeddings...")
        cursor.execute("SELECT COUNT(*) as count FROM product_embeddings")
        count = cursor.fetchone()['count']
        print(f"   Total products: {count}")

        # Test semantic search for "ribeye steak"
        print("\n4. Testing semantic search for 'ribeye steak'...")
        test_embedding = generate_embedding("ribeye steak", password)
        test_embedding_str = '[' + ','.join(map(str, test_embedding)) + ']'

        cursor.execute("""
            SELECT sku, product_name, search_text,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM product_embeddings
            ORDER BY embedding <=> %s::vector
            LIMIT 5
        """, (test_embedding_str, test_embedding_str))

        print("   Top 5 matches:")
        for row in cursor.fetchall():
            print(f"     {row['sku']}: {row['product_name']:30} Similarity: {row['similarity']:.4f}")

        print("\n✅ Done! Product embeddings regenerated successfully.")

    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    main()
