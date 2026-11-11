#!/usr/bin/env bash
# One command that answers "is this checkout still healthy?".
#
#   ./scripts/verify.sh            static checks only
#   ./scripts/verify.sh --browser  also run the Playwright checks against :3000
#
# The browser checks drive a real page, so they need `npm run dev` (and the API)
# already running. Static checks need nothing but node_modules.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

WITH_BROWSER=0
if [ "${1:-}" = "--browser" ]; then
  WITH_BROWSER=1
fi

echo "== typecheck (full, incremental cache disabled) =="
npm run --silent typecheck

echo "== eslint =="
npm run --silent lint

echo "== css module guard =="
npm run --silent check:css

if [ "$WITH_BROWSER" = "1" ]; then
  if ! curl -sf -o /dev/null http://127.0.0.1:3000/api/bot/status; then
    echo "!! web server not reachable on :3000 — start 'npm run dev' first" >&2
    exit 1
  fi
  echo "== browser regression checks =="
  node scripts/fb-bot-scroll-check.mjs
else
  echo "== browser checks skipped (pass --browser to run them) =="
fi

echo "verify OK"
