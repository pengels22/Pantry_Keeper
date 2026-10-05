"""Inventory IDs, guarded SQL updates and append-only history.

Services participate in the caller's transaction; callers must commit on success
and roll back on any failure. No service commits part of a recipe operation.
"""
import sys
from sqlalchemy import update
from fastapi import HTTPException
from models import Inventory, Product, InventoryTransaction, utc_now
from services.units import convert_amount, normalize_unit, valid_amount


def get_row(db, inventory_id):
    if isinstance(inventory_id, bool) or not isinstance(inventory_id, int) or inventory_id <= 0:
        raise HTTPException(400, "A positive inventory ID is required.")
    row = db.query(Inventory).filter_by(id=inventory_id).populate_existing().first()
    if not row:
        raise HTTPException(404, "Inventory item not found.")
    return row


def record(db, row, amount, kind, reason=None, recipe_id=None, field="usable_quantity", unit=None, undo_of_id=None):
    entry = InventoryTransaction(inventory_item_id=row.id, change_amount=amount,
        unit=unit if unit is not None else row.usable_unit, quantity_field=field,
        transaction_type=kind, reason=reason, recipe_id=recipe_id, undo_of_id=undo_of_id)
    db.add(entry)
    db.flush()
    return entry


def serialize_item(row, product):
    return {"inventory_id": row.id, "product_id": product.id, "name": product.name,
        "brand": product.brand, "upc": product.upc, "category": product.category,
        "location": row.location, "quantity": row.quantity, "size": product.size, "unit": product.unit,
        "package_quantity": row.package_quantity, "package_size": row.package_size,
        "package_unit": row.package_unit, "usable_quantity": row.usable_quantity,
        "usable_unit": row.usable_unit, "reserved_quantity": row.reserved_quantity,
        "available_quantity": None if row.usable_quantity is None else max(0, row.usable_quantity - row.reserved_quantity),
        "measurement_required": row.usable_quantity is None or row.usable_unit is None}


def get_inventory(db, query=None, category=None, limit=200, offset=0):
    rows = db.query(Inventory, Product).join(Product, Product.id == Inventory.product_id)
    if query:
        rows = rows.filter(Product.name.ilike(f"%{query}%", escape="\\"))
    if category:
        rows = rows.filter(Product.category == category)
    return [serialize_item(row, product) for row, product in rows.order_by(Product.name, Inventory.id).offset(offset).limit(limit).all()]


def get_inventory_item(db, inventory_id):
    row = get_row(db, inventory_id)
    return serialize_item(row, db.get(Product, row.product_id))


def search_inventory(db, query):
    # Escape wildcard metacharacters; values remain bound SQL parameters.
    query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return get_inventory(db, query=query)


def get_items_by_category(db, category):
    return get_inventory(db, category=category)


def get_available_quantity(db, inventory_id):
    row = get_row(db, inventory_id)
    if row.usable_quantity is None or not row.usable_unit:
        raise HTTPException(409, "Configure usable quantity and unit before recipe planning.")
    return max(0, row.usable_quantity - row.reserved_quantity)


def configure_measurements(db, inventory_id, payload):
    row = lock_inventory(db, inventory_id)
    usable_unit = normalize_unit(payload["usable_unit"])
    usable = payload.get("usable_quantity")
    package_quantity = payload.get("package_quantity")
    package_size = payload.get("package_size")
    package_unit = payload.get("package_unit")
    supplied = [x is not None for x in (package_quantity, package_size, package_unit)]
    if any(supplied) and not all(supplied):
        raise HTTPException(400, "Supply package quantity, size and unit together.")
    if all(supplied):
        package_quantity = valid_amount(package_quantity)
        package_size = valid_amount(package_size, positive=True)
        package_unit = normalize_unit(package_unit)
        # Also verifies compatible units even when usable quantity is explicit.
        derived = convert_amount(package_quantity * package_size, package_unit, usable_unit)
        if usable is None:
            usable = derived
    if usable is None:
        raise HTTPException(400, "Usable quantity or complete package measurements are required.")
    usable = valid_amount(usable)
    old_quantity = row.quantity
    old = row.usable_quantity
    old_unit = row.usable_unit
    values = dict(package_quantity=package_quantity, package_size=package_size, package_unit=package_unit,
        usable_quantity=usable, usable_unit=usable_unit, last_updated=utc_now())
    if package_quantity is not None:
        values["quantity"] = package_quantity
    # Recipe reservations lock the measurement interpretation.
    changed = db.execute(update(Inventory).where(Inventory.id == inventory_id, Inventory.reserved_quantity == 0)
        .values(**values).execution_options(synchronize_session=False)).rowcount
    if not changed:
        raise HTTPException(409, "Cancel active reservations before changing measurements.")
    row = get_row(db, inventory_id)
    if row.quantity != old_quantity:
        record(db, row, row.quantity - old_quantity, "manual_adjustment", "Explicit package count configured", field="quantity", unit="each")
    if old is not None and old_unit != usable_unit:
        record(db, row, -old, "manual_adjustment", "Measurement unit changed: previous balance", unit=old_unit)
        record(db, row, usable, "manual_adjustment", "Measurement unit changed: new balance")
    else:
        record(db, row, usable - (old or 0), "manual_adjustment", "Recipe measurements configured")
    return get_inventory_item(db, inventory_id)


def change_usable(db, inventory_id, amount, unit, kind, reason=None, recipe_id=None, undo_of_id=None):
    row = get_row(db, inventory_id)
    get_available_quantity(db, inventory_id)
    delta = convert_amount(abs(amount), unit, row.usable_unit) * (-1 if amount < 0 else 1)
    statement = update(Inventory).where(Inventory.id == inventory_id,
        Inventory.usable_unit == row.usable_unit,
        Inventory.usable_quantity + delta >= Inventory.reserved_quantity,
        Inventory.usable_quantity + delta <= sys.float_info.max).values(
            usable_quantity=Inventory.usable_quantity + delta, last_updated=utc_now())
    if not db.execute(statement.execution_options(synchronize_session=False)).rowcount:
        raise HTTPException(409, "Not enough unreserved usable inventory.")
    return record(db, get_row(db, inventory_id), delta, kind, reason, recipe_id, undo_of_id=undo_of_id)


def add_inventory(db, inventory_id, amount, unit, reason=None):
    return change_usable(db, inventory_id, valid_amount(amount), unit, "purchase", reason)


def subtract_inventory(db, inventory_id, amount, unit, reason=None):
    return change_usable(db, inventory_id, -valid_amount(amount), unit, "manual_adjustment", reason)


def reserve_inventory(db, inventory_id, amount, unit, recipe_id):
    row = get_row(db, inventory_id)
    get_available_quantity(db, inventory_id)
    amount = convert_amount(valid_amount(amount, positive=True), unit, row.usable_unit)
    statement = update(Inventory).where(Inventory.id == inventory_id,
        Inventory.usable_unit == row.usable_unit,
        Inventory.usable_quantity - Inventory.reserved_quantity >= amount).values(
            reserved_quantity=Inventory.reserved_quantity + amount, last_updated=utc_now())
    if not db.execute(statement.execution_options(synchronize_session=False)).rowcount:
        raise HTTPException(409, "Not enough available inventory to reserve.")
    record(db, get_row(db, inventory_id), amount, "recipe_reservation", recipe_id=recipe_id, field="reserved_quantity")
    return amount


def release_reserved_inventory(db, inventory_id, amount, unit, recipe_id):
    row = get_row(db, inventory_id)
    amount = convert_amount(amount, unit, row.usable_unit)
    if not db.execute(update(Inventory).where(Inventory.id == inventory_id,
        Inventory.usable_unit == row.usable_unit, Inventory.reserved_quantity >= amount).values(
        reserved_quantity=Inventory.reserved_quantity - amount, last_updated=utc_now())
        .execution_options(synchronize_session=False)).rowcount:
        raise HTTPException(409, "Reservation balance changed; reload the recipe.")
    record(db, get_row(db, inventory_id), -amount, "recipe_release", recipe_id=recipe_id, field="reserved_quantity")


def commit_reserved_inventory(db, inventory_id, reserved, actual, unit, recipe_id):
    row = get_row(db, inventory_id)
    reserved = convert_amount(reserved, unit, row.usable_unit)
    actual = convert_amount(actual, unit, row.usable_unit)
    # In one UPDATE, release this recipe's hold and consume only available stock.
    # Other recipes' reservations are protected even when actual use exceeds the hold.
    if not db.execute(update(Inventory).where(Inventory.id == inventory_id,
        Inventory.usable_unit == row.usable_unit, Inventory.reserved_quantity >= reserved,
        Inventory.usable_quantity - actual >= Inventory.reserved_quantity - reserved).values(
        usable_quantity=Inventory.usable_quantity - actual,
        reserved_quantity=Inventory.reserved_quantity - reserved, last_updated=utc_now())
        .execution_options(synchronize_session=False)).rowcount:
        raise HTTPException(409, "Actual use exceeds available inventory or reservation changed.")
    row = get_row(db, inventory_id)
    record(db, row, -reserved, "recipe_release", recipe_id=recipe_id, field="reserved_quantity")
    record(db, row, -actual, "recipe_consumption", recipe_id=recipe_id)
    return actual


def undo_inventory_transaction(db, transaction_id):
    entry = db.query(InventoryTransaction).filter_by(id=transaction_id).with_for_update().first()
    if not entry:
        raise HTTPException(404, "Inventory transaction not found.")
    if entry.quantity_field != "usable_quantity" or entry.transaction_type not in {"purchase", "manual_adjustment", "recipe_consumption"}:
        raise HTTPException(409, "Use the recipe lifecycle to release reservations; this transaction cannot be undone.")
    if db.query(InventoryTransaction).filter_by(undo_of_id=transaction_id).first():
        raise HTTPException(409, "Transaction already undone.")
    row = get_row(db, entry.inventory_item_id)
    if row.usable_unit != entry.unit:
        raise HTTPException(409, "Measurement unit changed; use an explicit manual adjustment.")
    return change_usable(db, row.id, -entry.change_amount, entry.unit, "undo",
        f"Undo transaction #{entry.id}", entry.recipe_id, undo_of_id=entry.id)


def lock_inventory(db, inventory_id):
    # SQLite takes its writer lock; PostgreSQL locks this row until commit.
    if not db.execute(update(Inventory).where(Inventory.id == inventory_id)
        .values(last_updated=Inventory.last_updated).execution_options(synchronize_session=False)).rowcount:
        raise HTTPException(404, "Inventory item not found.")
    return get_row(db, inventory_id)


def apply_legacy_delta(db, row, delta, kind, reason):
    """Legacy quantity remains available; explicit package setup enables scaling."""
    if delta == 0:
        return row
    record(db, row, delta, kind, reason, field="quantity", unit="each" if row.package_quantity is not None else "legacy")
    if row.package_quantity is not None:
        row.package_quantity = valid_amount(row.package_quantity + delta)
        db.flush()
        if row.package_size is not None and row.package_unit and row.usable_unit:
            usable_delta = convert_amount(abs(delta) * row.package_size, row.package_unit, row.usable_unit)
            change_usable(db, row.id, usable_delta if delta >= 0 else -usable_delta,
                row.usable_unit, kind, reason)
        db.flush()
    return get_row(db, row.id)


def set_legacy_quantity(db, inventory_id, quantity, location=None, update_location=False):
    quantity = valid_amount(quantity)
    row = lock_inventory(db, inventory_id)
    delta = quantity - row.quantity
    row.quantity = quantity
    if update_location:
        row.location = location
    db.flush()
    return apply_legacy_delta(db, row, delta, "manual_adjustment", "Legacy stock quantity changed")
