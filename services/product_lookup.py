import json
import os
from datetime import timedelta
from urllib.parse import quote_plus

import httpx
from sqlalchemy.orm import Session

from db import insert_if_absent
from models import Product, ProductLookupCache, ProductLookupRateLimit, utc_now
from services.upc import normalize_catalog_code

SOURCE = "open_food_facts"
CACHE_TTLS = {"FOUND": timedelta(days=7), "NOT_FOUND": timedelta(hours=24), "ERROR": timedelta(minutes=5)}


def find_local_products(db: Session, codes) -> dict[str, Product]:
    upcs = {normalize_catalog_code(code) for code in codes} - {""}
    if not upcs:
        return {}
    return {product.upc: product for product in db.query(Product).filter(Product.upc.in_(upcs)).all()}


def find_local_product(db: Session, raw_code: str, normalized_code: str | None = None):
    upc = normalize_catalog_code(raw_code or normalized_code)
    return find_local_products(db, [upc]).get(upc)


def cached_suggestion(db: Session, upc: str):
    if normalize_catalog_code(upc).startswith("costco:"):
        return None
    row = db.query(ProductLookupCache).filter_by(upc=normalize_catalog_code(upc), source=SOURCE).first()
    if not row or row.expires_at <= utc_now():
        return None
    return json.loads(row.raw_result) if row.status == "FOUND" and row.raw_result else None


async def lookup_open_food_facts(code: str) -> dict | None:
    """Low-level HTTP adapter. Call only through lookup_unknown_product."""
    if normalize_catalog_code(code).startswith("costco:"):
        return None
    url = f"https://world.openfoodfacts.org/api/v2/product/{normalize_catalog_code(code)}.json"
    async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
        resp = await client.get(url, headers={"User-Agent": "Pantry-Keeper/0.2"})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Invalid public UPC response.")
    if data.get("status") != 1:
        return None
    product = data.get("product") or {}
    if not isinstance(product, dict):
        raise ValueError("Invalid public UPC product.")
    name = product.get("product_name") or product.get("generic_name")
    if not isinstance(name, str) or not name.strip():
        return None
    return {"brand": product.get("brands"), "name": name,
            "size": product.get("quantity"), "category": product.get("categories"),
            "lookup_source": SOURCE}


async def lookup_unknown_product(db: Session, upc: str, fetcher=lookup_open_food_facts):
    """DB first, then persistent cache and a shared, conservative request gate.

    The gate is stored in the database so multiple server workers share the
    limit. Busy callers get RATE_LIMITED instead of spinning or retrying HTTP.
    """
    upc = normalize_catalog_code(upc)
    local = find_local_product(db, upc)
    if local:
        return {"product": local, "source": "Pantry Keeper", "lookup_status": "KNOWN"}
    if upc.startswith("costco:"):
        return {"lookup_status": "RETAILER_ITEM_NUMBER"}
    if not upc:
        return {"lookup_status": "INVALID_UPC"}
    now = utc_now()
    row = db.query(ProductLookupCache).filter_by(upc=upc, source=SOURCE).first()
    if row and row.expires_at > now:
        return {"suggestion": cached_suggestion(db, upc), "source": "External Lookup", "lookup_status": row.status, "cached": True}
    if os.getenv("ENABLE_PUBLIC_UPC_LOOKUP", "true").lower() not in {"1", "true", "yes", "on"}:
        return {"lookup_status": "DISABLED"}

    # Reserve a slot atomically, including across multiple application workers.
    insert_if_absent(db, ProductLookupRateLimit,
        {"source": SOURCE, "next_lookup_at": now}, ["source"])
    interval = max(1.0, float(os.getenv("UPC_LOOKUP_INTERVAL_SECONDS", "2")))
    claimed = db.query(ProductLookupRateLimit).filter(
        ProductLookupRateLimit.source == SOURCE, ProductLookupRateLimit.next_lookup_at <= now
    ).update({"next_lookup_at": now + timedelta(seconds=interval)}, synchronize_session=False)
    if not claimed:
        db.commit()
        return {"lookup_status": "RATE_LIMITED"}

    # Reserve this UPC too, so concurrent review requests do not repeat it.
    if row is None:
        inserted = insert_if_absent(db, ProductLookupCache,
            {"upc": upc, "source": SOURCE, "status": "ERROR",
             "lookup_timestamp": now, "expires_at": now + timedelta(seconds=30)}, ["upc", "source"])
        if not inserted:
            db.commit()
            return {"lookup_status": "RATE_LIMITED"}
    else:
        reserved = db.query(ProductLookupCache).filter(
            ProductLookupCache.id == row.id, ProductLookupCache.expires_at <= now
        ).update({"status": "ERROR", "expires_at": now + timedelta(seconds=30),
                  "raw_result": None}, synchronize_session=False)
        if not reserved:
            db.commit()
            return {"lookup_status": "RATE_LIMITED"}
    db.commit()
    try:
        suggestion = await fetcher(upc)
        status = "FOUND" if suggestion else "NOT_FOUND"
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        suggestion, status = None, "ERROR"
    row = db.query(ProductLookupCache).filter_by(upc=upc, source=SOURCE).one()
    row.status = status
    row.raw_result = json.dumps(suggestion) if suggestion else None
    row.product_name = suggestion.get("name") if suggestion else None
    row.brand = suggestion.get("brand") if suggestion else None
    row.lookup_timestamp = utc_now()
    row.expires_at = row.lookup_timestamp + CACHE_TTLS[status]
    db.commit()
    return {"suggestion": suggestion, "source": "External Lookup", "lookup_status": status, "cached": False}


def meijer_search_url(code: str) -> str:
    # This builds a manual link; it never requests or scrapes Meijer.
    return f"https://www.meijer.com/shopping/search.html?text={quote_plus(normalize_catalog_code(code))}"


def costco_search_url(code: str) -> str:
    number = normalize_catalog_code(code).removeprefix('costco:')
    return f"https://www.costco.com/s?keyword={quote_plus(number)}"
