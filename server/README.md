# Muse Telegram App v4 — server

Python stdlib only — no dependencies to install. Serves the frontend
(`../frontend/`) and the JSON + SSE API. See `server.py` for the full
endpoint/auth/cache documentation.

## Run locally

```bash
./start.sh                 # http://127.0.0.1:8765/  (default port 8765)
./start.sh --port 8765 --host 127.0.0.1
```

Without `TELEGRAM_BOT_TOKEN` the server starts in UNVERIFIED local-dev
mode (it says so on startup and in `/api/health`). That is fine for
local testing; set the token before any deployment:

```bash
TELEGRAM_BOT_TOKEN=<token> ./start.sh
```

The token comes ONLY from the environment variable — never from a file,
never from the repo, never from the frontend.

## Endpoints

| Method | Path | What it does |
|---|---|---|
| GET | `/` | Frontend (`frontend/index.html`) |
| GET | `/app.js`, `/styles.css`, `/commands.json` | Frontend assets |
| GET | `/api/health` | Liveness, fast commands, cache age, auth mode |
| GET | `/api/commands` | All 48 commands (in-app search) |
| GET | `/api/cache` | Cached snapshot + staleness flags |
| POST | `/api/message` | `{text, initData}` → Tier 1 cached reply, or Tier 2 `needs_muse` |
| GET | `/api/events?text=…` | SSE: `received` → `checking-cache` → `cached` \| `needs_muse` → `reply` (if a Muse worker writes one to `replies.jsonl`) \| `reply_timeout` (75 s) |
| GET | `/api/reply?queue_id=…` | Polling fallback: has a reply arrived for this Tier 2 queue id? |
| GET | `/api/queue` | v4.3 diagnostic: how many Tier 2 messages are queued + the most recent 5 (your own messages only) — lets a worker find a queue_id to answer |

## Cache-sync bridge (v4.1 — built, not yet live end to end)

- **Backend side (built, tested):** `server.py` polls `CACHE_SYNC_URL`
  every `CACHE_SYNC_INTERVAL_S` (default 60 s) in a background thread,
  verifies an optional HMAC signature, and adopts only a newer,
  parseable snapshot (atomic replace). `/api/health` and `/api/cache`
  report `cache_sync` status; sync silent >60 min ⇒ `stale`.
  Env: `CACHE_SYNC_URL`, `CACHE_SYNC_INTERVAL_S`, `CACHE_SYNC_TOKEN`
  (optional Bearer), `CACHE_SYNC_HMAC_SECRET` (optional, recommended).
- **Muse side (built, tested dry-run + live local refresh):**
  `refresh_cache.py` rebuilds `server/cache.json` from the live
  Muse-side snapshot (`--from-telegram-state`, default) or a fresh
  snapshot on stdin (`--from-stdin`, full fidelity), stamps
  `cache_updated_utc`, writes only on content change, and optionally
  pushes to GitHub (`--push-github --repo mani-moh/muse-telegram-cache`,
  needs `GITHUB_TOKEN` in env). The repo must already exist (private,
  created by John) — the script will not create it.
- **Still needed for the live bridge:** the `CACHE_SYNC_*` env vars on
  the hosted backend. ~~The private repo `mani-moh/muse-telegram-cache`
  (John creates it) and a refresher cron on this VM (15 min
  recommended — 96 Muse runs/day; 5 min = 288/day)~~ — **DONE: the
  repo exists** (private, `main`, verified live 2026-11-25; holds
  `cache.json`) **and the refresher cron is running** (15 min). Until
  the env vars are set the backend serves its local seed and labels
  Tier 1 answers stale.

## In-app reply (§10.6, v4.1 — built, tested)

After a Tier 2 queue, the SSE stream stays open up to 75 s watching
`replies.jsonl`. A Muse-side worker appends
`{"queue_id": "q-…", "reply": "…", "replied_at_utc": "…"}` when it
answers; the reply then streams into the open app (`reply` event).
Otherwise `reply_timeout` and the reply lands in the bot chat as
before. Same speed, better surface — never faster.
`GET /api/reply?queue_id=…` is the polling fallback.

### Worker side (v4.2 — PROPOSAL ONLY, not installed)

`in_app_reply_worker.py` + `IN_APP_REPLY_WORKER_PROPOSAL.md` (same
folder): the standalone worker tool that records a reply for a queued
entry — `--queue-id` (preferred) or `--match-text` (fallback, refuses
on ambiguity / already-answered), `--dry-run`, exit 0/1/2. Tested
locally end to end (SSE `reply` event at 2.43 s). The live hook script
was NOT read or edited; installing hook-side wiring needs John's
explicit approval.

### Reverse-sync (v4.4 — backend half BUILT, tested locally)

The hosted gap from v4.2 is now half-closed — see
`REVERSE_SYNC_DESIGN.md` for the full design. Built in v4.4:

- **Backend side (built, tested):** `server.py` polls
  `REPLIES_SYNC_URL` every `REPLIES_SYNC_INTERVAL_S` (default 15 s —
  faster than the 60 s cache poll, because the SSE window is 75 s)
  and adopts only lines whose `queue_id` is in its own `queue.jsonl`
  and is not already answered (foreign ids ignored, malformed lines
  rejected, optional per-line HMAC via `REPLIES_SYNC_HMAC_SECRET`,
  optional Bearer via `REPLIES_SYNC_TOKEN`). Adopted lines go into
  the local `replies.jsonl` (capped at 500 lines) and are marked
  answered in the same sidecar the worker uses, so a second poll never
  re-appends them. `/api/health` reports `replies_sync` status the
  same way it reports `cache_sync`. Tested locally end to end with a
  `file://` remote: good line adopted, foreign id ignored, malformed
  line rejected, signed line adopted / tampered + unsigned rejected,
  SSE `reply` event delivered in-app.
  Env: `REPLIES_SYNC_URL`, `REPLIES_SYNC_INTERVAL_S`,
  `REPLIES_SYNC_TOKEN` (optional), `REPLIES_SYNC_HMAC_SECRET`
  (optional, recommended).
- **Muse side (built, tested dry-run):** `reverse_sync_poller.py`
  polls the backend's `GET /api/queue` (default every 5 s, `--once`
  for a single poll) and prints `NEW <id> <queued_at> <text>` for
  entries it has not seen — read-only: it never wakes Muse, never
  sends a Telegram message, never touches the hook. Seen ids live in
  `~/.cache/muse-telegram-app/reverse-sync-seen.json` (or `--state`);
  entries older than `--max-age-min` (default 30) are marked seen
  silently. `in_app_reply_worker.py --to-github` publishes a reply
  line to the GitHub `replies.jsonl` (read-append-write, 409 retry
  once, last 200 lines, optional HMAC signing via
  `REPLIES_SYNC_HMAC_SECRET`; needs `GITHUB_TOKEN` in env).
- **Still NOT built / still needed:** the hook-side wiring (poller
  feeding the wake path) needs John's explicit approval; the
  token/interval decisions for the replies sync (dedicated PAT with
  `contents: write` on `mani-moh/muse-telegram-cache` only — the
  blackout PAT is scoped to `feed-data` and must NOT be reused).
  ~~The private repo `mani-moh/muse-telegram-cache` (John creates it)~~
  — **the repo EXISTS** (private, `main`, verified live 2026-11-25;
  holds `cache.json` only; `replies.jsonl` is created by the first
  `--to-github` publish). Live GitHub publishing is untested until
  the token decision lands — see `REVERSE_SYNC_LIVE_TEST.md` for the
  hosted test plan. Nothing is installed on the hosted box.

### Reverse-sync hardening (v4.6)

- Queue ids are monotonic: two Tier 2 sends in the same millisecond
  used to collide on `q-<ms>`; the server now steps the ms forward
  instead. Format stays `q-<ms>`, so the poller, worker and live-test
  docs are unchanged.
- `reverse_sync_poller.py` seen-ids file and the worker's answered
  sidecar are capped at the newest 500 ids (were unbounded).
- Probed in `../screenshots/test_v46.py`: poller unreachable (exit 1),
  NEW-once-then-silent, worker ambiguous / already-answered /
  empty-reply exits. `reverse_sync_e2e.py` still passes 7/7.

### End-to-end proof + live-test plan (v4.5)

- `reverse_sync_e2e.py` — proves the FULL loop locally in an
  isolated temp dir (~12 s): queue → poller `NEW` → worker
  `--to-github --dry-run` + merge → backend adopts → SSE `reply`
  in-app → no duplicate on a second sync. Exit 0 iff all 7 steps
  pass. The GitHub API round-trip is the one hop it does NOT cover
  (needs a real token + the real repo).
- `REVERSE_SYNC_LIVE_TEST.md` — the hosted live-test plan: what is
  already verified, the 3 decisions still needed from John, the
  backend env vars, 6 supervised steps, expected timings (~20 s
  publish→in-app), a failure table, and rollback.
- `in_app_reply_worker.py --queue-url <base>` (v4.5) — fetches
  `GET /api/queue` instead of a local queue file, because the hosted
  queue lives on the Soar box. Only the recent 5 entries are
  returned, so the id must be fresh.

## Files

- `server.py` — the whole backend (stdlib only). v4.19.1: 14 Tier 1
  cached commands (only VERSION bumped from 4.19.0 for the
  "Open in Muse" button move — no backend behaviour change); `/api/cache` also returns `notes`, `tasks`,
  `tracked`, `recent_decided_approvals`, `wallet`; `GET /api/queue`
  diagnostic (v4.3); replies reverse-sync poller
  (`REPLIES_SYNC_URL`, default 15 s, per-line HMAC) + `replies_sync`
  in `/api/health` (v4.4). The cache-sync fetch User-Agent now uses
  `VERSION` (v4.8: it still said `muse-telegram-app/4.6`).
  `humanize_note()` drops the stale "next is Increment 2+3" clause
  from tracked notes at render time (v4.16, same transform as the
  frontend's `humanizeNote()`).
- `REVERSE_SYNC_DESIGN.md` — v4.3 design; v4.4 builds the backend
  half + the Muse-side scripts (see "Reverse-sync" above). Hook-side
  wiring still needs John's approval.
- `reverse_sync_e2e.py` — v4.5 end-to-end proof harness (see above); v4.8 makes its version check semver-agnostic (the pinned "4.7.0" failed on every bump).
- `REVERSE_SYNC_LIVE_TEST.md` — v4.5 hosted live-test plan (see above).
- `reverse_sync_poller.py` — v4.4 Muse-side read-only poller for
  `GET /api/queue` (prints `NEW <id> <queued_at> <text>`; never wakes
  Muse, never touches the hook).
- `in_app_reply_worker.py` — worker tool (see above); v4.4 adds
  `--to-github` (publish the reply line to the GitHub replies.jsonl:
  read-append-write, 409 retry once, last 200 lines, optional HMAC);
  v4.5 adds `--queue-url` (fetch `/api/queue` from the hosted
  backend instead of a local queue file). Not installed, not wired
  into any hook.
- `IN_APP_REPLY_WORKER_PROPOSAL.md` — the worker proposal: design,
  test plan, honest gaps, install path (needs John's approval).
- `cache.json` — seed snapshot copied from the verified
  `custom.telegram/state/cache.json` (2026-11-24T17:05:00Z). Refresh it
  only with values a Muse tool just returned; never invent entries.
  Staleness rules (same as `telegram_fast.py`): `/review` trusted
  ≤15 min, others flagged >60 min, always with the cached time shown.
- `queue.jsonl` — created at runtime; every Tier 2 message is appended
  here with a queue id (`queued-needs-muse`). There is no live Muse
  reply path from this server yet — the queue is a record, not a wake.
  Do not delete it to hide that.
- `replies.jsonl` — created at runtime by a Muse-side worker; one line
  per in-app reply: `{"queue_id", "reply", "replied_at_utc"}`. The SSE
  stream watches it for up to 75 s after a Tier 2 queue (v4.1).
- `refresh_cache.py` — Muse-side cache refresher for the sync bridge
  (see "Cache-sync bridge" above). Run on this VM, never on the host.
- `start.sh` — local start script.
- `muse-telegram-app.service` — systemd unit for Lightsail/EC2 (NOT
  installed here). Token via `/etc/muse-telegram-app.env` (mode 600,
  root-only) or AWS Secrets Manager — never in the unit file.

## Deploy (NOT done — needs John's decisions, see project README)

1. Hosting: John's Soar box (whitedns i-00f5c72e349f7c862, 16.58.75.182)
   at $0 extra — see `deploy/DEPLOY.md`. Domain `app.mqj.cc.cd`,
   Cloudflare zone Pending at last check; DNS must resolve first.
2. Domain + TLS: Mini App URL must be HTTPS. Caddy in front of
   127.0.0.1:8765 with a Cloudflare Origin Certificate on 443.
3. Bot token: env var at deploy time / Secrets Manager.
4. Bot Menu button: point to `https://app.mqj.cc.cd/`.
5. Cache freshness for a remote backend: the sync bridge is BUILT
   (v4.1, see above) — still needs the private repo
   `mani-moh/muse-telegram-cache`, a refresher cron on this VM, and
   the `CACHE_SYNC_*` env vars on the backend. Until then, copy a
   fresh `cache.json` in at deploy time as a stopgap (buy a few hours).
