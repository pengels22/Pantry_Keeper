"""Recipe APIs use one database transaction per user action."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from db import get_db
from models import InventoryTransaction, RecipeSession
from services import recipe_inventory as stock
from services import recipe_service as recipes
from services.recipe_schemas import Measurements, RecipeProposal, SessionRequest, CommitRequest, Adjustment, ChatRequest, ConfirmedRequest
from services.openai_recipes import recipe_chat

router = APIRouter()


def mutate(db, operation):
    try:
        result = operation()
        db.commit()
        return result
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Inventory changed concurrently. Reload and try again.") from exc
    except Exception:
        db.rollback()
        raise


@router.get("/api/inventory")
def inventory(category: str | None = Query(None, max_length=128), limit: int = Query(200, ge=1, le=200),
              offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    return stock.get_inventory(db, category=category, limit=limit, offset=offset)


@router.get("/api/inventory/search")
def search(q: str = Query(..., min_length=1, max_length=200), db: Session = Depends(get_db)):
    return stock.search_inventory(db, q)


@router.get("/api/inventory/{inventory_id}")
def inventory_item(inventory_id: int, db: Session = Depends(get_db)):
    return stock.get_inventory_item(db, inventory_id)


@router.put("/api/inventory/{inventory_id}/measurements")
def measurements(inventory_id: int, payload: Measurements, db: Session = Depends(get_db)):
    return mutate(db, lambda: stock.configure_measurements(db, inventory_id, payload.model_dump()))


def adjustment_response(db, entry):
    return {"transaction_id": entry.id, "inventory": stock.get_inventory_item(db, entry.inventory_item_id)}


@router.post("/api/inventory/{inventory_id}/add")
def add(inventory_id: int, payload: Adjustment, db: Session = Depends(get_db)):
    return mutate(db, lambda: adjustment_response(db, stock.add_inventory(db, inventory_id, payload.amount, payload.unit, payload.reason)))


@router.post("/api/inventory/{inventory_id}/subtract")
def subtract(inventory_id: int, payload: Adjustment, db: Session = Depends(get_db)):
    return mutate(db, lambda: adjustment_response(db, stock.subtract_inventory(db, inventory_id, payload.amount, payload.unit, payload.reason)))


@router.get("/api/inventory/{inventory_id}/transactions")
def history(inventory_id: int, db: Session = Depends(get_db)):
    stock.get_row(db, inventory_id)
    return [{"id": row.id, "inventory_item_id": row.inventory_item_id, "change_amount": row.change_amount,
        "unit": row.unit, "quantity_field": row.quantity_field, "transaction_type": row.transaction_type,
        "reason": row.reason, "recipe_id": row.recipe_id, "undo_of_id": row.undo_of_id, "created_at": row.created_at.isoformat()}
        for row in db.query(InventoryTransaction).filter_by(inventory_item_id=inventory_id).order_by(InventoryTransaction.id.desc()).all()]


@router.post("/api/inventory/transactions/{transaction_id}/undo")
def undo(transaction_id: int, payload: ConfirmedRequest, db: Session = Depends(get_db)):
    return mutate(db, lambda: adjustment_response(db, stock.undo_inventory_transaction(db, transaction_id)))


@router.post("/api/recipes/chat")
async def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    return await recipe_chat(db, payload)


@router.post("/api/recipes/propose")
def propose(payload: RecipeProposal, db: Session = Depends(get_db)):
    return recipes.propose_recipe_usage(db, payload.model_dump())


@router.post("/api/recipes/session")
def create_session(payload: SessionRequest, db: Session = Depends(get_db)):
    return mutate(db, lambda: recipes.serialize_session(db, recipes.create_recipe_session(db,
        payload.proposal.model_dump() if payload.proposal else None, payload.title)))


@router.get("/api/recipes/sessions")
def sessions(db: Session = Depends(get_db)):
    return [{"id": row.id, "title": row.title, "status": row.status} for row in
        db.query(RecipeSession).order_by(RecipeSession.id.desc()).limit(100).all()]


@router.get("/api/recipes/{session_id}")
def recipe_session(session_id: int, db: Session = Depends(get_db)):
    return recipes.serialize_session(db, recipes.get_session(db, session_id))


@router.post("/api/recipes/{session_id}/select")
def select(session_id: int, payload: RecipeProposal, db: Session = Depends(get_db)):
    return mutate(db, lambda: recipes.serialize_session(db, recipes.select_recipe(db, session_id, payload.model_dump())))


@router.post("/api/recipes/{session_id}/reserve")
def reserve(session_id: int, db: Session = Depends(get_db)):
    return mutate(db, lambda: recipes.serialize_session(db, recipes.reserve_recipe_items(db, session_id)))


@router.post("/api/recipes/{session_id}/commit")
def commit(session_id: int, payload: CommitRequest, db: Session = Depends(get_db)):
    return mutate(db, lambda: recipes.serialize_session(db, recipes.commit_recipe_items(db, session_id,
        [item.model_dump() for item in payload.actual_usage], payload.confirmed)))


@router.post("/api/recipes/{session_id}/cancel")
def cancel(session_id: int, db: Session = Depends(get_db)):
    return mutate(db, lambda: recipes.serialize_session(db, recipes.cancel_recipe_session(db, session_id)))
