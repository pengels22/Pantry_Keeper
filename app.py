import os
import math
import json
import secrets
import csv
import hashlib
import io
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from PIL import UnidentifiedImageError, Image
from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from db import engine, get_db
from models import BrowserDraft, Inventory, Product, Receipt, ReceiptItem, utc_now
from services.inventory_service import add_to_inventory
from services.receipt_parser import parse_receipt
from services.ocr import OCRUnavailable, extract_text_from_image
from services.pdf import extract_receipt_pdf, InvalidReceiptPDF, ReceiptPDFTooLarge
from services.product_lookup import (
    find_local_product, find_local_products, lookup_open_food_facts,
    lookup_unknown_product, cached_suggestion, meijer_search_url, costco_search_url,
)
from services.product_catalog import product_values, save_identified_product, resolve_pending_items
from services.schema import initialize_database
from services.upc import normalize_catalog_code

BASE_DIR = Path(__file__).resolve().parent
APP_NAME = os.getenv("APP_NAME", "Pantry Keeper")
RECEIPT_API_TOKEN = os.getenv("RECEIPT_API_TOKEN", "")

app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

initialize_database(engine)

INGREDIENT_UNITS = {
    "tsp", "teaspoon", "teaspoons", "tbsp", "tablespoon", "tablespoons", "cup", "cups",
    "oz", "ounce", "ounces", "lb", "lbs", "pound", "pounds", "g", "gram", "grams",
    "kg", "ml", "l", "liter", "liters", "pinch", "can", "cans", "package", "packages",
    "pkg", "bag", "bags", "clove", "cloves", "slice", "slices", "whole",
}

INGREDIENT_WORDS_TO_IGNORE = {
    "fresh", "chopped", "diced", "minced", "sliced", "shredded", "grated", "ground",
    "large", "small", "medium", "optional", "to", "taste", "and", "or", "of", "the",
    "a", "an", "for", "with", "plus", "extra", "divided", "packed", "drained",
}


def require_extension_token(authorization: str | None):
    if not RECEIPT_API_TOKEN:
        return
    expected = f"Bearer {RECEIPT_API_TOKEN}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid API token.")


def normalized_items(parsed):
    items = []
    if parsed.get("store", "Meijer") not in {"Meijer", "Costco"}:
        raise HTTPException(status_code=400, detail="Unsupported receipt store.")
    for item in parsed.get("items", []):
        try:
            raw_code = item.get("raw_code") or item.get("upc") or item.get("normalized_code")
            if parsed.get("store") == "Costco":
                if not isinstance(raw_code, str):
                    raise ValueError("Costco item number must be text.")
                raw_code = "costco:" + raw_code.removeprefix("costco:")
            upc = normalize_catalog_code(raw_code)
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(status_code=400, detail="Invalid receipt item code.") from exc
        try:
            quantity = float(item.get("quantity", 1.0))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Receipt quantity must be numeric.")
        if not math.isfinite(quantity) or quantity < 0:
            raise HTTPException(status_code=400, detail="Receipt quantity must be finite and nonnegative.")
        items.append({**item, "raw_code": item.get("raw_code") or upc, "upc": upc, "normalized_code": upc, "quantity": quantity})
    return items


async def resolve_items(db, items, meijer_products=None):
    # Finish the full local comparison before any external service is eligible.
    known = find_local_products(db, [item["upc"] for item in items])
    resolutions = {}
    attempts = 0
    for item in items:
        upc = item["upc"]
        if upc in resolutions:
            continue
        if upc in known:
            resolutions[upc] = {"status": "resolved", "source": "Pantry Keeper",
                "lookup_status": "KNOWN", "product": serialize_product(known[upc])}
            continue
        if upc.startswith("costco:"):
            resolutions[upc] = {"status": "unresolved", "source": "Needs Identification",
                "lookup_status": "RETAILER_ITEM_NUMBER", "classification": "COSTCO_ITEM_NUMBER",
                "costco_search_url": costco_search_url(upc)}
            continue
        search = {"meijer_search_url": meijer_search_url(upc), "classification": "UNKNOWN_UPC"}
        candidates = (meijer_products or {}).get(item["upc"], []) or (meijer_products or {}).get(item.get("raw_code"), [])
        if candidates:
            resolutions[upc] = {**search, "status": "suggested", "source": "External Lookup",
                "suggestion": candidates[0], "candidates": candidates}
            continue
        if attempts >= 5:
            suggestion = cached_suggestion(db, upc)
            result = {"suggestion": suggestion, "lookup_status": "DEFERRED"}
        else:
            result = await lookup_unknown_product(db, upc, fetcher=lookup_open_food_facts)
            if not result.get("cached"):
                attempts += 1
        if result.get("product"):
            resolutions[upc] = {"status": "resolved", "source": "Pantry Keeper",
                "lookup_status": "KNOWN", "product": serialize_product(result["product"])}
        else:
            resolutions[upc] = {**search, **result,
                "source": "External Lookup" if result.get("suggestion") else "Needs Identification",
                "status": "suggested" if result.get("suggestion") else "unresolved"}
    return [{**item, **resolutions[item["upc"]]} for item in items]


def serialize_product(p: Product):
    return {
        "id": p.id,
        "upc": p.upc,
        "notes": p.notes,
        "receipt_code_raw": p.receipt_code_raw,
        "gtin_normalized": p.gtin_normalized,
        "brand": p.brand,
        "name": p.name,
        "size": p.size,
        "unit": p.unit,
        "category": p.category,
        "default_location": p.default_location,
        "lookup_source": p.lookup_source,
    }


def serialize_inventory_product(p: Product, inventory_by_product):
    row = inventory_by_product.get(p.id)
    return {
        **serialize_product(p),
        "inventory_id": row.id if row else None,
        "package_quantity": row.package_quantity if row else None,
        "package_size": row.package_size if row else None,
        "package_unit": row.package_unit if row else None,
        "usable_quantity": row.usable_quantity if row else None,
        "usable_unit": row.usable_unit if row else None,
        "reserved_quantity": row.reserved_quantity if row else 0,
        "available_quantity": max(0, row.usable_quantity - row.reserved_quantity) if row and row.usable_quantity is not None else None,
        "inventory_quantity": row.quantity if row else 0,
        "inventory_location": row.location if row and row.location is not None else p.default_location,
    }


def inventory_rows(db):
    products = db.query(Product).order_by(Product.name.asc()).all()
    inventory = db.query(Inventory).all()
    inventory_by_product = {x.product_id: x for x in inventory}
    return [serialize_inventory_product(p, inventory_by_product) for p in products]


def generated_inventory_code(name):
    digest = hashlib.sha1(name.strip().lower().encode("utf-8")).hexdigest()
    return "99" + str(int(digest[:14], 16)).zfill(17)[:17]


def parse_quantity(value, default=0.0):
    try:
        quantity = float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Quantity must be numeric.")
    if not math.isfinite(quantity) or quantity < 0:
        raise HTTPException(status_code=400, detail="Quantity must be finite and nonnegative.")
    return quantity


def ingredient_name(value):
    text = re.sub(r"\([^)]*\)", " ", value.lower())
    text = re.sub(r"\b\d+([./]\d+)?\b", " ", text)
    text = re.sub(r"[^\w\s-]", " ", text)
    tokens = []
    for token in re.split(r"\s+", text):
        token = token.strip("-_")
        if len(token) < 2 or token in INGREDIENT_UNITS or token in INGREDIENT_WORDS_TO_IGNORE:
            continue
        tokens.append(token)
    return " ".join(tokens).strip()


def ingredient_tokens(value):
    return set(ingredient_name(value).split())


def parse_recipe_text(text):
    ingredients = []
    for line in text.splitlines():
        line = line.strip(" \t-*•")
        if not line or line.lower() in {"ingredients", "ingredient list"}:
            continue
        ingredients.append({"raw": line, "name": ingredient_name(line) or line.lower()})
    return ingredients


def parse_recipe_csv(text):
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    fields = {field.lower().strip(): field for field in reader.fieldnames}
    name_field = fields.get("ingredient") or fields.get("name") or fields.get("item")
    quantity_field = fields.get("quantity") or fields.get("qty") or fields.get("amount")
    if not name_field:
        raise HTTPException(status_code=400, detail="Recipe CSV needs an ingredient, name, or item column.")
    ingredients = []
    for row in reader:
        raw_name = (row.get(name_field) or "").strip()
        if not raw_name:
            continue
        quantity = (row.get(quantity_field) or "").strip() if quantity_field else ""
        raw = f"{quantity} {raw_name}".strip()
        ingredients.append({"raw": raw, "name": ingredient_name(raw_name) or raw_name.lower()})
    return ingredients


def compare_ingredients(db, ingredients):
    stock = [row for row in inventory_rows(db)
             if float(row["available_quantity"] if row.get("available_quantity") is not None
                      else row.get("inventory_quantity") or 0) > 0]
    results = []
    for ingredient in ingredients:
        tokens = ingredient_tokens(ingredient["name"])
        best = None
        best_score = 0
        for product in stock:
            haystack = " ".join(str(product.get(field) or "") for field in ["name", "brand", "category", "notes"]).lower()
            product_tokens = set(re.findall(r"[a-z0-9]+", haystack))
            if not tokens or not product_tokens:
                continue
            overlap = len(tokens & product_tokens)
            score = overlap / max(len(tokens), 1)
            if ingredient["name"] and ingredient["name"] in haystack:
                score += 0.75
            if score > best_score:
                best = product
                best_score = score
        matched = bool(best and best_score >= 0.5)
        results.append({
            "ingredient": ingredient["raw"],
            "normalized_ingredient": ingredient["name"],
            "status": "in_stock" if matched else "purchase",
            "matched_product": best if matched else None,
            "match_score": round(best_score, 3) if matched else 0,
        })
    return {
        "items": results,
        "in_stock": [item for item in results if item["status"] == "in_stock"],
        "shopping_list": [item for item in results if item["status"] == "purchase"],
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"app_name": APP_NAME})


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    return {
        "products": inventory_rows(db),
        "receipt_count": db.query(Receipt).count(),
        "unknown_count": db.query(ReceiptItem).filter(ReceiptItem.status == "unresolved").count(),
    }


@app.get("/api/inventory/export")
def export_inventory(db: Session = Depends(get_db)):
    output = io.StringIO()
    fields = [
        "upc", "name", "brand", "size", "unit", "category", "notes",
        "default_location", "quantity", "location",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for product in inventory_rows(db):
        writer.writerow({
            "upc": product.get("upc") or product.get("receipt_code_raw") or "",
            "name": product.get("name") or "",
            "brand": product.get("brand") or "",
            "size": product.get("size") or "",
            "unit": product.get("unit") or "",
            "category": product.get("category") or "",
            "notes": product.get("notes") or "",
            "default_location": product.get("default_location") or "",
            "quantity": product.get("inventory_quantity") or 0,
            "location": product.get("inventory_location") or "",
        })
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="pantry-inventory.csv"'},
    )


@app.post("/api/inventory/import")
async def import_inventory_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    data = await file.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Inventory CSV is too large (maximum 2 MB).")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Inventory CSV must be UTF-8 text.") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="Inventory CSV needs a header row.")
    fields = {field.lower().strip(): field for field in reader.fieldnames}
    name_field = fields.get("name") or fields.get("product") or fields.get("item")
    quantity_field = fields.get("quantity") or fields.get("qty") or fields.get("inventory_quantity")
    if not name_field:
        raise HTTPException(status_code=400, detail="Inventory CSV needs a name, product, or item column.")

    imported = 0
    updated = 0
    for row in reader:
        name = (row.get(name_field) or "").strip()
        if not name:
            continue
        upc = (row.get(fields.get("upc", ""), "") or row.get(fields.get("receipt_code_raw", ""), "") or "").strip()
        if not upc:
            upc = generated_inventory_code(name)
        payload = {
            "name": name,
            "brand": row.get(fields.get("brand", ""), ""),
            "size": row.get(fields.get("size", ""), ""),
            "unit": row.get(fields.get("unit", ""), ""),
            "category": row.get(fields.get("category", ""), ""),
            "notes": row.get(fields.get("notes", ""), ""),
            "default_location": row.get(fields.get("default_location", ""), "") or row.get(fields.get("location", ""), ""),
            "lookup_source": "csv_import",
        }
        product = save_identified_product(db, upc, payload)
        quantity = parse_quantity(row.get(quantity_field) if quantity_field else 0)
        location = (row.get(fields.get("location", ""), "") or payload["default_location"] or "").strip() or None
        inventory = db.query(Inventory).filter(Inventory.product_id == product.id).first()
        if not inventory:
            add_to_inventory(db, product, 0, location)
            inventory = db.query(Inventory).filter(Inventory.product_id == product.id).first()
            imported += 1
        else:
            updated += 1
        from services.recipe_inventory import set_legacy_quantity
        set_legacy_quantity(db, inventory.id, quantity, location, update_location=True)
    db.commit()
    return {"imported": imported, "updated": updated}


@app.post("/api/recipes/compare")
def compare_recipe(payload: dict, db: Session = Depends(get_db)):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Recipe text is required.")
    ingredients = parse_recipe_text(text)
    if not ingredients:
        raise HTTPException(status_code=400, detail="No ingredients found.")
    return compare_ingredients(db, ingredients)


@app.post("/api/recipes/upload")
async def upload_recipe(file: UploadFile = File(...), db: Session = Depends(get_db)):
    data = await file.read(512 * 1024 + 1)
    if len(data) > 512 * 1024:
        raise HTTPException(status_code=413, detail="Recipe file is too large (maximum 512 KB).")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="Recipe file must be UTF-8 text or CSV.") from exc
    filename = (file.filename or "").lower()
    ingredients = parse_recipe_csv(text) if filename.endswith(".csv") else parse_recipe_text(text)
    if not ingredients:
        raise HTTPException(status_code=400, detail="No ingredients found.")
    return compare_ingredients(db, ingredients)


@app.post("/api/receipts/scan-image")
async def scan_image(
    source_type: str = Form("upload"),
    file: UploadFile = File(...),
):
    image_bytes = await file.read()
    try:
        text = extract_text_from_image(image_bytes)
    except OCRUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    parsed = parse_receipt(text)
    return {
        "source_type": source_type,
        "source_format": "image",
        "ocr_text": text,
        "parsed": parsed,
    }


@app.post("/api/receipts/scan-text")
async def scan_text(payload: dict):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="No receipt text supplied.")
    parsed = parse_receipt(text)
    return {
        "source_type": payload.get("source_type", "manual_text"),
        "source_format": "text",
        "raw_text": text,
        "parsed": parsed,
    }


@app.post("/api/receipts/browser")
async def scan_browser(
    payload: dict,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    require_extension_token(authorization)
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail="Browser extension supplied no text.")
    text = text.strip()
    if len(text) > 500_000:
        raise HTTPException(status_code=413, detail="Receipt text is too large. Open an individual receipt.")
    parsed = parse_receipt(text)
    if not parsed["items"]:
        raise HTTPException(status_code=422, detail="No receipt items recognized. Open an individual receipt or upload a screenshot.")
    scan = {
        "source_type": "safari_extension",
        "source_format": "webpage_text",
        "raw_text": text,
        "parsed": parsed,
    }
    return save_browser_draft(db, scan)


def save_browser_draft(db: Session, scan: dict):
    db.query(BrowserDraft).filter(BrowserDraft.expires_at <= utc_now()).delete()
    if db.query(BrowserDraft).count() >= 1000:
        db.commit()
        raise HTTPException(status_code=503, detail="Too many pending browser receipts. Try again later.")
    draft_id = secrets.token_urlsafe(32)
    db.add(BrowserDraft(id=draft_id, scan_json=json.dumps(scan),
                        expires_at=utc_now() + timedelta(minutes=30)))
    db.commit()
    items = normalized_items(scan["parsed"])
    known = find_local_products(db, [item["upc"] for item in items])
    return {**scan, "draft_id": draft_id,
        "known_items": [item for item in items if item["upc"] in known],
        "unknown_items": [item for item in items if item["upc"] not in known],
        # Only unique UPCs missing from our catalog are eligible for Meijer.
        "lookup_items": list({item["upc"]: {**item, "raw_code": item["upc"],
            "meijer_search_url": meijer_search_url(item["upc"])}
            for item in items if item["upc"] not in known and item["upc"] and not item["upc"].startswith("costco:")}.values()),
    }


@app.post("/api/receipts/browser-image")
async def scan_browser_image(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    require_extension_token(authorization)
    image_bytes = await file.read(20 * 1024 * 1024 + 1)
    if len(image_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Receipt image is too large (maximum 20 MB).")
    try:
        text = extract_text_from_image(image_bytes)
    except OCRUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(status_code=400, detail="Could not read this receipt image. Try a PNG or JPEG screenshot.")
    parsed = parse_receipt(text)
    if not parsed["items"]:
        raise HTTPException(status_code=422, detail="No receipt items recognized in the image. Try a clearer receipt screenshot.")
    return save_browser_draft(db, {
        "source_type": "safari_extension", "source_format": "image",
        "ocr_text": text, "parsed": parsed,
    })


@app.post("/api/receipts/browser-pdf")
async def scan_browser_pdf(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    require_extension_token(authorization)
    pdf_bytes = await file.read(20 * 1024 * 1024 + 1)
    if len(pdf_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Receipt PDF is too large (maximum 20 MB).")
    try:
        text, used_ocr = extract_receipt_pdf(pdf_bytes)
    except InvalidReceiptPDF as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ReceiptPDFTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except OCRUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    parsed = parse_receipt(text)
    if not parsed["items"]:
        raise HTTPException(status_code=422, detail="No receipt items recognized in this PDF. Try a clearer receipt screenshot.")
    return save_browser_draft(db, {
        "source_type": "safari_extension", "source_format": "pdf",
        "raw_text": text, "ocr_text": text if used_ocr else None, "parsed": parsed,
    })


@app.get("/api/receipts/browser-drafts/{draft_id}")
def browser_draft(draft_id: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    draft = db.get(BrowserDraft, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Receipt draft not found. Scan it again in Safari.")
    if draft.expires_at <= utc_now():
        db.delete(draft)
        db.commit()
        raise HTTPException(status_code=410, detail="Receipt draft expired. Scan it again in Safari.")
    return json.loads(draft.scan_json)


@app.post("/api/receipts/browser-drafts/{draft_id}/meijer-products")
def attach_meijer_products(
    draft_id: str, payload: dict,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    require_extension_token(authorization)
    draft = db.get(BrowserDraft, draft_id)
    if not draft or draft.expires_at <= utc_now():
        raise HTTPException(status_code=410, detail="Receipt draft expired. Scan it again.")
    scan = json.loads(draft.scan_json)
    if scan["parsed"].get("store", "Meijer") != "Meijer":
        raise HTTPException(status_code=400, detail="Meijer product matches cannot be attached to a Costco receipt.")
    codes = {normalize_catalog_code(item["raw_code"]) for item in scan["parsed"]["items"]}
    products = payload.get("products")
    if not isinstance(products, dict):
        raise HTTPException(status_code=400, detail="Product lookup results must be a mapping.")
    cleaned = {}
    for code, candidates in products.items():
        code = normalize_catalog_code(code)
        if code not in codes or not isinstance(candidates, list):
            raise HTTPException(status_code=400, detail="Product results must belong to this receipt.")
        cleaned[code] = []
        for candidate in candidates[:5]:
            if not isinstance(candidate, dict):
                continue
            name = candidate.get("name")
            url = candidate.get("url")
            if not isinstance(name, str) or not name.strip() or not isinstance(url, str):
                continue
            parsed_url = urlparse(url)
            host = parsed_url.hostname or ""
            if (parsed_url.scheme != "https" or not (host == "meijer.com" or host.endswith(".meijer.com"))
                or not parsed_url.path.startswith("/shopping/product/") or parsed_url.username or parsed_url.password):
                continue
            url_code = parsed_url.path.rsplit("/", 1)[-1].removesuffix(".html")
            row = {"name": name.strip()[:255], "url": url[:2048], "lookup_source": "meijer",
                   "match_kind": "exact_code" if url_code == code else "search_result"}
            for field, limit in [("brand", 128), ("size", 64), ("category", 128)]:
                value = candidate.get(field)
                row[field] = value.strip()[:limit] if isinstance(value, str) else None
            cleaned[code].append(row)
        cleaned[code].sort(key=lambda value: value["match_kind"] != "exact_code")
    scan["meijer_products"] = cleaned
    errors = payload.get("errors", [])
    scan["meijer_lookup_errors"] = [value[:255] for value in errors[:10] if isinstance(value, str)] if isinstance(errors, list) else []
    # Product searches may take time; keep the review available for 30 minutes afterward.
    draft.expires_at = utc_now() + timedelta(minutes=30)
    draft.scan_json = json.dumps(scan)
    db.commit()
    return {"matched_codes": sum(bool(value) for value in cleaned.values())}


@app.post("/api/receipts/resolve-preview")
async def resolve_preview(payload: dict, db: Session = Depends(get_db)):
    parsed = payload.get("parsed") or {}
    return {"items": await resolve_items(db, normalized_items(parsed), payload.get("meijer_products"))}


@app.get("/api/products/lookup")
async def lookup_product(upc: str, db: Session = Depends(get_db)):
    normalized = normalize_catalog_code(upc)
    if not normalized or len(normalized) > 64:
        raise HTTPException(status_code=400, detail="Enter a UPC of 1–64 digits.")
    return (await resolve_items(db, [{"raw_code": upc, "upc": normalized,
                                    "normalized_code": normalized}]))[0]


@app.post("/api/products")
def create_product(payload: dict, db: Session = Depends(get_db)):
    product = save_identified_product(db, payload.get("upc") or payload.get("receipt_code_raw"), payload)
    resolve_pending_items(db, product)
    db.commit()
    return serialize_product(product)


@app.post("/api/receipts/import")
def import_receipt(payload: dict, db: Session = Depends(get_db)):
    parsed = payload.get("parsed") or {}
    fingerprint = parsed.get("fingerprint")
    if not fingerprint:
        raise HTTPException(status_code=400, detail="Receipt fingerprint missing.")

    existing = db.query(Receipt).filter(Receipt.fingerprint == fingerprint).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Receipt already imported as #{existing.id}.")

    items = normalized_items(parsed)
    known = find_local_products(db, [item["upc"] for item in items])
    selected_products = payload.get("selected_products", {})
    codes = {item["upc"] for item in items}
    if not isinstance(selected_products, dict):
        raise HTTPException(status_code=400, detail="Selected products must be a mapping.")
    selected_normalized = {}
    for code, selected in selected_products.items():
        upc = normalize_catalog_code(code)
        if upc not in codes or not isinstance(selected, dict):
            raise HTTPException(status_code=400, detail="Selected products must belong to this receipt.")
        product_values(selected)
        selected_normalized[upc] = selected
    for upc, selected in selected_normalized.items():
        # Local catalog wins even if a stale browser review sends suggestions.
        if upc not in known:
            known[upc] = save_identified_product(db, upc, selected)
        resolve_pending_items(db, known[upc])

    receipt = Receipt(
        store=parsed.get("store", "Meijer"),
        purchase_date=parsed.get("purchase_date"),
        store_number=parsed.get("store_number"),
        terminal=parsed.get("terminal"),
        transaction_number=parsed.get("transaction_number"),
        operator=parsed.get("operator"),
        purchase_time=parsed.get("purchase_time"),
        total=parsed.get("total"),
        source_type=payload.get("source_type", "unknown"),
        source_format=payload.get("source_format", "unknown"),
        raw_text=payload.get("raw_text"),
        ocr_text=payload.get("ocr_text"),
        fingerprint=fingerprint,
    )
    db.add(receipt)
    db.flush()

    unresolved = 0
    for item in items:
        product = known.get(item["upc"])
        status = "resolved" if product else "unresolved"
        if not product:
            unresolved += 1

        row = ReceiptItem(
            receipt_id=receipt.id,
            product_id=product.id if product else None,
            raw_code=item.get("raw_code"),
            normalized_code=item.get("normalized_code"),
            receipt_description=item.get("receipt_description"),
            quantity=item.get("quantity", 1.0),
            weight=item.get("weight"),
            weight_unit=item.get("weight_unit"),
            unit_price=item.get("unit_price"),
            line_total=item.get("line_total"),
            tax_flag=item.get("tax_flag"),
            parse_confidence=item.get("parse_confidence"),
            status=status,
        )
        db.add(row)

        if product:
            add_to_inventory(db, product, float(item.get("quantity", 1.0)))

    db.commit()
    return {"receipt_id": receipt.id, "imported_items": len(parsed.get("items", [])),
            "resolved_items": len(parsed.get("items", [])) - unresolved, "unresolved_items": unresolved}


@app.get("/api/unknown-products")
def unknown_products(db: Session = Depends(get_db)):
    rows = (
        db.query(ReceiptItem)
        .filter(ReceiptItem.status == "unresolved")
        .order_by(ReceiptItem.id.desc())
        .all()
    )
    return [
        {
            "receipt_item_id": r.id,
            "raw_code": r.raw_code,
            "normalized_code": r.normalized_code,
            "upc": normalize_catalog_code(r.normalized_code or r.raw_code),
            "classification": "COSTCO_ITEM_NUMBER" if (r.normalized_code or "").startswith("costco:") else "UNKNOWN_UPC",
            "suggestion": cached_suggestion(db, r.normalized_code or r.raw_code or ""),
            "description": r.receipt_description,
            "quantity": r.quantity,
            "line_total": r.line_total,
            "meijer_search_url": None if (r.normalized_code or "").startswith("costco:") else meijer_search_url(r.raw_code or ""),
            "costco_search_url": costco_search_url(r.normalized_code) if (r.normalized_code or "").startswith("costco:") else None,
        }
        for r in rows
    ]


@app.post("/api/unknown-products/{receipt_item_id}/resolve")
def resolve_unknown(receipt_item_id: int, payload: dict, db: Session = Depends(get_db)):
    row = db.query(ReceiptItem).filter(ReceiptItem.id == receipt_item_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Receipt item not found.")

    if row.status == "resolved":
        return {"ok": True, "product": serialize_product(row.product)}

    code = row.normalized_code or row.raw_code or ""
    product = find_local_product(db, code)
    if not product:
        product = save_identified_product(db, code, payload)
    resolve_pending_items(db, product)
    db.commit()
    return {"ok": True, "product": serialize_product(product)}


@app.post("/api/inventory/{product_id}")
def update_inventory(product_id: int, payload: dict, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    try:
        quantity = float(payload["quantity"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="A numeric quantity is required.")
    if not math.isfinite(quantity) or quantity < 0:
        raise HTTPException(status_code=400, detail="Quantity must be finite and nonnegative.")
    row = db.query(Inventory).filter(Inventory.product_id == product_id).first()
    if not row:
        row = add_to_inventory(db, product, 0)
    if "location" in payload:
        location = payload["location"]
        if location is not None and not isinstance(location, str):
            raise HTTPException(status_code=400, detail="Location must be text.")
    from services.recipe_inventory import set_legacy_quantity
    row = set_legacy_quantity(db, row.id, quantity, payload.get("location"), update_location="location" in payload)
    db.commit()
    return {"product_id": product_id, "quantity": row.quantity, "location": row.location}


@app.get("/recipes", response_class=HTMLResponse)
def recipe_assistant(request: Request):
    return templates.TemplateResponse(request=request, name="recipes.html", context={"app_name": APP_NAME})


from recipe_routes import router as recipe_router
app.include_router(recipe_router)
