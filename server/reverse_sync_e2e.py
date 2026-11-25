#!/usr/bin/env python3
"""Reverse-sync end-to-end proof (v4.5, re-verified in v4.8) — the FULL loop, locally.

Runs every hop of the hosted reverse-sync design in an isolated temp
dir (a copy of server.py + cache.json + the two Muse-side scripts, so
the real server/ dir is never touched):

  1. Tier 2 message queued by the backend (POST /api/message)
     -> queue_id, SSE stream opened, `needs_muse` seen
  2. reverse_sync_poller.py --once reports NEW <queue_id>
     (the read side — prints, never wakes Muse, never touches the hook)
  3. in_app_reply_worker.py --to-github --dry-run prints the exact
     publish line for that queue_id; the line is then merged into the
     "remote" replies.jsonl with the worker's own merge_replies() —
     the same merge publish_to_github() performs after the GitHub API
     read. The GitHub API round-trip itself is NOT exercised here:
     it needs GITHUB_TOKEN + the real repo, and is covered by the
     hosted live-test plan (server/REVERSE_SYNC_LIVE_TEST.md).
  4. Backend poller (REPLIES_SYNC_URL=file://..., 5 s) adopts the
     line -> GET /api/reply?queue_id=... found=True
  5. The open SSE stream delivers the `reply` event in-app
  6. No duplicate re-append after another poll interval

Run: python3 reverse_sync_e2e.py [--port 8767] [--keep]
Exit 0 iff every step passes. Prints a timed line per step.
"""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
QUESTION = "what is my next goal?"
REPLY = "Your next goal is the Iran internet blackout emergency setup — it is active."


def log(step, msg):
    print(f"[{time.strftime('%H:%M:%S')}] {step}: {msg}", flush=True)


def post_message(base, text):
    req = urllib.request.Request(base + "/api/message",
                                 data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def get_json(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as resp:
        return json.loads(resp.read())


def sse_watch(base, text, out_q, stop_event):
    """Open an SSE stream; put parsed events on out_q until stopped."""
    url = base + "/api/events?text=" + urllib.parse.quote(text)
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=60) as resp:
            buf = ""
            while not stop_event.is_set():
                chunk = resp.read(1)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", "replace")
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    for line in block.splitlines():
                        if line.startswith("data: "):
                            try:
                                out_q.put(json.loads(line[6:]))
                            except ValueError:
                                pass
    except Exception as e:  # noqa: BLE001 — stream end / timeout is expected
        out_q.put({"event": "stream_error", "error": str(e)})


def main():
    ap = argparse.ArgumentParser(description="Prove the reverse-sync loop end to end, locally.")
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--keep", action="store_true", help="keep the temp dir for inspection")
    args = ap.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    tmp = Path(tempfile.mkdtemp(prefix="v45-e2e-"))
    ok = True

    def fail(step, detail):
        nonlocal ok
        ok = False
        log(step, f"FAIL — {detail}")

    try:
        for f in ("server.py", "cache.json", "reverse_sync_poller.py", "in_app_reply_worker.py"):
            shutil.copy(HERE / f, tmp / f)
        remote = tmp / "remote-replies.jsonl"
        remote.write_text("")
        env = dict(os.environ, TELEGRAM_BOT_TOKEN="LOCAL_TEST_ONLY_NOT_A_REAL_TOKEN",
                   REPLIES_SYNC_URL=f"file://{remote}",
                   REPLIES_SYNC_INTERVAL_S="5")
        env.pop("GITHUB_TOKEN", None)
        env.pop("REPLIES_SYNC_HMAC_SECRET", None)
        srv = subprocess.Popen([sys.executable, "server.py", "--port", str(args.port)],
                               cwd=tmp, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(1.5)
            # Step 0: backend up, replies sync enabled. v4.8: the
            # version check is semver-agnostic (the old pinned
            # "4.7.0" failed on every bump, like the v4.5/v4.6 suites).
            import re as _re
            health = get_json(base, "/api/health")
            if not _re.fullmatch(r"\d+\.\d+\.\d+", str(health.get("version", ""))) or health.get("replies_sync", {}).get("status") not in ("ok", "disabled"):
                fail("0-backend", f"unexpected health: {health.get('version')} {health.get('replies_sync')}")
            else:
                log("0-backend", f"up, version {health['version']}, replies_sync={health['replies_sync']['status']}")

            # Step 1: open the SSE stream first (like the open app), then queue
            out_q = queue.Queue()
            stop_event = threading.Event()
            t = threading.Thread(target=sse_watch, args=(base, QUESTION, out_q, stop_event), daemon=True)
            t.start()
            sse_qid = None
            events = []
            deadline = time.time() + 10
            while time.time() < deadline and not sse_qid:
                try:
                    ev = out_q.get(timeout=1)
                    events.append(ev.get("event"))
                    if ev.get("event") == "needs_muse":
                        sse_qid = ev.get("queue_id")
                except queue.Empty:
                    pass
            if not sse_qid:
                fail("1-queue", f"no needs_muse event, saw {events}")
                return 1
            log("1-queue", f"Tier 2 queued, queue_id={sse_qid} (events: {events})")

            # Step 2: the Muse-side poller sees it (read-only)
            seen = tmp / "seen.json"
            r = subprocess.run([sys.executable, "reverse_sync_poller.py", "--once",
                                "--base", base, "--state", str(seen)],
                               cwd=tmp, capture_output=True, text=True, env=env)
            if r.returncode != 0 or f"NEW {sse_qid}" not in r.stdout:
                fail("2-poller", f"rc={r.returncode} stdout={r.stdout.strip()!r} stderr={r.stderr.strip()!r}")
            else:
                log("2-poller", f"poller reports NEW {sse_qid} (read-only, no wake)")
            # ... and stays silent on a second run (seen ids are not re-reported)
            r2 = subprocess.run([sys.executable, "reverse_sync_poller.py", "--once",
                                 "--base", base, "--state", str(seen)],
                                cwd=tmp, capture_output=True, text=True, env=env)
            if r2.returncode != 0 or r2.stdout.strip() != "":
                fail("2b-poller-quiet", f"second run rc={r2.returncode} stdout={r2.stdout.strip()!r}")
            else:
                log("2b-poller-quiet", "second poller run silent (seen ids not re-reported)")

            # Step 3: worker --to-github dry-run prints the publish line;
            # merge that exact line into the remote file with the worker's
            # own merge_replies() (the GitHub API round-trip is the one
            # hop this local proof does NOT exercise — see module docstring).
            w = subprocess.run([sys.executable, "in_app_reply_worker.py",
                                "--queue-file", "queue.jsonl", "--to-github",
                                "--queue-id", sse_qid, "--reply", REPLY, "--dry-run"],
                               cwd=tmp, capture_output=True, text=True, env=env)
            line = None
            if w.returncode == 0 and "line to publish: " in w.stdout:
                try:
                    line = json.loads(w.stdout.split("line to publish: ", 1)[1].splitlines()[0])
                except ValueError:
                    line = None
            if w.returncode != 0 or not line or line.get("queue_id") != sse_qid:
                fail("3-worker", f"rc={w.returncode} stdout={w.stdout.strip()!r} stderr={w.stderr.strip()!r}")
                return 1
            sys.path.insert(0, str(tmp))
            from in_app_reply_worker import merge_replies
            remote.write_text(merge_replies(remote.read_text(), line))
            log("3-worker", f"--to-github dry-run ok, line merged into remote file (queue_id={line['queue_id']})")

            # Step 4: backend poller adopts the line
            adopted = False
            deadline = time.time() + 15
            while time.time() < deadline:
                rep = get_json(base, f"/api/reply?queue_id={sse_qid}")
                if rep.get("found") is True and rep.get("reply") == REPLY:
                    adopted = True
                    break
                time.sleep(1)
            rs = get_json(base, "/api/health").get("replies_sync", {})
            if not adopted:
                fail("4-adopt", f"/api/reply never found it; replies_sync={rs}")
            else:
                log("4-adopt", f"backend adopted the line (replies_sync={rs.get('status')}, adopted={rs.get('adopted')})")

            # Step 5: the open SSE stream delivers the reply in-app
            got_reply = False
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    ev = out_q.get(timeout=1)
                    events.append(ev.get("event"))
                    if ev.get("event") == "reply" and ev.get("queue_id") == sse_qid:
                        got_reply = REPLY in (ev.get("reply") or "")
                        break
                except queue.Empty:
                    pass
            stop_event.set()
            if not got_reply:
                fail("5-sse-reply", f"no in-app reply event, saw {events}")
            else:
                log("5-sse-reply", f"reply event arrived in-app (events: {events})")

            # Step 6: no duplicate re-append after another poll interval
            before = len((tmp / "replies.jsonl").read_text().splitlines())
            time.sleep(6)
            after = len((tmp / "replies.jsonl").read_text().splitlines())
            if before != after:
                fail("6-no-duplicate", f"local replies lines {before} -> {after}")
            else:
                log("6-no-duplicate", f"local replies.jsonl still {after} line(s) after a second sync")
        finally:
            srv.terminate()
            try:
                srv.wait(timeout=5)
            except subprocess.TimeoutExpired:
                srv.kill()
    finally:
        if args.keep:
            log("done", f"temp dir kept: {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nreverse-sync e2e: {'ALL 7 STEPS PASS' if ok else 'FAILED'} "
          f"(queue -> poller -> worker merge -> adopt -> SSE reply in-app)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
