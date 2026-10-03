# Pantry Keeper Safari extension

Captures visible text, the original receipt image, or a PDF from an open Meijer receipt, sends it to your Pantry Keeper server, and opens the app for review. Importing requires your confirmation in the app. Settings stay in local extension storage.

## Source bundle

`dist/Pantry-Keeper-Safari-source.zip` contains the extension and Mac packaging script. Extract it on a Mac, open Terminal in the extracted `Pantry-Keeper-Safari` directory, then follow the steps below. Rebuild the bundle with `.venv/bin/python scripts/build-extension-bundle.py` from the server repository.

## Package on a Mac

Install Xcode, copy the Pantry_Keeper folder to the Mac, then run:

```bash
./scripts/package-safari.sh
```

The script uses Apple's Safari extension packager (or its older converter name). It generates a Swift Xcode project for macOS and iOS in `safari-build/`. To select one platform, pass `--macos-only` or `--ios-only`. Resources are copied; rerun packaging after editing them. Use a fresh output directory when rebuilding:

```bash
PANTRY_SAFARI_PROJECT_DIR="$PWD/safari-build-v2" ./scripts/package-safari.sh
```

In Xcode, select your signing team for the app and extension targets, then build/run the desired Mac or connected iPhone/iPad target. Enable Pantry Keeper in Safari’s extension settings. For unsigned Mac development builds, enable Allow Unsigned Extensions in Safari’s developer settings if required. This Linux workspace cannot compile or sign Apple apps.

Apple documentation: [Packaging](https://developer.apple.com/documentation/safariservices/packaging-a-web-extension-for-safari), [Running](https://developer.apple.com/documentation/safariservices/running-your-safari-web-extension).

## Scan a receipt

1. Open an individual receipt on Meijer in Safari.
2. Open Pantry Keeper from Safari’s extension menu.
3. Set the server address, such as `192.168.1.20:8000`. Bare addresses automatically use `http://`; explicit `https://` is preserved. Use the server’s reachable IP/hostname; `0.0.0.0` is a bind address.
4. If the server has `RECEIPT_API_TOKEN` configured, enter the same token here.
5. Save settings and allow access to that server. Allow access to Meijer when prompted by Safari.
6. Choose **Scan this receipt**. Check the captured item count, then click **Review scanned receipt** to switch to the prepared Pantry Keeper tab. Your Meijer receipt remains open.

The server must be running and reachable from the device. On an iPhone, `localhost` refers to the phone. Website access is requested for your configured server; page text is captured only when you click Scan. The API token is not included in URLs.

Receipt drafts are persisted in SQLite/PostgreSQL for 30 minutes and survive app restarts. The handoff URL contains a random draft ID that grants access to that draft; the app clears it after reading. Expired drafts are cleaned up during later scans. At most 1,000 unexpired drafts are accepted. No Meijer credentials are collected.

## Troubleshooting

- **No receipt items recognized:** open one complete receipt and wait for it to load. Image-only pages automatically use Tesseract OCR on the server. The parser expects an 8–14 digit code, description, and price on each item line. If Meijer renders a different layout, upload a screenshot in the app or provide sample receipt text to refine the parser.
- **Could not reach Pantry Keeper:** verify the address from Safari and allow the extension access to the server website.
- **API token mismatch:** update the extension token to match the server environment, and restart the server after changing its token.
- **Draft expired:** scan the receipt again.

## Verify from the repository root

```bash
npm ci
npm test
.venv/bin/python -m unittest discover -s tests -v
```

The automated extension tests mock browser APIs. Actual Safari, signing, and real Meijer page behavior still require testing on an Apple device.

## Update to version 1.2.1

Download `dist/Pantry-Keeper-Safari-v1.2.1-source.zip` and rebuild/reload the
extension on your Mac. For packaged Xcode installations, copy the new resources
by rerunning the packaging script into a fresh directory and build/run again.

Version 1.2.1 automatically searches Meijer for unique UPCs missing from Pantry
Keeper's database. The server checks all codes locally first and sends only
unknown codes to the scanner. Known products trigger no Meijer or public-service
lookup. An empty unknown list opens no product search tabs.

The scanner searches sequentially in a temporary tab, preferring receipt-code
matches and trying descriptions if needed. Selected suggestions are saved with
the receipt; future purchases reuse the catalog. Allow Meijer website access and
keep the popup open during lookup. Store/sign-in trouble is reported and stops
further searches.

Unknown items have manual **Search Meijer**, **Copy Search URL**, and
**Enter Product Manually** controls. To search privately, copy the URL and paste
it into a Safari Private Browsing window. The web app cannot force Safari to
create a private window. Save Product remembers a UPC for all future receipts.

The server's unknown-only list also works with v1.1.x scanners. If you installed
v1.2.0 (which removed lookup), rebuild/reload v1.2.1 to restore automatic search.
Restart the server and refresh the web app after updating. See
[database-first workflow](../docs/database-first-lookup.md) for schema changes,
API details, caching, rate limits, and commands.
