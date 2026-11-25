"""Muse Telegram App v4 — backend server (Python stdlib only).

Serves the frontend (../frontend/) and a small JSON + SSE API.

Endpoints:
  GET  /                  -> frontend/index.html
  GET  /<static file>     -> frontend files (index.html, app.js, styles.css, commands.json)
  GET  /api/health        -> {"status":"ok", "fast_commands": [...], "cache_updated_utc": ...}
  GET  /api/commands      -> the 48 bot commands (for in-app search)
  GET  /api/cache         -> cached snapshot summary (spaces/feed/goals/ideas/library/review)
  POST /api/message       -> {"text": "...", "initData": "..."} (initData optional)
  GET  /api/events?text=  -> SSE stream: received -> checking cache -> cached | needs_muse

Two-tier latency (honest by design — see README.md):
  Tier 1 — cached commands (/help, /spaces, /review, /feed, /goals, /defaults,
    and since v4.2: /activity, /upcoming, /tasks, /notes, /library, /ideas,
    /wallet) answered from server/cache.json in ~1 ms, labelled with the
    cached time. The v4.2 additions are small verified snapshots (a count
    + a note from a live Muse run), never live data — each reply says so.
  Tier 2 — everything else: the server queues the message (server/queue.jsonl)
    and returns {"status": "needs_muse", "eta": "~10-20s"}. The reply itself
    still comes via the bot chat after a Muse wake (~10-20 s). The server
    cannot wake Muse and does not pretend to.

Auth:
  Telegram initData is validated per Telegram spec (HMAC-SHA-256, secret =
  HMAC-SHA-256(key="WebAppData", msg=bot_token)) ONLY when the bot token is
  available. The token is read ONLY from the TELEGRAM_BOT_TOKEN environment
  variable — never from a file, never from the repo, never from the frontend.
  If the variable is unset (e.g. local dev), initData is NOT validated and
  the server says so in /api/health ("auth": "unverified-no-token") and in
  every /api/message response. Cached (Tier 1) answers are public-ish
  snapshots anyway; nothing privileged is served without a token in
  production — set TELEGRAM_BOT_TOKEN before deploying.

Cache staleness — same rules as telegram_fast.py (do not change one
without the other):
  /review trusted only while cache is <= 15 min old; older => stale warning,
    never presented as fresh.
  /spaces, /feed, /goals, /defaults flagged when cache is > 60 min old,
    with the cached time shown.
  Missing/unparseable cache_updated_utc => treated as stale, age unknown.

Cache-sync bridge (v4.1, built — see deploy/CACHE_SYNC_BRIDGE.md):
  The hosted backend cannot call Muse's connectors itself, so a Muse-side
  refresher rebuilds cache.json and pushes it to a sync target; this
  server polls that target and adopts a newer, valid snapshot.
  Env: CACHE_SYNC_URL (https://.../cache.json or file://... for local
  tests; unset = bridge disabled, local seed only),
  CACHE_SYNC_INTERVAL_S (default 60), CACHE_SYNC_TOKEN (optional Bearer
  token for a private target), CACHE_SYNC_HMAC_SECRET (optional — if set,
  the remote file must carry a "signature" HMAC-SHA-256 over the
  canonical JSON without the signature field, verified before adoption).
  Rules: never adopt an unparseable file, never adopt an older snapshot,
  never present old data as fresh — /api/health reports cache_sync status
  and Tier 1 answers keep their cached-time label.

In-app reply (§10.6, v4.1): after a Tier 2 queue, the SSE stream stays
  open up to 75 s watching server/replies.jsonl for a {"queue_id",
  "reply"} line written by a Muse-side worker. If one arrives the reply
  is delivered IN the app; otherwise the stream ends with reply_timeout
  and the reply lands in the bot chat as before. Same speed, better
  surface — never claimed to be faster.

Reverse-sync (v4.4 — backend half built, see server/REVERSE_SYNC_DESIGN.md):
  on the hosted backend queue.jsonl/replies.jsonl live on the Soar box,
  so a Muse-side worker cannot write the hosted replies file directly.
  The backend now also polls REPLIES_SYNC_URL (a GitHub private-repo
  replies.jsonl, same pattern as the cache bridge) every
  REPLIES_SYNC_INTERVAL_S (default 15 s) and adopts only lines whose
  queue_id exists in its own queue.jsonl and is not already answered
  (optional per-line HMAC via REPLIES_SYNC_HMAC_SECRET, optional Bearer
  via REPLIES_SYNC_TOKEN). Adopted lines land in the local replies.jsonl
  and the open SSE stream delivers them in-app. /api/health reports
  replies_sync status. The Muse-side poller (server/reverse_sync_poller.py)
  and the worker's --to-github mode (in_app_reply_worker.py) are the
  other half — the hook-side wiring still needs John's approval, and the
  private repo + token decisions are still open.

  v4.5: the full loop is provable end-to-end locally with
  server/reverse_sync_e2e.py (queue -> poller -> worker --to-github
  dry-run + merge -> backend poller adopts -> SSE reply in-app), and
  the hosted live-test plan is written up in
  server/REVERSE_SYNC_LIVE_TEST.md. The cache repo
  mani-moh/muse-telegram-cache now EXISTS (verified live 2026-11-25:
  private, main, holds cache.json only — replies.jsonl is created by
  the first --to-github publish).

No dependencies. Run:  python3 server.py [--port 8765] [--host 127.0.0.1]
"""
import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
FRONTEND_DIR = HERE.parent / "frontend"
CACHE_FILE = HERE / "cache.json"
QUEUE_FILE = HERE / "queue.jsonl"
REPLIES_FILE = HERE / "replies.jsonl"
VERSION = "4.19.1"

REVIEW_MAX_AGE_S = 15 * 60
OTHER_MAX_AGE_S = 60 * 60
# initData older than this is rejected when a token is configured.
INITDATA_MAX_AGE_S = 5 * 60

# v4.3: Tier 1 broadened from 13 to 14 cached commands — /connectors joins
# (connected + not-connected lists come straight from server/cache.json).
FAST_COMMANDS = ["help", "spaces", "review", "feed", "goals", "defaults",
                 "activity", "upcoming", "tasks", "notes", "library",
                 "ideas", "wallet", "connectors"]

# v4.19: idea titles come from the cache note, not a hardcoded list.
# The cache stores only idea ids in "saved", but the worker-written
# "note" carries the titles in prose ("Saved ideas exist, e.g.
# idea_<32 hex> <title>, idea_<32 hex> <title>."). Parse them out of
# the note at reply time, so a newly saved idea whose title lands in
# the note shows up without a code change. The map below is only the
# fallback for ids the note does not name — never invent a title for
# an id listed in neither. Same parse as the frontend's
# parseIdeaTitles() — keep them in sync. Render time only; the
# snapshot itself is never edited.
KNOWN_IDEA_TITLES = {
    "idea_299b6569f1234467bd006eb25a6ad059": "Set your defaults from Telegram",
    "idea_18894899f1ff4b2ca60528696dfc7654": "Host your Telegram app on your AWS credits",
}


def parse_idea_titles(note):
    """Titles from the ideas cache note: idea_<32 hex> <title>, …

    Titles contain no commas in the note format, so each title runs
    to the next ", idea_" or the end; a trailing full stop is prose
    punctuation, not part of the title."""
    out = {}
    for m in re.finditer(r"idea_([0-9a-f]{32})\s+([^,]+)", str(note or "")):
        out["idea_" + m.group(1)] = m.group(2).strip().rstrip(".").strip()
    return {k: v for k, v in out.items() if v}


def idea_titles(cache):
    """id -> title for saved ideas: note-parsed first, known fallback."""
    titles = dict(KNOWN_IDEA_TITLES)
    titles.update(parse_idea_titles((cache.get("ideas") or {}).get("note", "")))
    return titles

HELP_TEXT = """Commands:
- /help - this list
- /review - pending approvals
- /spaces - list your Spaces
- /space <name> - open one Space
- /newspace <description> - build a new Space
- /share <name> - share a Space publicly
- /feed - latest Feed posts
- /feedpost <text> - write a Feed post now
- /feedsteer <instruction> - steer what the Feed covers
- /goals - your goals
- /newgoal <title> - start a new goal
- /goal <name> - one goal in detail
- /loggoal <name> <progress> - log progress on a goal
- /ideas - ideas for you
- /idea <topic> - one idea in full, then build it
- /library - your files and documents
- /doc <title> <content> - make a document
- /sheet <title> - make a spreadsheet
- /slides <title> - make a presentation
- /connectors - services you can connect
- /connect <service> - how to connect a service
- /default <category> <service> - set your default
- /defaults - your current defaults
- /activity - what Muse just did
- /upcoming - your scheduled tasks and next runs
- /tasks - tasks due today and this week
- /addtask <text> [due YYYY-MM-DD] - add a task
- /donetask <id or title> - complete a task
- /task <id or title> - one task in detail
- /calendar - today, tomorrow, free time
- /book <title> <date> <time> [guests] - book an event
- /email - unread mail that needs you, triaged
- /draft <to> <subject> <body> - save a Gmail draft
- /send <to> <subject> <body> - send, only with your go-ahead
- /wallet - your wallets and saved cards
- /find <text> - search everywhere
- /flights <from> <to> <date> - search flights
- /hotels <place> <dates> - search hotels
- /trip <description> - plan a trip
- /settings - settings and permissions in the app
Notes (Telegram Notes Space):
- /note <text> - save a note
- /notes - list your notes
- /search <text> - search your notes
- /pin <id> - pin a note
- /unpin <id> - unpin a note
- /done <id> - mark a note done
- /reopen <id> - reopen a note
- /delete <id> - delete a note
Or just chat normally."""

# 48 commands for /api/commands + frontend search. Canonical list from
# TELEGRAM_COMMANDS.md /help (40 spec + 8 Notes). Keep in sync with HELP_TEXT.
COMMANDS = [
    {"cmd": "/help", "desc": "This list — all 48 commands"},
    {"cmd": "/review", "desc": "Pending approvals and recent actions"},
    {"cmd": "/spaces", "desc": "List your Spaces"},
    {"cmd": "/space <name>", "desc": "Open one Space"},
    {"cmd": "/newspace <description>", "desc": "Build a new Space"},
    {"cmd": "/share <name>", "desc": "Share a Space publicly (only when you ask)"},
    {"cmd": "/feed", "desc": "Latest Feed posts"},
    {"cmd": "/feedpost <text>", "desc": "Write a Feed post now"},
    {"cmd": "/feedsteer <instruction>", "desc": "Steer what the Feed covers"},
    {"cmd": "/goals", "desc": "Your goals"},
    {"cmd": "/newgoal <title>", "desc": "Start a new goal"},
    {"cmd": "/goal <name>", "desc": "One goal in detail"},
    {"cmd": "/loggoal <name> <progress>", "desc": "Log progress on a goal"},
    {"cmd": "/ideas", "desc": "Ideas for you"},
    {"cmd": "/idea <topic>", "desc": "One idea in full, then build it"},
    {"cmd": "/library", "desc": "Your files and documents"},
    {"cmd": "/doc <title> <content>", "desc": "Make a document"},
    {"cmd": "/sheet <title>", "desc": "Make a spreadsheet"},
    {"cmd": "/slides <title>", "desc": "Make a presentation"},
    {"cmd": "/connectors", "desc": "Services you can connect"},
    {"cmd": "/connect <service>", "desc": "How to connect a service"},
    {"cmd": "/default <category> <service>", "desc": "Set your default for a category"},
    {"cmd": "/defaults", "desc": "Your current defaults"},
    {"cmd": "/activity", "desc": "What Muse just did"},
    {"cmd": "/upcoming", "desc": "Your scheduled tasks and next runs"},
    {"cmd": "/tasks", "desc": "Tasks due today and this week"},
    {"cmd": "/addtask <text> [due YYYY-MM-DD]", "desc": "Add a task"},
    {"cmd": "/donetask <id or title>", "desc": "Complete a task"},
    {"cmd": "/task <id or title>", "desc": "One task in detail"},
    {"cmd": "/calendar", "desc": "Today and tomorrow, and free time"},
    {"cmd": "/book <title> <date> <time> [guests]", "desc": "Book a calendar event"},
    {"cmd": "/email", "desc": "Unread mail that needs you, triaged"},
    {"cmd": "/draft <to> <subject> <body>", "desc": "Save a real Gmail draft"},
    {"cmd": "/send <to> <subject> <body>", "desc": "Send, only with your explicit go-ahead"},
    {"cmd": "/wallet", "desc": "Your wallets and saved cards"},
    {"cmd": "/find <text>", "desc": "Search everywhere: chats, files, notes, Feed"},
    {"cmd": "/flights <from> <to> <date>", "desc": "Search flights"},
    {"cmd": "/hotels <place> <dates>", "desc": "Search hotels"},
    {"cmd": "/trip <description>", "desc": "Plan a trip"},
    {"cmd": "/settings", "desc": "Your settings and permissions in the app"},
    {"cmd": "/note <text>", "desc": "Save a note (Telegram Notes)"},
    {"cmd": "/notes", "desc": "List your notes"},
    {"cmd": "/search <text>", "desc": "Search your notes"},
    {"cmd": "/pin <id>", "desc": "Pin a note"},
    {"cmd": "/unpin <id>", "desc": "Unpin a note"},
    {"cmd": "/done <id>", "desc": "Mark a note done"},
    {"cmd": "/reopen <id>", "desc": "Reopen a note"},
    {"cmd": "/delete <id>", "desc": "Delete a note"},
]

# v4.15: every command carries its tier, so in-app search can label it.
# Tier 1 = FAST_COMMANDS (answered from cache in ~1s); everything else
# is Tier 2 (needs a live Muse run). Derived here, never hand-listed
# twice — frontend/commands.json (the /api/commands fallback) mirrors
# this with a "tier" field on each entry, kept in sync by probe.
for _c in COMMANDS:
    _c["tier"] = 1 if _c["cmd"].split()[0][1:] in FAST_COMMANDS else 2


def load_cache():
    try:
        return json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        return {"cache_updated_utc": None}


def parse_cache_time(cache):
    raw = cache.get("cache_updated_utc")
    if not raw or not isinstance(raw, str):
        return None, raw
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")), raw
    except ValueError:
        return None, raw


def cache_age_s(cache):
    dt, _ = parse_cache_time(cache)
    if dt is None:
        return None
    age = time.time() - dt.timestamp()
    return max(0.0, age)


# v4.12: plain-words helpers. Cached replies are user-facing chat
# text — raw "(s)" plurals and status tokens ("not_connected") read
# like machine output. Humanize here, at reply time; the snapshot
# itself is never edited.
def plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


def humanize_note(s):
    """Plain-words rendering of a worker-written cache note.

    v4.14: the notes THEMSELVES still read like worker logs — raw ISO
    dates ("empty 2026-11-24 17:00 UTC", "verified 2026-11-24"), raw
    idea ids ("idea_299b…"), and internal jargon ("insert/patch/delete
    write path", "via runcommand /notes", "(fullstack)"). Format the
    dates into the same short "24 Nov 17:00 UTC" form fmt_time uses,
    drop the ids, and say the jargon in plain words. Same transforms
    as the frontend's humanizeNote() — keep them in sync. Render
    time only; the snapshot itself is never edited."""
    out = str(s)
    out = re.sub(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):\d{2}Z",
                 lambda m: (f"{int(m.group(3))} {MONTHS_SHORT[int(m.group(2)) - 1]} "
                            f"{int(m.group(4)):02d}:{int(m.group(5)):02d} UTC"), out)
    out = re.sub(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}) UTC",
                 lambda m: (f"{int(m.group(3))} {MONTHS_SHORT[int(m.group(2)) - 1]} "
                            f"{int(m.group(4)):02d}:{int(m.group(5)):02d} UTC"), out)
    out = re.sub(r"(\d{4})-(\d{2})-(\d{2})",
                 lambda m: f"{int(m.group(3))} {MONTHS_SHORT[int(m.group(2)) - 1]}", out)
    out = re.sub(r"idea_[0-9a-f]{32}\s*", "", out)
    out = out.replace("not_connected", "not connected").replace("not_supported", "not supported")
    out = out.replace("insert/patch/delete write path", "adding, completing and deleting tasks")
    out = out.replace(" via runcommand /notes:", ":")
    out = out.replace("(fullstack) ", "")
    out = out.replace("workspace/your_files", "your files")
    # v4.16: same stale-clause drop as the frontend's humanizeNote() —
    # the tracked goal's state note still promises Increment 2+3, which
    # shipped 2026-11-24 17:05 UTC (40 commands). The server renders
    # tracked titles only, but the transforms stay in sync for any note
    # that carries the clause. Snapshot never edited.
    out = re.sub(r";\s*next is Increment 2\+3 full features from Telegram\.?", ".", out)
    out = re.sub(r"  +", " ", out)
    return out


# v4.14: display names for connector ids in the /connectors reply —
# the same map the frontend uses (v4.8). Raw ids are still what
# /connect expects, so a not-connected row keeps its id in parens
# ("Google Calendar (google_calendar)") — the connected list is
# display names only.
SERVICE_NAMES = {
    "github": "GitHub", "gmail": "Gmail", "google_contacts": "Google Contacts",
    "google_docs": "Google Docs", "google_forms": "Google Forms",
    "google_sheets": "Google Sheets", "google_slides": "Google Slides",
    "google_tasks": "Google Tasks", "instagram_messages": "Instagram Messages",
    "meta_ads": "Meta Ads", "notion": "Notion", "telegram": "Telegram",
    "google_calendar": "Google Calendar", "google_drive": "Google Drive",
    "expedia": "Expedia", "shop-pay": "Shop Pay", "stripe-link": "Stripe Link",
}


def service_name(cid):
    return SERVICE_NAMES.get(cid, cid)


MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_time(iso):
    """Short plain-words time ("24 Nov 16:46 UTC") for a cached ISO stamp.

    v4.13: the /activity reply embedded requested_at verbatim
    ("2026-11-24T16:46:00Z") — the same raw-timestamp class v4.2
    fixed in bubbles and v4.12 fixed for "(s)"/tokens. Format here,
    at reply time; the snapshot itself is never edited."""
    if not iso or not isinstance(iso, str):
        return "unknown time"
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return "unknown time"
    return f"{d.day} {MONTHS_SHORT[d.month - 1]} {d.hour:02d}:{d.minute:02d} UTC"


def format_age(age_s):
    if age_s is None:
        return "unknown age"
    mins = int(age_s // 60)
    if mins < 1:
        return "under a minute"
    if mins < 60:
        return f"{mins} min"
    hours, mins = divmod(mins, 60)
    if hours < 24:
        return f"{hours} h {mins} min" if mins else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{plural(days, 'day', 'days')} {hours} h" if hours else plural(days, "day", "days")


# ---------- cache-sync bridge ----------
SYNC_STATE = {"status": "disabled", "last_sync_utc": None,
              "last_error": None, "last_remote_updated_utc": None}


def _canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _verify_signature(data):
    """If CACHE_SYNC_HMAC_SECRET is set, data must carry a valid signature."""
    secret = os.environ.get("CACHE_SYNC_HMAC_SECRET", "")
    if not secret:
        return True, None
    sig = data.pop("signature", None)
    if not sig:
        return False, "missing signature"
    calc = hmac.new(secret.encode(), _canonical_json(data), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, str(sig)):
        return False, "signature mismatch"
    return True, None


def _fetch_remote(url, token=""):
    """Fetch remote bytes (https or file:// for local tests)."""
    if url.startswith("file://"):
        return Path(urllib.parse.urlparse(url).path).read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "muse-telegram-app/" + VERSION})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read()


def sync_cache_once(url=None):
    """One sync attempt. Returns (adopted: bool, reason: str). Never raises."""
    url = url or os.environ.get("CACHE_SYNC_URL", "")
    if not url:
        SYNC_STATE["status"] = "disabled"
        return False, "no CACHE_SYNC_URL configured"
    try:
        raw = _fetch_remote(url, os.environ.get("CACHE_SYNC_TOKEN", ""))
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — sync must never crash the server
        SYNC_STATE["status"] = "error"
        SYNC_STATE["last_error"] = f"{type(e).__name__}: {e}"
        return False, SYNC_STATE["last_error"]
    if not isinstance(data, dict):
        SYNC_STATE["status"] = "error"
        SYNC_STATE["last_error"] = "remote cache is not a JSON object"
        return False, SYNC_STATE["last_error"]
    ok, err = _verify_signature(data)
    if not ok:
        SYNC_STATE["status"] = "error"
        SYNC_STATE["last_error"] = f"signature rejected: {err}"
        return False, SYNC_STATE["last_error"]
    remote_dt, remote_raw = parse_cache_time(data)
    if remote_dt is None:
        SYNC_STATE["status"] = "error"
        SYNC_STATE["last_error"] = "remote cache has no parseable cache_updated_utc"
        return False, SYNC_STATE["last_error"]
    local = load_cache()
    local_dt, _ = parse_cache_time(local)
    if local_dt is not None and remote_dt <= local_dt:
        SYNC_STATE["status"] = "ok"
        SYNC_STATE["last_remote_updated_utc"] = remote_raw
        SYNC_STATE["last_sync_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return False, "remote snapshot not newer than local"
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    os.replace(tmp, CACHE_FILE)
    SYNC_STATE["status"] = "ok"
    SYNC_STATE["last_remote_updated_utc"] = remote_raw
    SYNC_STATE["last_sync_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    SYNC_STATE["last_error"] = None
    return True, f"adopted remote cache updated {remote_raw}"


def sync_cache_loop():
    """Background poller. Interval from CACHE_SYNC_INTERVAL_S (default 60)."""
    try:
        interval = max(15, int(os.environ.get("CACHE_SYNC_INTERVAL_S", "60")))
    except ValueError:
        interval = 60
    while True:
        try:
            sync_cache_once()
        except Exception:  # noqa: BLE001 — poller must never die
            pass
        time.sleep(interval)


def start_sync_thread():
    if not os.environ.get("CACHE_SYNC_URL", ""):
        return
    t = threading.Thread(target=sync_cache_loop, daemon=True, name="cache-sync")
    t.start()


def sync_status():
    """Health view of the bridge: stale if no successful sync for >60 min."""
    if SYNC_STATE["status"] == "disabled":
        return {"status": "disabled",
                "note": ("Cache sync is off on this server — no CACHE_SYNC_URL is set, "
                         "so nothing is synced in or out and this snapshot is the local "
                         "seed only. On a hosted backend, set CACHE_SYNC_URL to pull a "
                         "fresh snapshot; until then the cached time above is the truth.")}
    last = SYNC_STATE.get("last_sync_utc")
    age = None
    if last:
        try:
            age = time.time() - datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
        except ValueError:
            age = None
    stale = SYNC_STATE["status"] != "ok" or (age is not None and age > 3600)
    return {"status": "stale" if stale else SYNC_STATE["status"],
            "last_sync_utc": last,
            "last_remote_updated_utc": SYNC_STATE.get("last_remote_updated_utc"),
            "last_error": SYNC_STATE.get("last_error")}


# ---------- replies reverse-sync (v4.4) ----------
# Mirror of the cache bridge, in the other direction: the hosted backend
# cannot be written to by a Muse-side worker, so replies arrive via a
# remote replies.jsonl (GitHub private repo, same as the cache bridge)
# that this server polls and adopts. Adoption rules (from
# REVERSE_SYNC_DESIGN.md): a line is adopted ONLY if its queue_id
# exists in this backend's own queue.jsonl, is not already answered,
# and is not already in the local replies.jsonl. Malformed lines,
# bad-signature lines (when REPLIES_SYNC_HMAC_SECRET is set), and
# foreign/duplicate ids are skipped, never adopted — a stale or foreign
# file cannot inject replies into the app.
REPLIES_STATE = {"status": "disabled", "last_sync_utc": None,
                 "last_error": None, "adopted": 0, "ignored": 0,
                 "rejected": 0, "last_sync_lines": None}
REPLIES_LOCAL_MAX_LINES = 500


def _answered_sidecar():
    return Path(str(QUEUE_FILE) + ".answered.json")


def _load_queue_ids():
    try:
        lines = QUEUE_FILE.read_text().splitlines()
    except OSError:
        return set()
    ids = set()
    for line in lines:
        try:
            qid = json.loads(line).get("id")
        except ValueError:
            continue
        if isinstance(qid, str):
            ids.add(qid)
    return ids


def _load_answered():
    try:
        return set(json.loads(_answered_sidecar().read_text()))
    except (OSError, ValueError):
        return set()


def _save_answered(answered):
    _answered_sidecar().write_text(json.dumps(sorted(answered)))


def _load_reply_ids():
    try:
        lines = REPLIES_FILE.read_text().splitlines()
    except OSError:
        return set()
    ids = set()
    for line in lines[-200:]:
        try:
            qid = json.loads(line).get("queue_id")
        except ValueError:
            continue
        if isinstance(qid, str):
            ids.add(qid)
    return ids


def _verify_line_signature(entry):
    """Per-line HMAC (v4.4): if REPLIES_SYNC_HMAC_SECRET is set, the line
    must carry a valid "signature" over the canonical JSON of the rest
    of the line. Same scheme as the cache bridge, per line."""
    secret = os.environ.get("REPLIES_SYNC_HMAC_SECRET", "")
    if not secret:
        return True, None
    sig = entry.pop("signature", None)
    if not sig:
        return False, "missing signature"
    calc = hmac.new(secret.encode(), _canonical_json(entry), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, str(sig)):
        return False, "signature mismatch"
    return True, None


def sync_replies_once(url=None):
    """One replies-sync attempt. Returns (adopted, ignored, rejected, reason).
    Never raises. Adopted lines are appended to the local replies.jsonl
    (so the open SSE stream delivers them in-app) and marked answered
    in the same sidecar the local worker uses."""
    url = url or os.environ.get("REPLIES_SYNC_URL", "")
    if not url:
        REPLIES_STATE["status"] = "disabled"
        return 0, 0, 0, "no REPLIES_SYNC_URL configured"
    try:
        raw = _fetch_remote(url, os.environ.get("REPLIES_SYNC_TOKEN", ""))
        text = raw.decode("utf-8")
    except Exception as e:  # noqa: BLE001 — sync must never crash the server
        REPLIES_STATE["status"] = "error"
        REPLIES_STATE["last_error"] = f"{type(e).__name__}: {e}"
        return 0, 0, 0, REPLIES_STATE["last_error"]
    queue_ids = _load_queue_ids()
    answered = _load_answered()
    have = _load_reply_ids()
    adopted = ignored = rejected = 0
    new_records = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            rejected += 1
            continue
        if not isinstance(entry, dict):
            rejected += 1
            continue
        ok, _err = _verify_line_signature(entry)
        if not ok:
            rejected += 1
            continue
        qid = entry.get("queue_id")
        reply = entry.get("reply")
        if not isinstance(qid, str) or not qid or not isinstance(reply, str) or not reply.strip():
            rejected += 1
            continue
        if qid not in queue_ids or qid in answered or qid in have:
            # Foreign id, already answered, or already adopted: normal,
            # not an error — just not adopted again.
            ignored += 1
            continue
        record = {"queue_id": qid, "reply": reply,
                  "replied_at_utc": entry.get("replied_at_utc")
                  or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                  "via": entry.get("via") or "bot-chat"}
        new_records.append(record)
        answered.add(qid)
        have.add(qid)
        adopted += 1
    if new_records:
        with REPLIES_FILE.open("a") as f:
            for record in new_records:
                f.write(json.dumps(record) + "\n")
        _save_answered(answered)
        # Cap the local replies file so it cannot grow without bound.
        try:
            lines = REPLIES_FILE.read_text().splitlines()
            if len(lines) > REPLIES_LOCAL_MAX_LINES:
                REPLIES_FILE.write_text("\n".join(lines[-REPLIES_LOCAL_MAX_LINES:]) + "\n")
        except OSError:
            pass
    REPLIES_STATE["status"] = "ok"
    REPLIES_STATE["last_sync_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    REPLIES_STATE["last_error"] = None
    REPLIES_STATE["last_sync_lines"] = len(text.splitlines())
    REPLIES_STATE["adopted"] += adopted
    REPLIES_STATE["ignored"] += ignored
    REPLIES_STATE["rejected"] += rejected
    return adopted, ignored, rejected, (
        f"adopted {adopted}, ignored {ignored} (foreign/duplicate), "
        f"rejected {rejected} (malformed/bad signature)")


def sync_replies_loop():
    """Background poller. Interval from REPLIES_SYNC_INTERVAL_S
    (default 15 — faster than the 60 s cache poll, because the SSE
    window is 75 s; a 60 s poll would usually miss it)."""
    try:
        interval = max(5, int(os.environ.get("REPLIES_SYNC_INTERVAL_S", "15")))
    except ValueError:
        interval = 15
    while True:
        try:
            sync_replies_once()
        except Exception:  # noqa: BLE001 — poller must never die
            pass
        time.sleep(interval)


def start_replies_sync_thread():
    if not os.environ.get("REPLIES_SYNC_URL", ""):
        return
    t = threading.Thread(target=sync_replies_loop, daemon=True, name="replies-sync")
    t.start()


def replies_sync_status():
    """Health view of the reverse-sync, same shape as sync_status()."""
    if REPLIES_STATE["status"] == "disabled":
        return {"status": "disabled",
                "note": ("Replies reverse-sync is off on this server — no "
                         "REPLIES_SYNC_URL is set, so in-app replies can only "
                         "arrive from a local worker writing replies.jsonl. "
                         "On a hosted backend, set REPLIES_SYNC_URL to the "
                         "GitHub replies.jsonl the Muse-side worker publishes "
                         "to; until then a Tier 2 reply lands in the bot chat.")}
    last = REPLIES_STATE.get("last_sync_utc")
    age = None
    if last:
        try:
            age = time.time() - datetime.fromisoformat(last.replace("Z", "+00:00")).timestamp()
        except ValueError:
            age = None
    stale = REPLIES_STATE["status"] != "ok" or (age is not None and age > 3600)
    return {"status": "stale" if stale else REPLIES_STATE["status"],
            "last_sync_utc": last,
            "last_sync_lines": REPLIES_STATE.get("last_sync_lines"),
            "adopted": REPLIES_STATE.get("adopted", 0),
            "ignored": REPLIES_STATE.get("ignored", 0),
            "rejected": REPLIES_STATE.get("rejected", 0),
            "last_error": REPLIES_STATE.get("last_error")}


def find_reply(queue_id):
    """Scan replies.jsonl (newest first) for a reply to queue_id. None if absent."""
    try:
        lines = REPLIES_FILE.read_text().splitlines()
    except OSError:
        return None
    for line in reversed(lines[-200:]):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("queue_id") == queue_id and entry.get("reply"):
            return entry
    return None


def stale_note(cache, age_s, max_age_s):
    dt, raw = parse_cache_time(cache)
    if dt is None:
        return "\n(Cached check time unknown — this may be out of date; ask for a fresh check if you need live data.)"
    if age_s is not None and age_s <= max_age_s:
        return None
    return (f"\n(Cached {raw} UTC, {format_age(age_s)} old — "
            "this may be out of date; ask for a fresh check if you need live data.)")


def reply_for(text, cache):
    """Tier 1 cached reply, or None if the text needs a live Muse run.

    Mirrors telegram_fast.py reply_for() (same staleness rules).
    Only exact bare commands are cached — anything with arguments
    (/space <name>, /addtask <text> ...) needs live Muse.
    """
    t = (text or "").strip().lower()
    age_s = cache_age_s(cache)
    if t in ("/help", "help"):
        return {"tier": 1, "reply": HELP_TEXT, "cached": True}
    if t in ("/spaces", "spaces"):
        spaces = cache.get("spaces", [])
        if not spaces:
            base = "You have no Spaces yet. Send /newspace <idea> to build one."
        else:
            names = ", ".join(s["name"] for s in spaces)
            base = (f"You have {plural(len(spaces), 'Space', 'Spaces')}: {names}. "
                    "Open in the Muse app under Library / sidebar.")
        note = stale_note(cache, age_s, OTHER_MAX_AGE_S)
        return {"tier": 1, "reply": base + note if note else base, "cached": True}
    if t in ("/review", "review"):
        pending = cache.get("pending_approvals", {}).get("count", 0)
        dt, raw = parse_cache_time(cache)
        if dt is None or (age_s is not None and age_s > REVIEW_MAX_AGE_S):
            return {"tier": 1, "reply": (
                f"My cached approval check is stale ({format_age(age_s)} old"
                + (f", from {raw} UTC" if raw else ", cached time unknown")
                + "). The last cached check showed "
                + (plural(pending, "approval", "approvals") + " waiting" if pending else "no approvals waiting")
                + ", but that may be out of date — a fresh check needs a full Muse run. "
                "I can't approve for you from Telegram — answer on the approval card in the Muse app."
            ), "cached": True, "stale": True}
        if pending == 0:
            return {"tier": 1, "reply": ("No approvals waiting right now. I can't approve for you "
                    "from Telegram — answer on the approval card in the Muse app."),
                    "cached": True}
        return {"tier": 1, "reply": plural(pending, "approval", "approvals") + " waiting. Answer on the approval card in the Muse app.",
                "cached": True}
    if t in ("/feed", "feed"):
        titles = cache.get("feed", {}).get("latest_titles", [])
        base = "Your Feed is empty." if not titles else "Latest Feed:\n- " + "\n- ".join(titles[:5])
        note = stale_note(cache, age_s, OTHER_MAX_AGE_S)
        return {"tier": 1, "reply": base + note if note else base, "cached": True}
    if t in ("/goals", "goals"):
        goals = cache.get("goals", {}).get("items", [])
        if not goals:
            base = "You have no goals yet. Tell me a goal and I'll track it."
        else:
            titles = [g.get("title", str(g)) if isinstance(g, dict) else str(g)
                      for g in goals[:5]]
            base = "Goals:\n- " + "\n- ".join(titles)
        note = stale_note(cache, age_s, OTHER_MAX_AGE_S)
        return {"tier": 1, "reply": base + note if note else base, "cached": True}
    if t in ("/defaults", "defaults"):
        defaults = cache.get("defaults", {})
        if not defaults:
            base = "No defaults set yet. Set one with /default <category> <service>."
        else:
            base = "Defaults:\n" + "\n".join(f"- {k}: {v}" for k, v in defaults.items())
        note = stale_note(cache, age_s, OTHER_MAX_AGE_S)
        return {"tier": 1, "reply": base + note if note else base, "cached": True}
    # ---- v4.2 Tier 1 additions: cached snapshots, always labelled ----
    if t in ("/activity", "activity"):
        decided = cache.get("recent_decided_approvals", [])
        if decided:
            lines = [f"- {d.get('decision', 'Decided')} · {fmt_time(d.get('requested_at'))}"
                     for d in decided[:5]]
            base = "Recent decided approvals (cached snapshot):\n" + "\n".join(lines)
        else:
            base = "No decided approvals in the cached snapshot."
        base += ("\n\nA full activity log needs a live Muse run — "
                 "ask again in chat and the reply lands in the bot chat (~10-20s).")
        note = stale_note(cache, age_s, OTHER_MAX_AGE_S)
        return {"tier": 1, "reply": base + note if note else base, "cached": True}
    if t in ("/upcoming", "upcoming"):
        tasks = cache.get("tasks", {})
        tracked = cache.get("tracked", [])
        parts = []
        if isinstance(tasks, dict):
            if tasks.get("note"):
                parts.append("Tasks: " + humanize_note(tasks["note"]))
            else:
                parts.append(f"Tasks: {tasks.get('count', 0)} due.")
        if tracked:
            parts.append("Tracking:\n- " + "\n- ".join(
                tr.get("title", str(tr)) if isinstance(tr, dict) else str(tr)
                for tr in tracked[:5]))
        base = "\n".join(parts) if parts else "Nothing scheduled in the cached snapshot."
        # v4.14: this used to start with a space, so the tracked title
        # and the sentence ran together ("...for Muse Next run times
        # need a live Muse run") — it is its own line now.
        base += ("\nNext run times need a live Muse run — ask again in chat "
                 "and the reply lands in the bot chat (~10-20s).")
        note = stale_note(cache, age_s, OTHER_MAX_AGE_S)
        return {"tier": 1, "reply": base + note if note else base, "cached": True}
    if t in ("/tasks", "tasks"):
        tasks = cache.get("tasks", {})
        if isinstance(tasks, dict) and tasks.get("note"):
            base = "Tasks (cached snapshot): " + humanize_note(tasks["note"])
        elif isinstance(tasks, dict):
            base = f"Tasks (cached snapshot): {tasks.get('count', 0)} due."
        else:
            base = "No task snapshot cached."
        base += " A fresh task list needs a live Muse run."
        note = stale_note(cache, age_s, OTHER_MAX_AGE_S)
        return {"tier": 1, "reply": base + note if note else base, "cached": True}
    if t in ("/notes", "notes"):
        notes = cache.get("notes", {})
        if isinstance(notes, dict) and notes.get("note"):
            base = "Notes (cached snapshot): " + humanize_note(notes["note"])
        elif isinstance(notes, dict):
            base = f"Notes (cached snapshot): {notes.get('count', 0)} saved."
        else:
            base = "No notes snapshot cached."
        note = stale_note(cache, age_s, OTHER_MAX_AGE_S)
        return {"tier": 1, "reply": base + note if note else base, "cached": True}
    if t in ("/library", "library"):
        files = cache.get("library", {}).get("files", [])
        if not files:
            base = "Nothing created yet — documents, dashboards, and other creations will appear here."
        else:
            base = "Library (cached snapshot):\n- " + "\n- ".join(files[:10])
        note = stale_note(cache, age_s, OTHER_MAX_AGE_S)
        return {"tier": 1, "reply": base + note if note else base, "cached": True}
    if t in ("/ideas", "ideas"):
        saved = cache.get("ideas", {}).get("saved", [])
        titles = idea_titles(cache)
        known = [titles[i] for i in saved if i in titles]
        unknown = len(saved) - len(known)
        if known:
            base = "Saved ideas (cached snapshot):\n- " + "\n- ".join(known)
            if unknown:
                base += f"\n(+ {plural(unknown, 'more saved idea', 'more saved ideas')} whose titles aren't in the snapshot.)"
        elif saved:
            base = plural(len(saved), "saved idea", "saved ideas") + " in the cached snapshot (titles not recorded)."
        else:
            base = "No saved ideas yet."
        note = stale_note(cache, age_s, OTHER_MAX_AGE_S)
        return {"tier": 1, "reply": base + note if note else base, "cached": True}
    if t in ("/wallet", "wallet"):
        wallet = cache.get("wallet", {})
        if isinstance(wallet, dict) and wallet.get("note"):
            base = "Wallet (cached snapshot): " + humanize_note(wallet["note"])
        else:
            base = "No wallet snapshot cached."
        base += " No tool pays from Telegram."
        note = stale_note(cache, age_s, OTHER_MAX_AGE_S)
        return {"tier": 1, "reply": base + note if note else base, "cached": True}
    if t in ("/connectors", "connectors"):
        connected = cache.get("connected_services", [])
        not_conn = cache.get("not_connected", [])
        parts = []
        if connected:
            parts.append("Connected (" + str(len(connected)) + "): " + ", ".join(service_name(c) for c in connected) + ".")
        else:
            parts.append("No connected services in the cached snapshot.")
        if not_conn:
            parts.append("Not connected: " + ", ".join(
                (f"{service_name(c)} ({c})" if service_name(c) != c else c) for c in not_conn
            ) + ". Use /connect <service> for the connect steps.")
        base = "Connectors (cached snapshot):\n" + "\n".join(parts)
        note = stale_note(cache, age_s, OTHER_MAX_AGE_S)
        return {"tier": 1, "reply": base + note if note else base, "cached": True}
    return None


def validate_init_data(init_data, token):
    """Telegram initData validation. Returns (valid, user_dict_or_None, reason).

    Secret = HMAC-SHA-256(key=b"WebAppData", msg=token). data_check_string =
    all pairs except hash, sorted by key, joined with "\\n". Compare with
    hmac.compare_digest. Rejects stale auth_date (> 5 min).
    """
    if not init_data:
        return False, None, "empty initData"
    if not token:
        return False, None, "no bot token configured on server"
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    except ValueError as e:
        return False, None, f"unparseable initData: {e}"
    recv_hash = pairs.pop("hash", None)
    if not recv_hash:
        return False, None, "no hash in initData"
    data_check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, recv_hash):
        return False, None, "hash mismatch"
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return False, None, "bad auth_date"
    if time.time() - auth_date > INITDATA_MAX_AGE_S:
        return False, None, "auth_date stale (> 5 min)"
    user = None
    if pairs.get("user"):
        try:
            user = json.loads(pairs["user"])
        except ValueError:
            user = None
    return True, user, "ok"


# v4.6: monotonic queue-id clock (see queue_message).
_last_queue_ms = 0


def queue_message(text, user):
    """Append a Tier 2 message to the queue file. Returns queue id."""
    # v4.6: ids must be unique even for two sends in the same
    # millisecond (fast double-tap / SSE+POST racing). The ms clock
    # is monotonic here: if this ms is not past the last one used,
    # step forward one ms instead of reusing the id. Format stays
    # q-<ms> so the poller, worker and live-test docs are unchanged.
    global _last_queue_ms
    ms = int(time.time() * 1000)
    if ms <= _last_queue_ms:
        ms = _last_queue_ms + 1
    _last_queue_ms = ms
    entry = {
        "id": f"q-{ms}",
        "queued_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "text": text,
        "user_id": (user or {}).get("id"),
        "status": "queued-needs-muse",
        "note": ("No live Muse reply path exists from this server yet. "
                 "Reply arrives via the bot chat after a Muse wake (~10-20s)."),
    }
    with QUEUE_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    # Cap the queue file so it cannot grow without bound on a 1 GiB box:
    # keep only the last 500 queued entries.
    try:
        lines = QUEUE_FILE.read_text().splitlines()
        if len(lines) > 1000:
            QUEUE_FILE.write_text("\n".join(lines[-500:]) + "\n")
    except OSError:
        pass
    return entry["id"]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep stdout clean; log one line per request
        print(f"{self.client_address[0]} {self.command} {self.path} -> {fmt % args}", flush=True)

    # -- helpers ---------------------------------------------------------
    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, content_type):
        try:
            data = path.read_bytes()
        except OSError:
            self.send_error(404, "Not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth(self, init_data):
        """Returns (valid, user, reason). No token configured => unverified, not an error."""
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            return False, None, "unverified — no TELEGRAM_BOT_TOKEN set on this server"
        return validate_init_data(init_data or "", token)

    # -- routes ----------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
        cache = load_cache()
        _, cache_raw = parse_cache_time(cache)

        if path in ("/", "/index.html"):
            self._send_file(FRONTEND_DIR / "index.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._send_file(FRONTEND_DIR / "app.js", "text/javascript; charset=utf-8")
        elif path == "/styles.css":
            self._send_file(FRONTEND_DIR / "styles.css", "text/css; charset=utf-8")
        elif path == "/commands.json":
            self._send_file(FRONTEND_DIR / "commands.json", "application/json")
        elif path == "/api/health":
            token_set = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
            self._send_json({
                "status": "ok", "version": VERSION,
                "fast_commands": ["/" + c for c in FAST_COMMANDS],
                "cache_updated_utc": cache_raw,
                "cache_age": format_age(cache_age_s(cache)),
                "cache_sync": sync_status(),
                "replies_sync": replies_sync_status(),
                "auth": "initData HMAC validation active" if token_set else "unverified-no-token",
                "latency": "Tier 1 cached ~1 ms server-side; Tier 2 live replies via bot chat ~10-20s (or in-app if a reply file arrives)",
            })
        elif path == "/api/commands":
            self._send_json({"count": len(COMMANDS), "commands": COMMANDS})
        elif path == "/api/cache":
            self._send_json({
                "cache_updated_utc": cache_raw,
                "cache_age": format_age(cache_age_s(cache)),
                "cache_sync": sync_status(),
                "review_stale": cache_age_s(cache) is None or cache_age_s(cache) > REVIEW_MAX_AGE_S,
                "other_stale": cache_age_s(cache) is None or cache_age_s(cache) > OTHER_MAX_AGE_S,
                "spaces": cache.get("spaces", []),
                "pending_approvals": cache.get("pending_approvals", {}),
                "feed": cache.get("feed", {}),
                "goals": cache.get("goals", {}),
                "ideas": cache.get("ideas", {}),
                "library": cache.get("library", {}),
                "defaults": cache.get("defaults", {}),
                "connected_services": cache.get("connected_services", []),
                "not_connected": cache.get("not_connected", []),
                # v4.2: extra cached snapshots for the new views
                # (Upcoming / Activity / Notes) and the panel history.
                "notes": cache.get("notes", {}),
                "tasks": cache.get("tasks", {}),
                "tracked": cache.get("tracked", []),
                "recent_decided_approvals": cache.get("recent_decided_approvals", []),
                "wallet": cache.get("wallet", {}),
            })
        elif path == "/api/reply":
            # Polling fallback for the in-app reply: GET /api/reply?queue_id=q-...
            qid = (qs.get("queue_id") or [""])[0]
            if not qid:
                self._send_json({"error": "queue_id is required"}, status=400)
            else:
                entry = find_reply(qid)
                self._send_json({"queue_id": qid, "found": entry is not None,
                                 "reply": entry.get("reply") if entry else None,
                                 "replied_at_utc": entry.get("replied_at_utc") if entry else None})
        elif path == "/api/queue":
            # v4.3 diagnostic (v4.4 groundwork): how many Tier 2 messages are
            # queued, and the most recent few (your own messages only — this
            # server serves one user). Lets a worker find a queue_id to answer.
            try:
                lines = QUEUE_FILE.read_text().splitlines()
            except OSError:
                lines = []
            entries = []
            for line in lines[-5:]:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
            self._send_json({"count": len(lines), "recent": entries})
        elif path == "/api/events":
            text = (qs.get("text") or [""])[0]
            init_data = (qs.get("initData") or [""])[0]
            valid, user, reason = self._auth(init_data) if init_data else (False, None, "no initData sent")
            self._sse(text, valid, user, reason)
        else:
            self.send_error(404, "Not found")

    def _sse(self, text, auth_valid, user, auth_reason):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def event(payload):
            line = "data: " + json.dumps(payload) + "\n\n"
            self.wfile.write(line.encode())
            self.wfile.flush()

        try:
            event({"event": "received", "text": text, "auth_valid": auth_valid, "auth": auth_reason})
            time.sleep(0.2)
            event({"event": "checking-cache", "text": text})
            time.sleep(0.2)
            cache = load_cache()
            fast = reply_for(text, cache)
            if fast:
                event({"event": "cached", "tier": 1, "reply": fast["reply"],
                       "cache_updated_utc": cache.get("cache_updated_utc"),
                       "stale": fast.get("stale", False)})
            else:
                qid = queue_message(text, user)
                event({"event": "needs_muse", "tier": 2, "queue_id": qid,
                       "eta": "~10-20s",
                       "message": ("This needs your live Muse — reply lands in the bot chat "
                                   "in ~10-20s, or open Muse for an instant live chat.")})
                # In-app reply (§10.6): keep the stream open up to 75 s
                # watching replies.jsonl. A Muse-side worker appends
                # {"queue_id": qid, "reply": "..."} when it answers; the
                # reply then streams into the open app. Same speed as bot
                # chat — the gain is the surface, not the latency.
                deadline = time.time() + 75
                while time.time() < deadline:
                    time.sleep(2)
                    entry = find_reply(qid)
                    if entry:
                        event({"event": "reply", "tier": 2, "queue_id": qid,
                               "reply": entry["reply"],
                               "replied_at_utc": entry.get("replied_at_utc")})
                        break
                else:
                    event({"event": "reply_timeout", "tier": 2, "queue_id": qid,
                           "message": "No in-app reply arrived in 75 s — the reply lands in the bot chat instead."})
            # End the stream cleanly after the terminal event. Without this
            # the handler returns with keep-alive and the browser's
            # EventSource reconnects, re-running the request (and re-queueing
            # a Tier 2 message). The client also closes on terminal events.
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/message":
            self.send_error(404, "Not found")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            self._send_json({"error": "invalid JSON body"}, status=400)
            return
        text = (body.get("text") or "").strip()
        if not text:
            self._send_json({"error": "text is required"}, status=400)
            return
        init_data = body.get("initData") or ""
        valid, user, reason = self._auth(init_data) if init_data else (False, None, "no initData sent")
        cache = load_cache()
        fast = reply_for(text, cache)
        if fast:
            self._send_json({
                "status": "cached", "tier": 1, "reply": fast["reply"],
                "cache_updated_utc": cache.get("cache_updated_utc"),
                "cache_age": format_age(cache_age_s(cache)),
                "stale": fast.get("stale", False),
                "auth_valid": valid, "auth": reason,
            })
            return
        qid = queue_message(text, user)
        self._send_json({
            "status": "needs_muse", "tier": 2, "queue_id": qid, "eta": "~10-20s",
            "message": ("This needs your live Muse. Reply lands in the bot chat "
                        "in ~10-20s — or open Muse for an instant live chat."),
            "auth_valid": valid, "auth": reason,
        })


def main():
    import argparse
    p = argparse.ArgumentParser(description="Muse Telegram App v4 backend (stdlib only)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    token_set = bool(os.environ.get("TELEGRAM_BOT_TOKEN"))
    print(f"Muse Telegram App v4 backend {VERSION} — http://{args.host}:{args.port}/", flush=True)
    print(f"  frontend: {FRONTEND_DIR}", flush=True)
    print(f"  cache: {CACHE_FILE} (updated {load_cache().get('cache_updated_utc')})", flush=True)
    print("  auth: initData HMAC validation ACTIVE" if token_set else
          "  auth: UNVERIFIED — TELEGRAM_BOT_TOKEN not set (local dev only; set it before deploying)", flush=True)
    sync_url = os.environ.get("CACHE_SYNC_URL", "")
    print(f"  cache-sync: {sync_url or 'disabled (no CACHE_SYNC_URL — serving local seed only)'}", flush=True)
    replies_url = os.environ.get("REPLIES_SYNC_URL", "")
    print(f"  replies-sync: {replies_url or 'disabled (no REPLIES_SYNC_URL — in-app replies only from a local worker)'}", flush=True)
    start_sync_thread()
    start_replies_sync_thread()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
