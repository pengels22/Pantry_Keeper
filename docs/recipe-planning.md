# Recipe planning and inventory consumption

The Recipe Assistant is at `/recipes`, linked from the inventory dashboard.
Pantry Keeper remains the inventory source of truth. AI tools only read inventory
and validate proposals. They cannot create reservations or consume stock.

## Files and schema

- `models.py`: adds inventory measurements, recipe sessions/items, and transaction history.
- `services/schema.py`: idempotent additive migration; no historical quantity inference.
- `scripts/migrate.py`: explicit migration command with a SQLite backup.
- `services/units.py`: aliases and compatible-dimension conversion.
- `services/recipe_inventory.py`: inventory reads, measured adjustments, reservations, consumption, undo, history, and legacy compatibility.
- `services/inventory_service.py`: receipt purchases participate in measurement updates and history.
- `services/recipe_service.py`: proposal validation and the user-driven recipe lifecycle.
- `services/recipe_schemas.py`: strict JSON request validation.
- `services/openai_recipes.py`: isolated Responses API client and five allowed function tools.
- `recipe_routes.py`: new JSON endpoints and transaction boundaries.
- `app.py`: new page/router, dashboard measurement fields, and recorded legacy adjustments/CSV imports.
- `templates/recipes.html`, `static/scripts/recipes.js`, `static/css/styles.css`: recipe chat, manual proposals, session recovery, measurement setup, reservation review, and actual usage confirmation.
- `templates/index.html`, `static/scripts/app.js`: assistant link and visible usable/reserved stock alongside the existing quantity editor. Existing recipe comparisons use measured available stock when configured.
- `README.md`, `package.json`: setup and test commands.
- `tests/test_recipes.py`, `tests/test_recipe_ui.cjs`: service, API, migration, concurrency, mocked OpenAI, and browser UI tests.

`inventory` adds nullable `package_quantity`, `package_size`, `package_unit`,
`usable_quantity`, `usable_unit`, plus `reserved_quantity` (default zero).
The legacy `quantity`, product `size` and `unit` remain available. Migrating old
rows leaves their measurement fields null because historical quantities may mean
packages or weight. No products, receipts, or stock are deleted or replaced.

New tables:

- `recipe_sessions`: title, instructions, status, and timestamps. Status is planning, selected, reserved, completed, or cancelled.
- `recipe_session_items`: exact inventory IDs, ingredient labels, requested/reserved/consumed amounts and units, and notes; unique per session/inventory item.
- `inventory_transactions`: inventory ID, signed amount, unit, quantity field, transaction type, reason, recipe ID, timestamp, and optional unique undo reference.

History is append-only through application APIs. Reservations record positive
changes to `reserved_quantity`; releases record negative changes to that balance.
Consumption records negative changes to `usable_quantity`. The `quantity_field`
column distinguishes these balances from historical package/legacy adjustments.
Undo appends a compensating entry and never removes the original entry.

## Configure measurements

Expand a product under Inventory Measurements. Configure a usable quantity/unit
and, optionally, complete package measurements. Leaving usable quantity blank
calculates it from package count × package size using compatible units.
Explicit usable quantities allow partially used packages.

| Example | Package count | Package size | Package unit | Usable quantity | Usable unit |
| --- | ---: | ---: | --- | ---: | --- |
| Pasta | 2 | 16 | oz | 32 | oz |
| Eggs | 1 | 12 | each | 12 | each |
| Minced garlic | 1 | 48 | oz | 48 | oz |

When you explicitly configure package count, the legacy quantity is synchronized
to that count and its adjustment is recorded. Future receipt purchases and legacy
quantity adjustments scale usable quantity only when package measurements have
been explicitly configured. Without package measurements, legacy counts and
usable quantities are independent; adjust usable quantity explicitly.

Recipes consume usable quantity. Package counts are not automatically recomputed
from partial ingredient use; an opened jar or box does not map unambiguously to a
fractional count of physical containers. Both balances appear on the dashboard.
The existing CSV format remains supported and covers the legacy inventory fields;
use the database backup to preserve recipe sessions, measured balances and history.

Aliases normalize to `each`, `oz`, `lb`, `g`, `kg`, `ml`, `L`, `tsp`, `tbsp`,
`cup`, `pint`, `quart`, and `gallon`. Volume units are US customary. Only weight
within weight, volume within volume, or count within count can convert. No density,
weight-to-volume, or package-to-ingredient conversion is guessed.

## Installation and migration

No new Python or JavaScript dependencies are required. HTTP requests use the
existing `httpx` dependency; the OpenAI SDK is not required.

If dependencies need installation:

```bash
cd /home/administrator/Pantry_Keeper
.venv/bin/python -m pip install -r requirements.txt
npm ci
```

Add these to the server `.env` without replacing your existing configuration:

```dotenv
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4-mini
```

Set the key on the server to enable chat. It is never rendered into templates,
returned by API endpoints, or sent to browser JavaScript. Model access depends on
your OpenAI account; override `OPENAI_MODEL` as needed. Chat does not save provider
responses (`store: false`). Inventory returned by tools is sent to OpenAI for the
recipe request. Manual recipe planning needs no API key.

Stop the running backend before migrating. For a systemd installation:

```bash
sudo systemctl stop pantry-keeper.service
.venv/bin/python scripts/migrate.py
sudo systemctl start pantry-keeper.service
```

The command backs up an existing SQLite database to a timestamped `.db` file
before applying the migration. For PostgreSQL, take a database backup with your
normal PostgreSQL tools before running the same migration command. Startup also
runs the same idempotent migration, so explicit migration is optional but lets
you take the SQLite backup before restart. For a manual installation, stop the
existing `run.sh` process, run the migration command, then run `./run.sh`.

Tests and migration verification during development use temporary databases and
a temporary copy of the existing database. They do not migrate the live database
or restart your running service.

## Workflow

1. Ask a recipe question or build a manual recipe proposal. Only inventory reads occur. Missing measurements must be configured first.
2. Choose This Recipe saves a selected session with exact inventory IDs. It does not change stock.
3. Start Cooking reserves quantities. Physical usable quantity stays unchanged; available quantity is usable minus reserved.
4. Finished Cooking opens editable actual-use amounts. No inventory changes occur yet.
5. Check the confirmation box and press Confirm Inventory Consumption. One transaction consumes actual quantities, releases holds, appends history, and completes the session.
6. Cancel Recipe releases holds and cancels a planning, selected, or reserved session without consuming ingredients.

Actual use may be smaller, zero, or larger than the reservation, provided it does
not consume stock held by other recipes. Failed reserves or commits roll back all
stock, history, item, and status changes. Repeated actions and simultaneous
requests cannot consume the same recipe twice. Inventory IDs, never model-supplied
names, determine which product is used. Reopen sessions from the dropdown or the
`/recipes?session=ID` URL after a browser refresh.

## API

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/inventory` | Read stock with category, limit, and offset filters |
| GET | `/api/inventory/search?q=...` | Search product names |
| GET | `/api/inventory/{inventory_id}` | Read a specific inventory item |
| PUT | `/api/inventory/{inventory_id}/measurements` | Explicitly configure package/usable measurements |
| POST | `/api/inventory/{inventory_id}/add` | Add usable stock with confirmation |
| POST | `/api/inventory/{inventory_id}/subtract` | Subtract unreserved usable stock with confirmation |
| GET | `/api/inventory/{inventory_id}/transactions` | Read append-only history |
| POST | `/api/inventory/transactions/{transaction_id}/undo` | Append a compensating physical-stock transaction with confirmation |
| POST | `/api/recipes/chat` | Chat using controlled Responses API tools |
| POST | `/api/recipes/propose` | Validate a read-only usage proposal |
| POST | `/api/recipes/session` | Save a selected proposal or create a planning session |
| GET | `/api/recipes/sessions` | List the most recent 100 sessions |
| GET | `/api/recipes/{session_id}` | Read session/items and live availability |
| POST | `/api/recipes/{session_id}/select` | Select a recipe in a planning session |
| POST | `/api/recipes/{session_id}/reserve` | Start cooking |
| POST | `/api/recipes/{session_id}/commit` | Consume confirmed actual usage |
| POST | `/api/recipes/{session_id}/cancel` | Cancel and release holds |

The existing `POST /api/inventory/{product_id}` remains the legacy quantity editor
and still accepts a product ID. All new inventory writes use internal inventory
IDs. Service operations participate in their caller's database transaction; the
HTTP mutation wrapper commits on success and rolls back on every failure.

Example selected-session request:

```json
{
  "proposal": {
    "recipe": "Chicken Pasta",
    "instructions": "Cook chicken thoroughly and combine with pasta.",
    "ingredients": [
      {"inventory_id": 142, "amount": 2, "unit": "each"},
      {"inventory_id": 87, "amount": 8, "unit": "oz"}
    ]
  }
}
```

Commit request:

```json
{
  "confirmed": true,
  "actual_usage": [
    {"inventory_id": 142, "amount": 2, "unit": "each"},
    {"inventory_id": 87, "amount": 8, "unit": "oz"}
  ]
}
```

Supply exactly the session's inventory IDs once each. Confirmation must be the
JSON boolean `true`, not a string or a number.

## Tests and remaining configuration choices

```bash
.venv/bin/python -m unittest discover -s tests -v
npm test
.venv/bin/python -m pip check
```

OpenAI integration tests mock HTTP calls. They validate the tool loop without
spending API credits. Live OpenAI access and PostgreSQL behavior must be checked
in an environment configured for those services; automated integration tests here
exercise SQLite. No MCP integration is included.

Configure actual product measurements and choose any model override. Reservations
persist until completion or cancellation; automatic expiration is a future policy
choice. Ingredient-specific density conversions and multi-user ownership are not
implemented. This page uses the existing app's access model; recipe confirmation
records the user action without introducing a new login system.

The implementation follows the official
[Responses API function-calling guide](https://developers.openai.com/api/docs/guides/function-calling)
and uses an overridable [GPT-5.4 Mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini) default.
