# Configuration audit — October 4, 2026

Read-only audit of the running service, local configuration, source, dependencies,
and database. Live service and database were not changed. Migration/startup checks
used a temporary SQLite backup copy.

## Working now

- `pantry-keeper.service` is active and enabled at boot; listens on all interfaces,
  port 8000. The local dashboard returns HTTP 200.
- Python dependencies pass `pip check`; Tesseract 5.5.0 is installed and OCR enabled.
- SQLite integrity check passes. Current database contains 82 products, 82 inventory
  rows, 6 receipts, and 35 receipt lines.
- 44 Python tests and 29 JavaScript tests pass. External API and browser integrations
  in these tests are mocked; these results do not establish live provider/device access.

## Remaining configuration and deployment

1. **Deploy the current code and migrate the database.** The running service began
   October 3, before the current recipe changes. `/recipes` and `/api/inventory`
   return 404 on the running service. The live inventory table lacks measurement
   columns; recipe tables are absent. The current source works on a migrated copy:
   dashboard, recipes, inventory, and recipe-session endpoints return 200, existing
   record counts remain unchanged, and database integrity passes.

   Recommended deployment from the repository directory:

   ```bash
   sudo systemctl stop pantry-keeper.service
   .venv/bin/python scripts/migrate.py
   sudo systemctl start pantry-keeper.service
   ```

   The migration script creates a timestamped SQLite backup before additive changes.
   Refresh browsers afterward. There are existing uncommitted implementation changes;
   record the intended version in Git for deployment traceability.

2. **Configure AI chat if wanted.** `OPENAI_API_KEY` is empty/missing in `.env`.
   Add a server-side key with API access and optionally `OPENAI_MODEL`, then restart
   the service. The source supplies a default model when no override is set. Live
   key validity, account billing, and model access were not tested. Manual recipe
   planning does not need a key.

3. **Configure inventory measurements.** All 82 existing inventory rows require
   explicit usable quantities and units after migration. Historical counts are
   deliberately not converted automatically. Configure the items used in recipes;
   optionally add package count/size/unit to scale future purchases correctly.

4. **Complete and verify Safari installation on each device.** Source bundles exist,
   but this Linux audit cannot establish whether the signed extension is installed
   or enabled. Package/build through Xcode, enable Safari permissions, configure a
   reachable server address (for example `http://192.168.1.6:8000` if reachable from
   that device), and scan an actual Meijer receipt. Check device reachability and
   real Meijer product search behavior. No Meijer credential setting is required
   on the server; the extension uses the browser session.

5. **Choose access controls appropriate to deployment.** `RECEIPT_API_TOKEN` is
   empty, so extension uploads require no token. If a token is desired, set it on
   the server and identically in the extension. That token protects selected
   extension endpoints only: it is not application-wide authentication. Dashboard,
   inventory changes, and recipe chat have no login/access control in the source.
   Keep access restricted to trusted devices or add an authenticated gateway before
   wider exposure. Pantry-specific HTTPS routing was not found in the checked
   Caddy/nginx configuration paths; other host services already listen on 80/443.

6. **Establish recurring backups.** No Pantry-specific scheduled backup was found
   in the checked systemd timers or repository. The migration backup protects that
   deployment only. Use consistent SQLite backups with retention and a restore check;
   CSV exports omit recipe sessions, measurements, and transaction history.

## Defaults that need no additional configuration

- SQLite is already selected; PostgreSQL is optional and was not tested.
- `TESSERACT_CMD` is unnecessary here because Tesseract is on PATH.
- Missing public lookup settings use the enabled default and a two-second interval.
  Live Open Food Facts connectivity and lookup results were not tested.
- Node dependencies are used for browser tests, not to run the FastAPI server.

## Verification limits

No live paid OpenAI call, real Safari scan, remote-device connection, PostgreSQL
check, firewall/exposure audit, or backup restoration was performed. One non-failing
FastAPI/Starlette test-client deprecation warning appeared; dependencies otherwise
passed the consistency check.
