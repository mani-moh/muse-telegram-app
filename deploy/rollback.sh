#!/bin/bash
# rollback.sh — remove the Muse Telegram App v4 from the Soar box completely.
# Leaves Soar (53/2053), the arvan-relay timer, and /home/ubuntu/.config/arvan-relay/env untouched.
# Usage: sudo bash /opt/muse-telegram-app/deploy/rollback.sh
set -u
echo "Stopping muse-telegram-app..."
systemctl stop muse-telegram-app 2>/dev/null || true
systemctl disable muse-telegram-app 2>/dev/null || true
rm -f /etc/systemd/system/muse-telegram-app.service
systemctl daemon-reload
echo "Removing Caddy site (Caddy itself stays installed)..."
rm -f /etc/caddy/Caddyfile
systemctl restart caddy 2>/dev/null || true
echo "Removing app files (env file with the token is kept — delete it by hand if wanted: /etc/muse-telegram-app.env)..."
rm -rf /opt/muse-telegram-app
echo "Re-verifying Soar + relay..."
ss -ltnp | grep -E ':53 |:2053 ' || echo "WARNING: Soar DNS/panel NOT listening — investigate before anything else"
systemctl list-timers --all --no-legend | grep -i arvan || echo "WARNING: arvan-relay timer not found"
echo "Rollback done. The box is back to Soar-only."
