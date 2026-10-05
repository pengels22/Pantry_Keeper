import sys
from fastapi import HTTPException
from services.units import valid_amount
from sqlalchemy.orm import Session
from db import database_insert
from models import Inventory, Product, utc_now


def add_to_inventory(db: Session, product: Product, quantity: float, location: str | None = None):
    quantity = valid_amount(quantity)
    # Increment in SQL so concurrent receipts cannot overwrite one another's stock.
    updates = {"quantity": Inventory.quantity + quantity, "last_updated": utc_now()}
    if location:
        updates["location"] = location
    statement = database_insert(db, Inventory).values(
        product_id=product.id, quantity=quantity,
        location=location or product.default_location, last_updated=utc_now(),
    ).on_conflict_do_update(index_elements=["product_id"], set_=updates,
        where=Inventory.quantity + quantity <= sys.float_info.max).returning(Inventory.id)
    inventory_id = db.execute(statement).scalar_one_or_none()
    if inventory_id is None:
        raise HTTPException(409, "Inventory quantity would overflow.")
    row = db.query(Inventory).filter_by(id=inventory_id).populate_existing().one()
    from services.recipe_inventory import apply_legacy_delta
    return apply_legacy_delta(db, row, quantity, "purchase", "Receipt purchase or new stock")
