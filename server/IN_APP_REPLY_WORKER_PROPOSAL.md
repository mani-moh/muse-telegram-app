# In-app reply worker — PROPOSAL (v4.2, 2026-11-25)

**Status: PROPOSAL ONLY. Nothing here is installed, nothing is wired
into the live hook (`~/hooks/scripts/telegram-new-message.sh`), and no
hook file was read or edited for this proposal.** The script
`in_app_reply_worker.py` (same folder) is a standalone tool the parent
Muse agent can call after it answers a message through the normal
Telegram flow. Installing any hook-side wiring needs John's explicit
approval, because the hook is live infrastructure.

## The gap it closes

v4.1 built the app side of the in-app reply (§10.6): after a Tier 2
queue, the SSE stream stays open up to 75 s watching
`server/replies.jsonl`; a line `{"queue_id", "reply"}` delivers the
reply in the app. What was missing is the **worker side** — who writes
that line when Muse answers. This proposal is that side, for the
local backend.

## How it works

```
App --POST /api/message--> backend queues {id: q-..., text} in queue.jsonl
Muse answers the message in the bot chat (normal flow, unchanged)
Parent/worker calls:
  in_app_reply_worker.py --queue-file queue.jsonl --replies-file replies.jsonl \
      --queue-id q-... --reply "<Muse's reply>"
Backend SSE sees the replies.jsonl line -> `reply` event -> app shows it
```

- **Matching:** `--queue-id` (robust, preferred) or `--match-text`
  (fallback: newest unmatched entry with exactly that text, queued
  within 30 min; refuses on ambiguity — two unmatched entries with
  the same text — and on already-answered ids).
- **No double delivery:** answered ids are recorded in
  `queue.jsonl.answered.json`; a second call for the same id exits 2.
  The queue file itself is never rewritten.
- **Safety:** `--dry-run` writes nothing. The script never sends a
  Telegram message, never calls `telegram.py`, never wakes Muse, never
  reads any secret. It only appends one JSON line to a replies file.
- **Exit codes:** 0 recorded / dry-run ok, 2 no/ambiguous match or
  already answered, 1 bad args / unreadable files.

## Test plan (local, no Telegram, no hook)

1. Start the backend, send a Tier 2 message from the app (or
   `POST /api/message {"text": "what is my next goal?"}`) — note the
   returned `queue_id`, confirm one line in `queue.jsonl`.
2. `python3 in_app_reply_worker.py --queue-file queue.jsonl
   --replies-file replies.jsonl --queue-id <id>
   --reply "Test reply" --dry-run` → prints the match, writes nothing
   (exit 0, no `replies.jsonl` created).
3. Same call without `--dry-run` → exit 0, one line in `replies.jsonl`,
   id in `queue.jsonl.answered.json`.
4. Repeat step 3 → exit 2, "already has a recorded reply".
5. `--match-text` with a wrong text → exit 2, "no unmatched queued
   entry"; with two identical queued texts → exit 2, "ambiguous".
6. SSE end to end: open `GET /api/events?text=<same text>` in one
   terminal, run the worker in another within 75 s → the stream emits
   a `reply` event with the worker's text. (v4.1 already proved the
   SSE side; this proves the worker side feeds it.)
7. Clean up: delete the test `queue.jsonl`, `replies.jsonl`, and
   `queue.jsonl.answered.json` after the run — they are runtime files,
   not repo state.

## What is NOT solved here (honest gaps)

- **The hosted gap.** On the deployed backend, `queue.jsonl` /
  `replies.jsonl` live on the Soar box, not on this VM. This script
  running here cannot write the hosted replies file. Phase 2 (NOT
  built): a reverse sync — the worker pushes the reply to a GitHub
  file (e.g. `mani-moh/muse-telegram-cache/replies.jsonl`, same
  private repo as the cache bridge) and the backend polls it the way
  it polls `CACHE_SYNC_URL` today. That needs the same decisions as
  the cache bridge (repo, token, interval) and is a separate build.
- **queue_id can't travel through bot chat.** Telegram messages carry
  no queue_id, so the worker must know the id some other way (the
  hook/worker recording it alongside the update, or text matching).
  Text matching is a fallback with the ambiguity guard above — it is
  not a substitute for carrying the id.
- **75 s window.** If Muse takes longer than 75 s to answer, the app
  stream has already timed out (`reply_timeout`) and the reply lands
  in the bot chat as before. The worker line is then harmless (no
  stream is listening) but useless for that message. The window is a
  v4.1 design choice, not something this script changes.

## Install path (needs John's approval — do not do silently)

1. John approves a hook-side change (separate approval from the
   fast-path hook edit — this is a different change).
2. The hook (or the worker it wakes) records each queued app message's
   `queue_id` next to the update it wakes for, and after Muse replies,
   calls this script with `--queue-id` + the reply text.
3. Phase 2 (hosted): build the GitHub replies sync + backend poller,
   then point the worker at the GitHub target instead of the local file.
4. Live test: send a Tier 2 message from the Mini App after deploy,
   confirm the reply arrives in-app within 75 s.
