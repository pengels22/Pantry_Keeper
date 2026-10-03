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

## Update to version 1.1.1

Download and extract the new source bundle. For a temporary Safari installation, remove the previous extension and add the new `browser_extension` folder, or replace the installed folder’s files and choose Reload in Safari’s extension settings. Packaged Xcode installations need new copied resources and a new build/run. Restart the Pantry Keeper server to enable the new PDF and image receipt endpoints.

This version fixes the narrow popup, accepts bare IP addresses, and scans image-only and PDF receipt pages. PDF files are downloaded directly rather than reading Safari’s PDF viewer. The server extracts embedded text first and uses OCR for scanned pages; uploads are limited to 20 MB and 10 pages. Allow Meijer website access when the scanner requests it.

Version 1.0.3 opens review in the background, reports scanned and expected item counts, and preserves your receipt tab. The server parser handles joined and wrapped item lines and tries additional PDF layouts or OCR when extraction appears incomplete. Pantry Keeper’s Scan From Browser link opens https://www.meijer.com/shopping/orders. Import confirmation reports every saved receipt line, including unidentified products.

## Automatic Meijer product lookup

Version 1.1.0 searches each unfamiliar receipt code on Meijer in a temporary background Safari tab. It reads product links and structured product details, then tries the receipt description if there is no exact code match. It does not send your Meijer credentials or cookies to Pantry Keeper. Keep the extension popup open until it finishes; closing it interrupts lookup. Progress is shown for each code.

Select a Meijer store and sign in if needed before scanning. If Meijer asks for attention, the search tab stays open so you can address it. Otherwise only that temporary search tab is closed; your receipt remains open.

Exact receipt-code matches are preselected in Review Receipt. Other search results require choosing a product. You can edit the name, brand, and size and open the Meijer product page before importing. Import saves selected matches in your catalog, then adds identified items to inventory. Future scans reuse saved matches without searching again. Unmatched items remain in Unknown Products; Open Food Facts remains the fallback when no Meijer candidate was collected.

Safari product-page scraping is tested with representative HTML and mocked browser APIs. This Linux workspace cannot verify your current signed-in Safari session; Meijer page layout and store permissions may require refinement.

Version 1.1.1 accepts Meijer search query redirects and checks the page document rather than waiting for Safari’s tab status to become complete. Store/sign-in and website-permission failures are reported separately.
