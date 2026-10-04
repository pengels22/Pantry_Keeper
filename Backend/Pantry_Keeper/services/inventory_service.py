from sqlalchemy.orm import Session
from db import database_insert
from models import Inventory, Product, utc_now


def add_to_inventory(db: Session, product: Product, quantity: float, location: str | None = None):
    # Increment in SQL so concurrent receipts cannot overwrite one another's stock.
    updates = {"quantity": Inventory.quantity + quantity, "last_updated": utc_now()}
    if location:
        updates["location"] = location
    statement = database_insert(db, Inventory).values(
        product_id=product.id, quantity=quantity,
        location=location or product.default_location, last_updated=utc_now(),
    ).on_conflict_do_update(index_elements=["product_id"], set_=updates).returning(Inventory.id)
    inventory_id = db.execute(statement).scalar_one()
    return db.query(Inventory).filter_by(id=inventory_id).populate_existing().one()
