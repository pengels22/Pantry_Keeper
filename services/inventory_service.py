from sqlalchemy.orm import Session
from models import Inventory, Product


def add_to_inventory(db: Session, product: Product, quantity: float, location: str | None = None):
    row = db.query(Inventory).filter(Inventory.product_id == product.id).first()
    if not row:
        row = Inventory(
            product_id=product.id,
            quantity=0.0,
            location=location or product.default_location,
        )
        db.add(row)

    row.quantity += quantity
    if location:
        row.location = location
    db.flush()
    return row
