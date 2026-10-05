"""Validate/save identified UPCs and claim pending receipt quantities once."""
from fastapi import HTTPException

from db import insert_if_absent
from models import Product, ReceiptItem
from services.upc import normalize_catalog_code
from services.product_lookup import find_local_product
from services.inventory_service import add_to_inventory

FIELDS = {"name": 255, "brand": 128, "size": 64, "category": 128,
          "unit": 64, "notes": 4000, "default_location": 128, "lookup_source": 64}


def product_values(payload):
    values = {}
    for field, limit in FIELDS.items():
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"{field} must be text.")
        values[field] = value.strip()[:limit] if value and value.strip() else None
    if not values["name"]:
        raise HTTPException(status_code=400, detail="Product name is required.")
    values["lookup_source"] = values["lookup_source"] or "manual"
    return values


def save_identified_product(db, code, payload):
    try:
        upc = normalize_catalog_code(code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not upc or len(upc) > 64:
        raise HTTPException(status_code=400, detail="A UPC of 1–64 digits is required.")
    values = product_values(payload)
    existing = find_local_product(db, upc)
    if existing:
        return existing
    # INSERT ON CONFLICT keeps concurrent saves clean and preserves the winner.
    insert_if_absent(db, Product,
        {"upc": upc, "receipt_code_raw": upc, "gtin_normalized": None if upc.startswith("costco:") else upc, **values}, ["upc"])
    return find_local_product(db, upc)


def resolve_pending_items(db, product):
    rows = db.query(ReceiptItem).filter(
        ReceiptItem.normalized_code == product.upc, ReceiptItem.status == "unresolved"
    ).all()
    quantity = 0.0
    for row in rows:
        claimed = db.query(ReceiptItem).filter(
            ReceiptItem.id == row.id, ReceiptItem.status == "unresolved"
        ).update({"product_id": product.id, "status": "resolved"}, synchronize_session=False)
        if claimed:
            quantity += row.quantity
    if quantity:
        add_to_inventory(db, product, quantity)
