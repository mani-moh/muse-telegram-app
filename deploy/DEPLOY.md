# Deploy — Muse Telegram App v4 on the Soar box (app.mqj.cc.cd)

**Status: PREPARED ONLY — do NOT install yet.**
**Gate 1:** Cloudflare zone `mqj.cc.cd` must be **Active** first (it was Pending at 22:57 +0330, 2026-11-24; activation typically takes 1–2 hours, up to 24).
**Gate 2:** John's explicit approval to install a second service on the blackout lifeline box.
**Gate 3:** The live read (§1) must pass before any change.

Target: EC2 `i-00f5c72e349f7c862`, `16.58.75.182`, t3.micro, 1 GiB RAM, Ubuntu 24.04, us-east-2.
Domain: `app.mqj.cc.cd` → A → 16.58.75.182, TTL 600, **Proxied** in Cloudflare (zone `ba4dc4249ce6a30e8b70778cb34ab42c`, Free). Cloudflare SSL mode is **Full (strict)** — see §4, this decides the TLS route.
The Soar domain `lhd.dpdns.org` is **not** touched by anything in this package.

What runs where after install:
- Caddy on :80/:443 → reverse proxy → app backend on `127.0.0.1:8765` (Python stdlib only, no deps).
- Soar DNS stays on 53, Soar panel on 2053, arvan-relay timer every 15 min — all untouched.
- The app must never touch port 53, the panel, the relay, or `/home/ubuntu/.config/arvan-relay/env`.

## 1. Live read first (read-only, no changes) — do this before anything else

From the AWS console (GitHub/Builder ID sign-in, us-east-2) or EC2 Instance Connect, as `ubuntu`:

```bash
# Box health baseline — record the output before installing anything.
df -h / ; free -h ; uptime
ss -ltnp                       # expect: 53 (Soar DNS), 2053 (panel). 80/443 must be FREE.
sudo ufw status 2>/dev/null || true

# Soar + relay still healthy? (Exact unit names vary — list them, don't guess.)
systemctl list-units --all | grep -iE 'soar|arvan|white' 
systemctl list-timers --all | grep -i arvan
ss -ltnp | grep -E ':53\b|:2053\b'

# Security group (AWS console → EC2 → Instances → i-00f5c72e349f7c862 → Security → whitedns-sg):
# confirm inbound 443 (and 80) is open to 0.0.0.0/0. Earlier note said "443 looks open" —
# that is NOT verified. If 443 is closed, stop here and open it (or note it as a blocker).
```

**Stop conditions:** 80 or 443 already in use; disk < 500 MB free; RAM free < 200 MB; Soar panel or relay not healthy; SG 443 closed. Any of these = report back, do not install.

## 2. Cloudflare checks (before the install)

```bash
# From anywhere (not the box): the zone must be Active and the name must resolve.
dig +short app.mqj.cc.cd        # Proxied => Cloudflare edge IPs, NOT 16.58.75.182 directly. That is expected.
curl -sI https://app.mqj.cc.cd # expect 502/503 (nothing listening yet) — NOT a DNS error.
```

If `dig` returns nothing or the zone is still Pending, wait. Do not install against a Pending zone.

## 3. TLS route — Cloudflare Origin Certificate (primary)

Because the record is **Proxied** and the zone SSL mode is **Full (strict)**, Cloudflare validates the origin certificate. The cleanest route is a free Cloudflare Origin Certificate (15-year, trusted only by Cloudflare — fine, all traffic comes through Cloudflare):

1. Cloudflare dashboard (account `manimohspam4@proton.me`) → `mqj.cc.cd` → SSL/TLS → Origin Server → Create Certificate.
2. Hostnames: `app.mqj.cc.cd`. Key type RSA (2048). Validity 15 years. Create.
3. Save the certificate and the private key to the box as root-only files (paste via the console; **never** into chat, never into the repo):
   - `/etc/caddy/certs/app.mqj.cc.cd.pem` (certificate), mode 600
   - `/etc/caddy/certs/app.mqj.cc.cd.key` (private key), mode 600
4. Caddy serves it with `tls <cert> <key>` (see `Caddyfile` in this folder).

**Fallback route (only if the Origin Certificate cannot be made):** switch the `app` record in Cloudflare from **Proxied to DNS-only**, then let Caddy obtain a Let's Encrypt certificate automatically (ACME HTTP-01 on :80, no cert files needed). This loses Cloudflare's proxying/DDoS shield and exposes the origin IP — prefer the Origin Certificate route. Do not run production with the record Proxied and no valid origin cert: Full (strict) will return 526.

## 4. Install Caddy

```bash
# Caddy is not in Ubuntu 24.04 default repos — use the official repo.
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
caddy version
```

## 5. Copy the app

From this VM (the workspace), copy the v4 project — frontend + server + deploy only:

```bash
# On the Soar box:
sudo mkdir -p /opt/muse-telegram-app
sudo chown ubuntu:ubuntu /opt/muse-telegram-app
# From the local VM (adjust the transfer route — scp with whitedns-key.pem, or git):
#   scp -i ~/.ssh/whitedns-key.pem -r frontend server deploy ubuntu@16.58.75.182:/opt/muse-telegram-app/
# Verify on the box:
ls -R /opt/muse-telegram-app
python3 -m py_compile /opt/muse-telegram-app/server/server.py
node --check /opt/muse-telegram-app/frontend/app.js   # if node is installed; otherwise skip
```

Do **not** copy `muse-telegram-app.html` (v3, live on GitHub Pages) or anything from the blackout feed project. The app serves only its own frontend + API.

## 6. Bot token — root-only env file, never in the repo

```bash
sudo install -m 600 /dev/null /etc/muse-telegram-app.env
sudo nano /etc/muse-telegram-app.env   # ONE line:
#   TELEGRAM_BOT_TOKEN=<token>
# The token comes from the existing Telegram connector credential, written
# directly into this file on the box at deploy time. Never paste it into
# chat, never commit it, never put it in the relay env file.
sudo chmod 600 /etc/muse-telegram-app.env
sudo chown root:root /etc/muse-telegram-app.env
```

**Do not deploy to production with the token missing.** Without it the server starts in UNVERIFIED mode (initData not validated) and logs a warning — acceptable for a local smoke test only, never for `app.mqj.cc.cd`.

## 7. Install the service + Caddy site

```bash
sudo cp /opt/muse-telegram-app/deploy/muse-telegram-app.service /etc/systemd/system/muse-telegram-app.service
sudo cp /opt/muse-telegram-app/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl daemon-reload
sudo systemctl enable --now muse-telegram-app
sudo systemctl restart caddy
sudo systemctl status muse-telegram-app caddy --no-pager
```

The unit already limits the app so it cannot starve Soar: `MemoryMax=256M`, `CPUQuota=50%`, `Restart=always`, `RestartSec=10`, `NoNewPrivileges=true`, `PrivateTmp=true`. It listens only on `127.0.0.1:8765`; only Caddy faces the internet.

## 8. Verify — app AND Soar

```bash
# On the box:
curl -s http://127.0.0.1:8765/api/health
# From anywhere:
curl -s https://app.mqj.cc.cd/api/health
curl -s https://app.mqj.cc.cd/api/commands | head -c 300   # expect 48 commands
curl -s -X POST https://app.mqj.cc.cd/api/message -H 'Content-Type: application/json' \
  -d '{"text":"/spaces"}'                                    # expect Tier 1 cached answer
# Then run the bundled checker (also re-verifies Soar + relay):
sudo bash /opt/muse-telegram-app/deploy/verify.sh
```

Expected `/api/health`: `{"status":"ok","auth":"verified","fast_commands":[...6 commands...],"cache_updated_utc":"..."}`. If `auth` is `unverified-no-token`, stop: the token env file is missing or unreadable.

## 9. Point the bot at the app + live-test

```bash
# Set the bot Menu button to the new URL (run from this VM, where the connector lives):
python3 ~/workspace/custom-connectors/custom.telegram/bin/telegram.py setChatMenuButton \
  --type web_app --text "Muse App" --url "https://app.mqj.cc.cd/"
python3 ~/workspace/custom-connectors/custom.telegram/bin/telegram.py getChatMenuButton
```

Then live-test in Telegram (John's account, Telegram Web): open the Mini App from the Menu button, tap a cached command (expect ~1 s, "from cache" label), tap a live question (expect Tier 2 queued + reply in bot chat in ~10–70 s — see `APP_V4_RESEARCH.md` §10; do not claim it is instant), check thread persistence (close + reopen the app, thread survives), check the drawer, search, Notes, Spaces.

## 10. Rollback — if anything degrades Soar

```bash
sudo bash /opt/muse-telegram-app/deploy/rollback.sh
# That stops + disables the app service, removes the unit and the Caddy site,
# restarts Caddy, and re-runs the Soar/relay checks. The app is fully removable
# without touching Soar, the panel, or the relay.
```

Manual equivalent: `sudo systemctl stop muse-telegram-app; sudo systemctl disable muse-telegram-app; sudo rm /etc/systemd/system/muse-telegram-app.service; sudo systemctl daemon-reload; sudo rm /etc/caddy/Caddyfile; sudo systemctl restart caddy` — then re-run `verify.sh` and confirm Soar/relay are healthy.

## 11. Known gaps at deploy time (do not silently omit)

- **Cache-sync bridge (biggest gap):** the backend's `server/cache.json` is a seed from 2026-11-24 17:05 UTC. On the remote box it will go stale and Tier 1 answers will be labelled stale (correct behaviour, but useless after a day). The fix is the bridge in `CACHE_SYNC_BRIDGE.md` (Muse-side 5-min refresher + GitHub/S3 sync) — design only, not built, needs John's approval. Until it exists, Tier 1 on the hosted backend is a snapshot, not live data.
- **Delay (§10):** the hosted version does **not** remove the 10–70 s wake for live questions. Tier 1 is ~1 s; Tier 2 replies land in bot chat (or via the §10.6 bridge, not built). Do not tell John the delay is gone.
- **Thread persistence** is implemented in the frontend (Telegram CloudStorage + localStorage fallback) but the CloudStorage path has only been tested via a Node stub — it needs a live Telegram tap test.
- **No monitoring/alerting** in this package. If the service dies, `Restart=always` brings it back; nobody is paged. A simple uptime check (e.g. a cron here curling `/api/health` every 15 min) is a follow-up, not included.
