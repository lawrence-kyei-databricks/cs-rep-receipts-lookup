"""
Giant Eagle — Product Embedding Pipeline
Generates vector embeddings for product names/descriptions and writes
them to Lakebase pgvector for semantic search.

Runs nightly via Databricks Workflow (Job).
Source: giant_eagle.gold.product_catalog (produced by Gold pipeline)
Target: giant_eagle_serving.public.product_embeddings (pgvector)

Trade-off: New products aren't semantically searchable until next run.
           Exact-match search via receipt_lookup works immediately.

embed_text format: "product_desc | department_code"
  e.g. "Roquefort Wedge 8oz | CHEESE"

Primary key in product_embeddings: sku (TEXT)
  — When upc is null, product_key = sku, so sku is always non-null.
  — Bronze quality gate enforces: upc IS NOT NULL OR sku IS NOT NULL
"""

import logging
from typing import Any

import psycopg
from pyspark.sql import SparkSession, DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import ArrayType, FloatType

logger = logging.getLogger(__name__)

# Model config
EMBEDDING_MODEL = "databricks-bge-large-en"  # Foundation Model endpoint
EMBEDDING_DIM = 1024
BATCH_SIZE = 100


def get_embedding_udf(spark: SparkSession):
    """
    Create a pandas UDF that calls the Databricks Foundation Model Serving
    endpoint to generate embeddings in batches.
    """
    from mlflow.deployments import get_deploy_client

    client = get_deploy_client("databricks")

    def embed_texts(texts: list[str]) -> list[list[float]]:
        """Call Foundation Model endpoint for batch embedding."""
        response = client.predict(
            endpoint=EMBEDDING_MODEL,
            inputs={"input": texts},
        )
        return [item["embedding"] for item in response["data"]]

    @F.pandas_udf(ArrayType(FloatType()))
    def embed_udf(texts: "pd.Series") -> "pd.Series":
        import pandas as pd

        results = []
        text_list = texts.tolist()

        for i in range(0, len(text_list), BATCH_SIZE):
            batch = text_list[i : i + BATCH_SIZE]
            embeddings = embed_texts(batch)
            results.extend(embeddings)

        return pd.Series(results)

    return embed_udf


def generate_embeddings(spark: SparkSession) -> DataFrame:
    """
    Read Gold product catalog, generate embeddings via Foundation Model,
    return DataFrame with sku, product_name, embedding.

    Source: giant_eagle.gold.product_catalog
    Columns used: product_key, upc, sku, product_desc, department_code

    product_key = COALESCE(upc, sku) — guaranteed non-null by Bronze quality gate.
    sku is used as the PK for product_embeddings because it's the stable
    internal identifier (upc can be null for store-branded items).
    """
    embed_udf = get_embedding_udf(spark)

    # Read from Gold product_catalog (produced by Gold pipeline).
    # purchase_count + last_seen allow prioritizing high-frequency products,
    # but for embeddings we need all products embedded.
    products = spark.table("giant_eagle.gold.product_catalog").select(
        "product_key",
        "upc",
        "sku",
        "product_desc",
        "department_code",
    )

    # Combine product_desc + department_code for richer embeddings.
    # Example: "Roquefort Wedge 8oz | CHEESE"
    products_with_text = products.withColumn(
        "embed_text",
        F.concat_ws(
            " | ",
            F.col("product_desc"),
            F.coalesce(F.col("department_code"), F.lit("")),
        ),
    )

    # Generate embeddings — runs as a distributed Spark job
    return products_with_text.withColumn(
        "embedding",
        embed_udf(F.col("embed_text")),
    ).select(
        # sku is the PK for product_embeddings (matches Lakebase DDL).
        # When upc is null, product_key == sku, so COALESCE is a no-op.
        F.coalesce(F.col("sku"), F.col("product_key")).alias("sku"),
        F.col("product_desc").alias("product_name"),
        "embedding",
    )


def write_embeddings_to_lakebase(
    embeddings_df: DataFrame,
    lakebase_conninfo: str,
) -> int:
    """
    Write embeddings to Lakebase pgvector table.

    Upserts on sku (PK). HNSW index is updated automatically by Postgres.

    Schema (product_embeddings):
      sku          TEXT PRIMARY KEY
      product_name TEXT
      embedding    vector(1024)
      updated_at   TIMESTAMPTZ
    """
    rows = embeddings_df.collect()
    written = 0

    with psycopg.connect(lakebase_conninfo) as conn:
        with conn.cursor() as cur:
            for row in rows:
                embedding_list = row["embedding"]
                embedding_str = f"[{','.join(str(x) for x in embedding_list)}]"

                cur.execute(
                    """
                    INSERT INTO product_embeddings
                        (sku, product_name, embedding, updated_at)
                    VALUES (%s, %s, %s::vector, NOW())
                    ON CONFLICT (sku) DO UPDATE SET
                        product_name = EXCLUDED.product_name,
                        embedding    = EXCLUDED.embedding,
                        updated_at   = NOW()
                    """,
                    (
                        row["sku"],
                        row["product_name"],
                        embedding_str,
                    ),
                )
                written += 1

        conn.commit()

    logger.info(f"Wrote {written} embeddings to Lakebase pgvector")
    return written


def run_embedding_pipeline(lakebase_conninfo: str) -> dict[str, Any]:
    """
    Main entry point for the nightly embedding pipeline.
    Called by Databricks Workflow (Job).

    Args:
        lakebase_conninfo: psycopg3 connection string for Lakebase.

    Returns:
        Dict with products_processed, embeddings_written, model, dimension.
    """
    spark = SparkSession.builder.getOrCreate()

    logger.info("Reading Gold product catalog from giant_eagle.gold.product_catalog...")
    logger.info("Generating product embeddings via Foundation Model...")
    embeddings_df = generate_embeddings(spark)

    product_count = embeddings_df.count()
    logger.info(f"Generated embeddings for {product_count} products")

    logger.info("Writing embeddings to Lakebase pgvector (product_embeddings)...")
    written = write_embeddings_to_lakebase(embeddings_df, lakebase_conninfo)

    return {
        "products_processed": product_count,
        "embeddings_written": written,
        "model": EMBEDDING_MODEL,
        "dimension": EMBEDDING_DIM,
    }
