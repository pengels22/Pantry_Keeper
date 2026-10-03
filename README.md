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
- PostgreSQL via Docker Compose
- SQLite fallback for direct local runs

## Directory layout

```text
Pantry_Keeper/
├── app.py
├── db.py
├── models.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
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
│   └── product_lookup.py
└── browser_extension/
    ├── manifest.json
    ├── content.js
    ├── popup.html
    └── popup.js
```

## Optional Docker setup

1. Edit `docker-compose.yml` and change:
   - `POSTGRES_PASSWORD`
   - `RECEIPT_API_TOKEN`

2. Start the app:

```bash
docker compose up -d --build
```

3. Open:

```text
http://YOUR_SERVER_IP:8000
```

## Quick start without Docker

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
cp .env.example .env
./run.sh
```

The startup script binds to all network interfaces (`0.0.0.0`) on port 8000. Open `http://YOUR_SERVER_IP:8000` from another device. Override the address or port with `PANTRY_HOST` or `PANTRY_PORT` when needed.

This defaults to SQLite. Use Python 3.14.8, the latest stable release verified for this app. Docker is optional.

## Safari extension

The extension captures receipt text, images, or PDFs on demand, saves server settings, and opens a temporary receipt draft in Pantry Keeper for review. It supports Mac and iPhone/iPad packaging through Xcode.

On a Mac with Xcode, run `./scripts/package-safari.sh`, build/run the generated project, and enable the extension in Safari. Configure it with your server’s reachable IP/hostname and API token, then open an individual Meijer receipt and choose **Scan this receipt**.

See [extension setup and troubleshooting](browser_extension/README.md) for packaging, permissions, and device instructions.

## Notes

- The Meijer parser is intentionally isolated in `services/meijer_parser.py` so it can be refined against additional real receipts.
- Open Food Facts is used as an automatic public lookup fallback.
- The Safari extension automatically searches Meijer for unfamiliar receipt codes and brings product suggestions into review. Exact code matches are preselected; description matches require selection. Selected products are saved when importing. A **Search Meijer** link remains available for manual identification.
- The first time an unknown code is manually resolved, that code is permanently remembered in the local product catalog.

## Inventory adjustments

Change a product's quantity in Current Inventory and click Save to record consumption or correct stock. Quantities can be fractional and must be nonnegative. Resolving an already identified receipt item does not add its quantity again.

## Checks

```bash
.venv/bin/python -m unittest discover -s tests -v
python -m pip check
```

Tests use a temporary SQLite database.
