#!/usr/bin/env python3
"""Reverse-sync poller (Muse side) for the Telegram App v4 in-app reply loop.

Reads the hosted backend's GET /api/queue every few seconds and reports
Tier 2 messages the app queued that this Muse machine has not seen yet,
so a worker can answer them and publish the reply to the GitHub
replies.jsonl (see in_app_reply_worker.py --to-github). The backend
then adopts the reply and the open app stream delivers it in-app.

This script is the READ side only. It NEVER wakes Muse, NEVER sends a
Telegram message, NEVER calls telegram.py, and NEVER touches the hook
script. Feeding its output into the normal hook/worker wake path is the
hook-side wiring — that still needs John's explicit approval (see
server/REVERSE_SYNC_DESIGN.md). Until that wiring exists, run this
with --once to see what the app queued; a human (or a supervised
worker) decides what to answer.

State: seen queue ids are kept in a JSON file (default
~/.cache/muse-telegram-app/reverse-sync-seen.json, override with
--state or REVERSE_SYNC_STATE_FILE) so a restart does not re-report
old entries. Entries older than --max-age-min (default 30) are marked
seen silently, so a first run never floods with a stale queue.

Output (one line per new entry, machine-parseable):
  NEW <queue_id> <queued_at_utc> <text>
With --once it exits after one poll; otherwise it loops every
--interval seconds (default 5, same cadence as the hook).

Usage:
  python3 reverse_sync_poller.py --once --base http://127.0.0.1:8765
  python3 reverse_sync_poller.py --base https://app.mqj.cc.cd --interval 5

Exit codes: 0 = ok (new entries printed, or none), 1 = error
(unreachable backend, bad JSON, unreadable state).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATE = (Path.home() / ".cache" / "muse-telegram-app"
                 / "reverse-sync-seen.json")


def now_utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_seen(state_file):
    try:
        data = json.loads(Path(state_file).read_text())
        if isinstance(data, list):
            return set(str(x) for x in data)
    except (OSError, ValueError):
        pass
    return set()


def save_seen(state_file, seen):
    # v4.6: cap the state file so it cannot grow without bound over
    # months of polling (one id per queued message). Queue ids are
    # q-<ms>, so sorting keeps the newest 500; older ids are already
    # past the 30-min reporting window and never need re-reporting.
    seen = set(sorted(seen)[-500:])
    p = Path(state_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sorted(seen)))
    os.replace(tmp, p)


def fetch_queue(base):
    url = base.rstrip("/") + "/api/queue"
    req = urllib.request.Request(url, headers={"User-Agent": "muse-telegram-reverse-sync/4.4"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def poll_once(base, seen, max_age_min):
    """One poll. Returns (new_entries, seen) — new entries are dicts
    with id / queued_at_utc / text. Never raises for a bad queue entry;
    raises only for transport / top-level JSON errors."""
    data = fetch_queue(base)
    entries = data.get("recent", []) if isinstance(data, dict) else []
    cutoff = time.time() - max_age_min * 60
    new = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        qid = entry.get("id")
        if not isinstance(qid, str) or not qid:
            continue
        if qid in seen:
            continue
        # Age check from queued_at_utc; unparseable timestamp = treat as
        # new (fail open — better to surface a message than drop it).
        try:
            qts = datetime.fromisoformat(
                entry.get("queued_at_utc", "").replace("Z", "+00:00")).timestamp()
        except ValueError:
            qts = time.time()
        seen.add(qid)
        if qts < cutoff:
            continue  # stale on first sight: seen, not reported
        new.append(entry)
    return new, seen


def main():
    p = argparse.ArgumentParser(description="Poll the app backend's /api/queue for new Tier 2 messages (read-only).")
    p.add_argument("--base", default=os.environ.get("APP_BASE_URL", "http://127.0.0.1:8765"),
                   help="app backend base URL (default http://127.0.0.1:8765)")
    p.add_argument("--once", action="store_true", help="poll once and exit")
    p.add_argument("--interval", type=int, default=5, help="loop interval in seconds (default 5)")
    p.add_argument("--max-age-min", type=int, default=30,
                   help="only report entries queued within this many minutes (default 30)")
    p.add_argument("--state", default=os.environ.get("REVERSE_SYNC_STATE_FILE", str(DEFAULT_STATE)),
                   help="seen-ids state file")
    args = p.parse_args()
    if args.interval < 1:
        print("error: --interval must be >= 1", file=sys.stderr)
        return 1

    seen = load_seen(args.state)
    while True:
        try:
            new, seen = poll_once(args.base, seen, args.max_age_min)
        except urllib.error.URLError as e:
            print(f"error: cannot reach {args.base}/api/queue: {e}", file=sys.stderr)
            return 1
        except (ValueError, KeyError) as e:
            print(f"error: bad /api/queue response: {e}", file=sys.stderr)
            return 1
        for entry in new:
            text = (entry.get("text") or "").replace("\n", " ")
            print(f"NEW {entry['id']} {entry.get('queued_at_utc', '?')} {text}", flush=True)
        try:
            save_seen(args.state, seen)
        except OSError as e:
            print(f"error: cannot write state file {args.state}: {e}", file=sys.stderr)
            return 1
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
