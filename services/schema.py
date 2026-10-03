"""Idempotent, additive migration for SQLite and PostgreSQL installations."""
from sqlalchemy import inspect, text
from db import Base
from services.upc import normalize_upc


def initialize_database(engine):
    with engine.begin() as connection:
        inspector = inspect(connection)
        if inspector.has_table("products"):
            columns = {column["name"] for column in inspector.get_columns("products")}
            rows = connection.execute(text("SELECT id, receipt_code_raw, gtin_normalized FROM products")).mappings().all()
            normalized = {}
            for row in rows:
                upc = normalize_upc(row["receipt_code_raw"] or row["gtin_normalized"])
                if not upc or upc in normalized:
                    raise RuntimeError(
                        f"UPC migration needs review for product #{row['id']}: empty or duplicate UPC {upc!r}. "
                        "Existing product records have been preserved."
                    )
                normalized[upc] = row["id"]
            if "upc" not in columns:
                connection.execute(text("ALTER TABLE products ADD COLUMN upc TEXT"))
                for upc, product_id in normalized.items():
                    connection.execute(text("UPDATE products SET upc=:upc WHERE id=:id"), {"upc": upc, "id": product_id})
            if "notes" not in columns:
                connection.execute(text("ALTER TABLE products ADD COLUMN notes TEXT"))
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_products_upc ON products (upc)"))
            if "upc" not in columns and inspector.has_table("receipt_items"):
                for row in connection.execute(text("SELECT id, raw_code, normalized_code FROM receipt_items")).mappings().all():
                    upc = normalize_upc(row["raw_code"] or row["normalized_code"])
                    connection.execute(text("UPDATE receipt_items SET normalized_code=:upc WHERE id=:id"), {"upc": upc, "id": row["id"]})
        Base.metadata.create_all(bind=connection)
