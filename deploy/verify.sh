#!/bin/bash
# verify.sh — post-install health check for the Muse Telegram App v4 on the Soar box.
# Run on the box after install, and any time Soar/relay health is in doubt.
# Exits 0 only if the app AND Soar AND the relay all look healthy.
# Usage: sudo bash /opt/muse-telegram-app/deploy/verify.sh
set -u
FAIL=0
say()  { printf '%-52s %s\n' "$1" "$2"; }
ok()   { say "$1" "OK"; }
bad()  { say "$1" "FAIL — $2"; FAIL=1; }

# --- App ---
if systemctl is-active --quiet muse-telegram-app; then ok "app service active"; else bad "app service active" "$(systemctl is-active muse-telegram-app 2>&1)"; fi
if systemctl is-active --quiet caddy; then ok "caddy active"; else bad "caddy active" "$(systemctl is-active caddy 2>&1)"; fi
if curl -fsS -m 5 http://127.0.0.1:8765/api/health >/tmp/muse-health.json 2>/dev/null; then
  ok "app /api/health (localhost)"
  grep -q '"status": "ok"' /tmp/muse-health.json && ok "health status ok" || bad "health status ok" "$(cat /tmp/muse-health.json)"
  if grep -q '"auth": "verified"' /tmp/muse-health.json; then ok "initData auth verified (token present)"; else bad "initData auth verified" "server running UNVERIFIED — token env file missing?"; fi
else bad "app /api/health (localhost)" "no response on 127.0.0.1:8765"; fi
if curl -fsS -m 10 https://app.mqj.cc.cd/api/health >/dev/null 2>&1; then ok "app /api/health (public HTTPS)"; else bad "app /api/health (public HTTPS)" "https://app.mqj.cc.cd/api/health unreachable"; fi
APP_MEM="$(systemctl show muse-telegram-app -p MemoryCurrent --value 2>/dev/null)"
say "app memory use" "${APP_MEM:-unknown} bytes (limit 256M)"

# --- Soar (must NOT be broken by the app) ---
if ss -ltnp 2>/dev/null | grep -q ':53 '; then ok "Soar DNS listening on 53"; else bad "Soar DNS listening on 53" "nothing on port 53"; fi
if ss -ltnp 2>/dev/null | grep -q ':2053 '; then ok "Soar panel listening on 2053"; else bad "Soar panel listening on 2053" "nothing on port 2053"; fi
SOAR_UNITS="$(systemctl list-units --all --no-legend 2>/dev/null | grep -iE 'soar|white' | grep -c active || true)"
say "soar/white units active" "$SOAR_UNITS (check by eye if 0 — unit names vary)"

# --- Relay (every 15 min) ---
if systemctl list-timers --all --no-legend 2>/dev/null | grep -qi arvan; then ok "arvan-relay timer present"; else bad "arvan-relay timer present" "no arvan timer found"; fi
LAST_RELAY="$(systemctl list-timers --all --no-legend 2>/dev/null | grep -i arvan | awk '{print $NF}')"
say "relay timer last trigger" "${LAST_RELAY:-unknown}"

# --- No port clashes ---
if ss -ltnp 2>/dev/null | grep -qE ':80 |:443 '; then ok "80/443 served (Caddy)"; else bad "80/443 served (Caddy)" "nothing on 80/443"; fi

echo
if [ "$FAIL" -eq 0 ]; then echo "VERIFY: ALL OK"; else echo "VERIFY: FAILURES ABOVE — see DEPLOY.md §10 / rollback.sh"; fi
exit "$FAIL"
