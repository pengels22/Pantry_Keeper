import os
from urllib.parse import quote_plus
import httpx
from sqlalchemy.orm import Session

from models import Product


def find_local_product(db: Session, raw_code: str, normalized_code: str | None = None):
    product = db.query(Product).filter(Product.receipt_code_raw == raw_code).first()
    if product:
        return product
    if normalized_code:
        return db.query(Product).filter(Product.gtin_normalized == normalized_code).first()
    return None


async def lookup_open_food_facts(code: str) -> dict | None:
    normalized = "".join(ch for ch in code if ch.isdigit())
    if not normalized:
        return None

    url = f"https://world.openfoodfacts.org/api/v2/product/{normalized}.json"
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Pantry-Keeper/0.1"})
            if resp.status_code != 200:
                return None
            data = resp.json()
    except Exception:
        return None

    if data.get("status") != 1:
        return None

    p = data.get("product") or {}
    name = p.get("product_name") or p.get("generic_name")
    if not name:
        return None

    return {
        "brand": p.get("brands"),
        "name": name,
        "size": p.get("quantity"),
        "category": p.get("categories"),
        "lookup_source": "open_food_facts",
    }


def meijer_search_url(code: str) -> str:
    # Reliable manual fallback without storing Meijer credentials or scraping.
    return f"https://www.meijer.com/shopping/search.html?text={quote_plus(code)}"
