#!/bin/bash
# Muse Telegram App v4 — start the backend (local dev).
# Usage:  ./start.sh [--port 8765] [--host 127.0.0.1]
# Env:    TELEGRAM_BOT_TOKEN — bot token, ONLY from the environment.
#         Never put the token in this file, in the repo, or in the frontend.
#         If unset, the server runs in UNVERIFIED local-dev mode (initData
#         is not validated; see server.py). Set it before any deployment.
set -euo pipefail
cd "$(dirname "$0")"
if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "WARNING: TELEGRAM_BOT_TOKEN not set — running UNVERIFIED (local dev only)." >&2
fi
exec python3 server.py "$@"
