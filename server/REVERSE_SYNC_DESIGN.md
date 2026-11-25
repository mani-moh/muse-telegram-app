# In-app reply — hosted reverse-sync design (v4.3, v4.4 groundwork)

**Status: BACKEND HALF BUILT in v4.4 (2026-11-25); FULL LOOP PROVEN
LOCALLY in v4.5 (`server/reverse_sync_e2e.py`, 7/7 steps, ~12 s) and
the hosted live-test plan is written up in
`server/REVERSE_SYNC_LIVE_TEST.md`.** The backend poller
(`server.py`, `REPLIES_SYNC_URL`), the Muse-side poller
(`server/reverse_sync_poller.py`), and the worker's `--to-github`
mode are built and tested locally with a synthetic replies file
(12/12 probes in `screenshots/test_v44.py`, SSE `reply` event
delivered in-app). **The repo now EXISTS** — `mani-moh/muse-telegram-cache`,
private, `main`, verified live 2026-11-25 (holds `cache.json` only so
far; `replies.jsonl` is created by the first `--to-github` publish).
NOT installed on the hosted box, NOT wired into the live hook — the
hook-side wiring and the GitHub token decisions still need John's
approval. The rest of this doc is the design as written in v4.3; the
"v4.4 build list" sections below are now the built pieces, kept here
as the record of what was decided.

## Goal

A Tier 2 message sent from the Mini App on the hosted backend gets its
Muse reply delivered **in the app** (same ~10–70 s as bot chat — the
gain is the surface, never the speed), with the bot chat as the
fallback when the reply arrives after the 75 s SSE window.

## Design (mirror of the cache-sync bridge, v4.1)

The cache bridge already solves the same problem in the other
direction (Muse side → hosted backend, via a GitHub file the backend
polls). Reverse-sync reuses that exact pattern:

```
App (hosted) --POST--> backend queues {id: q-..., text} in queue.jsonl
        |
        | backend exposes GET /api/queue (v4.3, diagnostic: count + recent 5)
        v
Muse-side poller (this VM, every 5 s — same cadence as the hook)
  reads /api/queue, finds queued entries it has not seen, wakes Muse
  (the normal hook flow — unchanged), Muse answers in bot chat
        |
        | worker records the reply
        v
GitHub private repo file  muse-telegram-cache/replies.jsonl
  (append-only JSONL, one line per reply:
   {"queue_id","reply","replied_at_utc","via":"bot-chat"})
        |
        | backend polls REPLIES_SYNC_URL (new env, same poller as CACHE_SYNC_URL)
        v
Backend adopts new lines into its local replies.jsonl
  (only lines whose queue_id is in its own queue.jsonl, and only if
   that id is not already answered — same guard as the local worker)
        |
        v
Open SSE stream emits `reply` -> app shows it in-thread
```

### Why GitHub, not a direct push to the Soar box

- The Soar box is John's DNS-tunnel host; the app must never open a
  new inbound port or SSH path to it (deploy constraints in
  `deploy/DEPLOY.md`). GitHub is already the sync target for the
  cache bridge, so the repo, token handling, and poller all exist.
- The backend only ever *pulls* (GET on a URL), exactly like the
  cache bridge. No inbound connection to the box, no new secret on
  the box beyond the existing `CACHE_SYNC_*` pattern.

### New backend pieces (v4.4 build list)

1. `REPLIES_SYNC_URL` env + `REPLIES_SYNC_INTERVAL_S` (default 15 s —
   faster than the 60 s cache poll, because the SSE window is 75 s;
   a 60 s poll would usually miss it).
2. `REPLIES_SYNC_TOKEN` (optional Bearer) + `REPLIES_SYNC_HMAC_SECRET`
   (optional, recommended — same HMAC scheme as the cache bridge:
   signature over the canonical JSON of each line's payload).
3. Poller: fetch the remote `replies.jsonl`, parse line by line,
   ignore malformed lines, adopt only lines whose `queue_id` exists
   in local `queue.jsonl` and is not already in
   `queue.jsonl.answered.json`, append adopted lines to local
   `replies.jsonl`, mark answered. Never adopt a reply for a queue id
   this backend never issued (prevents a stale/foreign file from
   injecting replies into the app).
4. `/api/health` reports `replies_sync` status the same way it
   reports `cache_sync` today (disabled / ok / stale / error, last
   sync time, last error). The app's cache label does not change —
   replies are not cache data.
5. `GET /api/queue` (built in v4.3) is the poller's read side: the
   Muse-side poller on this VM fetches it to learn which queue ids
   need answering. It returns only the user's own queued messages.

### New Muse-side pieces (v4.4 build list)

1. A poller script (same shape as `refresh_cache.py`): every 5 s,
   `GET https://app.mqj.cc.cd/api/queue`, diff against the ids it
   has already seen, and for each new id, hand the text to the
   normal hook/worker flow. **It does not replace the hook** — the
   hook still wakes Muse for bot-chat messages; the poller only
   adds app-queued texts to the same wake path.
2. After Muse replies, call `in_app_reply_worker.py` with
   `--queue-id` + the reply, but with a `--to-github` mode (new):
   instead of appending to a local `replies.jsonl`, it appends the
   line to the GitHub file via the `custom.blackout` connector's
   publish path (same PAT/token pattern as the cache bridge —
   **needs John's repo + token decisions, same as the cache bridge**).
   Append semantics: read the current file, append one line, write
   back; on a 409 (concurrent write), re-read and retry once.
   The existing `--dry-run`, ambiguity, and already-answered guards
   stay exactly as they are.
3. Retention: the GitHub `replies.jsonl` is capped (last 200 lines)
   by the same writer, so it cannot grow without bound.

### What is still NOT solved (honest gaps, carried from v4.2)

- **queue_id can't travel through bot chat.** Unchanged: the poller
  learns the id from `/api/queue`, not from the Telegram message,
  so text-matching is not needed on this path — but if the poller
  misses a queue entry (backend down, poll gap), the reply still
  lands in the bot chat as today.
- **75 s window.** Unchanged: if Muse takes longer than 75 s, the
  app stream has timed out (`reply_timeout`). The reply line is
  then harmless but useless for that message. The faster 15 s
  replies poll narrows the miss window but does not remove it.
- **Two sync targets, one repo.** Cache and replies share the
  private repo (`mani-moh/muse-telegram-cache`, John creates it) but
  are separate files with separate pollers, so a failure in one
  cannot corrupt the other.
- **No new approval is granted here.** The hook-side wiring (poller
  feeding the wake path) and the GitHub token/repo decisions still
  need John's explicit approval — same as the v4.2 proposal.

## Test plan (v4.4, local first — no deploy, no hook edit)

1. Unit: poller adopts only lines whose queue_id is in local
   `queue.jsonl`; a foreign queue_id is ignored; a malformed line
   is skipped; an already-answered id is not re-appended.
2. Local end to end (file:// `REPLIES_SYNC_URL`): queue a Tier 2
   message, write a reply line to the "remote" file, confirm the
   SSE stream delivers it in-app within one poll interval.
3. Bad-signature remote file is rejected (same test as the cache
   bridge v4.1); older/duplicate lines are ignored.
4. Hosted test only after deploy + John's approval: send a Tier 2
   message from the Mini App, confirm the reply arrives in-app
   within 75 s; confirm a >75 s reply still lands in bot chat.

## Decisions still needed from John (unchanged, except the repo)

- ~~Create the private repo `mani-moh/muse-telegram-cache` (or name
  another)~~ — **DONE 2026-11-24, verified live 2026-11-25:**
  private, `main`, holds `cache.json` only so far. The 15-min
  refresher cron is running (cache side). `replies.jsonl` is created
  by the first `--to-github` publish.
- Token + interval choices for the replies sync (15 s recommended).
- Approval for the hook-side wiring (poller feeding the wake path).
