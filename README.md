# Pantry Keeper

Pantry Keeper is a small FastAPI web app for building a grocery/product database from Meijer receipts.

## Included groundwork

- Screenshot receipt upload
- Mobile camera capture
- OCR pipeline with Tesseract
- Safari receipt scanning extension
- Meijer receipt parser
- Local UPC / receipt-code catalog
- Open Food Facts lookup fallback
- Manual unknown-product resolver
- Inventory quantities
- Duplicate-receipt fingerprinting
- Local SQLite database
- Optional PostgreSQL connection through DATABASE_URL

## Directory layout

```text
Pantry_Keeper/
├── app.py
├── db.py
├── models.py
├── requirements.txt
├── templates/
│   └── index.html
├── static/
│   ├── css/
│   │   └── styles.css
│   └── scripts/
│       └── app.js
├── services/
│   ├── inventory_service.py
│   ├── meijer_parser.py
│   ├── ocr.py
│   ├── product_lookup.py
│   ├── product_catalog.py
│   ├── schema.py
│   ├── upc.py
│   └── pdf.py
└── browser_extension/
    ├── manifest.json
    ├── content.js
    ├── popup.html
    └── popup.js
```

## Quick start

Install Tesseract:

```bash
sudo apt update
sudo apt install -y tesseract-ocr python3-venv
```

Then:

```bash
cd Pantry_Keeper
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cat > .env <<'EOF'
APP_NAME=Pantry Keeper
DATABASE_URL=sqlite:///./pantry_keeper.db
ENABLE_OCR=true
EOF
./run.sh
```

The startup script binds to all network interfaces (`0.0.0.0`) on port 8000. Open `http://YOUR_SERVER_IP:8000` from another device. Override the address or port with `PANTRY_HOST` or `PANTRY_PORT` when needed.

Create `.env` only for a new installation; preserve an existing configuration.
The file is ignored by Git. Optional settings include `RECEIPT_API_TOKEN`,
`TESSERACT_CMD`, `ENABLE_PUBLIC_UPC_LOOKUP` (defaults to `true`), and
`UPC_LOOKUP_INTERVAL_SECONDS` (defaults to `2`). Recipe chat additionally uses
`OPENAI_API_KEY` and optional `OPENAI_MODEL`.

This defaults to SQLite. The app is configured for Python 3.14.8 and runs directly with `./run.sh`.

## Start automatically with systemd

`deploy/pantry-keeper.service` runs the backend as `administrator` from
`/home/administrator/Pantry_Keeper`, using the existing virtual environment,
`.env`, and database. It starts at boot, restarts after failures, and sends logs
to the system journal. Adjust the user, group, and paths for another installation.

For initial installation, stop any manually started `./run.sh` process first
(Ctrl+C in its terminal), then run:

```bash
sudo install -m 644 deploy/pantry-keeper.service /etc/systemd/system/pantry-keeper.service
sudo systemctl daemon-reload
sudo systemctl enable --now pantry-keeper.service
```

Once installed, manage the backend with systemd instead of running a second
`./run.sh` process on the same port:

```bash
sudo systemctl status pantry-keeper.service
sudo systemctl restart pantry-keeper.service
sudo journalctl -u pantry-keeper.service -f
```

Restart the service after changing code or `.env`. To stop it, use
`sudo systemctl stop pantry-keeper.service`; to stop it and turn off boot startup,
use `sudo systemctl disable --now pantry-keeper.service`.

## Safari extension

The extension captures receipt text, images, or PDFs on demand, saves server settings, and opens a temporary receipt draft in Pantry Keeper for review. It supports Mac and iPhone/iPad packaging through Xcode.

On a Mac with Xcode, run `./scripts/package-safari.sh`, build/run the generated project, and enable the extension in Safari. Configure it with your server’s reachable IP/hostname and API token, then open an individual Meijer receipt and choose **Scan this receipt**.

See [extension setup and troubleshooting](browser_extension/README.md) for packaging, permissions, and device instructions.

## Notes

- The Meijer parser is intentionally isolated in `services/meijer_parser.py` so it can be refined against additional real receipts.
- Open Food Facts is used as an automatic public lookup fallback.
- All UPC lookup checks Pantry Keeper first. Known products use the saved catalog without external traffic. Unknown UPCs can use a cached Open Food Facts suggestion or manual entry. The Safari extension automatically searches Meijer only for unique UPCs the database does not know yet. **Search Meijer** and **Copy Search URL** remain available for manual searches. Selected suggestions are saved together with the receipt when importing.
- The first time an unknown code is manually resolved, that code is permanently remembered in the local product catalog.

## Inventory adjustments

Change a product's quantity in Current Inventory and click Save to record consumption or correct stock. Quantities can be fractional and must be nonnegative. Resolving an already identified receipt item does not add its quantity again.

## Checks

```bash
.venv/bin/python -m unittest discover -s tests -v
python -m pip check
```

Tests use a temporary SQLite database.

## Database-first identification

See [lookup, migration, API, and restart details](docs/database-first-lookup.md).
Startup adds a unique text UPC, notes, and lookup cache without clearing existing
records. Find or Add Product accepts typed UPCs and hardware barcode-reader input.
Save Product identifies every pending receipt line for that UPC and remembers it
for later purchases. Copy Search URL supports pasting into Safari Private Browsing.

Extension v1.2.1 automatically searches Meijer only for unknown UPCs; known products are resolved locally.
No additional dependencies are needed.

## Recipe Assistant

Open `/recipes` or choose **Recipe Assistant** from the dashboard. Configure usable
inventory measurements, ask about recipes (or plan manually), choose a recipe,
reserve with **Start Cooking**, then review actual quantities and explicitly
confirm consumption after cooking. Cancelling releases holds without consuming
stock. AI tools only read inventory and validate proposals.

Set `OPENAI_API_KEY` and optionally `OPENAI_MODEL` in the server `.env` to enable
chat. No new dependencies are required. See [recipe setup, additive migration,
API, and testing instructions](docs/recipe-planning.md).

With the backend stopped, `.venv/bin/python scripts/migrate.py` backs up an existing
SQLite database and applies additive migrations. Startup applies the same schema
changes idempotently. Historical measurements are left unset until configured.

Run `.venv/bin/python -m unittest discover -s tests -v` and `npm test` to check both
receipt workflows and recipe reservation/consumption behavior.

### Publish the inventory to GitHub Pages

Run `./scripts/publish-inventory.sh` to download the full HTML inventory from
`http://192.168.1.7:8000/api/inventory?format=html` and push it as `index.html`
to the current repository's `gh-pages` branch. Git needs credentials with push
access. In GitHub **Settings → Pages**, select **Deploy from a branch**, then
**gh-pages** and **/ (root)**. The page includes all inventory and linked product
fields, including notes, and follows your GitHub Pages visibility settings.

Use `./scripts/publish-inventory.sh --dry-run` to validate the download without
publishing. Set `PANTRY_PAGES_REPO`, `PANTRY_PAGES_BRANCH`, or
`PANTRY_INVENTORY_URL` to override the destination or source. Re-run the script
whenever you want to update the published snapshot. It uses a temporary clone,
preserves other files on the publishing branch, and skips unchanged snapshots.

The same publish command also updates `inventory.json`, available at
`https://pengels22.github.io/Pantry_Keeper/inventory.json`. It is a JSON array
with every inventory row and all linked product fields. Keys use `inventory.`
and `product.` prefixes; numbers remain numbers and missing values are null.
Both published files are snapshots updated when the script runs. The local full
JSON export is `/api/inventory?format=export-json` (without pagination).

### Custom GPT Actions

In the GPT builder, add an Action with authentication set to **None** and import
`https://pengels22.github.io/Pantry_Keeper/openapi.json`. Test
`getPantryInventory` in the Actions editor. It reads the published JSON snapshot
directly, without relying on web search. Suggested GPT instruction: "Use
getPantryInventory to check available ingredients before proposing pantry recipes.
The returned inventory is a published snapshot, not a live database connection."

### Costco warehouse receipts

Receipt uploads, pasted text, and Safari captures automatically detect Meijer or
Costco from the receipt header. Text PDFs without a selectable Costco logo can
also be identified by the warehouse header and Costco item layout. Open an
individual in-warehouse receipt in Costco Orders & Purchases before scanning.
Install/rebuild Safari extension v1.3.0 and allow access to costco.com.

Costco item numbers use a `costco:` prefix in the catalog's legacy `upc` field
(e.g. `costco:5331`). They are store identifiers, not barcodes; GTIN is left
empty, and these codes are never sent to Meijer or Open Food Facts. Unknown
items have receipt names prefilled for manual identification and a Costco search
link. Once saved, later receipts reuse the local product automatically.

Matched coupon rows reduce the referenced item's line price and do not add
inventory. Unmatched coupons remain in the scan with a review warning. Weight
is not inferred from prices: a Costco package counts as one unless the receipt
explicitly supplies usable measurements, which can be configured in Inventory
measurements. Gas-station receipts and online order invoices are not warehouse
receipt formats. Review quantities and missing items before importing.
