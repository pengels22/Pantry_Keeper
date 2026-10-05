"""Idempotent, additive migration for SQLite and PostgreSQL installations."""
from sqlalchemy import inspect, text
from db import Base
from services.upc import normalize_catalog_code


def initialize_database(engine):
    with engine.begin() as connection:
        inspector = inspect(connection)
        if inspector.has_table("products"):
            columns = {column["name"] for column in inspector.get_columns("products")}
            rows = connection.execute(text("SELECT id, receipt_code_raw, gtin_normalized FROM products")).mappings().all()
            normalized = {}
            for row in rows:
                upc = normalize_catalog_code(row["receipt_code_raw"] or row["gtin_normalized"])
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
                    upc = normalize_catalog_code(row["raw_code"] or row["normalized_code"])
                    connection.execute(text("UPDATE receipt_items SET normalized_code=:upc WHERE id=:id"), {"upc": upc, "id": row["id"]})
        if inspector.has_table("inventory"):
            inventory_columns = {column["name"] for column in inspector.get_columns("inventory")}
            additions = {
                "package_quantity": "FLOAT", "package_size": "FLOAT",
                "package_unit": "VARCHAR(16)", "usable_quantity": "FLOAT",
                "usable_unit": "VARCHAR(16)",
                "reserved_quantity": "FLOAT NOT NULL DEFAULT 0",
            }
            for name, definition in additions.items():
                if name not in inventory_columns:
                    connection.execute(text(f"ALTER TABLE inventory ADD COLUMN {name} {definition}"))
            # Historical quantity may mean packages or weight. Leave measurements
            # unknown until explicitly configured; never infer them from product size.
        Base.metadata.create_all(bind=connection)
