# Reverse-sync — hosted live-test plan (v4.5)

**Status: local loop PROVEN end to end** (`server/reverse_sync_e2e.py`,
7/7 steps, ~12 s wall-clock on 2026-11-25). **Hosted loop NOT yet
tested** — the steps below are the plan for the Soar box once the
app is deployed. Nothing here is installed, and the live hook is
untouched.

## What is already true (verified, not assumed)

- **The repo exists.** `mani-moh/muse-telegram-cache` — private, `main`
  branch, verified live 2026-11-25 via the `custom.blackout` connector
  (`repo-info` 200). It currently holds **only `cache.json`** (135
  bytes); `replies.jsonl` does **not** exist yet. That is fine: the
  worker's `--to-github` mode treats a 404 as "empty file" and creates
  it on the first publish.
- **The refresher cron is running** (15-min, cache side) — the cache
  bridge's repo + cron decisions are closed, not open.
- **The local loop is proven** (see the harness output in the v4.5
  changelog): queue → poller NEW → worker dry-run + merge → backend
  adopt → SSE `reply` in-app, no duplicate on a second sync.
- **The one hop the local proof does NOT cover** is the GitHub API
  round-trip in `publish_to_github()` (read → merge → PUT, 409 retry
  once). It needs a real token and the real repo, so it can only be
  proven live — step 4 below is that proof.

## Decisions still needed from John

1. **GitHub token for the worker.** A PAT with `contents: write` on
   `mani-moh/muse-telegram-cache` only (same scope pattern as the
   blackout PAT, which is scoped to `feed-data` only and must NOT be
   reused here). Stored as `GITHUB_TOKEN` in the Muse-side env, never
   in a file. The backend does NOT need this token.
2. **Backend read token + interval.** The hosted backend polls
   `REPLIES_SYNC_URL` with optional `REPLIES_SYNC_TOKEN` (Bearer) and
   optional `REPLIES_SYNC_HMAC_SECRET`. Recommended: 15 s interval
   (the SSE window is 75 s; a 60 s poll would usually miss it).
   HMAC recommended — the worker signs each line when
   `REPLIES_SYNC_HMAC_SECRET` is set, and the backend rejects
   unsigned/tampered lines.
3. **Hook-side wiring approval.** The poller feeding the wake path
   (poller output → normal hook/worker flow) is still NOT built and
   still needs John's explicit approval. Until then, steps 3–5 below
   run **supervised, by hand** — a human reads the poller output and
   runs the worker. That is the honest interim state, and it works.

## Hosted backend env (deploy-time, `/etc/muse-telegram-app.env`)

```text
REPLIES_SYNC_URL=https://raw.githubusercontent.com/mani-moh/muse-telegram-cache/main/replies.jsonl
REPLIES_SYNC_TOKEN=<fine-grained read-only token for that repo>
REPLIES_SYNC_INTERVAL_S=15
REPLIES_SYNC_HMAC_SECRET=<shared secret, same value as the worker side>
```

Same pattern as the existing `CACHE_SYNC_*` vars (see
`deploy/CACHE_SYNC_BRIDGE.md` and `deploy/muse-telegram-app.service`).
The backend only ever GETs the file — no inbound connection to the
Soar box, no new secret on the box beyond this pattern.

## Live test, step by step (after deploy)

Precondition: the app is deployed, the backend is up, and
`GET /api/health` shows `replies_sync: ok` (not `disabled`).

1. **From the Mini App (hosted):** send a Tier 2 question, e.g.
   "what is my next goal?", and **leave the app open**. The app
   shows "Muse is waking up — waiting for a reply in this app (up
   to ~75s)". Note the time.
2. **On this VM, supervised:** run the poller once:
   `python3 server/reverse_sync_poller.py --once --base https://app.mqj.cc.cd`
   Expect one line: `NEW q-<ms> <queued_at> what is my next goal?`
   (If it prints nothing, the queue entry is older than 30 min or
   the backend is unreachable — check `/api/queue` in a browser.)
3. **Answer it in the bot chat as normal** (the usual Muse wake +
   reply). Then record the reply for the app:
   `python3 server/in_app_reply_worker.py --queue-url https://app.mqj.cc.cd --to-github --queue-id q-<ms> --reply "<the reply text>"`
   — needs `GITHUB_TOKEN` in env. `--queue-url` (new in v4.5) fetches
   the backend's own `GET /api/queue` instead of needing a local
   queue file, because the hosted `queue.jsonl` lives on the Soar box,
   not on this VM. It only returns the recent 5 entries, so run it
   soon after the poller reports the id. Expect:
   `published to mani-moh/muse-telegram-cache/replies.jsonl commit <sha> (N lines in file)`.
4. **Watch the app.** Within ~15–20 s of step 3, the reply should
   appear **in the app thread** with the tag "Live reply — arrived
   in the app · <time> UTC". Check `GET /api/health` on the backend:
   `replies_sync.adopted` should have increased by 1.
5. **Timeout case (proves the fallback):** send a second Tier 2
   question, do NOT run the worker, and wait 75 s. The app must show
   the `reply_timeout` notice ("the reply lands in the bot chat"),
   and the reply must still arrive in the bot chat as today.
6. **Duplicate guard (proves no double-delivery):** re-run the
   worker for the same queue_id. Expect exit 2: "already has a
   recorded reply — refusing to double-deliver". And `GET
   /api/reply?queue_id=q-<ms>` must return exactly one reply.

## Expected timings (hosted)

- Poller sees a new queue entry: ≤ 5 s after the app queues it
  (poller cadence; supervised run is manual, so this is the poller's
  `--interval`, not the human).
- Worker publish → GitHub file updated: ~1–2 s (one contents-API
  read + one PUT; +1 retry on 409).
- Backend adopts the line: ≤ 15 s (its poll interval) after publish.
- SSE delivers it in-app: ≤ 2 s after adoption (the stream checks
  every 2 s).
- **Total, publish → in-app: ~20 s worst case**, inside the 75 s
  SSE window with room to spare. The Muse wake + thinking time
  (~10–70 s, measured) dominates — same as bot chat, never faster.

## Failure table

| Symptom | Likely cause | Check |
|---|---|---|
| `/api/health` replies_sync `disabled` | env vars not set on the hosted backend | `/etc/muse-telegram-app.env`, restart the service |
| `replies_sync: error` | backend cannot fetch the raw URL (private repo needs the read token) | `REPLIES_SYNC_TOKEN` set and valid; URL is the `main`-branch raw URL |
| adopted stays 0, ignored grows | queue_id mismatch — the line's id is not in the backend's queue.jsonl | `GET /api/queue` — is the id there? (queue file caps at 500; an old id may have been trimmed) |
| adopted stays 0, rejected grows | HMAC mismatch / unsigned line while a secret is set | same `REPLIES_SYNC_HMAC_SECRET` on both sides; worker prints "(HMAC-signed)" when set |
| Worker: 401/403 on publish | PAT lacks `contents: write` on this repo, or is the feed-data PAT | use the dedicated cache-repo PAT (decision 1) |
| Worker: 409 twice | concurrent publish | re-run once; the worker retries once by design |
| Reply in bot chat, never in-app | SSE window expired (>75 s) before adoption | expected when Muse is slow — the fallback IS the bot chat; check timings above |

## Rollback

Remove the four `REPLIES_SYNC_*` vars from
`/etc/muse-telegram-app.env` and restart the backend: `replies_sync`
returns to `disabled`, Tier 2 replies land in the bot chat exactly as
before v4.4, and nothing else changes. The GitHub `replies.jsonl`
can stay (the backend simply stops polling it); deleting it is a
separate, explicit decision.
