#!/usr/bin/env bash
# Starts the FX conversion service.
#   PORT              which port to listen on (default 8080)
#   FX_UPSTREAM_BASE  upstream base URL (read inside the app; never hardcoded)
set -euo pipefail
exec uvicorn app.main:app --port "${PORT:-8080}"
