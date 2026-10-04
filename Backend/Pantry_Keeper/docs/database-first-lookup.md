# Database-first product identification

Pantry Keeper treats its saved product catalog as authoritative. Receipt review,
manual UPC entry, hardware barcode-reader input, browser receipt capture, OCR,
PDF capture, imports, and unknown-product saves share the same normalization and
local lookup functions. The API is also available for future barcode scanners.

## Normalization and migration

`services/upc.py::normalize_upc` removes everything except ASCII digits. UPCs must
be supplied as strings. Every leading zero is preserved; no zero is added and
no integer conversion is performed. `71928395643` remains `71928395643`, and
`00-719 28395643` becomes `0071928395643`. Codes with different leading zeros
remain distinct. The receipt parser's `normalize_code` is a compatibility wrapper.

Startup runs the idempotent additive migration in `services/schema.py`:

- Add `products.upc` as TEXT and a unique index `ix_products_upc`.
- Backfill UPCs from raw receipt codes, retaining the old raw and GTIN columns.
  This reverses the old artificial 11-digit padding for lookup without changing
  the stored legacy product fields.
- Add nullable `products.notes` (the existing `unit` field is Default Unit).
- Normalize existing receipt-item lookup keys; retain receipt links and quantities.
- Create `product_lookup_cache` and `product_lookup_rate_limits`.

Existing products, receipts, inventory, and scan drafts are retained. If legacy
products normalize to the same UPC, or contain no digits, migration stops with an
error identifying the affected product. It does not delete or silently combine
conflicting records. New writes always require a nonempty text UPC.

## Local comparison and unknown items

Receipt review extracts and normalizes every code first, then queries products
with one `WHERE upc IN (...)` query. Known items receive the **Known · Pantry
Keeper** badge and use the saved name and fields. They have no automatic external
lookup or Search Meijer control. Imports similarly check the whole receipt
locally and use existing products even if an old review supplies a stale suggestion.

Unknown items are marked `UNKNOWN_UPC`. They remain temporary review items until
import; Safari drafts persist for 30 minutes. Imported unknowns become pending
receipt items in Unknown Products. Identifying one UPC resolves all pending
receipt items for it and adds their quantities once. Saving a product without a
purchase does not invent an inventory quantity.

Unknowns may receive an Open Food Facts suggestion. The prefilled fields remain
editable; importing confirms selected suggestions. Choose Leave unidentified to
defer identification. You can enter a product manually in receipt review, in
Unknown Products, or in Find or Add Product. Forms provide read-only populated
UPC, name, brand, size, category, Default Unit, notes, and default location.

Concurrent saves use database `INSERT ... ON CONFLICT DO NOTHING`. The existing
product wins, retains its saved information, and is returned instead of creating
a duplicate. Product and receipt saves share a transaction.

## Public lookup cache and rate limits

`product_lookup_cache` stores UPC, source, product name, brand, serialized result,
lookup timestamp, expiry, and status. TTLs are:

- FOUND: seven days.
- NOT_FOUND: 24 hours.
- ERROR: five minutes (in-progress reservations last 30 seconds).

Negative results never prevent manual identification. Known products always take
precedence over cache entries. The HTTP timeout is eight seconds; there are no
HTTP retries or redirect-following requests. A database request gate limits new
requests across server workers to one every two seconds by default, with a
minimum configurable interval of one second. A review considers at most five
uncached lookups. Busy or deferred items stay editable and offer **Try public
UPC lookup**; no background retry loop runs.

Optional `.env` settings (restart after changing):

```dotenv
ENABLE_PUBLIC_UPC_LOOKUP=true
UPC_LOOKUP_INTERVAL_SECONDS=2
```

## Manual Meijer search and private browsing

Meijer search URLs are built only by `services/product_lookup.py::meijer_search_url`
using the normalized UPC. The backend does not scrape Meijer. The Safari scanner
checks the server's database classification first and searches Meijer only for
unique unknown UPCs, sequentially in a temporary tab. It opens no search tab when
all codes are known. After review/import saves a suggestion, subsequent purchases
use the saved product. Searches pause between unknown products and stop on
permission, navigation, or sign-in trouble. The web app's manual UPC form and
screenshot uploads retain public-service/manual lookup; automatic Meijer search
runs through the Safari extension's local browser access.

Unknown items offer Search Meijer and Copy Search URL. Frontend functions:

- `openExternalProductSearch(url)`: the single web search-opening interface.
- `copySearchUrl(url)`: clipboard support with a fallback for HTTP servers.
- `addSearchControls(container, url)`: consistent Search and Copy controls.
- `productFieldsHtml` / `readProductFields`: shared identification forms.
- `lookupManualUpc(event)`: database-first manual/barcode-reader lookup.

A web page cannot force Safari Private Browsing. Copy Search URL lets you paste
into an existing private window. A future Mac-local helper can replace the body
of `openExternalProductSearch`; it must run on the Mac, remain optional, and
handle its own permissions and allowed URLs. No Linux server endpoint claiming
to control another machine's Safari has been added. The app works without a helper.

## API changes

New: `GET /api/products/lookup?upc=...` normalizes the string and returns a known
product, an external suggestion, or an unknown status with a manual search URL.
Known results do not include a search URL. A barcode reader or future scanner can
use this endpoint directly.

Updated existing APIs:

- `POST /api/receipts/resolve-preview`: batch local lookup, deduplicated/cached
  public lookups only for unknown codes, and source badges.
- `POST /api/receipts/import`: normalized local comparison and atomic
  `selected_products` saves; saved local information takes precedence.
- `POST /api/products`: accepts `upc` (or legacy `receipt_code_raw`), plus unit
  and notes; resolves pending purchases and returns an existing duplicate cleanly.
- `POST /api/unknown-products/{receipt_item_id}/resolve`: resolves every pending
  occurrence of the UPC without adding quantities twice.
- `GET /api/unknown-products`: includes normalized UPC and cached suggestions.
- Browser capture endpoints: batch local classification in `known_items` and
  `unknown_items`. `lookup_items` contains only unique unknown codes and their
  generated search URLs; known codes are never included.

The old draft Meijer-results attachment endpoint remains compatible with older
drafts. Older v1.1.x scanners can use the unknown-only list from this server.
If v1.2.0 was installed, rebuild v1.2.1 to restore automatic lookup.

## Run and validate

No dependencies were added. Stop the existing app process, then run:

```bash
cd /home/administrator/Pantry_Keeper
./run.sh
```

Startup applies the schema update automatically. Refresh Pantry Keeper in Safari
(the JavaScript asset URL was versioned to avoid old browser caches).

The extension source bundle is rebuilt with:

```bash
.venv/bin/python scripts/build-extension-bundle.py
```

Install/rebuild the v1.2.1 extension on your Mac using
[browser_extension/README.md](../browser_extension/README.md). Updating only the
server cannot restore the scanner JavaScript removed in extension v1.2.0.

Checks:

```bash
.venv/bin/python -m unittest discover -s tests -v
npm test
git diff --check
```

Tests cover zero external traffic for known UPCs, one local product query for
known receipts, mixed and duplicate codes, cache hits/expiry/errors, shared rate
limits, leading zeros, simultaneous saves, pending inventory quantities, manual
search/copy controls, migration preservation, and existing OCR/PDF workflows.
Actual signed Safari packaging and browsing require an Apple device.

## Changed files

Backend: `app.py`, `db.py`, `models.py`, `services/meijer_parser.py`,
`services/product_lookup.py`, `services/inventory_service.py`; added `services/upc.py`, `services/schema.py`, and
`services/product_catalog.py`.

Web UI: `templates/index.html`, `static/scripts/app.js`, `static/css/styles.css`.

Extension: `browser_extension/popup.js`, `popup.html`, `manifest.json`, `README.md`;
restored `product-lookup.js` and `product-capture.js` for unknown-only automatic searches.

Configuration/documentation/tests: `.env.example`, `README.md`, this document,
`tests/test_workflow.py`, and `tests/test_extension.cjs`.
