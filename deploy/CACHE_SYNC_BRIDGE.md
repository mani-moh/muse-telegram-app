# Cache-sync bridge — BUILT locally (v4.1), not yet live end to end

## The problem

The v4 backend answers Tier 1 commands (`/help /spaces /review /feed /goals /defaults`, and the proposed broadened set `/tasks /email /upcoming /activity /calendar /wallet`) from `server/cache.json`. That file is **VM-local on this Muse machine** and is refreshed only when a Muse worker runs. The hosted backend on the Soar box (`app.mqj.cc.cd`) has no live source of it: the seed in the repo is from **2026-11-24 17:05 UTC** and goes stale within hours. Without a bridge, Tier 1 on the hosted backend degrades to "stale snapshot, labelled as such" — honest, but not useful after a day.

The backend cannot fetch the data itself: Gmail, Google Tasks, Calendar, Drive, goals, feed, and review state all live in Muse's connectors, which a server process cannot call. The bridge must therefore be **Muse-side refresh + sync**, not backend-side fetch.

## What is built (v4.1, 2026-11-24 ~20:30 UTC, tested locally)

- **Backend poller** (`server/server.py`): `CACHE_SYNC_URL` env, background
  thread every `CACHE_SYNC_INTERVAL_S` (default 60), optional Bearer
  token (`CACHE_SYNC_TOKEN`) and HMAC verification (`CACHE_SYNC_HMAC_SECRET`).
  Adopts only a newer, parseable, signature-valid snapshot (atomic
  replace). `/api/health` + `/api/cache` report `cache_sync` status;
  silent >60 min ⇒ `stale`. Tested: a signed newer snapshot was adopted
  (Test Space appeared); a bad-signature snapshot was rejected
  ("signature rejected: signature mismatch"); an older snapshot was ignored.
- **Muse-side refresher** (`server/refresh_cache.py`): `--from-telegram-state`
  (default — copies the live `custom.telegram/state/cache.json`) or
  `--from-stdin` (a fresh snapshot from a live Muse run, full fidelity).
  Validates required keys, stamps `cache_updated_utc`, writes locally
  on every run (check time refreshed) but pushes to GitHub ONLY on a
  real content change. `--push-github --repo mani-moh/muse-telegram-cache`
  uses `GITHUB_TOKEN` from env; the repo must already exist — the script
  will not create it. Tested: dry-run ok, live local refresh ok
  (content unchanged ⇒ push skipped, as designed).
- **Deploy wiring:** `deploy/muse-telegram-app.service` documents the
  `CACHE_SYNC_*` env vars for `/etc/muse-telegram-app.env`.

## What is still needed for the live bridge

- ~~The private repo `mani-moh/muse-telegram-cache` (John creates it).~~
  **DONE — the repo exists** (private, `main`, verified live
  2026-11-25; holds `cache.json`), **and the refresher cron is
  running** (15 min). These two items are closed; do not list them
  as still needed.
- The `CACHE_SYNC_*` env vars on the hosted backend (see the service file).
- HMAC secret: shared between refresher env and backend env (recommended).

## Design (unchanged)

```
This VM (Muse)                              GitHub or S3 (public or private)        Soar box (app.mqj.cc.cd)
┌─────────────────────────┐                ┌──────────────────────────┐              ┌─────────────────────────┐
│ refresher cron (5 min)  │ ──push──▶      │ cache.json (1 file,    │ ──poll──▶    │ backend reloads       │
│  - reads live Gmail/    │  (git push or  │  ~10-50 KB, signed?)   │  (60 s)      │ cache.json into       │
│    Tasks/Calendar/goals │   S3 put)      │  versioned by           │              │ memory; /api/cache    │
│    /feed/review via      │                │  cache_updated_utc      │              │ shows the new time    │
│    existing connectors  │                └──────────────────────────┘              └─────────────────────────┘
│  - writes cache.json    │
│  - only pushes on change│
└─────────────────────────┘
```

### Components

1. **Refresher cron (this VM, Muse side).** Every 15 min (recommended)
   run `server/refresh_cache.py --from-telegram-state --push-github`
   (or `--from-stdin` when a live Muse run supplies a fresh snapshot).
   It rebuilds `cache.json` from the live sources and pushes only on
   a real content change (see `refresh_cache.py` — built and tested).
   Cost, stated plainly: a refresher run every 15 min, 24/7 (96/day);
   5 min would be ~288/day. The refresher applies the same staleness
   rules as `telegram_fast.py`: `/review` trusted only ≤15 min old;
   `/spaces /feed /goals /defaults` flagged >60 min.
2. **Sync target — GitHub file (default, built).** Private repo
   `mani-moh/muse-telegram-cache` with a single `cache.json`, updated
   by the refresher via a PAT with `contents: write` on that repo only.
   The backend polls the raw file over HTTPS every 60 s (with a
   fine-grained read-only token if private — stored as an env file like
   the bot token). Versioned for free (git history), auditable, and the
   same pattern the blackout relay already uses (feed-data).
   **Do not reuse the feed-data repo** — keep the projects separate.
   S3 alternative (documented, not built): the existing
   `muse-telegram-app` bucket with `cache.json`, public-read — simpler
   reads, but the upload path needs AWS credentials on this VM, which
   the Telegram project does not currently have.
3. **Backend reload — BUILT.** `server.py` polls `CACHE_SYNC_URL`,
   verifies parse + newer timestamp + optional HMAC signature, and
   atomically replaces the local file. Never serves an unparseable or
   older file. Sync silent >60 min ⇒ `/api/health` reports
   `cache_sync: stale` and Tier 1 answers carry the stale label.
4. **Integrity — BUILT (optional, recommended).** The refresher signs
   `cache.json` (HMAC-SHA-256 over canonical JSON without the signature
   field) when `CACHE_SYNC_HMAC_SECRET` is set; the backend verifies
   before adopting. The secret lives in env files on both sides, never
   in the repo.

## What is still NOT decided (needs John)

- Refresher interval: 15 min (recommended, 96 runs/day) vs 5 min (fresh, ~288 runs/day) vs no bridge (Tier 1 stays a stale snapshot).
- Whether the refresher cron's 24/7 wake cost is acceptable at all.
- HMAC signing: yes (recommended, built) or skip for v1.

## Until the bridge is live

The deployed backend serves the seed `cache.json` and labels every Tier 1 answer with its check time; once the seed is >60 min old the answers are flagged stale. Copy a fresh `cache.json` into `/opt/muse-telegram-app/server/` at deploy time as a stopgap — it buys a few hours, not a solution.
