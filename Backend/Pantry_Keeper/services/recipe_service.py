"""Only user-driven lifecycle methods write stock. Proposals are read-only."""
from sqlalchemy import update
from fastapi import HTTPException
from models import RecipeSession, RecipeSessionItem, utc_now
from services import recipe_inventory as stock
from services.units import convert_amount, valid_amount, normalize_unit


def propose_recipe_usage(db, recipe_data):
    if not isinstance(recipe_data, dict):
        raise HTTPException(400, "Recipe must be an object.")
    title = recipe_data.get("recipe") or recipe_data.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 255:
        raise HTTPException(400, "Recipe title must contain 1–255 characters.")
    instructions = recipe_data.get("instructions", "")
    if not isinstance(instructions, str) or len(instructions) > 20000:
        raise HTTPException(400, "Recipe instructions must be text of at most 20,000 characters.")
    ingredients = recipe_data.get("ingredients")
    if not isinstance(ingredients, list) or not 1 <= len(ingredients) <= 100:
        raise HTTPException(400, "A recipe needs 1–100 inventory ingredients.")
    seen, items = set(), []
    for ingredient in ingredients:
        if not isinstance(ingredient, dict):
            raise HTTPException(400, "Each ingredient must be an object.")
        inventory_id = ingredient.get("inventory_id")
        item = stock.get_inventory_item(db, inventory_id)
        if inventory_id in seen:
            raise HTTPException(400, "Combine duplicate inventory IDs into one ingredient.")
        seen.add(inventory_id)
        amount = valid_amount(ingredient.get("amount"), positive=True)
        unit = normalize_unit(ingredient.get("unit"))
        available = stock.get_available_quantity(db, inventory_id)
        normalized = convert_amount(amount, unit, item["usable_unit"])
        if normalized > available:
            raise HTTPException(409, f"Insufficient available quantity for {item['name']}.")
        notes = ingredient.get("notes") or ""
        if not isinstance(notes, str) or len(notes) > 2000:
            raise HTTPException(400, "Ingredient notes must be text of at most 2,000 characters.")
        # Product identity always comes from the database, never from model names.
        items.append({"inventory_id": inventory_id, "name": item["name"], "amount": amount,
            "unit": unit, "usable_amount": normalized, "usable_unit": item["usable_unit"],
            "available_quantity": available, "available": True, "notes": notes})
    return {"recipe": title.strip(), "instructions": instructions, "ingredients": items}


def get_session(db, session_id):
    row = db.query(RecipeSession).filter_by(id=session_id).populate_existing().first()
    if not row:
        raise HTTPException(404, "Recipe session not found.")
    return row


def serialize_session(db, session):
    db.expire(session, ["items"])
    return {"id": session.id, "title": session.title, "status": session.status,
        "instructions": session.instructions, "created_at": session.created_at.isoformat(),
        "updated_at": session.updated_at.isoformat(), "items": [
            {"inventory_id": item.inventory_item_id, "name": item.ingredient_name,
             "requested_amount": item.requested_amount, "requested_unit": item.requested_unit,
             "reserved_amount": item.reserved_amount, "reserved_unit": item.reserved_unit,
             "consumed_amount": item.consumed_amount, "consumed_unit": item.consumed_unit,
             "notes": item.notes, "inventory": stock.get_inventory_item(db, item.inventory_item_id)}
            for item in session.items]}


def create_recipe_session(db, recipe_data=None, title=None):
    if recipe_data is None:
        if not isinstance(title, str) or not title.strip() or len(title) > 255:
            raise HTTPException(400, "A planning session needs a title.")
        session = RecipeSession(title=title.strip(), status="planning")
        db.add(session)
        db.flush()
        return session
    proposal = propose_recipe_usage(db, recipe_data)
    session = RecipeSession(title=proposal["recipe"], instructions=proposal["instructions"], status="selected")
    db.add(session)
    db.flush()
    set_items(db, session, proposal)
    return session


def set_items(db, session, proposal):
    for ingredient in proposal["ingredients"]:
        db.add(RecipeSessionItem(recipe_session_id=session.id, inventory_item_id=ingredient["inventory_id"],
            ingredient_name=ingredient["name"], requested_amount=ingredient["amount"],
            requested_unit=ingredient["unit"], notes=ingredient["notes"]))
    db.flush()


def transition(db, session_id, expected, target):
    if not db.execute(update(RecipeSession).where(RecipeSession.id == session_id, RecipeSession.status.in_(expected))
        .values(status=target, updated_at=utc_now()).execution_options(synchronize_session=False)).rowcount:
        get_session(db, session_id)  # distinguish missing session from wrong state
        raise HTTPException(409, "Recipe state changed or this action is not allowed. Reload the session.")
    return get_session(db, session_id)


def select_recipe(db, session_id, recipe_data):
    proposal = propose_recipe_usage(db, recipe_data)
    session = transition(db, session_id, ["planning"], "selected")
    session.title, session.instructions = proposal["recipe"], proposal["instructions"]
    set_items(db, session, proposal)
    return session


def reserve_recipe_items(db, session_id):
    session = transition(db, session_id, ["selected"], "reserved")
    for item in sorted(session.items, key=lambda item: item.inventory_item_id):
        amount = stock.reserve_inventory(db, item.inventory_item_id, item.requested_amount, item.requested_unit, session.id)
        item.reserved_amount = amount
        item.reserved_unit = stock.get_row(db, item.inventory_item_id).usable_unit
    db.flush()
    return session


def commit_recipe_items(db, session_id, actual_usage, confirmed=False):
    if confirmed is not True:
        raise HTTPException(400, "Explicit confirmation is required to consume inventory.")
    if not isinstance(actual_usage, list):
        raise HTTPException(400, "Supply actual usage for every ingredient.")
    session = transition(db, session_id, ["reserved"], "completed")
    amounts = {}
    for usage in actual_usage:
        if not isinstance(usage, dict):
            raise HTTPException(400, "Actual usage must contain objects.")
        inventory_id = usage.get("inventory_id")
        stock.get_row(db, inventory_id)
        if inventory_id in amounts:
            raise HTTPException(400, "Duplicate actual usage ID.")
        amounts[inventory_id] = (valid_amount(usage.get("amount")), normalize_unit(usage.get("unit")))
    if set(amounts) != {item.inventory_item_id for item in session.items}:
        raise HTTPException(400, "Actual usage must contain exactly the recipe's inventory IDs.")
    for item in sorted(session.items, key=lambda item: item.inventory_item_id):
        amount, unit = amounts[item.inventory_item_id]
        actual = convert_amount(amount, unit, item.reserved_unit)
        item.consumed_amount = stock.commit_reserved_inventory(db, item.inventory_item_id,
            item.reserved_amount, actual, item.reserved_unit, session.id)
        item.consumed_unit = item.reserved_unit
        item.reserved_amount = 0
    db.flush()
    return session


def cancel_recipe_session(db, session_id):
    session = get_session(db, session_id)
    was_reserved = session.status == "reserved"
    session = transition(db, session_id, [session.status] if session.status in {"planning", "selected", "reserved"} else [], "cancelled")
    if was_reserved:
        for item in sorted(session.items, key=lambda item: item.inventory_item_id):
            stock.release_reserved_inventory(db, item.inventory_item_id, item.reserved_amount, item.reserved_unit, session.id)
            item.reserved_amount = 0
    db.flush()
    return session
