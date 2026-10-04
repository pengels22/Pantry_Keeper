#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec .venv/bin/uvicorn app:app --host "${PANTRY_HOST:-0.0.0.0}" --port "${PANTRY_PORT:-8000}"
