#!/usr/bin/env python3
"""In-app reply worker (PROPOSAL — NOT installed, NOT wired into any hook).

Purpose: close the v4.1 in-app reply loop (§10.6). After a Tier 2 message
is queued by the app backend (server/queue.jsonl), a Muse-side worker
answers it; this script records that answer as a line in
server/replies.jsonl so the open app's SSE stream delivers it in-app
(`reply` event) instead of only in the bot chat.

It NEVER sends a Telegram message, NEVER calls telegram.py, NEVER
touches the hook script, and NEVER wakes Muse. It only appends one
JSON line to a replies file. It is meant to be called by the parent
Muse agent (or a worker it supervises) AFTER it has already answered
a message through the normal Telegram flow.

Matching — how a bot-chat answer finds its queue entry:
  The queue_id cannot travel through bot chat (Telegram messages carry
  no queue_id), so matching is by content:
  1. Explicit mode (robust, preferred): --queue-id q-<ms> when the
     caller knows it (e.g. the hook/worker that queued the message
     recorded the id alongside the update).
  2. Text mode (fallback): --match-text "<exact message text>" finds
     the NEWEST unmatched queued entry with exactly that text, queued
     within --max-age-min (default 30). Refuses to match when two
     unmatched entries share the same text (ambiguous — caller must
     use --queue-id).

Replies file format (one JSON object per line, read by server.py):
  {"queue_id": "q-...", "reply": "<Muse's reply text>",
   "replied_at_utc": "2026-11-24T20:50:00Z", "via": "bot-chat"}

Safety:
  --dry-run prints what would be appended and writes nothing.
  Appends are atomic-ish (open in append mode, single write).
  A queue entry is marked answered by writing a sidecar file
  <queue-file>.answered.json listing answered queue ids, so a second
  run never double-delivers the same reply. (The queue file itself is
  never rewritten — it is the backend's record.)
  Exit codes: 0 = reply recorded (or dry-run ok), 2 = no match /
  ambiguous match, 1 = error (bad args, unreadable files).

LOCAL USE ONLY for now (see IN_APP_REPLY_WORKER_PROPOSAL.md):
  python3 in_app_reply_worker.py --queue-file queue.jsonl \
      --replies-file replies.jsonl --queue-id q-1730000000000 \
      --reply-file /tmp/reply.txt
  python3 in_app_reply_worker.py --queue-file queue.jsonl \
      --replies-file replies.jsonl --match-text "/tasks" \
      --reply "No tasks due today." --dry-run

Reverse-sync (v4.4): --to-github publishes the line to the GitHub
replies.jsonl the hosted backend polls (needs GITHUB_TOKEN in env;
the repo must already exist — the script will not create it):
  python3 in_app_reply_worker.py --queue-file queue.jsonl \
      --to-github --repo mani-moh/muse-telegram-cache \
      --queue-id q-1730000000000 --reply "No tasks due today." --dry-run

v4.5: --queue-url <backend base> fetches GET /api/queue instead of
reading a local queue file — the hosted backend's queue.jsonl lives
on the Soar box, not on this VM, so the supervised live flow is
  poller --once --base https://app.mqj.cc.cd   (prints NEW q-...)
  worker --queue-url https://app.mqj.cc.cd --to-github --queue-id q-... --reply "..."
Answered ids go to ~/.cache/muse-telegram-app/reverse-sync-answered.json
(override with --answered-file). Only the recent 5 queue entries are
returned by /api/queue, so the id must be fresh.

The hosted gap: on the deployed backend, queue.jsonl/replies.jsonl
live on the Soar box, not on this VM — this script alone cannot close
that loop. The proposal doc covers the reverse-sync phase 2 (GitHub
replies file + backend poller). v4.4 builds that phase 2:
  * --to-github publishes the reply line to the GitHub replies.jsonl
    (read-append-write, 409 retry once, last 200 lines kept, optional
    per-line HMAC via REPLIES_SYNC_HMAC_SECRET) instead of a local file.
  * server.py polls that file (REPLIES_SYNC_URL) and adopts matching
    lines into its local replies.jsonl.
  * server/reverse_sync_poller.py is the read side (finds new queue ids).
The hook-side wiring (poller feeding the wake path) is STILL not built
and still needs John's approval; the private repo + token decisions are
still open. Live GitHub publishing is therefore untested — only
--dry-run and the merge/cap logic are testable here.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def now_utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_jsonl(path):
    try:
        lines = Path(path).read_text().splitlines()
    except OSError as e:
        print(f"error: cannot read {path}: {e}", file=sys.stderr)
        return None
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries


def load_answered(sidecar):
    try:
        return set(json.loads(Path(sidecar).read_text()))
    except (OSError, ValueError):
        return set()


def save_answered(sidecar, answered):
    # v4.6: cap the sidecar at the newest 500 ids (same reason as the
    # poller's seen file: ids are q-<ms>, so sorting keeps the newest;
    # older ids are past any re-delivery window).
    answered = set(sorted(answered)[-500:])
    p = Path(sidecar)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    p.write_text(json.dumps(sorted(answered)))


def fetch_queue_entries(base):
    """Fetch the backend's GET /api/queue and return its `recent`
    entries (the backend's own queue, newest 5). None on any error.
    This is the hosted-backend path: the queue file lives on the Soar
    box, not on this VM, so the worker reads the ids over HTTPS
    instead of from a local file. Limitation, stated plainly: only
    the recent 5 are returned, so the queue_id must be fresh — run
    the worker soon after the poller reports NEW (that is the normal
    supervised flow)."""
    url = base.rstrip("/") + "/api/queue"
    req = urllib.request.Request(url, headers={"User-Agent": "muse-telegram-reply-worker"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001 — report, don't crash
        print(f"error: cannot fetch {url}: {e}", file=sys.stderr)
        return None
    if isinstance(data, dict) and isinstance(data.get("recent"), list):
        return data["recent"]
    print(f"error: unexpected /api/queue response from {url}", file=sys.stderr)
    return None


# ---------- --to-github mode (v4.4 reverse-sync) ----------

def sign_line(record, secret):
    """Add a per-line HMAC signature (same scheme as the cache bridge
    and the backend's replies poller: HMAC-SHA-256 over the canonical
    JSON of the record without the signature field). No secret => the
    record is returned unsigned."""
    if not secret:
        return dict(record)
    payload = {k: v for k, v in record.items() if k != "signature"}
    sig = hmac.new(secret.encode(),
                   json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
                   hashlib.sha256).hexdigest()
    return {**payload, "signature": sig}


def merge_replies(existing_text, new_record, cap=200):
    """Pure merge for the GitHub replies.jsonl: drop any existing line
    for the same queue_id (a re-publish must never double-deliver),
    append the new line, keep only the last `cap` lines. Returns the
    new file text. Malformed existing lines are dropped, not kept."""
    lines = []
    for line in (existing_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("queue_id") != new_record["queue_id"]:
            lines.append(line)
    lines.append(json.dumps(new_record))
    return "\n".join(lines[-cap:]) + "\n"


def github_read_file(repo, path, token):
    """GET a repo file via the contents API. Returns (text, sha) —
    ("", None) when the file does not exist yet (404). Raises on
    other HTTP errors."""
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "User-Agent": "muse-telegram-reply-worker"}
    req = urllib.request.Request(api, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "", None
        raise
    return base64.b64decode(data.get("content", "")).decode("utf-8", "replace"), data.get("sha")


def github_write_file(repo, path, token, text, sha, message):
    """PUT a repo file via the contents API. Returns the commit sha.
    Raises urllib.error.HTTPError (including 409 on a concurrent write)."""
    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "User-Agent": "muse-telegram-reply-worker"}
    body = {"message": message,
            "content": base64.b64encode(text.encode()).decode()}
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(api, data=json.dumps(body).encode(), headers=headers, method="PUT")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()).get("commit", {}).get("sha")


def publish_to_github(record, repo, path, token, secret):
    """Publish one reply record to the GitHub replies.jsonl. Read,
    merge (cap 200), write; on a 409 (concurrent write) re-read and
    retry once. Returns (commit_sha, lines_in_file)."""
    signed = sign_line(record, secret)
    existing, sha = github_read_file(repo, path, token)
    merged = merge_replies(existing, signed)
    try:
        commit = github_write_file(repo, path, token, merged, sha,
                                   f"Reply for {record['queue_id']}")
    except urllib.error.HTTPError as e:
        if e.code != 409:
            raise
        existing, sha = github_read_file(repo, path, token)
        merged = merge_replies(existing, signed)
        commit = github_write_file(repo, path, token, merged, sha,
                                   f"Reply for {record['queue_id']} (retry after 409)")
    return commit, len(merged.splitlines())


def main():
    p = argparse.ArgumentParser(description="Record a Muse reply for a queued app message (proposal).")
    p.add_argument("--queue-file", default="", help="queue.jsonl written by server.py (local mode)")
    p.add_argument("--queue-url", default="",
                   help="backend base URL to fetch /api/queue from instead of --queue-file (v4.5: the hosted backend's queue lives on the Soar box, not on this VM)")
    p.add_argument("--answered-file", default="",
                   help="where to record answered ids with --queue-url (default ~/.cache/muse-telegram-app/reverse-sync-answered.json)")
    p.add_argument("--replies-file", default="", help="replies.jsonl watched by server.py SSE (local mode; not needed with --to-github)")
    p.add_argument("--to-github", action="store_true",
                   help="publish the reply to the GitHub replies.jsonl instead of a local file (v4.4 reverse-sync)")
    p.add_argument("--repo", default="mani-moh/muse-telegram-cache",
                   help="GitHub repo for --to-github (default mani-moh/muse-telegram-cache)")
    p.add_argument("--replies-path", default="replies.jsonl",
                   help="path of the replies file in the repo for --to-github (default replies.jsonl)")
    p.add_argument("--queue-id", default="", help="explicit queue id (preferred)")
    p.add_argument("--match-text", default="", help="exact message text to match (fallback)")
    p.add_argument("--max-age-min", type=int, default=30, help="text-match recency window (default 30)")
    p.add_argument("--reply", default="", help="reply text (or use --reply-file)")
    p.add_argument("--reply-file", default="", help="file holding the reply text")
    p.add_argument("--dry-run", action="store_true", help="print what would be appended, write nothing")
    args = p.parse_args()

    if not args.queue_id and not args.match_text:
        print("error: need --queue-id or --match-text", file=sys.stderr)
        return 1
    if args.queue_id and args.match_text:
        print("error: pick one: --queue-id or --match-text, not both", file=sys.stderr)
        return 1
    reply = args.reply
    if args.reply_file:
        try:
            reply = Path(args.reply_file).read_text()
        except OSError as e:
            print(f"error: cannot read reply file: {e}", file=sys.stderr)
            return 1
    reply = (reply or "").strip()
    if not reply:
        print("error: reply text is empty (--reply or --reply-file)", file=sys.stderr)
        return 1

    if bool(args.queue_file) == bool(args.queue_url):
        print("error: need exactly one of --queue-file / --queue-url", file=sys.stderr)
        return 1
    if args.queue_url:
        queue = fetch_queue_entries(args.queue_url)
        if queue is None:
            return 1
        sidecar = args.answered_file or str(
            Path.home() / ".cache" / "muse-telegram-app" / "reverse-sync-answered.json")
    else:
        queue = read_jsonl(args.queue_file)
        if queue is None:
            return 1
        sidecar = args.queue_file + ".answered.json"
    answered = load_answered(sidecar)

    target = None
    if args.queue_id:
        for entry in reversed(queue):
            if entry.get("id") == args.queue_id:
                target = entry
                break
        if target is None:
            print(f"error: no queued entry with id {args.queue_id}", file=sys.stderr)
            return 2
        if args.queue_id in answered:
            print(f"error: {args.queue_id} already has a recorded reply — refusing to double-deliver", file=sys.stderr)
            return 2
    else:
        cutoff = time.time() - args.max_age_min * 60
        cands = []
        for entry in queue:
            if entry.get("text") != args.match_text:
                continue
            if entry.get("id") in answered:
                continue
            try:
                qts = datetime.fromisoformat(
                    entry.get("queued_at_utc", "").replace("Z", "+00:00")).timestamp()
            except ValueError:
                qts = 0
            if qts >= cutoff:
                cands.append(entry)
        if not cands:
            print(f"error: no unmatched queued entry with that exact text in the last {args.max_age_min} min", file=sys.stderr)
            return 2
        if len(cands) > 1:
            print(f"error: ambiguous — {len(cands)} unmatched entries share that text; use --queue-id", file=sys.stderr)
            return 2
        target = cands[0]

    record = {"queue_id": target["id"], "reply": reply,
              "replied_at_utc": now_utc(), "via": "bot-chat"}
    print(f"queue_id: {target['id']}")
    print(f"queued text: {target.get('text', '')!r}")
    print(f"reply ({len(reply)} chars): {reply[:120]!r}{'...' if len(reply) > 120 else ''}")
    if args.to_github:
        secret = os.environ.get("REPLIES_SYNC_HMAC_SECRET", "")
        signed = sign_line(record, secret)
        print(f"github target: {args.repo}/{args.replies_path}"
              + (" (HMAC-signed)" if secret else " (unsigned — set REPLIES_SYNC_HMAC_SECRET to sign)"))
        print(f"line to publish: {json.dumps(signed)}")
        if args.dry_run:
            print("dry-run: published nothing")
            return 0
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            print("error: --to-github needs GITHUB_TOKEN in the environment", file=sys.stderr)
            return 1
        try:
            commit, nlines = publish_to_github(record, args.repo, args.replies_path, token, secret)
        except Exception as e:  # noqa: BLE001 — report, don't crash silently
            print(f"error: GitHub publish failed: {e}", file=sys.stderr)
            return 1
        answered.add(target["id"])
        save_answered(sidecar, answered)
        print(f"published to {args.repo}/{args.replies_path} commit {commit} ({nlines} lines in file)")
        return 0
    if not args.replies_file:
        print("error: need --replies-file (local mode) or --to-github", file=sys.stderr)
        return 1
    if args.dry_run:
        print("dry-run: wrote nothing")
        return 0
    with open(args.replies_file, "a") as f:
        f.write(json.dumps(record) + "\n")
    answered.add(target["id"])
    save_answered(sidecar, answered)
    print(f"recorded in {args.replies_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
