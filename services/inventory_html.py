"""Read-only HTML export of inventory and its product details."""
from html import escape
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from models import Inventory, Product


def inventory_export_rows(db):
    columns = [column.label(f'inventory.{column.name}') for column in Inventory.__table__.columns]
    columns += [column.label(f'product.{column.name}') for column in Product.__table__.columns]
    result = db.execute(select(*columns).select_from(Inventory).outerjoin(
        Product, Inventory.product_id == Product.id).order_by(Inventory.id))
    return result.keys(), result.all()


def inventory_export_json(db):
    keys, rows = inventory_export_rows(db)
    return [dict(zip(keys, row)) for row in rows]


def inventory_html(db):
    keys, records = inventory_export_rows(db)
    headers = ''.join(f'<th scope="col">{escape(key)}</th>' for key in keys)
    rows = []
    for row in records:
        cells = ''.join(f'<td>{escape(str(value)) if value is not None else ""}</td>' for value in row)
        rows.append(f'<tr>{cells}</tr>')
    body = ''.join(rows) or f'<tr><td colspan="{len(keys)}">No inventory items.</td></tr>'
    return HTMLResponse(f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pantry Keeper inventory</title><style>
body {{ font-family: system-ui, sans-serif; margin: 1.5rem; }}
.table-scroll {{ overflow-x: auto; }}
table {{ border-collapse: collapse; font-size: .9rem; }}
th, td {{ border: 1px solid #cbd5e1; padding: .5rem; text-align: left; vertical-align: top; white-space: pre-wrap; }}
th {{ background: #e2e8f0; }} tbody tr:nth-child(even) {{ background: #f8fafc; }}
</style></head><body><h1>Pantry Keeper inventory</h1><p>{len(rows)} inventory items · All inventory and product fields</p>
<div class="table-scroll"><table><caption>Inventory and product details</caption><thead><tr>{headers}</tr></thead><tbody>{body}</tbody></table></div>
</body></html>''', headers={'Cache-Control': 'no-store'})
