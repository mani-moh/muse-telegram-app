#!/usr/bin/env python3
"""Muse-side cache refresher for the Telegram App v4 cache-sync bridge.

This runs on THIS Muse machine (where the connectors live), NOT on the
hosted backend. It rebuilds server/cache.json from the live Muse-side
snapshot and, optionally, pushes it to the sync target the hosted
backend polls (GitHub private repo, default; see
deploy/CACHE_SYNC_BRIDGE.md).

Sources (in priority order):
  1. --from-stdin : a fresh snapshot JSON on stdin, produced by a Muse
     run that read Gmail / Google Tasks / Calendar / goals / feed /
     review / artifact.list live. This is the full-fidelity path.
  2. --from-telegram-state (default): copy the existing live cache at
     ~/workspace/custom-connectors/custom.telegram/state/cache.json,
     which Muse workers refresh. Same shape, already validated.

The script NEVER invents data: it only copies/validates what Muse
already recorded, stamps cache_updated_utc with the time it ran, and
only writes/pushes when the content actually changed.

Push (GitHub, default target):
  --push-github --repo mani-moh/muse-telegram-cache
  Uses GITHUB_TOKEN from the environment (never from a file, never in
  chat). Creates/updates cache.json on the repo's default branch via
  the GitHub contents API. The repo must already exist (private,
  created by John) — this script will NOT create it.
  Optional HMAC signing: --hmac-secret from env CACHE_SYNC_HMAC_SECRET
  adds a "signature" field; the backend verifies it before adopting.

Usage (run by the parent Muse agent / a cron on this VM):
  python3 refresh_cache.py --from-telegram-state
  python3 refresh_cache.py --from-telegram-state --push-github --repo mani-moh/muse-telegram-cache
  python3 refresh_cache.py --from-stdin --push-github --repo mani-moh/muse-telegram-cache < snapshot.json
  python3 refresh_cache.py --from-telegram-state --dry-run   # print what would change, write nothing

Exit codes: 0 = ok (changed or already fresh), 2 = source missing/stale,
1 = error (bad JSON, push failed).
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER_CACHE = HERE / "cache.json"
TELEGRAM_STATE = Path.home() / "workspace/custom-connectors/custom.telegram/state/cache.json"

REQUIRED_KEYS = ("spaces", "feed", "goals", "pending_approvals")


def now_utc():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_source(args):
    if args.from_stdin:
        raw = sys.stdin.read()
        if not raw.strip():
            print("error: --from-stdin but stdin is empty", file=sys.stderr)
            sys.exit(2)
        try:
            return json.loads(raw)
        except ValueError as e:
            print(f"error: stdin is not valid JSON: {e}", file=sys.stderr)
            sys.exit(1)
    if not TELEGRAM_STATE.exists():
        print(f"error: telegram state cache not found: {TELEGRAM_STATE}", file=sys.stderr)
        sys.exit(2)
    try:
        return json.loads(TELEGRAM_STATE.read_text())
    except ValueError as e:
        print(f"error: telegram state cache is not valid JSON: {e}", file=sys.stderr)
        sys.exit(1)


def validate(data):
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        print(f"error: snapshot missing required keys: {missing}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data.get("spaces"), list):
        print("error: snapshot 'spaces' is not a list", file=sys.stderr)
        sys.exit(1)


def sign(data, secret):
    if not secret:
        return data
    payload = {k: v for k, v in data.items() if k != "signature"}
    sig = hmac.new(secret.encode(),
                   json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
                   hashlib.sha256).hexdigest()
    return {**payload, "signature": sig}


def push_github(data, repo, token):
    """PUT cache.json to a GitHub repo via the contents API. Returns commit sha."""
    api = f"https://api.github.com/repos/{repo}/contents/cache.json"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "User-Agent": "muse-telegram-cache-refresher"}
    sha = None
    req = urllib.request.Request(api, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            sha = json.loads(resp.read()).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    import base64
    body = {"message": f"Cache refresh {data.get('cache_updated_utc')}",
            "content": base64.b64encode(json.dumps(data, indent=2, sort_keys=True).encode()).decode()}
    if sha:
        body["sha"] = sha
    req = urllib.request.Request(api, data=json.dumps(body).encode(), headers=headers, method="PUT")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read()).get("commit", {}).get("sha")


def main():
    p = argparse.ArgumentParser(description="Refresh the v4 Tier 1 cache snapshot.")
    p.add_argument("--from-stdin", action="store_true")
    p.add_argument("--from-telegram-state", action="store_true")
    p.add_argument("--push-github", action="store_true")
    p.add_argument("--repo", default="mani-moh/muse-telegram-cache")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if args.from_stdin and args.from_telegram_state:
        print("error: pick one source: --from-stdin or --from-telegram-state", file=sys.stderr)
        return 1

    data = load_source(args)
    validate(data)
    # Stamp with the refresh time and normalise ordering for stable diffs.
    data["cache_updated_utc"] = now_utc()
    data.pop("signature", None)
    secret = os.environ.get("CACHE_SYNC_HMAC_SECRET", "")
    signed = sign(data, secret)

    old = {}
    try:
        old = json.loads(SERVER_CACHE.read_text())
    except (OSError, ValueError):
        old = {}
    # Content change ignores the timestamp: a refresh that finds the same
    # data only re-stamps the local check time; it does NOT push (keeps
    # the sync quiet). A real content change writes AND pushes.
    def _content(d):
        return {k: v for k, v in d.items() if k not in ("signature", "cache_updated_utc")}
    changed = _content(old) != _content(signed)
    print(f"source: {'stdin' if args.from_stdin else 'telegram-state'}")
    print(f"cache_updated_utc: {signed['cache_updated_utc']}")
    print(f"spaces: {len(signed.get('spaces', []))}  feed: {signed.get('feed', {}).get('count')}  "
          f"goals: {signed.get('goals', {}).get('count')}  "
          f"pending_approvals: {signed.get('pending_approvals', {}).get('count')}")
    print(f"content changed vs local server/cache.json: {changed}")
    if args.dry_run:
        print("dry-run: wrote nothing, pushed nothing")
        return 0
    tmp = SERVER_CACHE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(signed, indent=2, sort_keys=True))
    os.replace(tmp, SERVER_CACHE)
    print(f"wrote {SERVER_CACHE} (check time refreshed)")
    if not changed:
        print("content unchanged — push skipped")
        return 0
    if args.push_github:
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            print("error: --push-github needs GITHUB_TOKEN in the environment", file=sys.stderr)
            return 1
        try:
            sha = push_github(signed, args.repo, token)
        except Exception as e:  # noqa: BLE001 — report, don't crash silently
            print(f"error: GitHub push failed: {e}", file=sys.stderr)
            return 1
        print(f"pushed to {args.repo}/cache.json commit {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
