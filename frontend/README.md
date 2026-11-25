# Muse Telegram App v4 — frontend (BUILT 2026-11-24)

**Status: BUILT 2026-11-24 as part of the dynamic v4 app
(frontend + backend). NOT deployed — see the project README
(`../README.md`) for hosting decisions still needed from John.**
The pause is over: the architecture investigation returned
(`APP_V4_RESEARCH.md` §8) and the rebuild is done. No Telegram
messages sent, no `artifact.share`, no secrets in files.

## What changed

- v4 is **NOT** a single self-contained static HTML file. John's
  correction: "The telegram mini app doesn't have to be static. it can
  be just like any other telegram app. It should just be a new app
  through Telegram."
- v4 is a **real dynamic app: frontend + backend** (server with
  state/database, live updates via SSE/WebSocket, server-side logic).
  The backend is now the expected route to removing the delay, not a
  workaround.
- Bot chat remains **backup only**; `tg.sendData` remains
  **backup-only** (tap-gated, labelled slow).
- Primary flow (intended): frontend → backend → (fast-path / live
  Muse) → push/stream back to the app.
- **No hosting signed up for, spent on, or deployed to yet.**
  No Telegram messages sent, no `artifact.share`, no secrets in files.

## Frontend work done (v4 build, 2026-11-24)

Built, local-tested (see `../README.md` for measured timings):

- `index.html` — app shell: centred avatar header, hamburger drawer,
  5 views (Chat / Feed / Ideas / Goals / Library), search view,
  avatar panel, pill composer + 5-icon pill tab bar.
- `app.js` — `fetch POST /api/message` + `EventSource /api/events`
  (SSE); two-tier UI (Tier 1 cached ~1s with cached time; Tier 2
  "Muse is waking up — reply lands in the bot chat in ~10-20s");
  escape hatch (copy-to-clipboard + `tg.openLink("https://muse.ai")`);
  `tg.sendData` ONLY on the explicit tap-gated bot button; chips
  prefill only, never send; search over all 48 commands.
- `styles.css` — Muse Android UI: light + dark via Telegram theme
  variables, pale-blue user vs light-grey Muse bubbles, hairline
  lists, large pill composer, floating pill tab bar.
- `commands.json` — all 48 commands (same list as `GET /api/commands`).

Research + inventory done before the pause (kept, still accurate):

1. Read `~/workspace/goals/telegram-bot-for-muse/APP_V4_RESEARCH.md`
   in full (Muse Android UI research, 2026-11-24).
2. Read `~/workspace/custom-connectors/custom.telegram/TELEGRAM_COMMANDS.md`
   in full; inventoried the command set:
   - 40 spec commands under `## /…` headings
     (`/help /review /activity /upcoming /tasks /addtask /donetask
     /task /calendar /book /email /draft /send /spaces /space
     /newspace /share /connectors /connect /default /defaults
     /feed /feedpost /feedsteer /goals /newgoal /goal /loggoal
     /ideas /idea /library /wallet /find /doc /sheet /slides
     /flights /hotels /trip /settings`)
   - + 8 Telegram Notes commands
     (`/note /notes /search /pin /unpin /done /reopen /delete`)
     = **48 commands total** (matches the live bot menu).
3. Read `state/cache.json` (verified snapshot, 2026-11-24 17:05 UTC)
   for the only verified live values to show:
   - Goal: "Iran internet blackout emergency setup"
     (`goal_b74506dfa7b2`, active) — the one verified user goal.
   - Ideas: "Set your defaults from Telegram"
     (`idea_299b6569f1234467bd006eb25a6ad059`) and
     "Host your Telegram app on your AWS credits"
     (`idea_18894899f1ff4b2ca60528696dfc7654`) — the only verified
     saved ideas; present as suggestions, nothing invented.
   - Library: empty ("Nothing created yet" — no files in
     `workspace/your_files/`).
   - Feed: 12 units (9 intro + 3 generated 2026-11-24); do not
     hard-code titles — fetch live from the backend.
   - Pending approvals: 0 at last check — never hard-code a count;
     show live state from the backend.
   - Spaces: do NOT hard-code names; list live via `/spaces`
     (backend / `artifact.list`).
4. Read `DEVELOPMENT_PROMPT.md` and
   `~/workspace/custom-connectors/custom.telegram/README.md`
   (parity matrix, changelog, speed honesty, safeguards).

## v4.19.1 (2026-11-25 ~13:00 +0330)

- **"Open in Muse" button out of the chat, into Settings** (John:
  "Remove the open in mus anoyying button and put it in settings.
  Its block the vision."): the filled button sat in the chat's tier
  bar (boot + after every live question) and blocked the view. The
  boot tier bar now carries the latency text only, and
  `tier2Buttons()` renders only the tap-gated "Send via Telegram bot
  instead" ghost button. The waiting/timeout notice points at
  Settings instead. The button lives ONLY in Settings now (the row
  already existed); the drawer Channels row stays — it is hidden
  until the drawer opens and doesn't block the chat. Avatar-panel
  approval buttons and Soul/Memory rows still open Muse (panels,
  not the chat).
- Probes: `../screenshots/test_v4191.py` 11/11 (first run green);
  test_v47.py probes 3–4 updated to the new behaviour; all older
  suites green on 4.19.1 (317 probes total). New screenshots:
  73-main-chat-no-button, 74-tier2-waiting-no-button,
  75-settings-open-muse, 76-settings-open-muse-row (all viewed).

## v4.19 (2026-11-25 ~05:25 +0330)

- **Idea titles come from the cache note, not a hardcoded list:**
  the titles were hardcoded in three places (server IDEA_TITLES,
  app.js Ideas render, app.js search index) while the cache note
  already carries them in prose ("Saved ideas exist, e.g.
  idea_<32 hex> <title>, …"). Both halves now parse titles out of
  the note at render time (server `parse_idea_titles()` /
  frontend `parseIdeaTitles()`, same regex, kept in sync); the
  known map is only the fallback for ids the note does not name.
  A newly saved idea whose title lands in the note shows up with
  no code change — proven with a synthetic third idea on a
  throwaway server copy (appears in /ideas, the Ideas view, and
  search). An id with no title anywhere gets no invented title:
  /ideas and the Ideas view say so honestly. Source guards forbid
  a second title list outside the fallback maps.
- Probes: `../screenshots/test_v419.py` 18/18 (first run 17/18 —
  two probe bugs: Search is not a tab-bar tab, and the
  untitled-line guard matched a literal apostrophe against the
  source's escaped one); all older suites green on 4.19.0 (see
  project README). New screenshot: 72-ideas-search (search
  "defaults" — the saved-idea hit from the parsed map).

## v4.18 (2026-11-25 ~04:37 +0330)

- **Calendar view (new):** Calendar was the only parity-matrix
  capability with no view — only a chip prefilling /calendar and
  the "Book an event" row inside Upcoming. New `view-calendar`
  honestly says no calendar is cached, with Check my calendar
  (Prefills /calendar — today and tomorrow, plus free time, Tier 2)
  and Book an event (Prefills /book — title, date, time, Tier 2;
  "Works once Google Calendar is connected", matching the cache as
  it stands) rows; drawer Calendar row under MUSE opens it.
- **Tier-label wording is one form now:** the Goals "See the live
  timeline" row and the Library Media "Create an image" row used
  the comma form "(Tier 2, reply in-app within ~75s, else bot
  chat)" while every other Tier 2 row uses the em-dash form
  "(Tier 2 — reply in-app within ~75s, else bot chat)". Both now
  use the standard form; a source guard + a rendered-page guard
  forbid the comma form anywhere in the frontend. (The Tier 1 form
  "(Tier 1, cached ~1s)" legitimately has a comma.)
- Probes: `../screenshots/test_v418.py` 21/21 (first run 19/21 —
  both failures were probe bugs: an over-broad comma guard and a
  drawer left open intercepting a tab click); all older suites
  green on 4.18.0 (see project README). Tier-label audit on the
  touched rows: DOM-wide probe (every rendered "Prefills /cmd"
  row) + source audit + API truth.

## v4.17 (2026-11-25 ~04:22 +0330)

- **Tasks view (new):** Tasks had no view — only a chip prefilling
  /tasks and the Tasks row inside Upcoming. New `view-tasks` renders
  the cached tasks note (humanized, same row as Upcoming — same cache
  field, so both stay in sync) + List (Tier 1) / Add / Complete /
  Detail (Tier 2) rows; drawer Tasks row under MUSE opens it.
- **Email view (new):** Email also had no view (only the /email
  chip). New `view-email` honestly says no inbox is cached, with
  Check / Draft / Send rows, all Tier 2; Send needs an explicit
  go-ahead and an approval card in the Muse app. Drawer Email row
  under MUSE opens it.
- **"YYYY-MM-DD" becomes plain words:** the last raw machine format
  in user-facing prose (Upcoming "Add a task" desc) now reads "write
  it year-month-day, e.g. 2026-11-25"; same wording in the new Tasks
  view. Command syntax keeps YYYY-MM-DD.
- **Drawer tier labels:** Activity, Upcoming, Connectors, Wallet, My
  notes and All Spaces descs now carry "(Tier 1, cached ~1s)";
  Activity's "live check in chat" wording is gone.
- Probes: `../screenshots/test_v417.py` 29/29 (first run); all older
  suites green on 4.17.0 (see project README). Tier-label audit on
  the touched rows: DOM-wide probe (every rendered "Prefills /cmd"
  row) + source audit + API truth.

## v4.16 (2026-11-25 ~04:10 +0330)

- **Prefill-row tier audit finished:** the v4.10 audit (rows) and
  v4.15 audit (search hits) never reached the "Prefills /cmd" rows —
  12 static rows in `index.html` and 4 generated rows in `app.js`
  said "Prefills /cmd — …" with no tier at all (/newgoal, /settings,
  /addtask, /book, /note ×2, /search, /pin, /done, /delete, /newspace,
  /feedsteer; plus the generated /review pending row, /loggoal, /idea
  Run desc, /connect not-connected rows). Every prefill row now
  states its tier in the same words the rows use elsewhere. The
  labels were verified at the API level first: POST /settings,
  /newgoal, /addtask, /book, /note, /search, /pin, /done, /delete,
  /newspace, /feedsteer, /loggoal, /connect, /goal, /idea all return
  needs_muse / tier 2; /review returns cached / tier 1.
- **Waiting line goes SVG:** the tier bar's "Muse is waking up"
  line still used the hourglass colour emoji — the last one left
  after the v4.11 SVG unification (shot 05). All four sites now use
  the line-SVG clock from the ICON map, with a new `#tierBar
  .tier-text svg` sizing rule. No hourglass emoji remains in the
  frontend source or the rendered page.
- **Tracked note stops promising a shipped increment:** the Upcoming
  "Telegram bot for Muse" row rendered its state note verbatim,
  still saying "next is Increment 2+3 full features from Telegram"
  (shipped 2026-11-24 17:05 UTC: 40 commands). `humanizeNote()` /
  `humanize_note()` drop that clause at render time; the true first
  clause stays and the snapshot is never edited. The source note
  itself is still stale and should be refreshed by a worker.
- Probes: `../screenshots/test_v416.py` 24/24 (first run 23/24 —
  a probe bug, not an app bug: it read `#goalsList` for the
  "Create a goal" row, which is a sibling button, not inside the
  list); all older suites green on 4.16.0 (see project README).
  Tier-label audit on the touched rows: DOM-wide probe (every
  rendered "Prefills /cmd" row) + source audit + API truth.

## v4.15 (2026-11-25 ~03:45 +0330)

- **Search results state their tier now:** the one place the v4.10
  tier audit never reached — a search hit showed no tier, so
  /upcoming (Tier 1, ~1s) and /addtask (Tier 2, live) looked
  identical. Every hit now carries its tier in the same words the
  rows use elsewhere: command hits "(Tier 1, cached ~1s)" for the 14
  FAST_COMMANDS or "(Tier 2 — reply in-app within ~75s, else bot
  chat)" for the rest; Feed / Space / Goal / Idea hits are all Tier 2
  (each tap prefills free text or a Tier 2 command). The suffix is
  part of the indexed desc, so "tier 1", "tier 2" and "cached" are
  searchable too — searching "tier 1" lists only the 14 cached
  commands. The tier lives on the command entry itself ("tier": 1/2):
  `/api/commands` derives it from FAST_COMMANDS (never hand-listed
  twice); `commands.json` (the fallback) mirrors it, kept in sync by
  probe.
- **"(Tier 1, cached)" fixed:** the Connectors and Wallet rows still
  lacked the "~1s" every other Tier 1 row has.
- Probes: `../screenshots/test_v415.py` 17/17 (first run stopped at
  the search-tab click with 2 probe failures, both probe bugs not app
  bugs — the "Check …" rows live outside the #…List containers, and
  Search is not a tab-bar tab); all older suites green on 4.15.0 (see
  project README). Tier-label audit on the touched rows: search hits
  match FAST_COMMANDS; "Check connectors" / "Check wallet status"
  still Tier 1.

## v4.14 (2026-11-25 ~03:20 +0330)

- **The cache notes themselves read like plain words now:** the
  views were plain words but the notes were still rendered verbatim
  — Tasks note ("empty 2026-11-24 17:00 UTC; insert/patch/delete
  write path…"), Notes note ("Telegram Notes (fullstack) verified
  live 2026-11-24 via runcommand /notes: empty."), Wallet note
  ("(verified 2026-11-24)"), Feed note ("12 units 2026-11-24: …
  from 2026-11-23 + … from 2026-11-24."), Ideas note carrying raw
  `idea_<32 hex>` ids. `humanizeNote()` (extended from v4.12) and
  the server's `humanize_note()` now share the same transforms:
  ISO datetimes and bare dates → short "24 Nov 17:00 UTC" /
  "24 Nov" form, raw idea ids dropped, "insert/patch/delete write
  path" → "adding, completing and deleting tasks", "via runcommand
  /notes:" → ":", "(fullstack)" dropped, "workspace/your_files" →
  "your files". Applied at all four verbatim frontend sites and
  the three verbatim server replies (/upcoming, /tasks, /notes);
  the snapshot itself is never edited.
- **/upcoming run-on fixed:** the tracked title and the "Next run
  times" sentence ran together ("…for Muse Next run times need a
  live Muse run") — the sentence starts on its own line now.
- **/connectors reply uses display names:** it still listed raw
  ids while the app views went display-name in v4.8. Connected
  list is display names only; not-connected rows keep the raw id
  in parens ("Google Calendar (google_calendar)") because that id
  is still what /connect needs.
- **Goals timeline title no longer orphans "UTC":** v4.13 shot 56
  showed "Tasks — checked 24 Nov 17:00" with "UTC" alone on the
  next line; the time chunk is now a `.nowrap` span in both Tasks
  titles.
- Probes: `../screenshots/test_v414.py` 24/24 (first run,
  including the derived-time proof: a cache whose tasks note says
  "2026-11-25 09:30 UTC" moves both the Upcoming desc and the
  /tasks reply to "25 Nov 09:30 UTC"); all older suites green on
  4.14.0 (see project README). Tier-label audit on the touched
  rows: "Check upcoming" still Tier 1, "See the live timeline"
  still Tier 2.

## v4.13 (2026-11-25 ~03:10 +0330)

- **Timestamps read like plain words now:** the server's /activity
  reply embedded the raw ISO stamp ("Allowed ·
  2026-11-24T16:46:00Z") — now "Allowed · 24 Nov 16:46 UTC" via a
  new `fmt_time()` in `server.py` (same short form as the
  frontend's `fmtTime`); the snapshot is never edited.
- **Upcoming + Goals timeline titles are plain words:** "Tasks —
  24 Nov 17:00 UTC" → "Tasks — checked 24 Nov 17:00 UTC";
  "Snapshot checked — 24 Nov 23:27 UTC" → "No scheduled-task
  list" (Upcoming, check time moved into the desc) and "Goal
  status" (Goals timeline, check time in the desc); "Tasks
  snapshot — 24 Nov 17:00 UTC" → "Tasks — checked 24 Nov
  17:00 UTC".
- **The Tasks time is derived, not hardcoded:** new
  `tasksCheckedTime()` in `frontend/app.js` reads the timestamp
  out of the tasks note itself. The old code matched a literal
  /2026-11-24 17:00 UTC/, so the Goals timeline's Tasks row would
  have vanished the moment the refresher stamped a new time, and
  the Upcoming title would have gone stale. Proven by a second
  server running a cache whose note says "2026-11-25 09:30 UTC":
  both titles follow it.
- Probes: `../screenshots/test_v413.py` 19/19 (18/18 first run, +1
  for the /activity reply line-break fix caught by looking at shot
  58); all older suites green on 4.13.0 (see project README).
  Tier-label audit on the touched rows: "Check upcoming" still
  Tier 1, "See the live timeline" still Tier 2.

## v4.12 (2026-11-25 ~02:55 +0330)

- **"Check live" titles removed from Tier 1 cached rows:** the five
  static rows said "Check live feed / connectors / wallet status /
  upcoming / activity" while their descs say "(Tier 1, cached ~1s)"
  — now "Check the Feed", "Check connectors", "Check wallet status",
  "Check upcoming", "Check activity". The Feed row's desc stated no
  tier at all — now "(Tier 1, cached ~1s)".
- **Raw tokens humanized at render time:** Wallet showed the cache
  note verbatim ("Shop Pay not_connected") — now "not connected"
  via `humanizeNote()`; the snapshot is never edited. `plural(n,
  one, many)` replaces the four "(s)" sites (Settings, Notes, Ideas,
  panel).
- **Ideas Run/Dismiss are line SVGs** (`ICON.play` / `ICON.x`,
  replacing ▶ / ✕); spreadsheet row uses the new `ICON.table`;
  Activity row uses the list icon, not the hamburger.
- Probes: `../screenshots/test_v412.py` 21/21 (first run 16/18:
  the Feed tier label was genuinely missing, fixed in the app; one
  probe read titles via innerText, now reads the `.title` span;
  +3 API probes added after shot 01 caught "(s)" in the server
  replies too); all older suites green on 4.12.0 (see project
  README). The server replies got the same plain-words fix
  (`server.py`: plural helper + humanize, 6 sites + wallet note;
  `telegram_fast.py`: 4 sites, kept in sync by hand).

## v4.11 (2026-11-25 ~02:45 +0330)

- **Row icons are line SVGs now, not emoji:** one `ICON` map in
  `app.js` (30 stroke SVGs, same style as the tab bar) serves every
  generated row; `index.html` has the same SVGs inline in all 35
  `.icon` spans plus the menu, Search/Settings drawer and Copy
  buttons. Probe counts 83 rendered `.row .icon` elements, all SVG,
  zero emoji text. Reactions (👍❤️👎) stay emoji by design.
- **Greeting shortened again:** 101 chars, 3 lines measured on the
  greeting text node alone (was 219 chars / 8 lines in v4.10 shot
  48); keeps "within about 75s".
- **Activity view verified, not assumed:** heading top 114 px vs
  header bottom 102 px at scrollY=0, status idle.
- Tier-label audit on touched rows: /ideas, /upcoming, /activity
  still Tier 1.
- Probes: `../screenshots/test_v411.py` 20/20 (18 + 2 added after
  shot 50 caught Discuss/About rendering literal `ICON.chat` /
  `ICON.info` text — fixed to real SVG concatenation, guarded by a
  no-literal-`ICON.` probe); all older suites green on 4.11.0
  (see project README).

## v4.10 (2026-11-25 ~02:23 +0330)

- **Tier-label audit:** "More ideas" (/ideas), "Check live upcoming"
  (/upcoming) and "Check live activity" (/activity) wrongly said
  Tier 2 — all three are cached commands, now "(Tier 1, cached ~1s)".
  Notes view sub fixed (listing is cached), both "List my notes"
  rows carry "(Tier 1, cached ~1s)", drawer "All Spaces" no longer
  promises a "live list".
- **Composer icons are line SVGs now:** mic (was 🎙️ emoji) +
  paper-plane send (was ➤ glyph), matching the v4.9 tab bar.
- **Avatar is a cream plush blob now (Jolly), not a 🦔 hedgehog:**
  inline SVG blob in the header + avatar panel.
- **Avatar-panel segmented control is line SVGs now** (was the
  ☰ 🛡️ 🕐 👤 emoji row); Library seg shares the CSS, Media
  segment still works.
- Probes: `../screenshots/test_v410.py` 22/22; all older suites green
  on 4.10.0 (see project README).

## v4.9 (2026-11-25 ~02:10 +0330)

- **Tab bar icons are line SVGs now, not emoji:** five inline stroke
  SVGs (chat / newspaper / bulb / check-square / grid), currentColor
  stroke, active tab tinted accent, inactive tabs muted — the emoji
  rendered as mismatched colour glyphs in every v4.8 screenshot.
- **Bottom scrim behind the pill:** `#tabScrim`, fixed 96 px band,
  transparent → page background, z-index 19 (pill 20),
  pointer-events none — scrolled list text fades instead of peeking
  out beside the pill (v4.8 shots 06/09/13).
- **Greeting shortened again:** 219 chars, 5 lines in shot 01 (was
  7); keeps the exact phrase "within about 75s".
- **Activity "Waiting for review" section:** count from the cached
  snapshot (0 pending → "Nothing waiting for review"); when >0 a
  button prefilling /review (`data-review` delegation handler).
- Probes: `../screenshots/test_v49.py` 12/12; all older suites green
  on 4.9.0 (see project README).

## v4.8 (2026-11-25 ~02:00 +0330)

- **Version pins fixed properly:** test_v45/test_v46 failed only on
  their pinned "4.6.0"/"v4.6" strings (and test_v47 pinned "4.7.0",
  same failure mode on the next bump). All three suites + the
  `reverse_sync_e2e.py` harness now read the version from
  `/api/health` at runtime and assert the greeting/Settings rows
  match THAT version (semver check, no pinned string) — the probes
  themselves were not deleted. The cache-sync fetch User-Agent now
  uses the server `VERSION` (it still said `muse-telegram-app/4.6`).
- **Minimal scroll (found by looking):** the greeting's first lines
  + the "Today" divider were hidden under the sticky header in v4.7
  shot 01 — the blind scroll-to-document-bottom overshot by ~120 px
  on short threads (the 360 px bottom padding is part of
  scrollHeight). `ensureVisibleBelow()` now scrolls just enough to
  put the newest bubble above the composer; a short thread doesn't
  scroll at all (scrollY 0, greeting top 153 px vs header bottom
  102 px). Tall threads still scroll (seeded 12-message thread:
  scrollY 947, last bubble above the composer). Goal detail uses
  the same helper for its Log progress button.
- **Cache notes are one line now:** the same 4-line sync-off
  disclaimer repeated on every view + the drawer (v4.7 shots
  06/08/09/13/14/15/17/25). Each view keeps "From cache, checked
  <time> (<age> old)"; the full explanation lives in exactly one
  place — a new Settings "Cache sync" row. The v4.3 probe that
  asserted the sentence in the Feed note now asserts it in
  Settings.
- **Activity headings derived:** the "24 November" heading was
  hardcoded — now grouped by day from the approvals'
  `requested_at` dates.
- **Connector display names:** Settings + Connectors show GitHub,
  Google Contacts, Google Calendar, Shop Pay… (was raw ids); the
  raw id is still what `/connect` prefills.
- Screenshots: 40 PNGs in `../screenshots/` (39–40 new, each viewed);
  probes: `../screenshots/test_v48.py` 13/13; all older suites green
  on 4.8.0 (see project README).

## v4.7 (2026-11-25 ~01:40 +0330)

- Tier bar is chat-only now: hidden on Feed/Library/Search and every
  other non-chat view (it was fixed chrome over all of them, ~150 px,
  and a Tier 2 wait leaked onto Search). Returning to chat restores
  the exact state (boot button, or a pending wait with both buttons).
  Composer bar measured 211 px on chat, 117 px off-chat.
- Header is sticky (was scrolling away on long views); tab switches
  scroll to the top of the new view; sending (or the mic) from a
  non-chat view switches to chat first so the reply is visible.
- Feed "Check live feed" copy fixed ("latest cached list").
- Screenshots: 38 PNGs in `../screenshots/` (36–38 new, each viewed);
  probes: `../screenshots/test_v47.py` 10/10; e2e harness version pin
  bumped to 4.7.0.

## v4.6 (2026-11-25 ~02:00 +0330)

- Drawer: Connectors/Wallet moved under a new "Tools" heading; "Notes"
  now heads only the notes rows (was mislabelled since v4.0).
- Goal detail scrolls the page to the bottom on open, so "See the live
  timeline" / "Log progress" sit fully above the composer (v4.5 shot 07
  hid both under it).
- Greeting shortened to 3 sentences (320 chars, was a 9-line wall);
  same honest latency line, tag v4.6.
- Feed: "Showing 5 of 12 units" footer + "Check live feed" row
  (prefills /feed, Tier 1 cached).
- Screenshots: 35 PNGs in `../screenshots/` (33–35 new, each viewed);
  probes: `../screenshots/test_v46.py` 12/12.

## v4.5 (2026-11-25 ~01:45 +0330)

- **Bottom-padding fix (found by looking):** the fixed 300 px `main`
  bottom padding was ~30 px short of the fixed chrome when the tier
  bar shows both buttons, so the last bubble's tag, the waiting line
  and the last list rows slid under the composer/tab bar (v4.4 shots
  03/14/17/26). `fitBottomPadding()` now measures the real
  composerBar height and sets the padding to bar + 70 px tab-bar
  zone + 24 px margin (floored at 360 px), re-run after every
  tier-bar change, on resize, and at boot; tier-bar changes also
  re-scroll the chat to the bottom. Measured: waiting line ends
  29 px above the composer bar; Notes last row likewise.
- Settings gains "Your data in this app" (thread storage, device-only
  prefs, Clear chat) + a "Muse Telegram App v4.5" version row.
- Library Media segment was a dead end — now offers "Create an
  image" (prefills free-chat text); fixed the segment leaking the
  Artifacts create rows + footnote into the Media view (first caught
  on the new shot 31).
- Copy: prefill rows now say "reply in-app within ~75s, else bot
  chat" (was "reply in bot chat") — the timeout/fallback tags keep
  the old wording, correctly.
- Screenshots: 32 PNGs in `../screenshots/` (30–32 new, each
  viewed); probes: `../screenshots/test_v45.py` 14/14.

## v4.4 (2026-11-25 ~01:00 +0330)

- Chat date groups: restored thread shows Yesterday / Today / date dividers from stored message times.
- Every bubble opens the action sheet on tap or long-press (user bubbles too — Copy / Retry).
- Waiting indicator keeps animated dots + live seconds; new bubbles scroll fully above the composer.
- In-app reply tag uses the short time ("25 Nov 00:00 UTC", was raw ISO).
- Composer bar is now solid (was a transparent gradient — scrolled text showed through behind the chips).
- Drawer Channels adds the Telegram bot chat row (@Manis_muse_bot).
- Screenshots: 29 PNGs in `../screenshots/` (26–29 new, each viewed); probes: `../screenshots/test_v44.py` 12/12.

## v4.3 (2026-11-25 ~01:00 +0330)

- 14 Tier 1 cached commands (was 13): `/connectors` added.
- New views: Settings, Connectors, Wallet — all rendered from the
  cached snapshot, nothing invented.
- Feed Like (device-only) + About sheet with real metadata;
  Ideas Run + Dismiss (device-only, Undo); Goals timeline from real
  cache dates + 7 create categories; Library doc/sheet/slides;
  Upcoming dated rows; Notes manage rows; Search recent searches.
- Chat: tap a Muse bubble for Copy / Retry / reactions
  (device-only); Tier 2 waiting clock ticks live, one short line.
- Copy fixes: honest 75 s in-app greeting, sync-off explanation,
  restacked tier bar, chips fade cue.
- Screenshots: 25 PNGs in `../screenshots/` (01–25, each new one
  viewed); probes: `../screenshots/test_v43.py` 19/19.

## v4.2 (2026-11-24 ~21:00 UTC)

- 13 Tier 1 cached commands (was 6): `/activity`, `/upcoming`,
  `/tasks`, `/notes`, `/library`, `/ideas`, `/wallet` added.
- New views: Upcoming, Activity, Notes; goal activity timeline;
  Feed date heading + Discuss/info + dark instructions sheet;
  search across commands + content, grouped.
- Avatar working pulse, animated thinking dots, short cache times
  ("24 Nov 20:48 UTC"), tab bar now bottom-most chrome.
- In-app reply worker: proposal only (`in_app_reply_worker.py` +
  `IN_APP_REPLY_WORKER_PROPOSAL.md`); hook untouched.
- Screenshots: 15 PNGs in `../screenshots/` (01–15, each viewed).

## v4 frontend structure (implemented 2026-11-24 — see "Frontend work done" above)

- Chat-first single Main Chat thread (not a dashboard of tiles).
- Centred avatar header with live status; tapping avatar opens the
  Activity / Approvals / Upcoming / Soul & Memory panel.
- Floating pill bottom tab bar, 5 icon-only tabs:
  Chat / Feed / Ideas / Goals / Library.
- Hamburger drawer with TABS + SPACES + CHANNELS sections.
  Drawer must NOT hard-code Space names — "Ask Muse for your live
  list" + an All Spaces action.
- Lists with hairline dividers, not tiles. Pale-blue user bubbles
  vs light-grey Muse bubbles. Large pill composer (+ left, mic
  right). "One task needs review" banner above the composer when
  applicable.
- Approvals as first-class cards: Allow once / Allow for this task /
  Deny — but NEVER claim an approval was granted; those buttons must
  send the request to Muse / open the Muse app, they cannot approve
  in the app.
- Quick actions: 5 chips above the composer (Review, Spaces, Notes,
  Email, Tasks) — tapping one prefills the composer, does NOT send.
- One search filtering all 48 commands/features; each result says
  what it does and prefills the composer on tap.
- Tabs as views inside the app: Feed (editorial list, date heading,
  Discuss prefills composer, Feed-instructions field), Ideas
  (grouped suggestion list, verified ideas only), Goals (activity
  timeline, verified goal only), Library (Artifacts | Media
  segmented list, empty state "Nothing created yet").
- Thread persistence: NOT implemented — thread is in-memory only
  for now (Telegram CloudStorage / localStorage persistence is still
  open). Never store secrets. Thread is local-only until sent via
  one of the two paths — say that in Help.
- Only external asset in the static build was
  `https://telegram.org/js/telegram-web-app.js`; in the dynamic
  app this stays the Telegram SDK, plus the app's own backend API.
  Telegram theme variables, light + dark, fast load, 44px targets,
  safe-area insets. No fake switches, no settings only the Muse app
  can set, no hard-coded counts/statuses/Space names, no invented
  links (only telegram.org script + https://muse.ai +
  https://ai.meta.com/muse/download/).
- Primary flow (built): message appears instantly in-thread
  (optimistic, local) with a "Muse is thinking…" state; the app
  POSTs to the backend and follows the SSE stream. Tier 1 cached
  commands answer in ~1s, labelled with the cached time. Tier 2
  shows "Muse is waking up — reply lands in the bot chat in
  ~10-20s" plus the "Open in Muse — instant, live chat" escape
  hatch (copy-to-clipboard + `tg.openLink("https://muse.ai")`),
  which STAYS as the escape hatch — decided in the rebuild.
- Backup flow, clearly labelled slow: "Send via Telegram bot
  instead (reply in bot chat, ~10–20s)" — the ONE path that may use
  `tg.sendData`, only on explicit tap; warn before tapping that the
  app closes after sendData.
- Honesty (unchanged, and now measured): replies inside Telegram
  take 10–20s because Muse has to wake up; do NOT pretend the delay
  is gone. The dynamic backend does NOT remove it — it removes the
  silence (live "waking up" status + cached answers in ~1s). The
  local timings are in `../README.md`.

## Files in this folder

- `README.md` (this file)
- `index.html`, `app.js`, `styles.css`, `commands.json` — the v4
  frontend, served by `../server/server.py` (same-origin).

## v3 untouched

`../muse-telegram-app.html` (v3, 520 lines, 34,127 bytes,
last modified 2026-11-24 17:11) was NOT overwritten, per instruction.
It remains the live file until the v4 rebuild replaces it.

## Next step (awaiting John via parent)

Hosting / domain / token-storage decisions (see `../README.md`
"Decisions needed"). Then deploy, point the bot Menu button at
`https://<host>/`, and live-test from Telegram.
