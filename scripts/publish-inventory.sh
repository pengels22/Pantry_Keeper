#!/usr/bin/env bash
# Publish inventory HTML and JSON to a GitHub Pages branch.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source_url=${PANTRY_INVENTORY_URL:-http://192.168.1.7:8000/api/inventory?format=html}
repo_url=${PANTRY_PAGES_REPO:-$(git -C "$script_dir/.." remote get-url origin)}
branch=${PANTRY_PAGES_BRANCH:-gh-pages}
json_url=${PANTRY_INVENTORY_JSON_URL:-$(python3 - "$source_url" <<'PYURL'
import sys
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
u = urlsplit(sys.argv[1])
q = [(k, v) for k, v in parse_qsl(u.query) if k != "format"]
q.append(("format", "export-json"))
print(urlunsplit(u._replace(query=urlencode(q))))
PYURL
)}

if [[ ${1:-} == --help ]]; then
  cat <<'HELP'
Usage: scripts/publish-inventory.sh [--dry-run]

Downloads the complete inventory HTML and publishes index.html and inventory.json to gh-pages.
Configure GitHub Pages to deploy from that branch's root directory.
Requires python3, curl, git, and Git credentials with push access.
The published page includes all inventory and product fields, including notes.
Its visibility depends on the destination's GitHub Pages configuration.

Environment overrides:
  PANTRY_INVENTORY_URL  Inventory endpoint (default: LAN endpoint with format=html)
  PANTRY_INVENTORY_JSON_URL  Full JSON export endpoint (derived from HTML URL)
  PANTRY_PAGES_REPO     Git clone URL (default: this project's origin)
  PANTRY_PAGES_BRANCH   Publishing branch (default: gh-pages)

--dry-run downloads and validates the HTML without connecting to GitHub.
HELP
  exit 0
fi
if [[ $# -gt 1 || ( $# -eq 1 && $1 != --dry-run ) ]]; then
  echo 'Use --help for usage.' >&2
  exit 2
fi

git check-ref-format --branch "$branch" >/dev/null
publish_tmp=$(mktemp -d)
trap 'rm -rf -- "$publish_tmp"' EXIT

curl --fail --silent --show-error --location --connect-timeout 10 --max-time 120 \
  "$source_url" -o "$publish_tmp/inventory.html"
if ! head -c 512 "$publish_tmp/inventory.html" | grep -qi '<!doctype html>'; then
  echo 'Endpoint did not return an HTML document. Use ?format=html and restart the server if needed.' >&2
  exit 1
fi
curl --fail --silent --show-error --location --connect-timeout 10 --max-time 120 \
  "$json_url" -o "$publish_tmp/inventory.json"
python3 - "$publish_tmp/inventory.json" <<'PYJSON'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
if not isinstance(data, list) or any(not isinstance(row, dict) or
        'inventory.id' not in row or 'product.id' not in row for row in data):
    raise SystemExit('Expected a complete inventory JSON export; nothing published.')
PYJSON
if [[ ${1:-} == --dry-run ]]; then
  echo 'Inventory HTML and JSON downloaded and validated. Nothing pushed.'
  exit 0
fi

# Work in a temporary clone so the application's working tree stays untouched.
git clone --quiet --no-checkout --single-branch "$repo_url" "$publish_tmp/repo"
cd -- "$publish_tmp/repo"
refs=$(git ls-remote --heads origin "refs/heads/$branch")
if [[ -n $refs ]]; then
  git fetch --quiet origin "refs/heads/$branch:refs/remotes/origin/$branch"
fi
if git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
  git checkout --quiet -b "$branch" "origin/$branch"
else
  git checkout --quiet --orphan "$branch"
fi
cp -- "$publish_tmp/inventory.html" index.html
cp -- "$publish_tmp/inventory.json" inventory.json
cp -- "$script_dir/../integrations/pantry-actions.openapi.json" openapi.json
touch .nojekyll
git add -- index.html inventory.json openapi.json .nojekyll
if git diff --cached --quiet; then
  echo 'Inventory page is unchanged. Nothing pushed.'
  exit 0
fi
git -c user.name="Pantry Keeper" -c user.email="pantry-keeper@users.noreply.github.com" \
  commit --quiet -m 'Update inventory page'
git push origin "HEAD:refs/heads/$branch"
echo "Inventory page pushed to $branch. Enable GitHub Pages from this branch's root directory."
