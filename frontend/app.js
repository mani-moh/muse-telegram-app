/* Muse Telegram App v4.19.1 — frontend.
   Chat-first. Frontend -> backend (/api/message, /api/events SSE).
   Two-tier latency, honest in the UI:
     Tier 1 — cached commands answered by the backend in ~1s, labelled
       "from cache, checked <time>". v4.3: 14 cached commands
       (/help /spaces /review /feed /goals /defaults + /activity
       /upcoming /tasks /notes /library /ideas /wallet /connectors).
     Tier 2 — everything else: queued, reply lands in-app within 75 s
       if a Muse-side worker publishes it via the reverse-sync
       (v4.4 backend poller + v4.5 end-to-end proof + live-test plan),
       otherwise in the BOT CHAT after the Muse wake (~10-20s).
       The app says so plainly and never pretends a Tier 2 reply is instant.
   Escape hatch: "Open in Muse — instant, live chat" lives ONLY in
   Settings (v4.19.1: the filled button in the chat's tier bar blocked
   the view, so it was removed from the chat entirely). It copies the
   message to the clipboard first, then tg.openLink("https://muse.ai").
   tg.sendData is used ONLY by the explicit, tap-gated
   "Send via Telegram bot instead (slow, ~10-20s)" button.
   No hard-coded Space names, counts, or statuses: every value shown
   comes from /api/cache and is labelled with its cached time.
   Only links: https://muse.ai and https://ai.meta.com/muse/download/ .
*/
(function () {
  "use strict";
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  if (tg) { try { tg.ready(); tg.expand(); } catch (e) {} }
  var initData = tg ? (tg.initData || "") : "";

  /* Device-only prefs (never sent to Muse, never synced): liked Feed
     posts and dismissed ideas. Stored in localStorage; the cache still
     holds the real saved list, so /ideas shows a dismissed idea again. */
  function loadPref(key) {
    try { var v = JSON.parse(window.localStorage ? window.localStorage.getItem(key) : null); return Array.isArray(v) ? v : []; }
    catch (e) { return []; }
  }
  function savePref(key, arr) {
    try { if (window.localStorage) window.localStorage.setItem(key, JSON.stringify(arr)); } catch (e) {}
  }
  var likedPosts = loadPref("muse-v4-liked-posts");
  var dismissedIdeas = loadPref("muse-v4-dismissed-ideas");
  var recentSearches = loadPref("muse-v4-recent-searches");

  var thread = document.getElementById("thread");
  var input = document.getElementById("composerInput");
  var statusLine = document.getElementById("statusLine");
  var tierBar = document.getElementById("tierBar");
  var cacheData = null;   // from /api/cache
  var commands = [];      // 48 commands, from /api/commands (fallback: commands.json)

  /* ---------- thread persistence ----------
     Thread survives closing/reopening the Mini App.
     Primary store: Telegram CloudStorage (per-device, tied to the bot).
     Fallback: localStorage. Last resort: in-memory only (this session).
     Stored: last 50 bubbles {who, text, tag, ts}. No secrets, no tokens.
     "Clear chat" in the drawer wipes the stored thread (app only —
     nothing is deleted in Muse or in the bot chat). */
  var THREAD_KEY = "muse-v4-thread-v1";
  var THREAD_MAX = 50;
  var threadMsgs = [];
  var saveTimer = null;
  var store = {
    get: function (cb) {
      if (tg && tg.CloudStorage && tg.CloudStorage.getItem) {
        try { tg.CloudStorage.getItem(THREAD_KEY, function (err, val) { cb(err ? null : val); }); return; } catch (e) {}
      }
      try { cb(window.localStorage ? window.localStorage.getItem(THREAD_KEY) : null); } catch (e) { cb(null); }
    },
    set: function (val, cb) {
      if (tg && tg.CloudStorage && tg.CloudStorage.setItem) {
        try { tg.CloudStorage.setItem(THREAD_KEY, val, function (err) { if (cb) cb(!err); }); return; } catch (e) {}
      }
      try { window.localStorage.setItem(THREAD_KEY, val); if (cb) cb(true); } catch (e) { if (cb) cb(false); }
    },
    remove: function (cb) {
      if (tg && tg.CloudStorage && tg.CloudStorage.removeItem) {
        try { tg.CloudStorage.removeItem(THREAD_KEY, function () { if (cb) cb(); }); return; } catch (e) {}
      }
      try { window.localStorage.removeItem(THREAD_KEY); } catch (e) {}
      if (cb) cb();
    }
  };
  function scheduleSave() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      try { store.set(JSON.stringify(threadMsgs.slice(-THREAD_MAX))); } catch (e) {}
    }, 300);
  }
  /* Chat date groups (v4.4): the real app groups the thread by day.
     "Today" / "Yesterday" / "24 Nov 2026", by the device's local day.
     The static #dateDivider in the HTML is the first day's divider;
     later days get a .chat-date divider inserted before the bubble. */
  var lastBubbleDay = null;   // dayKey of the last bubble in the thread
  function dayKey(ts) {
    var d = new Date(ts);
    return d.getFullYear() + "-" + (d.getMonth() + 1) + "-" + d.getDate();
  }
  function fmtDayLabel(ts) {
    var d = new Date(ts), now = new Date();
    if (dayKey(ts) === dayKey(now.getTime())) return "Today";
    var y = new Date(now.getTime() - 86400000);
    if (dayKey(ts) === dayKey(y.getTime())) return "Yesterday";
    return d.getDate() + " " + MONTHS[d.getMonth()] + " " + d.getFullYear();
  }
  function maybeDayDivider(ts) {
    var key = dayKey(ts);
    if (lastBubbleDay === key) return;
    lastBubbleDay = key;
    if (!thread.querySelector(".bubble")) {
      var dd = $("dateDivider"); if (dd) dd.textContent = fmtDayLabel(ts);
      return;
    }
    var div = document.createElement("div");
    div.className = "chat-date";
    div.textContent = fmtDayLabel(ts);
    thread.appendChild(div);
  }
  function clearThread() {
    threadMsgs = [];
    thread.innerHTML = "";
    lastBubbleDay = null;
    var dd = $("dateDivider"); if (dd) dd.textContent = "Today";
    store.remove(function () {
      addBubble("Chat cleared in this app. Your messages in Muse and the bot chat are untouched.", "muse");
    });
  }

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]; }); }
  /* v4.12: plain-language helpers. Cache notes are written by workers and
     sometimes carry raw status tokens (wallet.note: "not_connected") and
     "(s)" plurals — both read as machine output in the UI. Humanize at
     render time only; the cache snapshot itself is never edited.
     v4.14: the notes THEMSELVES still read like worker logs — raw ISO
     dates ("empty 2026-11-24 17:00 UTC", "verified 2026-11-24"), raw
     idea ids ("idea_299b…"), and internal jargon ("insert/patch/delete
     write path", "via runcommand /notes", "(fullstack)"). Format the
     dates into the same short "24 Nov 17:00 UTC" form fmtTime uses,
     drop the ids, and say the jargon in plain words — still render
     time only, the snapshot is never edited.
     v4.16: the tracked goal's state note still promises a shipped
     increment — "next is Increment 2+3 full features from Telegram"
     (Increment 2+3 shipped 2026-11-24 17:05 UTC: 40 commands, spec +
     app). The clause is verifiably false now, so it is dropped at
     render time; the true first clause ("Menu button is live…") stays.
     The source note itself is stale and should be refreshed by a
     worker — this only stops the app repeating it. */
  function humanizeNote(s) {
    var out = String(s);
    function fmtDate(y, mo, d) { return parseInt(d, 10) + " " + MONTHS[parseInt(mo, 10) - 1]; }
    function fmtDateTime(y, mo, d, h, mi) {
      function p(n) { return (n < 10 ? "0" : "") + n; }
      return parseInt(d, 10) + " " + MONTHS[parseInt(mo, 10) - 1] + " " + p(parseInt(h, 10)) + ":" + p(parseInt(mi, 10)) + " UTC";
    }
    out = out.replace(/(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):\d{2}Z/g, function (m, y, mo, d, h, mi) { return fmtDateTime(y, mo, d, h, mi); });
    out = out.replace(/(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}) UTC/g, function (m, y, mo, d, h, mi) { return fmtDateTime(y, mo, d, h, mi); });
    out = out.replace(/(\d{4})-(\d{2})-(\d{2})/g, function (m, y, mo, d) { return fmtDate(y, mo, d); });
    out = out.replace(/idea_[0-9a-f]{32}\s*/g, "");
    out = out.replace(/not_connected/g, "not connected").replace(/not_supported/g, "not supported");
    out = out.replace(/insert\/patch\/delete write path/g, "adding, completing and deleting tasks");
    out = out.replace(/ via runcommand \/notes:/g, ":");
    out = out.replace(/\(fullstack\) /g, "");
    out = out.replace(/workspace\/your_files/g, "your files");
    out = out.replace(/;\s*next is Increment 2\+3 full features from Telegram\.?/g, ".");
    out = out.replace(/  +/g, " ");
    return out;
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  /* v4.19: idea titles come from the cache note, not a hardcoded
     list. The cache stores only idea ids in "saved", but the
     worker-written "note" carries the titles in prose ("Saved ideas
     exist, e.g. idea_<32 hex> <title>, idea_<32 hex> <title>.").
     Titles contain no commas in that format, so each title runs to
     the next ", idea_" or the end; a trailing full stop is prose
     punctuation, not part of the title. KNOWN_IDEA_TITLES is only
     the fallback for ids the note does not name — never invent a
     title for an id listed in neither. Same parse as the server's
     parse_idea_titles() — keep them in sync. Render time only;
     the snapshot itself is never edited. */
  var KNOWN_IDEA_TITLES = {
    "idea_299b6569f1234467bd006eb25a6ad059": "Set your defaults from Telegram",
    "idea_18894899f1ff4b2ca60528696dfc7654": "Host your Telegram app on your AWS credits"
  };
  function parseIdeaTitles(note) {
    var out = {};
    var re = /idea_([0-9a-f]{32})\s+([^,]+)/g, m;
    while ((m = re.exec(String(note || "")))) {
      var t = m[2].trim().replace(/\.+$/, "").trim();
      if (t) out["idea_" + m[1]] = t;
    }
    return out;
  }
  function ideaTitlesMap() {
    var titles = {};
    Object.keys(KNOWN_IDEA_TITLES).forEach(function (k) { titles[k] = KNOWN_IDEA_TITLES[k]; });
    var parsed = parseIdeaTitles((cacheData.ideas && cacheData.ideas.note) || "");
    Object.keys(parsed).forEach(function (k) { titles[k] = parsed[k]; });
    return titles;
  }

  /* v4.11: one line-SVG icon set (same stroke style as the v4.9
     tab bar). Every row icon in the app uses these now — the v4.10
     screenshots still showed ~50 colour-emoji row icons (drawer
     tabs, Feed posts, Notes, Settings, Library, panel) rendering
     as mismatched glyphs next to the SVG tab bar. */
  var ICON = {
    menu: '<svg viewBox="0 0 24 24" aria-hidden="true"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>',
    chat: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>',
    news: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="4" width="20" height="16" rx="2"/><line x1="6" y1="9" x2="10" y2="9"/><line x1="6" y1="13" x2="14" y2="13"/><line x1="6" y1="17" x2="12" y2="17"/></svg>',
    bulb: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.4 1 2.3h6c0-.9.4-1.8 1-2.3A7 7 0 0 0 12 2z"/></svg>',
    checkSq: '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>',
    checkCircle: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
    grid: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>',
    clock: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    list: '<svg viewBox="0 0 24 24" aria-hidden="true"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>',
    shield: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>',
    user: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
    heart: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>',
    pen: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z"/></svg>',
    sliders: '<svg viewBox="0 0 24 24" aria-hidden="true"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>',
    plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
    gear: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    plane: '<svg viewBox="0 0 24 24" aria-hidden="true"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>',
    zap: '<svg viewBox="0 0 24 24" aria-hidden="true"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
    card: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>',
    doc: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
    chart: '<svg viewBox="0 0 24 24" aria-hidden="true"><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/></svg>',
    slides: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>',
    calendar: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>',
    search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
    bookmark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
    trash: '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
    target: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>',
    image: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>',
    database: '<svg viewBox="0 0 24 24" aria-hidden="true"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>',
    info: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    ban: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>',
    book: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/></svg>',
    /* v4.12: the Ideas action buttons used the ▶ / ✕ text glyphs and the
       spreadsheet row used a bar chart — both now line SVGs like the rest. */
    play: '<svg viewBox="0 0 24 24" aria-hidden="true"><polygon points="6 3 20 12 6 21 6 3"/></svg>',
    x: '<svg viewBox="0 0 24 24" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    table: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="3" y1="15" x2="21" y2="15"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="15" y1="3" x2="15" y2="21"/></svg>',
  };
  /* Short, readable cache times: "24 Nov 20:48 UTC" instead of the raw
     ISO string with microseconds (which wrapped ugly in bubbles). */
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  /* v4.8: display names for connector ids. Settings + Connectors
     showed raw ids (google_contacts, shop-pay) — the ids are still
     what /connect prefills (data-connect keeps the raw id), the
     names are display-only. Unknown ids fall back to the raw id. */
  var SERVICE_NAMES = {
    github: "GitHub", gmail: "Gmail", google_contacts: "Google Contacts",
    google_docs: "Google Docs", google_forms: "Google Forms",
    google_sheets: "Google Sheets", google_slides: "Google Slides",
    google_tasks: "Google Tasks", instagram_messages: "Instagram Messages",
    meta_ads: "Meta Ads", notion: "Notion", telegram: "Telegram",
    google_calendar: "Google Calendar", google_drive: "Google Drive",
    expedia: "Expedia", "shop-pay": "Shop Pay", "stripe-link": "Stripe Link"
  };
  function serviceName(id) { return SERVICE_NAMES[id] || id; }
  var DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  function parseIso(s) {
    if (!s) return null;
    var d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }
  function fmtTime(iso) {
    var d = parseIso(iso);
    if (!d) return "unknown time";
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return d.getUTCDate() + " " + MONTHS[d.getUTCMonth()] + " " + p(d.getUTCHours()) + ":" + p(d.getUTCMinutes()) + " UTC";
  }
  /* v4.13: the Tasks check time lives in the tasks note itself
     ("...empty 2026-11-24 17:00 UTC; ..."). Derive it from the
     note instead of hard-coding the date — the old code matched a
     literal /2026-11-24 17:00 UTC/, so the Goals timeline's Tasks
     row silently vanished the moment the refresher stamped a new
     time, and the Upcoming title went stale with it. Returns the
     short "24 Nov 17:00 UTC" form, or "" when the note carries no
     timestamp. */
  function tasksCheckedTime() {
    var note = (cacheData.tasks && cacheData.tasks.note) || "";
    var m = note.match(/(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}) UTC/);
    if (!m) return "";
    return fmtTime(m[1] + "-" + m[2] + "-" + m[3] + "T" + m[4] + ":" + m[5] + ":00Z");
  }
  /* Date-group heading like the real app's "Wednesday afternoon":
     weekday + part of day, derived from the actual cache timestamp. */
  function fmtDayPart(iso) {
    var d = parseIso(iso);
    if (!d) return "Latest";
    var h = d.getUTCHours();
    var part = h < 12 ? "morning" : (h < 18 ? "afternoon" : "evening");
    return DAYS[d.getUTCDay()] + " " + part;
  }
  function haptic() { if (tg && tg.HapticFeedback) { try { tg.HapticFeedback.impactOccurred("light"); } catch (e) {} } }
  function apiBase() { return ""; } // same-origin: backend serves this frontend

  function setStatus(text, working) {
    statusLine.textContent = text;
    statusLine.classList.toggle("working", !!working);
    var av = $("avatarBtn"); if (av) av.classList.toggle("working", !!working);
  }

  /* ---------- thread ---------- */
  var msgSheetTarget = null;   // {el, text} for the open message-action sheet
  function openMsgSheet(el, text) {
    msgSheetTarget = { el: el, text: text };
    $("msgSheetText").textContent = text.length > 160 ? text.slice(0, 160) + "…" : text;
    $("msgSheet").classList.add("show"); $("msgBackdrop").classList.add("show");
  }
  function closeMsgSheet() { $("msgSheet").classList.remove("show"); $("msgBackdrop").classList.remove("show"); msgSheetTarget = null; }
  $("msgCloseBtn").addEventListener("click", closeMsgSheet);
  $("msgBackdrop").addEventListener("click", closeMsgSheet);
  $("msgCopyBtn").addEventListener("click", function () {
    if (!msgSheetTarget) return;
    copyText(msgSheetTarget.text, function () { closeMsgSheet(); });
  });
  $("msgRetryBtn").addEventListener("click", function () {
    if (!msgSheetTarget) return;
    var t = msgSheetTarget.text; closeMsgSheet();
    input.value = t; send();
  });
  function reactBubble(emoji) {
    if (!msgSheetTarget) return;
    var el = msgSheetTarget.el, old = el.querySelector(".reaction");
    if (old) old.remove();
    var r = document.createElement("span"); r.className = "reaction"; r.textContent = emoji;
    el.appendChild(r);
    for (var i = threadMsgs.length - 1; i >= 0; i--) {
      if (threadMsgs[i].text === msgSheetTarget.text) { threadMsgs[i].reaction = emoji; break; }
    }
    scheduleSave(); closeMsgSheet();
  }
  $("msgReactLikeBtn").addEventListener("click", function () { reactBubble("👍"); });
  $("msgReactLoveBtn").addEventListener("click", function () { reactBubble("❤️"); });
  $("msgReactDownBtn").addEventListener("click", function () { reactBubble("👎"); });

  function addBubble(text, who, tag, opts) {
    var ts = (opts && opts.ts) || Date.now();
    maybeDayDivider(ts);
    var el = document.createElement("div");
    el.className = "bubble " + who;
    el.innerHTML = esc(text) + (tag ? '<span class="tag">' + esc(tag) + "</span>" : "") +
      (opts && opts.reaction ? '<span class="reaction">' + esc(opts.reaction) + "</span>" : "");
    /* v4.4: every bubble opens the action sheet — tap, or long-press
       (500 ms touch / right-click), like the real app's long-press menu.
       User bubbles get Copy / Retry too; reactions stay device-only. */
    el.addEventListener("click", function () { openMsgSheet(el, text); });
    var pressTimer = null;
    el.addEventListener("touchstart", function () {
      if (pressTimer) clearTimeout(pressTimer);
      pressTimer = setTimeout(function () { openMsgSheet(el, text); haptic(); }, 500);
    }, { passive: true });
    el.addEventListener("touchend", function () { if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; } });
    el.addEventListener("touchmove", function () { if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; } });
    el.addEventListener("contextmenu", function (e) { e.preventDefault(); openMsgSheet(el, text); });
    thread.appendChild(el);
    /* v4.8: scroll JUST ENOUGH to show this bubble above the fixed
       composer — never a blind scroll to document bottom. The old
       window.scrollTo(0, document.body.scrollHeight) overshot on
       short threads: the measured bottom padding is part of
       scrollHeight, so the page scrolled ~120 px past the last
       bubble and the greeting's first lines + the "Today" divider
       slid under the sticky header (v4.7 shot 01, reproduced:
       scrollY 121, greeting top at 32 px, header bottom 102 px).
       Now a short thread doesn't scroll at all and the greeting
       stays fully visible; a long thread scrolls only by the
       overflow, so the newest bubble clears the composer. */
    scrollChatToBottom();
    if (!opts || opts.record !== false) {
      threadMsgs.push({ who: who, text: String(text), tag: tag || "", ts: ts, reaction: (opts && opts.reaction) || "" });
      if (threadMsgs.length > THREAD_MAX) threadMsgs = threadMsgs.slice(-THREAD_MAX);
      scheduleSave();
    }
    return el;
  }
  /* v4.5: keep the last bubble / list row clear of the fixed bottom
     chrome. The chrome's height is NOT constant — the tier bar grows
     from one line to two full-width buttons while a Tier 2 reply is
     pending — so measure the real composerBar height and set main's
     bottom padding to (bar + 70 px tab-bar zone + 24 px margin),
     floored at the 360 px CSS value. Called after every tier-bar
     change, on resize, and at boot. The old fixed 300 px was ~30 px
     short with both tier buttons shown: the last bubble's tag and
     the last list rows slid under the composer/tab bar (v4.4 shots
     03/14/17/26). */
  function fitBottomPadding() {
    var main = document.querySelector("main");
    var bar = $("composerBar");
    if (!main || !bar) return;
    var pad = bar.offsetHeight + 70 + 24;
    if (pad < 360) pad = 360;
    main.style.paddingBottom = pad + "px";
  }
  window.addEventListener("resize", fitBottomPadding);
  /* v4.8: scroll JUST ENOUGH to show an element above the fixed
     composer — see the note in addBubble (v4.7 shot 01). */
  function ensureVisibleBelow(el) {
    if (!el) return;
    var bar = $("composerBar");
    var limit = (bar ? bar.getBoundingClientRect().top : window.innerHeight) - 8;
    var r = el.getBoundingClientRect();
    if (r.bottom > limit) { try { window.scrollBy(0, r.bottom - limit); } catch (e) {} }
  }
  function scrollChatToBottom() {
    ensureVisibleBelow(thread.lastElementChild);
  }
  function setTierBar(html) {
    tierBar.innerHTML = html;
    fitBottomPadding();
    /* The tier bar changing height moves the visible area: if the
       chat is showing, re-scroll so the last bubble stays fully
       above the chrome (a Tier 2 wait growing the bar used to cover
       the waiting line / reply tag). */
    if ($("view-chat") && $("view-chat").classList.contains("active")) scrollChatToBottom();
  }

  /* ---------- cached data (tabs / drawer) ----------
     v4.8: the per-view cache note is ONE line now. The old note
     repeated the full sync-off explanation (4 lines) on every view
     + the drawer, pushing real content down on all 10 tabs
     (v4.7 shots 06/08/09/13/14/15/17/25). The full explanation
     lives in exactly one place — the Settings "Cache sync" row
     (cacheSyncNote) — and the short label keeps the checked time,
     which is what each view needs. */
  function cacheLabel() {
    if (!cacheData || !cacheData.cache_updated_utc) return "Cached check time unknown — this may be out of date.";
    var label = "From cache, checked " + fmtTime(cacheData.cache_updated_utc) + " (" + (cacheData.cache_age || "?") + " old).";
    var sync = cacheData.cache_sync;
    if (sync && sync.status && sync.status !== "ok" && sync.status !== "disabled") {
      label += " Cache sync: " + sync.status + ".";
    }
    return label;
  }
  function cacheSyncNote() {
    var sync = cacheData && cacheData.cache_sync;
    if (!sync || !sync.status) return "Cache sync status unknown — this snapshot may be out of date.";
    if (sync.status === "disabled") {
      return "Sync is off on this local server — no remote sync URL is set, so this is the local snapshot only, not a live check.";
    }
    if (sync.status === "ok") {
      return "Cache sync is on — the snapshot refreshes from the live Muse state" + (sync.last_sync_utc ? " (last sync " + fmtTime(sync.last_sync_utc) + ")" : "") + ".";
    }
    return "Cache sync: " + sync.status + (sync.last_sync_utc ? " (last sync " + fmtTime(sync.last_sync_utc) + ")" : " (never synced)") + " — this snapshot may be out of date.";
  }
  function renderSkeletons() {
    var sk = '<div class="skeleton" style="width:82%"></div><div class="skeleton" style="width:64%"></div><div class="skeleton" style="width:74%"></div>';
    ["feedList", "ideasList", "goalsList", "libraryList", "drawerSpaces"].forEach(function (id) {
      var el = $(id); if (el) el.innerHTML = sk;
    });
    ["feedCache", "ideasCache", "goalsCache", "libraryCache", "drawerSpacesCache"].forEach(function (id) {
      var el = $(id); if (el) el.textContent = "Loading your latest snapshot…";
    });
  }
  function showBackendError(show) {
    var b = $("backendBanner"); if (b) b.classList.toggle("show", !!show);
  }
  function renderCacheError() {
    var msg = '<p class="empty">Couldn\'t load your snapshot — the backend didn\'t answer. ' +
      'Cached answers may be out of date. <button class="ghost-btn" id="cacheRetryBtn">Retry</button> ' +
      'or open Muse for live data.</p>';
    ["feedList", "ideasList", "goalsList", "libraryList", "drawerSpaces"].forEach(function (id) {
      var el = $(id); if (el) el.innerHTML = msg;
    });
    var r = $("cacheRetryBtn"); if (r) r.addEventListener("click", loadCache);
  }
  function renderCacheViews() {
    if (!cacheData) return;
    // Feed — editorial list with a date-group heading (like the real
    // app's "Wednesday afternoon"), a Discuss action and an info action
    // per post. Titles come from the cache snapshot, nothing invented.
    var titles = (cacheData.feed && cacheData.feed.latest_titles) || [];
    $("feedCache").textContent = cacheLabel() + " " + (cacheData.feed && cacheData.feed.count ? cacheData.feed.count + " units in your Feed." : "");
    $("feedDateHeading").textContent = fmtDayPart(cacheData.cache_updated_utc);
    $("feedList").innerHTML = titles.length ? titles.map(function (t, i) {
      var liked = likedPosts.indexOf(t) !== -1;
      return '<div class="row"><span class="icon">' + ICON.news + '</span><span class="body"><span class="title">' + esc(t) + '</span></span></div>' +
        '<div class="post-actions"><button class="' + (liked ? "liked" : "") + '" data-like="' + esc(t) + '">' + (liked ? ICON.heart + " Liked" : ICON.heart + " Like") + '</button>' +
        '<button class="discuss" data-discuss="' + esc(t) + '">' + ICON.chat + ' Discuss</button>' +
        '<button data-feedinfo="' + esc(t) + '" data-feedindex="' + i + '">' + ICON.info + ' About this post</button></div>';
    }).join("") + (function () {
      /* v4.6: the Feed showed 5 titles with no statement of how many
         exist — "12 units in your Feed" lived only in the cache note
         above. Say plainly what is shown vs held, and where the full
         fresh list comes from (/feed, Tier 1 cached). */
      var total = (cacheData.feed && cacheData.feed.count) || 0;
      return total > titles.length
        ? '<p class="sub">Showing ' + titles.length + ' of ' + total + ' units in your Feed — the rest need a fresh check. “Check the Feed” below runs /feed (cached, ~1s).</p>'
        : "";
    })() : '<p class="empty">Your Feed is empty.</p>';
    // Ideas — titles parsed from the ideas cache note (v4.19), with
    // the known-titles fallback for ids the note does not name. A
    // saved id with no title anywhere shows no invented title — the
    // honest count line below says so instead.
    var saved = (cacheData.ideas && cacheData.ideas.saved) || [];
    var ideaTitlesMapAll = ideaTitlesMap();
    var ideaTitles = saved.map(function (id) {
      return { id: id, title: ideaTitlesMapAll[id] || null };
    }).filter(function (i) { return i.title; });
    var untitledCount = saved.length - ideaTitles.length;
    var visibleIdeas = ideaTitles.filter(function (i) { return dismissedIdeas.indexOf(i.id) === -1; });
    var hiddenCount = ideaTitles.filter(function (i) { return dismissedIdeas.indexOf(i.id) !== -1; }).length;
    $("ideasList").innerHTML = (visibleIdeas.length ? visibleIdeas.map(function (i) {
      return '<div class="row"><span class="icon">' + ICON.bulb + '</span><span class="body"><span class="title">' + esc(i.title) + '</span><span class="desc">Run it (Tier 2 — reply in-app within ~75s, else bot chat), or dismiss it (dismiss hides it on this device only).</span></span></div>' +
        '<div class="post-actions"><button class="discuss" data-idea="' + esc(i.title) + '">' + ICON.play + ' Run this idea</button>' +
        '<button data-idea-dismiss="' + esc(i.id) + '">' + ICON.x + ' Dismiss</button></div>';
    }).join("") : '<p class="empty">No saved ideas shown.</p>') +
    (untitledCount ? '<p class="sub">' + plural(untitledCount, "more saved idea", "more saved ideas") + ' whose titles aren\'t in the snapshot — /ideas still shows them in Muse.</p>' : "") +
    (hiddenCount ? '<p class="idea-dismissed">' + plural(hiddenCount, "idea", "ideas") + ' hidden on this device. <button id="ideasUndoBtn">Undo</button> — /ideas still shows them in Muse.</p>' : "");
    // Goals — tapping one opens its activity timeline below the list.
    var goals = (cacheData.goals && cacheData.goals.items) || [];
    $("goalsCache").textContent = cacheLabel();
    $("goalsList").innerHTML = goals.length ? goals.map(function (g) {
      return '<button class="row" data-goal-detail="' + esc(g.title) + '" data-goal-status="' + esc(g.status || "") + '"><span class="icon">' + ICON.target + '</span><span class="body"><span class="title">' + esc(g.title) + '</span><span class="desc">' + esc(g.status || "") + '</span></span><span class="meta">Activity ›</span></button>';
    }).join("") : '<p class="empty">You have no goals yet. Tell Muse one and it will track it.</p>';
    // Library — verified empty
    var files = (cacheData.library && cacheData.library.files) || [];
    $("libraryCache").textContent = cacheLabel();
    $("libraryList").innerHTML = files.length ? files.map(function (f) {
      return '<div class="row"><span class="icon">' + ICON.doc + '</span><span class="body"><span class="title">' + esc(f) + '</span></span></div>';
    }).join("") : '<p class="empty">Nothing created yet — documents, dashboards, and other creations will appear here.</p>';
    // Drawer spaces — names come from the cache snapshot, labelled with its time; never hard-coded here.
    var spaces = cacheData.spaces || [];
    $("drawerSpacesCache").textContent = cacheLabel();
    $("drawerSpaces").innerHTML = spaces.length ? spaces.map(function (s) {
      return '<button class="row" data-space="' + esc(s.name) + '"><span class="icon">' + ICON.grid + '</span><span class="body"><span class="title">' + esc(s.name) + '</span></span></button>';
    }).join("") : '<p class="empty">No Spaces yet.</p>';
    // Upcoming — tasks snapshot + tracked items from the cache.
    var tasks = cacheData.tasks || {};
    var tracked = cacheData.tracked || [];
    $("upcomingCache").textContent = cacheLabel();
    var upHtml = "";
    var tasksTime = tasksCheckedTime();
    if (tasks.note) upHtml += '<div class="row"><span class="icon">' + ICON.checkSq + '</span><span class="body"><span class="title">Tasks' + (tasksTime ? ' — checked <span class="nowrap">' + esc(tasksTime) + '</span>' : '') + '</span><span class="desc">' + esc(humanizeNote(tasks.note)) + '</span></span></div>';
    upHtml += '<div class="row"><span class="icon">' + ICON.clock + '</span><span class="body"><span class="title">No scheduled-task list</span><span class="desc">The cached snapshot holds no scheduled-task list — checked ' + esc(fmtTime(cacheData.cache_updated_utc)) + '. Next run times need live Muse.</span></span></div>';
    if (tracked.length) upHtml += tracked.map(function (tr) {
      // v4.16: the state note used to render verbatim — it still
      // promised Increment 2+3, which shipped. humanizeNote drops
      // that stale clause (see its comment); the snapshot is untouched.
      return '<div class="row"><span class="icon">' + ICON.bookmark + '</span><span class="body"><span class="title">' + esc(tr.title || "") + '</span><span class="desc">' + esc(humanizeNote(tr.state || "")) + '</span></span></div>';
    }).join("");
    $("upcomingList").innerHTML = upHtml || '<p class="empty">Nothing scheduled in the cached snapshot.</p>';
    // Tasks — the cached tasks note, same humanized row as Upcoming.
    // v4.17: Tasks had no view of its own (only a chip prefilling
    // /tasks and the Tasks row inside Upcoming). The note is the same
    // cache field Upcoming renders, so both stay in sync for free.
    $("tasksCache").textContent = cacheLabel();
    $("tasksList").innerHTML = tasks.note
      ? '<div class="row"><span class="icon">' + ICON.checkSq + '</span><span class="body"><span class="title">Tasks' + (tasksTime ? ' — checked <span class="nowrap">' + esc(tasksTime) + '</span>' : '') + '</span><span class="desc">' + esc(humanizeNote(tasks.note)) + '</span></span></div>'
      : '<p class="empty">No tasks snapshot cached.</p>';
    // Activity — recent decided approvals from the cache.
    // v4.8: the date heading is derived from the approvals'
    // requested_at dates, grouped by day — it was hardcoded
    // "24 November", which would have been wrong the day a
    // decision from another date arrived.
    var decided = cacheData.recent_decided_approvals || [];
    $("activityCache").textContent = cacheLabel();
    var MONTHS_FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
    function fmtDayHeading(iso) {
      var d = parseIso(iso);
      if (!d) return "Earlier";
      return d.getUTCDate() + " " + MONTHS_FULL[d.getUTCMonth()];
    }
    var actHtml = "";
    if (decided.length) {
      var seenDays = [];
      decided.forEach(function (d) {
        var pd = parseIso(d.requested_at);
        var dk = pd ? dayKey(pd.getTime()) : "unknown";
        if (seenDays.indexOf(dk) === -1) {
          seenDays.push(dk);
          actHtml += '<h2 class="date-heading">' + esc(fmtDayHeading(d.requested_at)) + '</h2>';
        }
        actHtml += '<div class="row"><span class="icon">' + ICON.checkCircle + '</span><span class="body"><span class="title">' + esc(d.decision || "Decided") + '</span><span class="desc">Approval decided · ' + esc(fmtTime(d.requested_at)) + '</span></span></div>';
      });
    }
    // v4.9: "Waiting for review" section — Activity only showed
    // decided approvals, so a pending approval was invisible here
    // (it only appeared in the review banner, and only on a fresh
    // cache). Count comes straight from the cached snapshot.
    var pendCount = (cacheData.pending_approvals && cacheData.pending_approvals.count) || 0;
    var pendHtml = '<h2 class="date-heading">Waiting for review</h2>' +
      (pendCount > 0
        ? '<button class="row" data-review><span class="icon">' + ICON.clock + '</span><span class="body"><span class="title">' + pendCount + (pendCount === 1 ? " task needs" : " tasks need") + ' review</span><span class="desc">Prefills /review (Tier 1, cached ~1s) — approvals are answered in the Muse app only, Telegram shows the list.</span></span></button>'
        : '<div class="row"><span class="icon">' + ICON.checkCircle + '</span><span class="body"><span class="title">Nothing waiting for review</span><span class="desc">No approvals are pending in the cached snapshot — /review shows anything waiting.</span></span></div>');
    $("activityList").innerHTML = pendHtml + actHtml || '<p class="empty">No decided approvals in the cached snapshot. The full activity log needs live Muse.</p>';
    // Notes — cached count + note only; the list itself needs live Muse.
    var notes = cacheData.notes || {};
    $("notesCache").textContent = cacheLabel();
    $("notesList").innerHTML = notes.note
      ? '<div class="row"><span class="icon">' + ICON.list + '</span><span class="body"><span class="title">' + plural(notes.count || 0, "note", "notes") + ' in the cached snapshot</span><span class="desc">' + esc(humanizeNote(notes.note)) + '</span></span></div>'
      : '<p class="empty">No notes snapshot cached.</p>';
    // Settings view — connected services, defaults, wallet, permissions.
    var conn = cacheData.connected_services || [];
    var notConn = cacheData.not_connected || [];
    var defs = cacheData.defaults || {};
    $("settingsCache").textContent = cacheLabel();
    $("settingsList").innerHTML =
      '<div class="row"><span class="icon">' + ICON.zap + '</span><span class="body"><span class="title">' + plural(conn.length, "service", "services") + ' connected</span><span class="desc">' + (conn.length ? esc(conn.map(serviceName).join(", ")) : "None in the cached snapshot.") + '</span></span></div>' +
      '<div class="row"><span class="icon">' + ICON.gear + '</span><span class="body"><span class="title">Defaults</span><span class="desc">' + (Object.keys(defs).length ? esc(Object.keys(defs).map(function (k) { return k + ": " + defs[k]; }).join("; ")) : "No defaults set yet — /default <category> <service> sets one.") + '</span></span></div>' +
      '<div class="row"><span class="icon">' + ICON.refresh + '</span><span class="body"><span class="title">Cache sync</span><span class="desc">' + esc(cacheSyncNote()) + '</span></span></div>' +
      '<div class="row"><span class="icon">' + ICON.shield + '</span><span class="body"><span class="title">Permissions</span><span class="desc">Approvals are answered in the Muse app only — Telegram cannot approve or deny. /settings gives the exact path.</span></span></div>' +
      '<div class="row"><span class="icon">' + ICON.user + '</span><span class="body"><span class="title">Soul &amp; Memory</span><span class="desc">How Muse talks and what it remembers — they live in the Muse app. Use “Open in Muse” below.</span></span></div>' +
      '<div class="row"><span class="icon">' + ICON.database + '</span><span class="body"><span class="title">Your data in this app</span><span class="desc">This chat thread is saved on this device (Telegram CloudStorage, local storage as fallback) — the last 50 messages. Feed likes, dismissed ideas, reactions and recent searches are device-only, never sent to Muse. “Clear chat” in the drawer deletes the saved thread in this app only — nothing is deleted in Muse or the bot chat.</span></span></div>' +
      '<div class="row"><span class="icon">' + ICON.info + '</span><span class="body"><span class="title">Muse Telegram App v4.19.1</span><span class="desc">Dynamic app (frontend + backend) — the bot chat is backup only. Cached answers ~1s, live Muse ~10-20s, in-app replies up to ~75s.</span></span></div>';
    // Connectors view
    $("connectorsCache").textContent = cacheLabel();
    $("connectorsList").innerHTML = conn.length ? conn.map(function (c) {
      return '<div class="row"><span class="icon">' + ICON.checkCircle + '</span><span class="body"><span class="title">' + esc(serviceName(c)) + '</span><span class="desc">Connected (cached snapshot).</span></span></div>';
    }).join("") : '<p class="empty">No connected services in the cached snapshot.</p>';
    $("connectorsNotList").innerHTML = notConn.length ? notConn.map(function (c) {
      return '<button class="row" data-connect="' + esc(c) + '"><span class="icon">' + ICON.zap + '</span><span class="body"><span class="title">' + esc(serviceName(c)) + '</span><span class="desc">Not connected — tap for the connect steps (/connect ' + esc(c) + ', Tier 2 — reply in-app within ~75s, else bot chat).</span></span></button>';
    }).join("") : '<p class="empty">Everything in the snapshot is connected.</p>';
    // Wallet view
    var wallet = cacheData.wallet || {};
    $("walletCache").textContent = cacheLabel();
    $("walletList").innerHTML = (wallet.note
      ? '<div class="row"><span class="icon">' + ICON.card + '</span><span class="body"><span class="title">Wallet status</span><span class="desc">' + esc(humanizeNote(wallet.note)) + '</span></span></div>'
      : '<p class="empty">No wallet snapshot cached.</p>') +
      '<div class="row"><span class="icon">' + ICON.ban + '</span><span class="body"><span class="title">No payments from Telegram</span><span class="desc">No tool pays from here — wallet is status only.</span></span></div>';
    // Goal categories (real app's 7) — rendered once per cache load.
    var cats = ["Health", "Relationships", "Finance", "Career", "Interests", "Productivity", "Other"];
    var gc = $("goalCats"); if (gc && !gc.dataset.done) {
      gc.dataset.done = "1";
      gc.innerHTML = cats.map(function (c) { return '<button data-goal-cat="' + c + '">' + c + '</button>'; }).join("");
    }
    // Review banner — only when a FRESH cache says something is pending.
    var pending = (cacheData.pending_approvals && cacheData.pending_approvals.count) || 0;
    if (pending > 0 && !cacheData.review_stale) {
      $("reviewBannerText").textContent = pending === 1 ? "One task needs review" : pending + " tasks need review";
      $("reviewBanner").classList.add("show");
    } else {
      $("reviewBanner").classList.remove("show");
    }
  }

  /* Goal detail — activity timeline. The cached snapshot holds only
     the goal's title/status, so the timeline honestly says no activity
     entries are cached and offers the live /goal check. Nothing is
     invented: no fake dates, no fake completed deeds. */
  function renderGoalDetail(title, status) {
    /* Timeline built ONLY from dates the cache actually holds: the
       snapshot check time, and the Tasks check time derived from the
       tasks note (v4.13 tasksCheckedTime — the old code matched a
       literal /2026-11-24 17:00 UTC/, so this row vanished on the
       next refresh). No dated deeds are cached for this goal, and
       none are shown. */
    var timeline = '<div class="row"><span class="icon">' + ICON.target + '</span><span class="body"><span class="title">Goal status</span><span class="desc">Goal is ' + esc(status || "unknown") + ' in the cached snapshot (checked ' + esc(fmtTime(cacheData.cache_updated_utc)) + ').</span></span></div>';
    var tasksNote = (cacheData.tasks && cacheData.tasks.note) || "";
    var tasksTime = tasksCheckedTime();
    if (tasksNote) timeline += '<div class="row"><span class="icon">' + ICON.checkSq + '</span><span class="body"><span class="title">Tasks' + (tasksTime ? ' — checked <span class="nowrap">' + esc(tasksTime) + '</span>' : '') + '</span><span class="desc">' + esc(humanizeNote(tasksNote)) + '</span></span></div>';
    $("goalDetail").innerHTML =
      '<div class="goal-card"><div class="title">' + esc(title) + '</div>' +
      '<div class="desc">Status: ' + esc(status || "unknown") + ' · ' + esc(cacheLabel()) + '</div>' +
      '<h2>Activity</h2>' + timeline +
      '<p class="sub">No dated activity entries for this goal are in the cached snapshot — the full timeline (dated completed deeds) needs live Muse.</p>' +
      '<button class="row" data-goal-live="' + esc(title) + '"><span class="icon">' + ICON.target + '</span><span class="body"><span class="title">See the live timeline</span><span class="desc">Prefills /goal ' + esc(title) + ' (Tier 2 — reply in-app within ~75s, else bot chat).</span></span></button>' +
      '<button class="row" data-goal-log="' + esc(title) + '"><span class="icon">' + ICON.pen + '</span><span class="body"><span class="title">Log progress</span><span class="desc">Prefills /loggoal ' + esc(title) + ' — add what you did (Tier 2 — reply in-app within ~75s, else bot chat).</span></span></button></div>';
    /* v4.6: the old scrollIntoView({block:"nearest"}) left the card's
       bottom rows — "See the live timeline" / "Log progress" — hidden
       under the fixed composer (v4.5 shot 07: the last paragraph was
       half-clipped and both buttons were invisible). The card sits at
       the end of the Goals view, so scrolling just enough to put its
       last row fully above the chrome (v4.8 ensureVisibleBelow, same
       minimal-scroll rule as the chat) keeps both buttons visible. */
    setTimeout(function () {
      var btns = document.querySelectorAll("#goalDetail [data-goal-log]");
      ensureVisibleBelow(btns[btns.length - 1]);
    }, 50);
  }

  /* Library segmented control — was a dead tap (no handler). */
  function renderLibrary(kind) {
    /* The doc/sheet/slides create rows + the footnote belong to the
       Artifacts segment only — they sit outside #libraryList, so
       switching segments must hide them too (v4.5 shot 31 caught
       them leaking into the Media view). */
    var showCreate = kind !== "media";
    ["libCreateBtn", "libSheetBtn", "libSlidesBtn", "libFootNote"].forEach(function (id) {
      var el = $(id); if (el) el.style.display = showCreate ? "" : "none";
    });
    if (kind === "media") {
      /* v4.5: the Media segment was a dead end ("No media yet" and
         nothing to do). It still shows no cached media — nothing is
         invented — but now offers the real route: describe an image
         in chat and Muse makes it (free chat + image skills, Tier 2). */
      $("libraryList").innerHTML = '<p class="empty">No media yet — photos and videos Muse creates will appear here.</p>' +
        '<button class="row" id="libImageBtn"><span class="icon">' + ICON.image + '</span><span class="body"><span class="title">Create an image</span><span class="desc">Describe it in chat — Muse makes it (Tier 2 — reply in-app within ~75s, else bot chat).</span></span></button>';
      var ib = $("libImageBtn");
      if (ib) ib.addEventListener("click", function () { prefill("Create an image of "); showTab("chat"); });
      return;
    }
    renderCacheViews(); // Artifacts: re-render from the cache snapshot.
  }
  document.querySelectorAll("#libSeg button").forEach(function (b) {
    b.addEventListener("click", function () {
      document.querySelectorAll("#libSeg button").forEach(function (x) { x.classList.remove("active"); });
      b.classList.add("active"); renderLibrary(b.dataset.lib); haptic();
    });
  });

  /* ---------- tabs / drawer / panel ---------- */
  var TABS = [["chat", ICON.chat, "Chat"], ["feed", ICON.news, "Feed"], ["ideas", ICON.bulb, "Ideas"], ["goals", ICON.checkSq, "Goals"], ["library", ICON.grid, "Library"]];
  function showTab(name) {
    document.querySelectorAll(".view").forEach(function (v) { v.classList.remove("active"); });
    var v = $("view-" + name); if (v) v.classList.add("active");
    document.querySelectorAll("#tabbar button").forEach(function (b) { b.classList.toggle("active", b.dataset.tab === name); });
    document.querySelectorAll("#drawerTabs .row").forEach(function (b) { b.classList.toggle("active", b.dataset.tab === name); });
    /* v4.7: the tier bar is CHAT status chrome (latency line + the
       Open-in-Muse / bot-fallback buttons for the message in flight).
       On every other view it was fixed chrome with nothing to do —
       ~150 px over the Feed/Library/Search lists (v4.6 shots 04/06/08/
       09/13/14/15/35), and a Tier 2 wait in flight leaked onto the
       Search tab in shot 04. Hide it off-chat by toggling a class on
       the composer bar — display:none keeps the nodes and their
       listeners intact, so returning to chat restores the exact state
       (boot button, or a pending Tier 2 wait with both buttons).
       The escape hatch stays reachable off-chat via the drawer
       Channels "Open in Muse" row and the Settings view row.
       fitBottomPadding() re-measures the shorter bar, so lists gain
       the space back. Switching tabs also scrolls to the top of the
       new view (was: a view opened mid-scroll, e.g. v4.6 shot 07). */
    var bar = $("composerBar");
    if (bar) bar.classList.toggle("no-tier", name !== "chat");
    fitBottomPadding();
    try { window.scrollTo(0, 0); } catch (e) {}
    closeDrawer(); haptic();
  }
  $("drawerTabs").innerHTML = TABS.map(function (t) {
    return '<button class="row" data-tab="' + t[0] + '"><span class="icon">' + t[1] + '</span><span class="body"><span class="title">' + t[2] + '</span></span></button>';
  }).join("");
  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-tab]"); if (t) showTab(t.dataset.tab);
    var d = e.target.closest("[data-discuss]"); if (d) { prefill("Tell me more about: " + d.dataset.discuss); showTab("chat"); }
    var lk = e.target.closest("[data-like]"); if (lk) {
      var t = lk.dataset.like, ix = likedPosts.indexOf(t);
      if (ix === -1) likedPosts.push(t); else likedPosts.splice(ix, 1);
      savePref("muse-v4-liked-posts", likedPosts); renderCacheViews(); return;
    }
    var fi = e.target.closest("[data-feedinfo]"); if (fi) {
      var total = (cacheData.feed && cacheData.feed.count) || 0;
      var shown = ((cacheData.feed && cacheData.feed.latest_titles) || []).length;
      var note = (cacheData.feed && cacheData.feed.note) || "";
      $("infoTitle").textContent = fi.dataset.feedinfo;
      $("infoBody").textContent = "Post " + (parseInt(fi.dataset.feedindex, 10) + 1) + " of " + shown +
        " shown (Feed holds " + total + " units in total).\n" + cacheLabel() +
        (note ? "\n" + humanizeNote(note) : "") +
        "\nThe post body is not in the cached snapshot — only the title is. Tap Discuss to talk about it, or /feed for a fresh list.";
      $("infoDiscussBtn").onclick = function () { closeInfoSheet(); prefill("Tell me more about: " + fi.dataset.feedinfo); showTab("chat"); };
      $("infoSheet").classList.add("show"); $("infoBackdrop").classList.add("show");
      return;
    }
    var g = e.target.closest("[data-goal]"); if (g) { prefill("/goal " + g.dataset.goal); showTab("chat"); }
    var gd = e.target.closest("[data-goal-detail]"); if (gd) renderGoalDetail(gd.dataset.goalDetail, gd.dataset.goalStatus);
    var gl = e.target.closest("[data-goal-live]"); if (gl) { prefill("/goal " + gl.dataset.goalLive); showTab("chat"); }
    var rv = e.target.closest("[data-review]"); if (rv) { prefill("/review"); showTab("chat"); }
    var glog = e.target.closest("[data-goal-log]"); if (glog) { prefill("/loggoal " + glog.dataset.goalLog + " "); showTab("chat"); }
    var i = e.target.closest("[data-idea]"); if (i) { prefill("/idea " + i.dataset.idea); showTab("chat"); }
    var idis = e.target.closest("[data-idea-dismiss]"); if (idis) {
      dismissedIdeas.push(idis.dataset.ideaDismiss); savePref("muse-v4-dismissed-ideas", dismissedIdeas); renderCacheViews(); return;
    }
    var iundo = e.target.closest("#ideasUndoBtn"); if (iundo) {
      dismissedIdeas = []; savePref("muse-v4-dismissed-ideas", dismissedIdeas); renderCacheViews(); return;
    }
    var s = e.target.closest("[data-space]"); if (s) { prefill("/space " + s.dataset.space); showTab("chat"); }
    var cn = e.target.closest("[data-connect]"); if (cn) { prefill("/connect " + cn.dataset.connect); showTab("chat"); }
    var gcat = e.target.closest("[data-goal-cat]"); if (gcat) { prefill("/newgoal [" + gcat.dataset.goalCat + "] "); showTab("chat"); }
  });
  function openDrawer() { $("drawer").classList.add("open"); $("scrim").classList.add("show"); }
  function closeDrawer() { $("drawer").classList.remove("open"); $("scrim").classList.remove("show"); }
  $("menuBtn").addEventListener("click", openDrawer);
  $("scrim").addEventListener("click", closeDrawer);
  $("allSpacesBtn").addEventListener("click", function () { prefill("/spaces"); showTab("chat"); });
  $("newSpaceBtn").addEventListener("click", function () { prefill("/newspace "); showTab("chat"); });
  $("drawerActivityBtn").addEventListener("click", function () { showTab("activity"); });
  $("drawerUpcomingBtn").addEventListener("click", function () { showTab("upcoming"); });
  $("drawerTasksBtn").addEventListener("click", function () { showTab("tasks"); });
  $("drawerEmailBtn").addEventListener("click", function () { showTab("email"); });
  $("drawerCalendarBtn").addEventListener("click", function () { showTab("calendar"); });
  $("drawerNotesBtn").addEventListener("click", function () { showTab("notes"); });
  $("openMuseRow").addEventListener("click", function () { closeDrawer(); openInMuse(""); });
  $("noteAddBtn").addEventListener("click", function () { prefill("/note "); showTab("chat"); });
  $("noteListBtn").addEventListener("click", function () { prefill("/notes"); showTab("chat"); });
  $("notesAddBtn").addEventListener("click", function () { prefill("/note "); showTab("chat"); });
  $("notesListBtn").addEventListener("click", function () { prefill("/notes"); showTab("chat"); });
  $("tasksListBtn").addEventListener("click", function () { prefill("/tasks"); showTab("chat"); });
  $("tasksAddBtn").addEventListener("click", function () { prefill("/addtask "); showTab("chat"); });
  $("tasksDoneBtn").addEventListener("click", function () { prefill("/donetask "); showTab("chat"); });
  $("tasksDetailBtn").addEventListener("click", function () { prefill("/task "); showTab("chat"); });
  $("emailCheckBtn").addEventListener("click", function () { prefill("/email"); showTab("chat"); });
  $("emailDraftBtn").addEventListener("click", function () { prefill("/draft "); showTab("chat"); });
  $("emailSendBtn").addEventListener("click", function () { prefill("/send "); showTab("chat"); });
  $("calCheckBtn").addEventListener("click", function () { prefill("/calendar"); showTab("chat"); });
  $("calBookBtn").addEventListener("click", function () { prefill("/book "); showTab("chat"); });
  $("newGoalBtn").addEventListener("click", function () { prefill("/newgoal "); showTab("chat"); });
  $("ideasMoreBtn").addEventListener("click", function () { prefill("/ideas"); showTab("chat"); });
  $("libCreateBtn").addEventListener("click", function () { prefill("/doc "); showTab("chat"); });
  $("upcomingLiveBtn").addEventListener("click", function () { prefill("/upcoming"); showTab("chat"); });
  $("activityLiveBtn").addEventListener("click", function () { prefill("/activity"); showTab("chat"); });
  $("clearThreadBtn").addEventListener("click", function () { closeDrawer(); showTab("chat"); clearThread(); });
  $("whatsappRow").addEventListener("click", function () {
    showTab("chat");
    addBubble("WhatsApp is the only native Muse messaging channel. Telegram stays as this bot + app — ask in chat if you want the WhatsApp setup.", "muse");
  });
  $("telegramBotRow").addEventListener("click", function () {
    /* v4.4: the Channels section now lists the bot chat itself — the
       backup route when the app can't answer in-app. Tap-gated, opens
       the real bot chat in Telegram. */
    closeDrawer();
    if (tg) { try { tg.openLink("https://t.me/Manis_muse_bot"); return; } catch (e) {} }
    window.open("https://t.me/Manis_muse_bot", "_blank");
  });
  $("drawerSearchBtn").addEventListener("click", function () { closeDrawer(); showTab("search"); $("searchInput").focus(); });
  $("drawerSettingsBtn").addEventListener("click", function () { closeDrawer(); showTab("settings"); });
  $("drawerConnectorsBtn").addEventListener("click", function () { closeDrawer(); showTab("connectors"); });
  $("drawerWalletBtn").addEventListener("click", function () { closeDrawer(); showTab("wallet"); });
  $("settingsLiveBtn").addEventListener("click", function () { prefill("/settings"); showTab("chat"); });
  $("settingsOpenMuseBtn").addEventListener("click", function () { openInMuse(""); });
  $("connectorsLiveBtn").addEventListener("click", function () { prefill("/connectors"); showTab("chat"); });
  $("walletLiveBtn").addEventListener("click", function () { prefill("/wallet"); showTab("chat"); });
  $("feedPostBtn").addEventListener("click", function () { prefill("/feedpost "); showTab("chat"); });
  $("feedLiveBtn").addEventListener("click", function () { prefill("/feed"); showTab("chat"); });
  $("libSheetBtn").addEventListener("click", function () { prefill("/sheet "); showTab("chat"); });
  $("libSlidesBtn").addEventListener("click", function () { prefill("/slides "); showTab("chat"); });
  $("notesSearchBtn").addEventListener("click", function () { prefill("/search "); showTab("chat"); });
  $("notesPinBtn").addEventListener("click", function () { prefill("/pin "); showTab("chat"); });
  $("notesDoneBtn").addEventListener("click", function () { prefill("/done "); showTab("chat"); });
  $("notesDeleteBtn").addEventListener("click", function () { prefill("/delete "); showTab("chat"); });
  $("upAddTaskBtn").addEventListener("click", function () { prefill("/addtask "); showTab("chat"); });
  $("upBookBtn").addEventListener("click", function () { prefill("/book "); showTab("chat"); });
  $("reviewBannerBtn").addEventListener("click", function () { prefill("/review"); showTab("chat"); });

  /* Feed instructions sheet (dark bottom sheet, like the real app).
     Save only drafts /feedsteer in the composer — applying the
     instruction still needs live Muse; the sheet never claims it did. */
  function openFeedSheet() { $("feedSheet").classList.add("show"); $("sheetBackdrop").classList.add("show"); $("feedSheetInput").focus(); }
  function closeFeedSheet() { $("feedSheet").classList.remove("show"); $("sheetBackdrop").classList.remove("show"); }
  $("feedSteerBtn").addEventListener("click", openFeedSheet);
  $("feedSheetCancel").addEventListener("click", closeFeedSheet);
  function closeInfoSheet() { $("infoSheet").classList.remove("show"); $("infoBackdrop").classList.remove("show"); }
  $("infoCloseBtn").addEventListener("click", closeInfoSheet);
  $("infoBackdrop").addEventListener("click", closeInfoSheet);
  $("sheetBackdrop").addEventListener("click", closeFeedSheet);
  $("feedSheetSave").addEventListener("click", function () {
    var v = $("feedSheetInput").value.trim();
    closeFeedSheet();
    if (v) { prefill("/feedsteer " + v); showTab("chat"); }
    else { showTab("chat"); addBubble("Write an instruction first — e.g. what topics and when — then Save drafts /feedsteer for you to send.", "muse"); }
  });

  /* Avatar panel */
  $("avatarBtn").addEventListener("click", function () {
    $("panelStatus").textContent = statusLine.classList.contains("working") ? "Muse is working…" : "Connected";
    $("panel").classList.add("show"); renderPanel("activity");
  });
  $("panelClose").addEventListener("click", function () { $("panel").classList.remove("show"); });
  document.querySelectorAll("#panelSeg button").forEach(function (b) {
    b.addEventListener("click", function () {
      document.querySelectorAll("#panelSeg button").forEach(function (x) { x.classList.remove("active"); });
      b.classList.add("active"); renderPanel(b.dataset.panel);
    });
  });
  function renderPanel(kind) {
    var body = $("panelBody");
    if (kind === "approvals") {
      var pending = (cacheData && cacheData.pending_approvals && cacheData.pending_approvals.count) || 0;
      var stale = cacheData ? cacheData.review_stale : true;
      var head = stale
        ? "<p class='sub'>Cached approval check is stale (" + cacheLabel() + ") — a fresh check needs a live Muse run.</p>"
        : "<p class='sub'>Last cached check: " + (pending === 0 ? "no approvals waiting." : plural(pending, "approval", "approvals") + " waiting.") + "</p>";
      var decided = (cacheData && cacheData.recent_decided_approvals) || [];
      var hist = decided.length
        ? "<h2>Approvals history</h2>" + decided.map(function (d) {
            return '<div class="row"><span class="icon">' + ICON.checkCircle + '</span><span class="body"><span class="title">' + esc(d.decision || "Decided") + '</span><span class="desc">Decided · ' + esc(fmtTime(d.requested_at)) + '</span></span></div>';
          }).join("")
        : "<p class='sub'>No decided approvals in the cached snapshot.</p>";
      body.innerHTML = "<h2>Needs review</h2>" + head +
        "<div class='approval'><div class='title'>Approvals are answered in the Muse app</div>" +
        "<div class='desc'>Telegram cannot approve or deny on your behalf — there is no tool for it. These buttons open Muse, where you answer on the approval card.</div>" +
        "<button class='allow-once' data-approve='once'>Allow once</button>" +
        "<button class='allow-task' data-approve='task'>Allow for this task</button>" +
        "<button class='deny' data-approve='deny'>Deny</button></div>" +
        "<p class='sub'>No approval is ever granted from here. Tapping a button only opens Muse.</p>" + hist;
      body.querySelectorAll("[data-approve]").forEach(function (b) {
        b.addEventListener("click", function () { openInMuse(""); });
      });
    } else if (kind === "upcoming") {
      body.innerHTML = "<h2>Upcoming</h2><p class='sub'>Scheduled tasks need live Muse for next run times — the cached snapshot is in the Upcoming view.</p>" +
        "<button class='row' id='panelUpcoming'><span class='icon'>" + ICON.clock + "</span><span class='body'><span class='title'>Open Upcoming view</span><span class='desc'>Cached tasks + tracked items, with a live /upcoming check in chat.</span></span></button>";
      $("panelUpcoming").addEventListener("click", function () { $("panel").classList.remove("show"); showTab("upcoming"); });
    } else if (kind === "identity") {
      body.innerHTML = "<h2>Soul &amp; Memory</h2>" +
        "<button class='row' data-open-muse=''><span class='icon'>" + ICON.user + "</span><span class='body'><span class='title'>Soul</span><span class='desc'>How Muse talks and acts for you — lives in the Muse app. Tap to open Muse.</span></span></button>" +
        "<button class='row' data-open-muse=''><span class='icon'>" + ICON.book + "</span><span class='body'><span class='title'>Memory</span><span class='desc'>What Muse remembers — lives in the Muse app. Tap to open Muse.</span></span></button>";
      body.querySelectorAll("[data-open-muse]").forEach(function (b) {
        b.addEventListener("click", function () { openInMuse(""); });
      });
    } else {
      body.innerHTML = "<h2>Activity</h2><p class='sub'>What Muse just did — the cached snapshot is in the Activity view; the full log needs live Muse.</p>" +
        "<button class='row' id='panelActivity'><span class='icon'>" + ICON.list + "</span><span class='body'><span class='title'>Open Activity view</span><span class='desc'>Recent decided approvals from the cache, with a live /activity check in chat.</span></span></button>";
      $("panelActivity").addEventListener("click", function () { $("panel").classList.remove("show"); showTab("activity"); });
    }
  }

  /* ---------- search: commands + cached content ----------
     One search across all 48 commands AND the cached Feed titles,
     Spaces, goals and ideas. Content hits open their view (Feed /
     Goals / Ideas / drawer Space); command hits prefill the composer.
     Nothing is searched that isn't in the cache or the command list.
     v4.15: every hit states its tier in the same words the rows use
     elsewhere — Tier 1 cached ~1s, Tier 2 live. The suffix is part of
     the indexed desc, so "tier 1" / "tier 2" / "cached" are searchable
     too. Command tiers come from the command entry itself
     ("tier": 1/2 in /api/commands + commands.json, derived from the
     server's FAST_COMMANDS); content hits all trigger a Tier 2 action
     (free text or /space /goal /idea), so they are Tier 2. */
  var TIER1_SUFFIX = " (Tier 1, cached ~1s)";
  var TIER2_SUFFIX = " (Tier 2 — reply in-app within ~75s, else bot chat)";
  function searchIndex() {
    var items = [];
    commands.forEach(function (c) { items.push({ group: "Commands", label: c.cmd, desc: c.desc + (c.tier === 1 ? TIER1_SUFFIX : TIER2_SUFFIX), kind: "cmd", val: c.cmd }); });
    if (cacheData) {
      ((cacheData.feed && cacheData.feed.latest_titles) || []).forEach(function (t) {
        items.push({ group: "Feed", label: t, desc: "Feed post — tap to discuss in chat." + TIER2_SUFFIX, kind: "discuss", val: t });
      });
      (cacheData.spaces || []).forEach(function (s) {
        items.push({ group: "Spaces", label: s.name, desc: "Space — tap to open in chat." + TIER2_SUFFIX, kind: "space", val: s.name });
      });
      ((cacheData.goals && cacheData.goals.items) || []).forEach(function (g) {
        items.push({ group: "Goals", label: g.title, desc: "Goal — " + (g.status || "") + TIER2_SUFFIX, kind: "goal", val: g.title });
      });
      var ideaTitles = ideaTitlesMap(); // v4.19: parsed from the note, known fallback
      ((cacheData.ideas && cacheData.ideas.saved) || []).forEach(function (id) {
        if (ideaTitles[id]) items.push({ group: "Ideas", label: ideaTitles[id], desc: "Saved idea — tap to open in chat." + TIER2_SUFFIX, kind: "idea", val: ideaTitles[id] });
      });
    }
    return items;
  }
  function renderSearch(q) {
    q = (q || "").trim().toLowerCase();
    var box = $("searchResults");
    if (!q) {
      var rec = recentSearches.length
        ? "<div class='recent-label'>Recent searches</div>" + recentSearches.map(function (r) {
            return '<button class="row" data-recent="' + esc(r) + '"><span class="body"><span class="title">' + esc(r) + '</span></span></button>';
          }).join("")
        : "";
      box.innerHTML = "<p class='sub'>Type to search " + commands.length + " commands, Feed posts, Spaces, goals and ideas.</p>" + rec;
      box.querySelectorAll("[data-recent]").forEach(function (b) {
        b.addEventListener("click", function () { $("searchInput").value = b.dataset.recent; renderSearch(b.dataset.recent); });
      });
      return;
    }
    if (recentSearches.indexOf(q) === -1) {
      recentSearches.unshift(q); recentSearches = recentSearches.slice(0, 5);
      savePref("muse-v4-recent-searches", recentSearches);
    }
    var hits = searchIndex().filter(function (it) { return (it.label + " " + it.desc).toLowerCase().indexOf(q) !== -1; });
    if (!hits.length) { box.innerHTML = "<p class='empty'>Nothing found for “" + esc(q) + "”. Press Find to search everywhere with /find.</p>"; return; }
    var groups = [];
    hits.forEach(function (h) { if (groups.indexOf(h.group) === -1) groups.push(h.group); });
    box.innerHTML = groups.map(function (g) {
      return "<div class='search-group'>" + esc(g) + "</div>" + hits.filter(function (h) { return h.group === g; }).map(function (h) {
        return '<button class="row" data-kind="' + esc(h.kind) + '" data-val="' + esc(h.val) + '"><span class="body"><span class="cmd">' + esc(h.label) + '</span><br><span class="desc">' + esc(h.desc) + '</span></span></button>';
      }).join("");
    }).join("");
    box.querySelectorAll("[data-kind]").forEach(function (b) {
      b.addEventListener("click", function () {
        var kind = b.dataset.kind, val = b.dataset.val;
        if (kind === "cmd") { prefill(val + (val.indexOf("<") !== -1 ? " " : "")); showTab("chat"); }
        else if (kind === "discuss") { prefill("Tell me more about: " + val); showTab("chat"); }
        else if (kind === "space") { prefill("/space " + val); showTab("chat"); }
        else if (kind === "goal") { prefill("/goal " + val); showTab("chat"); }
        else if (kind === "idea") { prefill("/idea " + val); showTab("chat"); }
      });
    });
  }
  $("searchInput").addEventListener("input", function () { renderSearch(this.value); });
  $("searchFindBtn").addEventListener("click", function () {
    var q = $("searchInput").value.trim();
    if (q) { prefill("/find " + q); showTab("chat"); }
  });

  /* ---------- composer ---------- */
  function prefill(text) { input.value = text; input.focus(); }
  document.querySelectorAll("#chips button").forEach(function (b) {
    b.addEventListener("click", function () { prefill(b.dataset.chip); }); // prefill only — does NOT send
  });
  $("plusBtn").addEventListener("click", function () {
    // "+" is "more actions": open the search over all 48 commands.
    showTab("search"); $("searchInput").focus();
  });
  $("micBtn").addEventListener("click", function () {
    /* v4.7: switch to chat first — the answer is a chat bubble, and
       on any other view it would land in the hidden thread. */
    showTab("chat");
    addBubble("Voice input isn't available inside Telegram. Type your message instead — or open Muse for voice mode.", "muse");
  });
  $("sendBtn").addEventListener("click", send);
  input.addEventListener("keydown", function (e) { if (e.key === "Enter") send(); });

  function copyText(text, done) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(text); done(); });
    } else { fallbackCopy(text); done(); }
  }
  function fallbackCopy(text) {
    var ta = document.createElement("textarea"); ta.value = text;
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (e) {}
    document.body.removeChild(ta);
  }

  function openInMuse(text) {
    // Escape hatch: copy first, then open the live Muse chat (tap-gated).
    var done = function () {
      if (tg) { try { tg.openLink("https://muse.ai"); return; } catch (e) {} }
      window.open("https://muse.ai", "_blank");
    };
    if (text) copyText(text, done); else done();
  }

  function sendViaBot(text) {
    // The ONLY tg.sendData path. Explicit tap, labelled slow, warns first.
    if (!tg) { addBubble("You're not inside Telegram, so there's no bot to send to. Copy the message and paste it to @Manis_muse_bot instead.", "muse"); return; }
    var ok = true;
    if (tg.showConfirm) {
      tg.showConfirm("Send via the Telegram bot instead? This closes the app and the reply lands in the bot chat in ~10-20s.", function (confirmed) {
        if (confirmed) { try { tg.sendData(text); } catch (e) { addBubble("Couldn't send via the bot: " + e.message, "muse"); } }
      });
    } else if (window.confirm("Send via the Telegram bot instead? This closes the app; reply lands in the bot chat in ~10-20s.")) {
      try { tg.sendData(text); } catch (e) { addBubble("Couldn't send via the bot: " + e.message, "muse"); }
    }
    return ok;
  }

  function send() {
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    /* v4.7: the reply lands in the chat thread — if the user sent
       from another view (the composer is always visible), switch to
       chat first or the reply + waiting state would be invisible
       behind that view. */
    showTab("chat");
    addBubble(text, "user");
    setStatus("Muse is thinking…", true);
    setTierBar("Checking…");
    var thinking = document.createElement("div");
    thinking.className = "thinking";
    thread.appendChild(thinking);
    var waitStart = Date.now();
    function setThinking(text) {
      /* v4.4: keep the animated dots AND a live seconds counter. The
         timer updates only .wait-secs — v4.3 set textContent every
         second, which wiped the <span class="dots"> and killed the
         animation after the first tick. */
      thinking.innerHTML = "";
      thinking.appendChild(document.createTextNode(text + " "));
      var secs = document.createElement("span"); secs.className = "wait-secs";
      secs.textContent = Math.floor((Date.now() - waitStart) / 1000) + "s";
      thinking.appendChild(secs);
      var dots = document.createElement("span"); dots.className = "dots";
      thinking.appendChild(dots);
    }
    setThinking("Muse is thinking…");
    var waitTimer = setInterval(function () {
      var secs = thinking.querySelector(".wait-secs");
      if (secs) secs.textContent = Math.floor((Date.now() - waitStart) / 1000) + "s";
    }, 1000);
    scrollChatToBottom(); // v4.8: just enough to show the waiting line

    // Live status via SSE, then the answer (or the honest Tier 2 notice).
    var es = null;
    try {
      es = new EventSource(apiBase() + "/api/events?text=" + encodeURIComponent(text) + "&initData=" + encodeURIComponent(initData));
    } catch (e) { es = null; }
    var settled = false;
    var terminal = false;   // a cached / reply / reply_timeout SSE event already arrived
    var waiting = false;    // needs_muse seen: stream stays open for an in-app reply
    var seenAny = false;    // any SSE event at all (guards the POST safety net)
    var posted = false;     // the POST fallback already ran (prevents double queueing)
    function finish(replyText, tag, tierHtml) {
      if (settled) return; settled = true;
      if (waitTimer) clearInterval(waitTimer);
      if (es) es.close();
      thinking.remove();
      setStatus("Tap the avatar for activity & approvals", false);
      if (replyText) addBubble(replyText, "muse", tag);
      if (tierHtml) setTierBar(tierHtml);
    }
    function tier2Buttons() {
      // v4.19.1: the "Open in Muse" filled button used to sit here in the
      // tier bar and blocked the chat view — it now lives ONLY in Settings.
      // The only in-chat button left is the tap-gated bot fallback.
      return '<button class="ghost-btn" id="sendBotBtn">Send via Telegram bot instead (slow, ~10-20s)</button>';
    }
    function wireTier2Buttons() {
      var sb = $("sendBotBtn"); if (sb) sb.addEventListener("click", function () { sendViaBot(text); });
    }
    function botChatNotice() {
      return "This needs your live Muse. The reply lands in the bot chat in ~10-20s — Muse has to wake up first. " +
        "Or open Muse from Settings for an instant live chat.";
    }
    if (es) {
      es.onmessage = function (ev) {
        seenAny = true;
        var d; try { d = JSON.parse(ev.data); } catch (e) { return; }
        if (d.event === "checking-cache") { thinking.innerHTML = "Checking the cache<span class=\"dots\"></span>"; }
        else if (d.event === "cached") {
          terminal = true;
          finish(d.reply, "from cache, checked " + fmtTime(d.cache_updated_utc) + (d.stale ? " — stale" : ""),
            "✓ Answered from cache in ~1s · checked " + esc(fmtTime(d.cache_updated_utc)));
        } else if (d.event === "needs_muse") {
          // Not terminal: the stream stays open for an in-app reply (§10.6).
          // Same speed as bot chat — the reply just arrives here instead.
          waiting = true;
          /* Short on purpose: the full sentence wrapped to two lines
             and the second line slid under the fixed composer (v4.4
             shot 03). The detail already lives in the tier bar below. */
          setThinking("Muse is working…");
          // v4.16: the waiting line used the hourglass colour emoji — the last
          // one left after the v4.11 SVG unification; it rendered as a
          // mismatched glyph next to the SVG tab bar. Now the line-SVG
          // clock from the ICON map, like every other icon.
          setTierBar('<span class="tier-text">' + ICON.clock + ' Muse is waking up — waiting for a reply in this app (up to ~75s)</span>' + tier2Buttons());
          wireTier2Buttons();
        } else if (d.event === "reply") {
          terminal = true;
          finish(d.reply, "Live reply — arrived in the app" + (d.replied_at_utc ? " · " + fmtTime(d.replied_at_utc) : ""),
            '<span class="tier-text">✓ Reply arrived in the app</span>' + tier2Buttons());
          wireTier2Buttons();
        } else if (d.event === "reply_timeout") {
          terminal = true;
          finish(botChatNotice(), "Tier 2 — reply in bot chat, ~10-20s",
            '<span class="tier-text">' + ICON.clock + ' Muse is waking up — reply lands in the bot chat in ~10-20s</span>' + tier2Buttons());
          wireTier2Buttons();
        }
      };
      // Only fall back to POST if the stream died BEFORE any event arrived.
      // After needs_muse the stream is intentionally kept open for the
      // in-app reply; a dead stream there means the reply lands in bot chat.
      es.onerror = function () {
        if (settled || terminal) return;
        try { es.close(); } catch (e) {}
        if (waiting) {
          finish(botChatNotice(), "Tier 2 — reply in bot chat, ~10-20s",
            '<span class="tier-text">' + ICON.clock + ' Muse is waking up — reply lands in the bot chat in ~10-20s</span>' + tier2Buttons());
          wireTier2Buttons();
        } else if (!seenAny) { postMessage(); }
      };
      // Safety net: if SSE stalls with NO event at all, POST after 5s.
      setTimeout(function () { if (!settled && !terminal && !waiting && !seenAny) { try { es.close(); } catch (e) {} postMessage(); } }, 5000);
    } else { postMessage(); }

    function postMessage() {
      if (posted) return; posted = true;
      fetch(apiBase() + "/api/message", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text, initData: initData })
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.status === "cached") {
          finish(d.reply, "from cache, checked " + fmtTime(d.cache_updated_utc) + (d.stale ? " — stale" : ""),
            "✓ Answered from cache in ~1s · checked " + esc(fmtTime(d.cache_updated_utc)));
        } else {
          finish(botChatNotice(), "Tier 2 — reply in bot chat, ~10-20s",
            '<span class="tier-text">' + ICON.clock + ' Muse is waking up — reply lands in the bot chat in ~10-20s</span>' + tier2Buttons());
          wireTier2Buttons();
        }
      }).catch(function (err) {
        showBackendError(true);
        finish("Couldn't reach the app backend (" + err.message + "). " +
          "Your message wasn't sent anywhere. You can still open Muse from Settings for a live chat, or send it via the bot.",
          "Backend unreachable",
          tier2Buttons());
        wireTier2Buttons();
      });
    }
  }

  /* ---------- boot ---------- */
  // Restore the saved thread if there is one; only greet on a fresh thread.
  // v4.11: greeting shortened again — v4.9's 219 chars still
  // wrapped to 8 lines in v4.10 shot 48 (large bubble font at
  // 390 px). Two short sentences, ~130 chars now; the latency
  // detail already lives in the tier bar below the composer.
  // Keeps the exact phrase "within about 75s" (a v4.6 probe guards it).
  var GREETING = "Hi — this is your Muse, in Telegram. Ask anything; cached ~1s, " +
    "live ~10–20s, in-app within about 75s.";
  store.get(function (raw) {
    var saved = null;
    try { saved = raw ? JSON.parse(raw) : null; } catch (e) { saved = null; }
    if (Array.isArray(saved) && saved.length) {
      threadMsgs = saved.filter(function (m) { return m && typeof m.text === "string" && (m.who === "user" || m.who === "muse"); }).slice(-THREAD_MAX);
      threadMsgs.forEach(function (m) { addBubble(m.text, m.who, m.tag || "", { record: false, reaction: m.reaction || "", ts: m.ts || Date.now() }); });
      setStatus("Thread restored — pick up where you left off", false);
    } else {
      addBubble(GREETING, "muse", "Muse Telegram App v4.19.1 — dynamic app, bot is backup only");
    }
  });
  // v4.19.1: no "Open in Muse" button in the chat — it blocked the view.
  // It lives ONLY in Settings now; the tier bar carries the latency text.
  setTierBar('<span class="tier-text">Cached answers ~1s · live Muse ~10-20s, in-app up to ~75s, otherwise in bot chat</span>');
  $("backendRetryBtn").addEventListener("click", function () { showBackendError(false); loadCache(); });

  renderSkeletons();
  function loadCache() {
    renderSkeletons();
    fetch(apiBase() + "/api/cache").then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (d) {
      showBackendError(false);
      cacheData = d; renderCacheViews();
    }).catch(function () { cacheData = null; showBackendError(true); renderCacheError(); });
  }
  loadCache();
  fetch(apiBase() + "/api/commands").then(function (r) { return r.json(); }).then(function (d) {
    commands = d.commands || []; renderSearch("");
  }).catch(function () {
    fetch(apiBase() + "/commands.json").then(function (r) { return r.json(); }).then(function (d) {
      commands = d; renderSearch("");
    }).catch(function () { commands = []; });
  });
  renderSearch("");
})();
