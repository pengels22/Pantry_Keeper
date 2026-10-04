#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$(uname -s)" != Darwin ]]; then
  echo 'Safari packaging needs a Mac with Xcode. Copy this project to your Mac and run this script there.' >&2
  exit 1
fi
if ! command -v xcrun >/dev/null; then
  echo 'Install Xcode and select it in Xcode Settings > Locations > Command Line Tools.' >&2
  exit 1
fi
packager='safari-web-extension-packager'
if ! xcrun --find "$packager" >/dev/null 2>&1; then
  packager='safari-web-extension-converter'
fi
if ! xcrun --find "$packager" >/dev/null 2>&1; then
  echo 'Safari extension packaging is unavailable. Install full Xcode and select its developer directory.' >&2
  exit 1
fi
project_dir="${PANTRY_SAFARI_PROJECT_DIR:-$root_dir/safari-build}"
bundle_id="${PANTRY_SAFARI_BUNDLE_ID:-com.pantrykeeper.receipts}"
exec xcrun "$packager" "$root_dir/browser_extension" \
  --project-location "$project_dir" \
  --app-name 'Pantry Keeper' \
  --bundle-identifier "$bundle_id" \
  --swift --copy-resources "$@"
