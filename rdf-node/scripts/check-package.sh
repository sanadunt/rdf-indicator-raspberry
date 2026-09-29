#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"
if [ -f SHA256SUMS ]; then sha256sum --quiet -c SHA256SUMS; fi
python3 -m compileall -q src tests
for f in install.sh scripts/*.sh deploy/kiosk/rdf-kiosk deploy/kiosk/rdf-kiosk-session; do bash -n "$f"; done
if command -v node >/dev/null; then node --check web/app.js; node --check web/ground.js; fi
python3 run.py selftest
