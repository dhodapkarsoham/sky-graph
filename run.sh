#!/usr/bin/env bash
# Rebuild sky.rl from scratch: fetch -> prepare -> build -> verify -> export for the web.
# Pass --refresh to re-download the source datasets.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }
PY=.venv/bin/python
if [ "${1:-}" = "--refresh" ]; then $PY pipeline/fetch.py --force; else $PY pipeline/fetch.py; fi
$PY pipeline/prepare.py
$PY pipeline/build.py
$PY pipeline/verify.py
$PY pipeline/export_web.py
