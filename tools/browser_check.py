#!/usr/bin/env python3
"""Drive the real app in headless Chrome and check that its controls respond.

    python tools/browser_check.py
    python tools/browser_check.py --compare-stale 3db9b76
    python tools/browser_check.py --url https://choochatgpt.github.io/notes/

The static checker can prove an id exists in the markup and that a listener is
attached in the source. It cannot prove a *click does anything*, and that is the
failure this project actually hit: a publish briefly paired a new index.html with
a still-cached app.js, the mismatch threw during startup, and because the controls
were wired after the first render every button on the page went dead with nothing
on screen explaining why. The report was "the top buttons do nothing".

So this clicks the real buttons in a real browser and inspects the real DOM.

--compare-stale rebuilds the broken pairing from a git revision and runs both, to
confirm the harness still detects the fault it was written for. A check that can
no longer fail is not a check.

--url drives an app that is already served, which is the only way to test what a
device actually downloads. Confirming the published bytes match the local copy
proves the upload landed, not that the deployed app works.

Two things this harness must not do, both of which produced misleading results in
an earlier version and are easy to reintroduce:

  * It must not race the app. It waits until the app has visibly settled -- the
    status line stops saying "checking" -- rather than sleeping a fixed amount.
    It also polls for the app shell, because an iframe starts on about:blank,
    whose document is already "complete".
  * It must not use --virtual-time-budget. Virtual time ran past the waits while
    real IndexedDB work was still outstanding, so the app was inspected
    mid-startup and looked dead for the wrong reason.

The probe reports over the network rather than through --dump-dom, so Chrome stays
alive until the answer actually arrives. alert() is stubbed inside the frame,
because a real alert blocks headless Chrome forever.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import re
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORK = Path(tempfile.gettempdir()) / "notes_browser_check"

# The app's own copy carries characters the Windows console codepage (cp1252 on
# this machine) cannot encode -- an accent or an en dash printed inside a status
# line would kill the report after every check had already passed. Print in
# UTF-8 and never let the console encoding decide whether a run can be read.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/chromium"),
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
]

PROBE = """<!doctype html>
<meta charset="utf-8">
<title>probe</title>
<iframe id="app" src="./index.html" width="900" height="760"></iframe>
<script>
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const results = [];
  // v27 debug instrument: beats name the last check executed, so a hang is
  // reported as "last check before the stall" instead of a bare deadline.
  let lastCheck = "boot";
  setInterval(() => {
    fetch("/__beat", { method: "POST", body: JSON.stringify({ last: lastCheck }) })
      .catch(() => {});
  }, 1500);
  const check = (name, ok, detail) => { lastCheck = name; console.log("CHECK:", ok ? "ok" : "FAIL", name); return results.push({ name, ok: !!ok, detail: detail || "" }); };

  // The confirmation has to state what goes with the folder, and in the singular
  // -- "1 note", not "1 notes". The count is the entire point of the wording:
  // "delete this?" reads identically for an empty folder and for one holding
  // twenty notes.
  const writingSaysOneNote = text => /(^|\\D)1 note\\b/.test(text) && !/1 notes/.test(text);

  async function waitFor(predicate, ms) {
    const deadline = Date.now() + (ms || 15000);
    while (Date.now() < deadline) {
      if (predicate()) return true;
      await sleep(100);
    }
    return false;
  }

  try {
    const frame = document.getElementById("app");

    // Poll for the app itself. An iframe starts on about:blank, whose document is
    // already "complete", so waiting on readyState inspects the wrong document.
    const appeared = await waitFor(
      () => frame.contentDocument && frame.contentDocument.querySelector(".app-shell")
    );
    check("app shell rendered", appeared, appeared ? "" : "the iframe never presented the app");
    if (!appeared) throw new Error("no app shell in the frame; the rest cannot be probed");

    const doc = frame.contentDocument;
    const body = doc.body;
    const q = sel => doc.querySelector(sel);
    const status = () => { const el = q("#app-status"); return el ? el.textContent : "n/a"; };
    const statusbar = () => q(".statusbar");

    // Stub the blocking dialogs inside the frame before anything can call them.
    // confirm() is answerable rather than fixed, so a destructive path can be
    // cancelled and then taken, and the message it showed can be inspected.
    const alerts = [];
    const confirms = [];
    let confirmAnswer = false;
    frame.contentWindow.alert = message => alerts.push(message);
    frame.contentWindow.confirm = message => {
      confirms.push(String(message));
      return confirmAnswer;
    };

    // Fake both network transports (v27; per-identity stores since v29): the
    // Drive copy's GIS token client is stubbed on the frame's window so each
    // mocked identity ("A"/"B") gets its own token, and a selective fetch
    // wrapper scripts api.github.com (the relay happy path) and
    // www.googleapis.com (folder find-or-create, resumable initiation,
    // session PUT) with ONE fake Drive per bearer token -- so every identity
    // exercises its own store and the probe can prove the two users' runs
    // never touch each other's Drive. Everything else falls through to the
    // real fetch, so the probe still reads sw.js itself. Result: the entire
    // sync + Drive flow below is exercised with ZERO real network -- the
    // same discipline as the no-token sync checks.
    let ghMode = "ok";
    const ghCalls = [];
    const gapiCalls = [];
    let probeAccount = "A";
    const gapiStores = {};   // bearer token -> that account's fake Drive
    frame.contentWindow.__gapiStores = gapiStores;
    let driveUploadStatus = 201;
    const gisCalls = { init: 0, request: 0, lastConfig: null, mode: "ok", tokens: [] };

    function fakeResponse(status, data, headers) {
      return {
        status,
        text: async () => JSON.stringify(data),
        headers: { get: name => (headers && headers[String(name).toLowerCase()]) || null }
      };
    }
    function ghFake(u, init) {
      const method = (init.method || "GET").toUpperCase();
      const path = u.replace("https://api.github.com", "");
      if (ghMode === "fail") {
        // Scripted whole-provider outage (v28): used to prove a relay
        // failure cannot mask a Google Drive success.
        return fakeResponse(500, { message: "probe github outage" });
      }
      if (method === "GET" && /\\/git\\/ref\\/heads\\/notes-inbox$/.test(path)) {
        return fakeResponse(200, { object: { sha: "parentsha1" } });
      }
      if (method === "GET" && /\\/git\\/commits\\/parentsha1$/.test(path)) {
        return fakeResponse(200, { tree: { sha: "treesha1" } });
      }
      if (method === "GET" && /\\/git\\/trees\\//.test(path)) {
        return fakeResponse(200, { tree: [] });
      }
      if (method === "POST" && /\\/git\\/blobs$/.test(path)) {
        return fakeResponse(201, { sha: "blobsha" + Math.random().toString(16).slice(2, 8) });
      }
      if (method === "POST" && /\\/git\\/trees$/.test(path)) {
        return fakeResponse(201, { sha: "newtreesha" });
      }
      if (method === "POST" && /\\/git\\/commits$/.test(path)) {
        return fakeResponse(201, { sha: "newcommitsha" });
      }
      if (method === "PATCH" && /\\/git\\/refs/.test(path)) {
        return fakeResponse(200, { object: { sha: "newcommitsha" } });
      }
      return fakeResponse(999, { message: "unscripted github " + method + " " + path });
    }
    function storeFor(token) {
      if (!gapiStores[token]) {
        gapiStores[token] = {
          folderCreated: 0,
          folderListHasFolder: false,
          folderId: "",
          files: [],
          lastUploadName: ""
        };
      }
      return gapiStores[token];
    }
    function gapiFake(u, init) {
      const method = (init.method || "GET").toUpperCase();
      const bearer = /Bearer (\\S+)/.exec((init.headers && init.headers.Authorization) || "");
      const store = storeFor(bearer ? bearer[1] : "(unauthenticated)");
      if (method === "GET" && u.indexOf("/drive/v3/files?") !== -1) {
        return fakeResponse(200, { files: store.folderListHasFolder
          ? [{ id: store.folderId, name: "Notes Backup" }] : [] });
      }
      if (method === "POST" && u.indexOf("/upload/") !== -1) {
        // Real Drive behaves this way too: the PUT's final response echoes the
        // file resource with the name the initiation's metadata carried, which
        // is what the app prints ("Google Drive: Copied <name> to your Drive.").
        try { store.lastUploadName = JSON.parse(init.body).name || store.lastUploadName; } catch (e) {}
        return fakeResponse(200, {},
          { location: "https://www.googleapis.com/upload/session/probe" });
      }
      if (method === "POST") {
        store.folderCreated++;
        store.folderListHasFolder = true;
        store.folderId = "fld-" + (bearer ? bearer[1].slice(-1) : "?");
        return fakeResponse(200, { id: store.folderId, name: "Notes Backup" });
      }
      if (method === "PUT") {
        if (driveUploadStatus !== 200 && driveUploadStatus !== 201) {
          // A refused upload must not create a file in the store either.
          return fakeResponse(driveUploadStatus,
            { message: "probe upload refused" });
        }
        store.files.push({
          id: "zipfile-" + store.files.length,
          name: store.lastUploadName || "notes-backup-probe.zip"
        });
        return fakeResponse(driveUploadStatus,
          { id: "zipfile-" + (store.files.length - 1), name: store.lastUploadName });
      }
      return fakeResponse(999, { message: "unscripted gapi " + method + " " + u });
    }
    frame.contentWindow.google = { accounts: { oauth2: {
      initTokenClient: cfg => {
        gisCalls.init++;
        gisCalls.lastConfig = cfg;
        return { requestAccessToken: () => {
          gisCalls.request++;
          // Each "account" the probe selects gets its own token, the way two
          // real Google accounts would; every googleapis call below routes by
          // this token so identity separation is provable end to end.
          const token = "probe-drive-token-" + probeAccount;
          gisCalls.tokens.push(token);
          setTimeout(() => {
            if (gisCalls.mode === "popup") {
              if (cfg.error_callback) {
                cfg.error_callback({ type: "popup_failed_to_open", message: "blocked" });
              }
              return;
            }
            if (cfg.callback) {
              cfg.callback({ access_token: token, expires_in: 3599 });
            }
          }, 30);
        } };
      }
    } } };
    const realFrameFetch = frame.contentWindow.fetch.bind(frame.contentWindow);
    frame.contentWindow.fetch = (url, init = {}) => {
      const u = String(url);
      if (u.indexOf("https://api.github.com") === 0) {
        ghCalls.push({ u, init });
        return Promise.resolve(ghFake(u, init));
      }
      if (u.indexOf("www.googleapis.com") !== -1) {
        gapiCalls.push({ u, init });
        return Promise.resolve(gapiFake(u, init));
      }
      return realFrameFetch(url, init);
    };

    // Wait for the app to settle on an explicit signal, never on a string the
    // app happens to print. data-ready is set at the end of init(); data-fatal
    // is set instead when startup threw.
    const settled = await waitFor(
      () => body.dataset.fatal === "true" || body.dataset.ready === "true"
    );
    check("app settled after startup", settled,
          "ready=" + body.dataset.ready + " fatal=" + body.dataset.fatal);

    check("no fatal startup banner", body.dataset.fatal !== "true", "status=" + status());

    // --- the status bar is for failures and takes no space otherwise ---
    // It used to hold two lines of small print: local storage usage, and the
    // next reminder. The agenda above already says what is coming up, so both
    // were removed -- and the bar has to actually give the space back, not just
    // empty out while keeping its padding and border.
    const bar = statusbar();
    if (bar) {
      const style = frame.contentWindow.getComputedStyle(bar);
      // getComputedStyle hands back a LIVE declaration, so these have to be
      // read out as strings now. Holding the object and comparing it to itself
      // after a change compares the new value with the new value.
      const restDisplay = style.display;
      const restBg = style.backgroundColor;
      const box = bar.getBoundingClientRect();
      const said = q("#storage-status") || q("#reminder-status");
      check("the footer takes no space when nothing has failed",
            (restDisplay === "none" || box.height === 0),
            "display=" + restDisplay + " height=" + Math.round(box.height));
      check("the storage and next-reminder lines are gone, not just hidden",
            !said, said ? "still present: " + said.id : "");

      // Hiding the bar at rest is only safe if a failure still brings it back.
      // Force the two things a startup failure sets and measure the result,
      // then put the page back exactly as it was.
      bar.hidden = false;
      body.dataset.fatal = "true";
      await sleep(60);
      const fatalStyle = frame.contentWindow.getComputedStyle(bar);
      const fatalBox = bar.getBoundingClientRect();
      check("a failure brings the footer back and tints it",
            fatalBox.height > 0 && fatalStyle.display !== "none"
            && fatalStyle.backgroundColor !== restBg,
            "height=" + Math.round(fatalBox.height) + " display=" + fatalStyle.display
            + " bg=" + fatalStyle.backgroundColor + " rest=" + restBg);
      bar.hidden = true;
      delete body.dataset.fatal;
      await sleep(60);
      check("the footer goes away again once the failure is cleared",
            bar.getBoundingClientRect().height === 0,
            "height=" + Math.round(bar.getBoundingClientRect().height));
    } else {
      // Removing the element entirely is the trap: it is the only thing that
      // makes a startup failure visible, and an app that cannot start otherwise
      // looks like one whose every button is broken.
      check("the footer still exists to carry a startup failure", false,
            "no .statusbar in the shell");
    }

    // --- the split: the two panes share the screen evenly, at the default ---
    // Equal rows in the source do not prove equal panes on screen; a min-height
    // or a max-height on either one breaks the split without touching the rule.
    // This runs before any Settings click, so what is measured is the CSS
    // fallback (1fr/1fr) a fresh profile starts from -- the ratio button is
    // exercised further down, and it must move this from where it starts.
    const upperPane = q(".app-shell > .pane:not(.pane-agenda)");
    const lowerPane = q(".pane-agenda");
    if (upperPane && lowerPane) {
      const notesH = upperPane.getBoundingClientRect().height;
      const agendaH = lowerPane.getBoundingClientRect().height;
      const gap = Math.abs(notesH - agendaH);
      check("the notes pane and the agenda pane are the same height (the default 50%)",
            gap <= Math.max(2, notesH * 0.02),
            "notes=" + Math.round(notesH) + " agenda=" + Math.round(agendaH)
            + " gap=" + Math.round(gap));
    } else {
      check("both panes are in the shell", false,
            "notesPane=" + !!upperPane + " agendaPane=" + !!lowerPane);
    }

    // --- top-bar wiring: click the real controls, look for the real effect ---
    const folderForm = q("#new-folder-form");
    q("#new-folder-btn").click();
    await sleep(300);
    check("New folder button responds", !folderForm.classList.contains("hidden"),
          "formHidden=" + folderForm.classList.contains("hidden"));

    q("#new-note-btn").click();
    await sleep(900);
    const noteEditor = q("#note-editor");
    check("New note button responds",
          body.dataset.mode === "edit" && !noteEditor.classList.contains("hidden"),
          "mode=" + body.dataset.mode + " editorHidden=" + noteEditor.classList.contains("hidden")
          + " status=" + status());

    q("#reminders-tab").click();
    await sleep(500);
    check("Reminders tab switches the pane", body.dataset.kind === "reminders",
          "data-kind=" + body.dataset.kind);

    q("#new-reminder-btn").click();
    await sleep(900);
    check("New reminder button responds",
          body.dataset.mode === "edit" && body.dataset.kind === "reminders",
          "mode=" + body.dataset.mode + " kind=" + body.dataset.kind + " status=" + status());

    // Fill it in and save, so the agenda below has a real reminder to lay out
    // rather than its empty-state paragraph. A weekly Tue+Thu rule is the case
    // the user described, and it is the one whose label used to omit the period.
    q("#reminder-title").value = "Probe Standup";
    q("#reminder-start").value = "2027-01-05T09:00";
    const repeat = q("#reminder-repeat");
    repeat.value = "weekly";
    repeat.dispatchEvent(new frame.contentWindow.Event("change", { bubbles: true }));
    await sleep(300);
    for (const day of ["2", "4"]) {
      const box = [...doc.querySelectorAll("#weekday-picker input")].find(b => b.value === day);
      if (box) box.checked = true;
    }
    q("#reminder-editor").requestSubmit();
    await sleep(900);
    q("#editor-back").click();
    await sleep(700);

    // The release chip beside the title (2026-10-05): visible without
    // opening Settings, and always showing the RUNNING version.
    const homeChip = q("#home-version");
    const settingsChip = q("#app-version");
    check("the home screen shows the release beside the title",
          !!homeChip && /^v\\d+$/.test(homeChip.textContent)
          && (!settingsChip || homeChip.textContent === "v" + settingsChip.textContent),
          "home=" + (homeChip ? homeChip.textContent : "missing")
          + " settings=" + (settingsChip ? settingsChip.textContent : "missing"));

    // --- settings: the dialog, the ratio cycle, the export handoff ---
    // The dialog is modal, so everything driven inside it happens here and it
    // is closed again before the page-level checks below resume.
    const settingsBtn = q("#settings-btn");
    check("the top bar offers Settings", !!settingsBtn,
          settingsBtn ? "" : "no #settings-btn");
    if (settingsBtn) {
      settingsBtn.click();
      await sleep(300);
      const settingsDialog = q("#settings-dialog");
      check("Settings opens the dialog", !!(settingsDialog && settingsDialog.open),
            "open=" + (settingsDialog ? settingsDialog.open : "no dialog"));

      // The visible release number (the user asked for it): the chip must
      // carry exactly the version the harness read out of view.js when it
      // built this probe -- a stale or missing chip fails outright.
      const shownVersion = (q("#app-version") || {}).textContent || "";
      const expectedVersion = "__EXPECTED_VERSION__";
      check("the dialog shows the current app version",
            shownVersion.trim() === expectedVersion,
            "shown=" + JSON.stringify(shownVersion)
            + " expected=" + expectedVersion);
      // On a deployment the live sw.js is reachable, so the chip can also be
      // compared against what the shell ACTUALLY caches there. A local stage
      // deliberately ships no sw.js (its registration 404s by design), so for
      // it the injected expectation above is the whole assertion.
      if (frame.contentWindow.location.origin !== location.origin) {
        try {
          // fetch inside the frame's realm, so "sw.js" resolves against the
          // app's own origin; the fresh profile's empty cache cannot answer
          // it with a stale cached copy.
          const response = await frame.contentWindow.fetch("sw.js");
          const cacheMatch = /CACHE_NAME = "notes-shell-v(\\d+)"/.exec(await response.text());
          check("the shown version is the shell cache version",
                !!cacheMatch && cacheMatch[1] === shownVersion.trim(),
                "shown=" + shownVersion.trim()
                + " sw=" + (cacheMatch ? cacheMatch[1] : "none"));
        } catch (error) {
          check("the shown version is the shell cache version", false,
                String(error && error.message || error));
        }
      }

      // The three controls, by the exact labels the user asked for.
      const dialogText = settingsDialog ? settingsDialog.textContent : "";
      for (const label of ["Notes/Reminders panel display ratio",
                           "Export notes/reminders to email",
                           "Import notes/reminders"]) {
        check("the dialog offers " + JSON.stringify(label),
              dialogText.indexOf(label) !== -1);
      }

      // The cycle: chip text AND the real pane heights. A chip that updates
      // while the grid does not would pass a text-only check, and that is the
      // regression this exists for. The 6px gap between the panes cancels out
      // of top/(top+bottom), so the measured share should equal the ratio's
      // left-hand side wherever the cycle happens to be.
      const chipText = () => {
        const el = q("#ratio-value");
        return el ? el.textContent.trim() : "";
      };
      const paneShare = () => {
        const top = q(".app-shell > .pane:not(.pane-agenda)");
        const bot = q(".pane-agenda");
        if (!top || !bot) return -1;
        const t = top.getBoundingClientRect().height;
        const b = bot.getBoundingClientRect().height;
        return t + b > 0 ? t / (t + b) : -1;
      };
      // Before any click the dialog must show the default even split: the chip
      // STARTS at 50%, and since the 10%-step revision (2026-10-05) 50% is
      // itself one of the offered stops, so the first click simply advances.
      check("the ratio control opens on the default 50%",
            chipText() === "50%" && Math.abs(paneShare() - 0.5) <= 0.04,
            "chip=" + chipText() + " topShare=" + paneShare().toFixed(3));
      // The ten-percent steps in cycle order from the even split, then one
      // more click to prove the cycle continues past 50% -- which also leaves
      // a non-default share behind for the persistence checks at the end.
      const cycle = [["60%", 0.6], ["70%", 0.7], ["80%", 0.8], ["90%", 0.9],
                     ["10%", 0.1], ["20%", 0.2], ["30%", 0.3], ["40%", 0.4],
                     ["50%", 0.5], ["60%", 0.6]];
      for (const [label, share] of cycle) {
        q("#ratio-btn").click();
        await waitFor(() => chipText() === label, 3000);
        await sleep(150);  // let layout settle after the style write
        const got = paneShare();
        check("a ratio click lands on " + label + " and re-balances the panes",
              chipText() === label && Math.abs(got - share) <= 0.04,
              "chip=" + chipText() + " topShare=" + got.toFixed(3)
              + " expected=" + share.toFixed(3));
      }

      // Export: the CSV itself, the mailto handoff (the attribute is read, never
      // clicked -- a real mailto hangs headless Chrome), and the copy
      // confirmation. The panel is exactly Share CSV for backup + Export CSV
      // since the user asked the other two buttons off (2026-10-01).
      q("#export-btn").click();
      await waitFor(() => {
        const box = q("#export-csv");
        return box && box.value.length > 0;
      }, 5000);
      const exportPanel = q("#export-panel");
      check("the export panel opens",
            !!exportPanel && !exportPanel.classList.contains("hidden"));
      const csvBox = q("#export-csv");
      const exportedCsv = csvBox ? csvBox.value : "";
      check("the CSV holds the current backup under the versioned header",
            exportedCsv.indexOf("# notes-backup v" + "__EXPECTED_SCHEMA__") === 0
            && exportedCsv.indexOf("Probe Standup") !== -1,
            exportedCsv.slice(0, 70));
      check("the CSV carries all three sections",
            ["## folders", "## notes", "## reminders"]
              .every(section => exportedCsv.indexOf(section) !== -1));

      // Exactly two ways out, under the exact labels the user asked for.
      check("the export panel offers exactly Share CSV for backup + Export CSV",
            !!q("#copy-csv-btn") && /Share CSV for backup/.test(q("#copy-csv-btn").textContent)
            && !!q("#open-email-btn") && /Export CSV/.test(q("#open-email-btn").textContent),
            "copy=" + (q("#copy-csv-btn") ? q("#copy-csv-btn").textContent.trim() : "missing")
            + " export=" + (q("#open-email-btn") ? q("#open-email-btn").textContent.trim() : "missing"));
      check("the download and share buttons are gone, not hidden",
            !q("#download-csv-btn") && !q("#share-csv-btn"),
            "download=" + !!q("#download-csv-btn") + " share=" + !!q("#share-csv-btn"));

      const emailInput = q("#backup-email");
      emailInput.value = "probe@example.com";
      emailInput.dispatchEvent(new frame.contentWindow.Event("change", { bubbles: true }));
      await sleep(400);

      const mailLink = q("#open-email-btn");
      const href = mailLink ? (mailLink.getAttribute("href") || "") : "";
      check("the mail link addresses the entered email",
            href.indexOf("mailto:probe@example.com?") === 0, href.slice(0, 60));
      const decoded = decodeURIComponent(href);
      check("...and carries the backup in the body",
            decoded.indexOf("# notes-backup v" + "__EXPECTED_SCHEMA__") !== -1
            && decoded.indexOf("Probe Standup") !== -1,
            "decoded=" + decoded.length + " chars");
      check("the mail link is offered, since this backup fits the safe limit",
            !!mailLink && !mailLink.classList.contains("hidden")
            && mailLink.getAttribute("aria-disabled") !== "true",
            "hidden=" + (mailLink ? mailLink.classList.contains("hidden") : "n/a"));

      q("#copy-csv-btn").click();
      await sleep(400);
      const sizeNote = q("#export-size-note");
      check("the clipboard copy confirms or explains itself",
            /Copied to the clipboard|Copying was blocked/.test(
              sizeNote ? sizeNote.textContent : ""),
            "said=" + (sizeNote ? sizeNote.textContent : "n/a"));

      // One-tap backup (2026-10-03; destination-independent since v28): the
      // export panel's primary action runs every configured destination, and
      // each destination gates itself. NO network is touched in these first
      // checks: the profile starts with no token and no Client ID, so
      // runBackupAll must end before its first fetch.
      const syncBtn = q("#sync-now-btn");
      check("the backup button is present and destination-neutral",
            !!syncBtn && /Back up notes \\+ photos now/.test(syncBtn.textContent),
            "label=" + (syncBtn ? syncBtn.textContent.trim() : "missing"));
      const tokenInput = q("#sync-token");
      check("the token field exists, is a password field, and is not prefilled",
            !!tokenInput && tokenInput.type === "password" && !tokenInput.value,
            "type=" + (tokenInput ? tokenInput.type : "missing"));
      // Opening the export panel IS a backup attempt now: with no destination
      // configured it says so plainly, so an empty line here would mean the
      // auto-run never happened.
      check("the sync status line exists and opening export already explains the setup",
            !!q("#sync-status") && /backup token/i.test(q("#sync-status").textContent),
            "said=" + (q("#sync-status") ? q("#sync-status").textContent : "missing"));
      // TEST F of the v28 matrix: NEITHER destination configured -- the
      // overall verdict must name the empty state and nothing may be
      // contacted.
      check("TEST F: with no destination configured the overall line says so",
            !!q("#backup-overall")
              && /No backup destination is configured/.test(q("#backup-overall").textContent),
            "said=" + (q("#backup-overall") ? q("#backup-overall").textContent : "missing"));
      check("TEST F: both destination lines say Not configured",
            /Not configured/.test(q("#drive-status").textContent)
              && /Not configured/.test(q("#sync-status").textContent),
            "drive=" + q("#drive-status").textContent
              + " relay=" + q("#sync-status").textContent);
      syncBtn.click();
      await sleep(400);
      check("clicking backup with no destination configured explains instead of failing",
            /No backup destination is configured/.test(q("#backup-overall").textContent),
            "said=" + q("#backup-overall").textContent);
      check("TEST F: the empty-state tap contacted nothing at all",
            ghCalls.length === 0 && gapiCalls.length === 0 && gisCalls.request === 0,
            "gh=" + ghCalls.length + " gapi=" + gapiCalls.length
              + " gis=" + gisCalls.request);
      check("...and made no network call (backup stays idle)",
            q("#sync-now-btn").disabled === false,
            "disabled=" + q("#sync-now-btn").disabled);
      // v29: the per-device Client ID paste flow is gone from the shared app.
      // Every visitor connects with a button; with nothing configured that
      // button is inert and the status line is the explanation.
      check("v29: the per-device Client ID paste field is gone",
            !q("#drive-client-id") && !q("#drive-id-save") && !q("#drive-id-remove"),
            "field=" + !!q("#drive-client-id") + " save=" + !!q("#drive-id-save"));
      check("v29: the Drive buttons are Connect/Disconnect Google Drive",
            !!q("#drive-connect")
              && /Connect Google Drive/.test(q("#drive-connect").textContent)
              && !!q("#drive-disconnect")
              && /Disconnect Google Drive/.test(q("#drive-disconnect").textContent),
            "connect=" + (q("#drive-connect") ? q("#drive-connect").textContent : "missing"));
      check("v29: Connect with nothing configured is disabled and the line explains",
            q("#drive-connect").disabled === true
              && /Not configured/.test(q("#drive-status").textContent)
              && gisCalls.request === 0,
            "disabled=" + (q("#drive-connect") ? q("#drive-connect").disabled : "missing")
              + " said=" + q("#drive-status").textContent
              + " gis=" + gisCalls.request);

      // Token round trip: saved into this frame's localStorage only, never
      // echoed back into the page.
      tokenInput.value = "probe-token-12345";
      q("#sync-token-save").click();
      await sleep(300);
      check("saving the token stores it on the device",
            frame.contentWindow.localStorage.getItem("notes.sync.token") === "probe-token-12345",
            "stored=" + frame.contentWindow.localStorage.getItem("notes.sync.token"));
      check("the token box clears and never echoes the secret",
            tokenInput.value === "" && /saved on this device/.test(tokenInput.placeholder),
            "value=" + JSON.stringify(tokenInput.value) + " placeholder=" + tokenInput.placeholder);
      check("a saved token locks the field so a tap cannot overwrite it",
            tokenInput.readOnly === true && q("#sync-token-save").disabled === true,
            "readOnly=" + tokenInput.readOnly + " saveDisabled=" + q("#sync-token-save").disabled);
      q("#sync-token-remove").click();
      await sleep(300);
      check("removing the token clears the device",
            frame.contentWindow.localStorage.getItem("notes.sync.token") === null
            && tokenInput.placeholder.indexOf("paste once") !== -1,
            "stored=" + frame.contentWindow.localStorage.getItem("notes.sync.token"));
      check("removing the token unlocks the field again",
            tokenInput.readOnly === false && q("#sync-token-save").disabled === false,
            "readOnly=" + tokenInput.readOnly + " saveDisabled=" + q("#sync-token-save").disabled);

      // --- destinations, independently (v28; per-user v29) ----------------
      // Both transports are the fakes installed above; nothing here touches
      // the real GitHub or Google. Sequence: TEST F (above, neither
      // destination configured) -> relay round trip + TEST B relay-only
      // (Drive has no id anywhere) -> the v29 device override turns Drive ON
      // (same lookup the config.js value answers; the token comes off again
      // so TEST A is Drive-ONLY) -> TEST C one shared bundle, identity "A"
      // -> v29 identity switch: Disconnect clears only the browser's Google
      // state, "B" connects and backs up into B's OWN fake store -> TEST E
      // Drive 500 -> TEST D relay outage -> both fail -> 401 expiry ->
      // blocked popup (via Connect) -> waits-only auto-run -> cleanup.

      // Relay round trip (write-once, unchanged in v29). Drive stays
      // UNCONFIGURED here so TEST B is relay-only.
      tokenInput.value = "probe-token-12345";
      q("#sync-token-save").click();
      await sleep(300);
      check("TEST B setup: the relay token is saved on the device",
            frame.contentWindow.localStorage.getItem("notes.sync.token")
              === "probe-token-12345",
            "stored=" + frame.contentWindow.localStorage.getItem("notes.sync.token"));
      const gapiBeforeRelay = gapiCalls.length;
      syncBtn.click();
      await sleep(700);
      check("TEST B: a relay-only tap still reports the PC sync",
            /Sent/.test(q("#sync-status").textContent),
            "said=" + q("#sync-status").textContent);
      check("TEST B: no Google endpoint was touched and no sign-in was asked",
            gapiCalls.length === gapiBeforeRelay && gisCalls.request === 0,
            "gapi=" + gapiCalls.length + " (was " + gapiBeforeRelay + ")"
              + " gis=" + gisCalls.request);
      check("TEST B: the Drive line says how to turn it on",
            /Not configured/.test(q("#drive-status").textContent)
              && /Client ID/.test(q("#drive-status").textContent),
            "said=" + q("#drive-status").textContent);
      check("TEST B: the overall verdict is plain success",
            /Backup completed/.test(q("#backup-overall").textContent)
              && !/warning|failed/i.test(q("#backup-overall").textContent),
            "said=" + q("#backup-overall").textContent);

      // Drive turns ON the v29 way: a device override in localStorage, which
      // getClientId() re-reads at every tap -- no reload, and the same call
      // the shipped config.js value answers through hasClientId(). The relay
      // token comes off again first, so TEST A stays Drive-ONLY (the
      // acceptance case: no token, PC off, watcher disabled).
      q("#sync-token-remove").click();
      await sleep(300);
      check("TEST A setup: the relay token is off again",
            frame.contentWindow.localStorage.getItem("notes.sync.token") === null,
            "stored=" + frame.contentWindow.localStorage.getItem("notes.sync.token"));
      frame.contentWindow.localStorage.setItem("notes.drive.client",
        "probe-client-id.apps.googleusercontent.com");

      // TEST A: Drive configured, NO GitHub token, home PC off, watcher
      // disabled. The tap must reach Google and nothing else, and no relay
      // blocker may stop it.
      const ghBeforeA = ghCalls.length;
      syncBtn.click();
      await sleep(1000);
      check("TEST A: a Drive-only backup uploads the export-named zip",
            /Copied notes-backup-\\d{8}-[0-9a-f]{8}\\.zip/
              .test(q("#drive-status").textContent),
            "said=" + q("#drive-status").textContent);
      check("TEST A: GitHub was never contacted (delta from TEST B's relay)",
            ghCalls.length === ghBeforeA, "gh=" + ghCalls.length
              + " (was " + ghBeforeA + ")");
      check("TEST A: the relay line says Not configured, not a refusal",
            /Not configured/.test(q("#sync-status").textContent)
              && /backup token/i.test(q("#sync-status").textContent),
            "said=" + q("#sync-status").textContent);
      check("TEST A: the overall verdict is plain success",
            /Backup completed/.test(q("#backup-overall").textContent)
              && !/warning|failed/i.test(q("#backup-overall").textContent),
            "said=" + q("#backup-overall").textContent);
      check("TEST A: exactly one sign-in was requested",
            gisCalls.request === 1, "requests=" + gisCalls.request);
      check("TEST A: the sign-in scope is exactly drive.file",
            !!gisCalls.lastConfig
              && gisCalls.lastConfig.scope === "https://www.googleapis.com/auth/drive.file",
            "scope=" + (gisCalls.lastConfig ? gisCalls.lastConfig.scope : "none"));
      check("TEST A: the Drive folder was created exactly once (identity A)",
            !!frame.contentWindow.__gapiStores["probe-drive-token-A"]
              && frame.contentWindow.__gapiStores["probe-drive-token-A"].folderCreated === 1,
            "created=" + (frame.contentWindow.__gapiStores["probe-drive-token-A"]
              ? frame.contentWindow.__gapiStores["probe-drive-token-A"].folderCreated
              : "none"));
      const initCall = gapiCalls.filter(c =>
        (c.init.method || "").toUpperCase() === "POST"
        && c.u.indexOf("uploadType=resumable") !== -1)[0];
      check("TEST A: the upload initiation is resumable",
            !!initCall, initCall ? "" : "initiate call not found");
      if (initCall) {
        const meta = JSON.parse(initCall.init.body);
        check("TEST A: the upload is named after the export id",
              /^notes-backup-\\d{8}-[0-9a-f]{8}\\.zip$/.test(meta.name), "name=" + meta.name);
        check("TEST A: the upload carries the zip mime hints",
              initCall.init.headers["X-Upload-Content-Type"] === "application/zip",
              "headers=" + JSON.stringify(initCall.init.headers));
      }
      const putCall = gapiCalls.filter(c =>
        (c.init.method || "").toUpperCase() === "PUT")[0];
      check("TEST A: the session PUT carries a real zip",
            !!putCall && putCall.init.body instanceof frame.contentWindow.Uint8Array
              && putCall.init.body[0] === 0x50 && putCall.init.body[1] === 0x4b,
            "put=" + (putCall ? typeof putCall.init.body : "missing"));
      if (putCall) {
        const zipBody = putCall.init.body;
        const head = new frame.contentWindow.TextDecoder()
          .decode(zipBody.subarray(0, Math.min(2048, zipBody.length)));
        const n = zipBody.length;
        check("TEST A: the zip carries the backup CSV",
              head.indexOf("backup.csv") !== -1, "head=" + head.slice(0, 80));
        check("TEST A: the zip ends with a central directory and EOCD",
              zipBody[n - 22] === 0x50 && zipBody[n - 21] === 0x4b
                && zipBody[n - 20] === 0x05 && zipBody[n - 19] === 0x06,
              "tail=" + [zipBody[n - 22], zipBody[n - 21], zipBody[n - 20], zipBody[n - 19]]);
        check("TEST A: the PUT went to the scripted session URI",
              putCall.u === "https://www.googleapis.com/upload/session/probe", putCall.u);
      }

      // Both destinations for the joint scenarios: the override stays on,
      // and the relay token comes back.
      tokenInput.value = "probe-token-12345";
      q("#sync-token-save").click();
      await sleep(300);
      check("TEST C: both destinations are configured",
            frame.contentWindow.localStorage.getItem("notes.sync.token")
              === "probe-token-12345"
              && frame.contentWindow.localStorage.getItem("notes.drive.client")
                === "probe-client-id.apps.googleusercontent.com",
            "stored=" + frame.contentWindow.localStorage.getItem("notes.sync.token")
              + " / " + frame.contentWindow.localStorage.getItem("notes.drive.client"));

      // TEST C: both destinations in one tap -- and one SHARED bundle. The
      // relay commit message names the export id; so does the zip name.
      syncBtn.click();
      await sleep(1000);
      const commitCall = ghCalls.filter(c =>
        (c.init.method || "").toUpperCase() === "POST"
        && c.u.indexOf("/git/commits") !== -1
        && c.u.indexOf("/git/commits/") === -1).pop();
      const commitId = commitCall
        ? ((JSON.parse(commitCall.init.body).message || "")
            .match(/\\d{8}-[0-9a-f]{8}/) || [""])[0]
        : "";
      const driveName = q("#drive-status").textContent;
      const zipIdMatch = driveName.match(/notes-backup-(\\d{8}-[0-9a-f]{8})\\.zip/);
      check("TEST C: the PC relay reports success",
            /Sent/.test(q("#sync-status").textContent),
            "said=" + q("#sync-status").textContent);
      check("TEST C: the Drive copy reports success",
            /Copied notes-backup-\\d{8}-[0-9a-f]{8}\\.zip/.test(driveName),
            "said=" + driveName);
      check("TEST C: one bundle -- relay commit and zip share the export id",
            !!commitId && !!zipIdMatch && commitId === zipIdMatch[1],
            "relay=" + commitId + " drive=" + (zipIdMatch ? zipIdMatch[1] : "none"));
      check("TEST C: the folder is reused, not created again (identity A)",
            !!frame.contentWindow.__gapiStores["probe-drive-token-A"]
              && frame.contentWindow.__gapiStores["probe-drive-token-A"].folderCreated === 1,
            "created=" + (frame.contentWindow.__gapiStores["probe-drive-token-A"]
              ? frame.contentWindow.__gapiStores["probe-drive-token-A"].folderCreated
              : "none"));
      check("TEST C: the still-live token signs nothing new in",
            gisCalls.request === 1, "requests=" + gisCalls.request);

      // --- v29: two mocked identities, two independent Drives -------------
      // Identity "A" has just backed up (TEST C). Disconnect must clear ONLY
      // this browser's Google sign-in state; then identity "B" connects and
      // backs up into B's OWN fake store, never touching A's.
      const storeA = () => frame.contentWindow.__gapiStores["probe-drive-token-A"]
        || { folderCreated: 0, files: [] };
      const storeB = () => frame.contentWindow.__gapiStores["probe-drive-token-B"]
        || { folderCreated: 0, files: [] };
      const callsFor = token => gapiCalls.filter(c =>
        ((c.init || {}).headers || {}).Authorization === "Bearer " + token);
      const csvBeforeSwitch = q("#export-csv").value;
      // The shell must have LOADED config.js, not merely cache it: drive.js
      // reads globalThis.NOTES_APP_CONFIG, which exists in the browser only
      // through index.html's classic script tag (v30 fix -- deployment day
      // found the tag missing, so a pasted Client ID would never reach the
      // app despite every file-level check passing).
      check("v30: the shell loaded config.js (NOTES_APP_CONFIG.driveClientId is a string)",
            frame.contentWindow.NOTES_APP_CONFIG
              && typeof frame.contentWindow.NOTES_APP_CONFIG.driveClientId === "string",
            "driveClientId=" + String(frame.contentWindow.NOTES_APP_CONFIG
              && frame.contentWindow.NOTES_APP_CONFIG.driveClientId));
      const aCallsBefore = callsFor("probe-drive-token-A").length;
      // A already backs up twice before here (TEST A + TEST C): what matters
      // is that its store never changes again after the B steps.
      const aFilesBefore = storeA().files.length;
      q("#drive-disconnect").click();
      await sleep(400);
      check("v29: Disconnect reports it on the Drive line",
            /disconnected on this device/.test(q("#drive-status").textContent)
              && /unchanged/.test(q("#drive-status").textContent),
            "said=" + q("#drive-status").textContent);
      check("v29: Disconnect touched no local data -- the backup CSV is byte-identical",
            q("#export-csv").value === csvBeforeSwitch,
            "same=" + (q("#export-csv").value === csvBeforeSwitch));
      check("v29: Disconnect leaves the relay token alone",
            frame.contentWindow.localStorage.getItem("notes.sync.token")
              === "probe-token-12345",
            "stored=" + frame.contentWindow.localStorage.getItem("notes.sync.token"));
      check("v29: Disconnect cleared the in-memory token (A's store sits still)",
            storeA().files.length === aFilesBefore,
            "aFiles=" + storeA().files.length + " (was " + aFilesBefore + ")");

      // B connects. The Connect button became usable when this panel opened
      // (refreshDriveUi re-reads the configuration), and the sign-in stub now
      // issues B's token -- a second, DIFFERENT identity.
      q("#settings-close").click();
      await sleep(250);
      settingsBtn.click();
      await sleep(250);
      q("#export-btn").click();
      await sleep(600);
      const gisBeforeConnect = gisCalls.request;
      probeAccount = "B";
      check("v29: with Drive configured, Connect is usable after a reopen",
            q("#drive-connect").disabled === false,
            "disabled=" + q("#drive-connect").disabled);
      q("#drive-connect").click();
      await sleep(900);
      check("v29: B's connect signs B in -- a second, different identity",
            gisCalls.request === gisBeforeConnect + 1
              && gisCalls.tokens.join("|")
                === "probe-drive-token-A|probe-drive-token-B",
            "tokens=" + gisCalls.tokens.join(" | "));
      check("v29: the connect reports itself and names the Back up path",
            /connected/.test(q("#drive-status").textContent),
            "said=" + q("#drive-status").textContent);
      syncBtn.click();
      await sleep(1000);
      check("v29: B's tap reports both destinations",
            /Sent/.test(q("#sync-status").textContent)
              && /Copied notes-backup-\\d{8}-[0-9a-f]{8}\\.zip/
                .test(q("#drive-status").textContent),
            "drive=" + q("#drive-status").textContent);
      check("v29: B's tap created B's OWN folder in B's own store",
            storeB().folderCreated === 1 && storeB().folderId === "fld-B",
            "created=" + storeB().folderCreated);
      check("v29: A's store is untouched by B's run -- no overwrite, no echo",
            storeA().files.length === aFilesBefore && storeA().folderCreated === 1
              && callsFor("probe-drive-token-A").length === aCallsBefore,
            "aFiles=" + storeA().files.length + " (was " + aFilesBefore + ")"
              + " aCalls=" + callsFor("probe-drive-token-A").length
              + " (was " + aCallsBefore + ")");
      check("v29: each identity holds only its own, differently-named backup",
            storeA().files[0] && storeB().files[0]
              && storeA().files[0].name !== storeB().files[0].name
              && /^notes-backup-\\d{8}-[0-9a-f]{8}\\.zip$/.test(storeA().files[0].name)
              && /^notes-backup-\\d{8}-[0-9a-f]{8}\\.zip$/.test(storeB().files[0].name),
            "A=" + storeA().files[0].name + " B=" + storeB().files[0].name);
      const googleText = () => gapiCalls.map(c =>
        c.u + "|" + (typeof c.init.body === "string" ? c.init.body : "")).join("\\n");
      check("v29: no request embeds a token outside its Authorization header",
            googleText().indexOf("probe-drive-token") === -1);
      check("v29: no request carries a user or account marker at all",
            !/account|user_|\"sub\"|'sub'/.test(googleText()));

      // TEST E: Google refuses the upload (500). The relay's success line
      // must survive untouched and the overall verdict must be a warning
      // that names Drive -- never a plain failure, never a mixed sentence.
      driveUploadStatus = 500;
      syncBtn.click();
      await sleep(1000);
      check("TEST E: the PC sync still reports success",
            /Sent/.test(q("#sync-status").textContent),
            "said=" + q("#sync-status").textContent);
      check("TEST E: the Drive line says why it failed",
            /Google Drive: backup failed/.test(q("#drive-status").textContent),
            "said=" + q("#drive-status").textContent);
      check("TEST E: the overall verdict warns and names Drive",
            /warning/i.test(q("#backup-overall").textContent)
              && /Google Drive/.test(q("#backup-overall").textContent),
            "said=" + q("#backup-overall").textContent);
      check("TEST E: the refused upload created no file in B's own store",
            storeB().files.length === 1, "bFiles=" + storeB().files.length);

      // TEST D: the relay fails (scripted GitHub outage). Google Drive must
      // still succeed on the same tap, and the warning must name the relay.
      driveUploadStatus = 201;
      ghMode = "fail";
      syncBtn.click();
      await sleep(1000);
      check("TEST D: the Drive copy still succeeds",
            /Copied notes-backup-\\d{8}-[0-9a-f]{8}\\.zip/
              .test(q("#drive-status").textContent),
            "said=" + q("#drive-status").textContent);
      check("TEST D: the relay line reports its own failure",
            /Sync failed/.test(q("#sync-status").textContent)
              && /PC relay:/.test(q("#sync-status").textContent),
            "said=" + q("#sync-status").textContent);
      check("TEST D: the overall verdict warns and names the relay",
            /warning/i.test(q("#backup-overall").textContent)
              && /PC relay/.test(q("#backup-overall").textContent),
            "said=" + q("#backup-overall").textContent);

      // Both configured and both failing: a plain failure, no masking either
      // way and no false success.
      driveUploadStatus = 500;
      syncBtn.click();
      await sleep(1000);
      check("both destinations fail: the verdict is a plain failure",
            /Backup failed/.test(q("#backup-overall").textContent)
              && !/warning/i.test(q("#backup-overall").textContent),
            "said=" + q("#backup-overall").textContent);
      ghMode = "ok";

      // Upload answers 401: the in-memory token dies; NO automatic re-sign-in.
      driveUploadStatus = 401;
      syncBtn.click();
      await sleep(1000);
      check("expired sign-in: the PC sync still succeeds",
            /Sent/.test(q("#sync-status").textContent),
            "said=" + q("#sync-status").textContent);
      check("expired sign-in: asks for one more tap and never auto-pops",
            /expired/.test(q("#drive-status").textContent) && gisCalls.request === 2,
            "said=" + q("#drive-status").textContent + " requests=" + gisCalls.request);

      // Popup blocked: requested by the CONNECT BUTTON -- a failed sign-in
      // from the button itself is named, not swallowed, and nothing retries
      // on its own.
      gisCalls.mode = "popup";
      driveUploadStatus = 201;
      q("#drive-connect").click();
      await sleep(1000);
      check("blocked popup: the connect button reports it and nothing retries",
            /popup/i.test(q("#drive-status").textContent) && gisCalls.request === 3,
            "said=" + q("#drive-status").textContent + " requests=" + gisCalls.request);
      gisCalls.mode = "ok";

      // Waits-only auto-run: token removed, Drive's token dead after the
      // popup failure. The auto-run signs in at NOTHING, runs no relay, and
      // both lines explain themselves.
      q("#sync-token-remove").click();
      await sleep(300);
      const gapiBeforeWaits = gapiCalls.length;
      const ghBeforeWaits = ghCalls.length;
      q("#settings-close").click();
      await sleep(250);
      settingsBtn.click();
      await sleep(250);
      q("#export-btn").click();
      await sleep(1500);
      check("waits-only auto-run touches no destination",
            gisCalls.request === 3 && gapiCalls.length === gapiBeforeWaits
              && ghCalls.length === ghBeforeWaits,
            "gis=" + gisCalls.request + " gapi=" + gapiCalls.length
              + " (was " + gapiBeforeWaits + ") gh=" + ghCalls.length
              + " (was " + ghBeforeWaits + ")");
      check("waits-only auto-run says the sign-in waits for a tap",
            /next tap/.test(q("#drive-status").textContent)
              && q("#drive-connect").disabled === false,
            "said=" + q("#drive-status").textContent
              + " connectDisabled=" + q("#drive-connect").disabled);
      check("waits-only auto-run: the relay line stays honest",
            /Not configured/.test(q("#sync-status").textContent),
            "said=" + q("#sync-status").textContent);
      check("waits-only auto-run: the overall line says nothing ran",
            /Nothing was backed up/.test(q("#backup-overall").textContent),
            "said=" + q("#backup-overall").textContent);

      // Leave the profile as the v29 checks leave it: no override, no relay
      // token, and no Google sign-in state in this frame.
      frame.contentWindow.localStorage.removeItem("notes.drive.client");
      q("#drive-disconnect").click();
      await sleep(300);
      check("v29 cleanup: the override is gone and the frame is signed out",
            frame.contentWindow.localStorage.getItem("notes.drive.client") === null
              && /disconnected on this device/.test(q("#drive-status").textContent),
            "stored=" + frame.contentWindow.localStorage.getItem("notes.drive.client"));
      q("#sync-token-remove").click();
      await sleep(250);

      q("#settings-close").click();
      await sleep(250);
      check("the dialog closes", !(q("#settings-dialog") || {}).open);
      settingsBtn.click();
      await sleep(250);
      const emailAgain = q("#backup-email");
      check("reopening the dialog keeps the entered email",
            !!emailAgain && emailAgain.value === "probe@example.com",
            "value=" + (emailAgain ? emailAgain.value : "n/a"));
      q("#settings-close").click();
      await sleep(250);
    }

    // --- the lower pane ---
    const agenda = q("#agenda-list");
    check("agenda pane rendered", !!agenda && agenda.children.length > 0,
          "children=" + (agenda ? agenda.children.length : "n/a"));

    // --- every agenda row is one line, and says how it repeats ---
    // "One line" is a layout fact the source cannot state: the same markup
    // stacks into three lines if the row is a grid or the due block wraps. So it
    // is measured -- the row must be shorter than twice its own line box, which
    // a stacked row exceeds.
    const agendaRows = [...doc.querySelectorAll("#agenda-list .reminder-row")];
    check("the agenda shows a reminder row", agendaRows.length === 1,
          "rows=" + agendaRows.length);

    if (agendaRows.length === 1) {
      const row = agendaRows[0];
      const chip = row.querySelector(".chip");
      const label = chip ? chip.textContent.trim() : "";
      check("the row states how often it repeats, period included",
            /Weekly/.test(label) && /Tue/.test(label) && /Thu/.test(label),
            "chip=" + JSON.stringify(label));

      const title = row.querySelector(".item-title");
      const style = frame.contentWindow.getComputedStyle(title);
      const line = parseFloat(style.lineHeight) || 20;
      const height = row.getBoundingClientRect().height;
      check("the row is a single line tall", height <= line * 1.9,
            "height=" + Math.round(height) + " line=" + Math.round(line));

      // The pieces must be siblings on the row, not nested inside a stack.
      const selectors = [".item-title", ".chip", ".item-when"];
      const parts = selectors.map(sel => row.querySelector(sel));
      check("the recurrence and the due time sit on the row beside the title",
            parts.every(el => el && el.parentElement === row),
            parts.map(el => el ? el.className : "missing").join(" | "));

      const whenDir = parts[2]
        ? frame.contentWindow.getComputedStyle(parts[2]).flexDirection
        : "n/a";
      check("the due block lies along the row rather than stacking",
            whenDir === "row", "flex-direction=" + whenDir);

      // The user asked for the date itself on the row (2026-10-01). At desktop
      // width both labels show: the relative time first, then the absolute
      // date after the separator. The absolute label must be a real date --
      // non-empty and carrying a digit -- not an empty span.
      const computed = frame.contentWindow.getComputedStyle;
      const abs = row.querySelector(".when-abs");
      const rel = row.querySelector(".when-rel");
      const absText = abs ? abs.textContent.trim() : "";
      check("the row carries the date of the next occurrence",
            !!abs && absText.length > 0 && /\\d/.test(absText),
            "abs=" + JSON.stringify(absText));
      check("at desktop width both the relative time and the date show",
            !!rel && computed(rel).display !== "none"
            && computed(abs).display !== "none",
            "rel=" + (rel ? computed(rel).display : "missing")
            + " abs=" + (abs ? computed(abs).display : "missing"));
    }

    // --- the notes half still swaps into the editor ---
    q("#notes-tab").click();
    await sleep(500);
    const noteRows = doc.querySelectorAll("#note-list .item-row");
    if (noteRows.length) {
      noteRows[0].click();
      await sleep(600);
      check("tapping a note opens the editor in place",
            body.dataset.mode === "edit" && !q("#note-editor").classList.contains("hidden"),
            "mode=" + body.dataset.mode);
      check("back arrow is offered while editing",
            !q("#editor-back").classList.contains("hidden"));
    } else {
      check("a note row exists to tap", false, "note-list is empty");
    }

    // --- moving a note into a folder ---
    // The note created above sits in Unfiled because that is what was being
    // browsed. Make a folder, move the note into it, then check BOTH lists:
    // it must be gone from where it was, not merely present where it went.
    // Read-only counts come from a fresh profile, so they mean something.
    let movedOk = false;
    q("#new-folder-btn").click();
    await sleep(400);
    q("#new-folder-name").value = "Probe Folder";
    q("#new-folder-form").requestSubmit();
    await sleep(800);

    const folderRow = [...doc.querySelectorAll("#folder-tree .folder-row")]
      .find(row => row.dataset.folder);
    check("the new folder appears in the tree", !!folderRow,
          "folders=" + doc.querySelectorAll("#folder-tree .folder-row").length);
    const targetId = folderRow ? folderRow.dataset.folder : "";

    // The 40:60 folder:contents split the user asked for (2026-10-05).
    {
      const foldersBox = doc.querySelector(".browse-notes .folders-col").getBoundingClientRect();
      const listBox = doc.querySelector(".browse-notes .list-col").getBoundingClientRect();
      const share = foldersBox.width / (foldersBox.width + listBox.width);
      check("the folder column takes 40% and the contents 60%",
            Math.abs(share - 0.4) <= 0.05,
            "folders=" + Math.round(foldersBox.width) + "px list=" + Math.round(listBox.width)
            + "px share=" + share.toFixed(3));
    }

    const unfiledRows = doc.querySelectorAll("#note-list .item-row");
    check("the note starts out in Unfiled", unfiledRows.length === 1,
          "unfiled rows=" + unfiledRows.length);
    if (unfiledRows.length) {
      unfiledRows[0].click();
      await sleep(700);

      const picker = q("#note-folder");
      check("the note editor offers a folder picker", !!picker);
      const listed = picker ? [...picker.options].some(o => o.value === targetId) : false;
      check("the picker lists the folder as a destination", listed,
            "options=" + (picker ? picker.options.length : "n/a"));

      if (listed) {
        picker.value = targetId;
        q("#note-editor").requestSubmit();
        await sleep(900);

        q("#editor-back").click();
        await sleep(700);
        const left = doc.querySelectorAll("#note-list .item-row").length;
        check("the note has left Unfiled", left === 0, "unfiled rows=" + left);

        const dest = doc.querySelector(
          '#folder-tree .folder-row[data-folder="' + targetId + '"] .folder-select');
        if (dest) {
          dest.click();
          await sleep(700);
          const arrived = doc.querySelectorAll("#note-list .item-row").length;
          check("the note is now in the folder it moved to", arrived === 1,
                "destination rows=" + arrived);
          movedOk = arrived === 1;
        } else {
          check("the destination folder can be opened", false, "row not found");
        }
      }
    }

    // --- deleting a folder ---
    // The control exists but used to be hover-revealed, which made it impossible
    // to find and unreachable on a touch screen -- and the user asked for folder
    // delete precisely because they could not find it. So this checks it is on
    // screen at rest, that cancelling is honoured, that the confirmation states
    // what will be destroyed, and that confirming really removes it.
    const rowSel = '#folder-tree .folder-row[data-folder="' + targetId + '"]';
    const delBtn = doc.querySelector(rowSel + " .folder-del");
    check("the folder row offers a delete control", !!delBtn,
          "row=" + (!!doc.querySelector(rowSel)));

    if (delBtn) {
      const style = frame.contentWindow.getComputedStyle(delBtn);
      const box = delBtn.getBoundingClientRect();
      check("the delete control is on screen without hovering",
            box.width > 0 && box.height > 0 && style.opacity !== "0"
            && style.visibility !== "hidden" && style.pointerEvents !== "none",
            "w=" + Math.round(box.width) + " opacity=" + style.opacity
            + " pointerEvents=" + style.pointerEvents);

      // Cancel first. A confirmation that removes the folder whichever way it is
      // answered is not a confirmation at all, and that is invisible from the
      // source -- confirm() is stubbed out in every other test here.
      confirmAnswer = false;
      delBtn.click();
      await sleep(600);
      check("cancelling the confirmation keeps the folder",
            !!doc.querySelector(rowSel), "row=" + (!!doc.querySelector(rowSel)));

      const wording = confirms[confirms.length - 1] || "";
      check("the confirmation names the folder",
            wording.indexOf("Probe Folder") !== -1, "said=" + wording);
      if (movedOk) {
        check("the confirmation counts the notes that go with it",
              writingSaysOneNote(wording), "said=" + wording);
      }

      confirmAnswer = true;
      delBtn.click();
      await sleep(900);
      check("confirming removes the folder from the tree",
            !doc.querySelector(rowSel));
      if (movedOk) {
        // Only meaningful when the note actually made it in there first.
        check("its notes go with it",
              doc.querySelectorAll("#note-list .item-row").length === 0,
              "rows=" + doc.querySelectorAll("#note-list .item-row").length);
      }
    }

    // --- attaching photos to a note ---
    // The picker cannot be driven directly in headless Chrome, so the change
    // event is fired by hand with synthetic files built on a canvas and handed
    // over through a DataTransfer -- the same objects a real picker produces.
    // The checks follow the storage split: a thumbnail on the strip, the full
    // bytes in OPFS, both surviving save + reopen, and both going away when
    // the note (or the photo) is removed.
    const opfsCount = async () => {
      const root = await frame.contentWindow.navigator.storage.getDirectory();
      const dir = await root.getDirectoryHandle("media");
      const names = [];
      for await (const name of dir.keys()) names.push(name);
      return names;
    };
    const mediaRows = async () => await new Promise((resolve, reject) => {
      const rq = frame.contentWindow.indexedDB.open("notes-local");
      rq.onsuccess = () => {
        const db = rq.result;
        try {
          const all = db.transaction("media", "readonly").objectStore("media").getAll();
          all.onsuccess = () => { db.close(); resolve(all.result); };
          all.onerror = () => { db.close(); reject(all.error); };
        } catch (error) {
          db.close();
          reject(error);
        }
      };
      rq.onerror = () => reject(rq.error);
    });
    const makeImageFile = async (name, color) => {
      const canvas = doc.createElement("canvas");
      canvas.width = 32;
      canvas.height = 32;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = color;
      ctx.fillRect(0, 0, 32, 32);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
      return new frame.contentWindow.File([blob], name, { type: "image/png" });
    };

    let photosOk = false;
    try {
      q("#new-note-btn").click();
      await sleep(800);
      q("#note-title").value = "Photo Note";
      q("#note-editor").requestSubmit();
      await sleep(500);

      const input = q("#media-input");
      const transfer = new frame.contentWindow.DataTransfer();
      transfer.items.add(await makeImageFile("one.png", "#e0533d"));
      transfer.items.add(await makeImageFile("two.png", "#3d7be0"));
      input.files = transfer.files;
      input.dispatchEvent(new frame.contentWindow.Event("change", { bubbles: true }));
      await sleep(1500);

      const thumbs = [...doc.querySelectorAll("#media-strip .media-thumb")];
      check("attaching two photos puts two thumbnails on the strip",
            thumbs.length === 2, "thumbs=" + thumbs.length);
      check("the thumbnails carry the note's media ids",
            thumbs.every(t => t.dataset.media), "ids=" + thumbs.map(t => t.dataset.media).join(","));
      const opfsNames = await opfsCount();
      check("the full-size bytes landed in OPFS", opfsNames.length === 2,
            "files=" + opfsNames.length);
      const rows = await mediaRows();
      check("the media records landed in IndexedDB", rows.length === 2
            && rows.every(r => r.thumb && r.thumb.startsWith("data:image/")),
            "rows=" + rows.length);

      // Removing one photo takes its bytes with it, not just the thumbnail.
      const firstRemove = thumbs[0] && thumbs[0].querySelector(".media-remove");
      if (firstRemove) firstRemove.click();
      await sleep(900);
      const afterRemove = [...doc.querySelectorAll("#media-strip .media-thumb")];
      check("removing a photo takes it off the strip",
            afterRemove.length === 1, "thumbs=" + afterRemove.length);
      check("...and its bytes out of OPFS",
            (await opfsCount()).length === 1, "files=" + (await opfsCount()).length);

      // Save + reopen: the attachment is part of the note, not editor state.
      q("#note-editor").requestSubmit();
      await sleep(800);
      q("#editor-back").click();
      await sleep(600);
      const photoRow = [...doc.querySelectorAll("#note-list .item-row")]
        .find(row => row.textContent.includes("Photo Note"));
      if (photoRow) photoRow.click();
      await sleep(800);
      const reopened = doc.querySelector("#media-strip .media-thumb img");
      check("the photo survives save + reopen",
            !!reopened && (reopened.getAttribute("src") || "").startsWith("data:image/"),
            "img=" + !!reopened);
      photosOk = !!reopened;

      // The viewer: tapping a thumb opens the OPFS copy through an object URL,
      // and "Save to device" is present and clickable without breaking the
      // dialog (the headless download itself is the browser's to do; the pin
      // for the anchor+createObjectURL mechanics is static).
      reopened.click();
      await sleep(900);
      const viewer = doc.querySelector("#media-dialog");
      const viewerImg = doc.querySelector("#media-view");
      check("tapping a thumbnail opens the viewer on the OPFS bytes",
            !!viewer && viewer.open && !!viewerImg && !viewerImg.classList.contains("hidden")
            && (viewerImg.getAttribute("src") || "").startsWith("blob:"),
            "open=" + !!(viewer && viewer.open)
            + " src=" + ((viewerImg && viewerImg.getAttribute("src") || "").slice(0, 5)));
      const saveBtn = doc.querySelector("#media-save-btn");
      check("the viewer offers Save to device",
            !!saveBtn && !saveBtn.disabled
            && /Save to device/.test(saveBtn.textContent),
            "save=" + (saveBtn ? saveBtn.textContent.trim() : "missing"));
      if (saveBtn) saveBtn.click();
      await sleep(400);
      check("saving does not close or break the viewer",
            !!viewer && viewer.open, "open=" + !!(viewer && viewer.open));
      doc.querySelector("#media-close").click();
      await sleep(400);
      check("closing the viewer clears the picture",
            !(doc.querySelector("#media-dialog") || {}).open
            && !(doc.querySelector("#media-view").getAttribute("src")),
            "open=" + !!(doc.querySelector("#media-dialog") || {}).open);

      // Deleting the note takes the rest of its media with it. The
      // confirmation must name the photos that are about to go.
      confirmAnswer = true;
      const confirmsBeforeDelete = confirms.length;
      q("#delete-note-btn").click();
      await sleep(1000);
      const deleteWording = confirms[confirms.length - 1] || "";
      check("deleting a photo note warns about the photos",
            confirms.length > confirmsBeforeDelete
            && /Photo Note/.test(deleteWording) && /photo/.test(deleteWording),
            "said=" + deleteWording.slice(0, 120));
      check("the photo note is gone from the list",
            !([...doc.querySelectorAll("#note-list .item-row")]
              .some(row => row.textContent.includes("Photo Note"))));
      check("...and its remaining bytes are gone from OPFS",
            (await opfsCount()).length === 0,
            "files=" + (await opfsCount()).length);
      check("...and its media records are gone from IndexedDB",
            (await mediaRows()).length === 0,
            "rows=" + (await mediaRows()).length);
    } catch (error) {
      check("the photo pipeline ran to completion", false,
            String(error && error.message || error));
    }

    // --- import: paste the backup back, preview it, replace everything ---
    // Export first, while a keeper note and a nested folder pair exist but the
    // doomed note does not -- so the doomed note is provably absent from the
    // backup and only an over-everything restore can remove it. The pane ratio
    // chosen above must come out untouched, because a restore is not allowed
    // to reach the settings store at all.
    if (settingsBtn) {
      let freshCsv = "";

      q("#new-note-btn").click();
      await sleep(800);
      q("#note-title").value = "Keeper Note";
      // A three-line body on purpose: the list preview must show the Enter
      // keys (the \\n in this PROBE string is a real newline once it reaches
      // the page), and the same cell later round-trips the CSV newline path.
      q("#note-body").value = "first line\\nsecond line\\nthird line";
      q("#note-editor").requestSubmit();
      await sleep(800);
      q("#editor-back").click();
      await sleep(600);

      // The preview fix, asserted on the real rendered row: the text keeps
      // its line breaks, the element actually renders multi-line tall, and
      // the CSS half of the fix (pre-line) is what the browser resolved.
      const keeperRow = [...doc.querySelectorAll("#note-list .item-row")]
        .find(row => row.textContent.includes("Keeper Note"));
      const keeperSub = keeperRow && keeperRow.querySelector(".item-sub");
      check("the keeper row exists to preview", !!keeperRow);
      if (keeperSub) {
        const preview = keeperSub.textContent;
        // The sub is an inline span, so clientHeight is always 0 -- measure its
        // real painted box instead, against the row's single-line title.
        const title = keeperRow.querySelector(".item-title");
        const subH = keeperSub.getBoundingClientRect().height;
        const titleH = title ? title.getBoundingClientRect().height : 0;
        check("the preview keeps the note's line breaks",
              preview.startsWith("first line") && preview.includes("\\n")
              && preview.includes("third line"),
              JSON.stringify(preview));
        check("the preview paints about three line boxes, not one",
              subH > titleH * 1.8,
              "subH=" + Math.round(subH) + " titleH=" + Math.round(titleH));
        check("the browser resolved .item-sub to pre-line",
              getComputedStyle(keeperSub).whiteSpace === "pre-line",
              getComputedStyle(keeperSub).whiteSpace);
      }

      q("#new-folder-btn").click();
      await sleep(400);
      q("#new-folder-name").value = "Probe Root";
      q("#new-folder-form").requestSubmit();
      await sleep(800);

      const rootRow = [...doc.querySelectorAll("#folder-tree .folder-row")]
        .find(row => row.dataset.folder);
      check("the export's root folder is created", !!rootRow,
            "rows=" + doc.querySelectorAll("#folder-tree .folder-row").length);
      if (rootRow) {
        // Selecting the root is what makes the next folder its child:
        // submitNewFolder parents new folders under whatever is selected.
        rootRow.querySelector(".folder-select").click();
        await sleep(500);
        q("#new-folder-btn").click();
        await sleep(400);
        q("#new-folder-name").value = "Probe Sub";
        q("#new-folder-form").requestSubmit();
        await sleep(800);
        // Back to Unfiled so the doomed note below starts out beside its keeper.
        const unfiledRow = [...doc.querySelectorAll("#folder-tree .folder-row")]
          .find(row => !row.dataset.folder);
        if (unfiledRow) {
          unfiledRow.querySelector(".folder-select").click();
          await sleep(500);
        }
      }

      // Export while the doomed note does not exist yet.
      settingsBtn.click();
      await sleep(300);
      q("#export-btn").click();
      await waitFor(() => {
        const box = q("#export-csv");
        return box && box.value.indexOf("Probe Root") !== -1;
      }, 5000);
      freshCsv = q("#export-csv").value;
      check("the export picks up the folders and the keeper note",
            freshCsv.indexOf("Probe Sub") !== -1
            && freshCsv.indexOf("Keeper Note") !== -1
            && freshCsv.indexOf("Doomed Note") === -1,
            "len=" + freshCsv.length);
      q("#settings-close").click();
      await sleep(250);

      // The doomed note: in the database, never in the backup.
      q("#new-note-btn").click();
      await sleep(800);
      q("#note-title").value = "Doomed Note";
      q("#note-editor").requestSubmit();
      await sleep(800);
      q("#editor-back").click();
      await sleep(600);

      settingsBtn.click();
      await sleep(300);
      q("#import-btn").click();
      await sleep(250);
      const importPanel = q("#import-panel");
      check("the import panel opens",
            !!importPanel && !importPanel.classList.contains("hidden"));

      // Garbage is refused, and refused without arming the restore button.
      const pasteBox = q("#restore-input");
      pasteBox.value = "this is not a backup, just some text";
      q("#preview-btn").click();
      await sleep(400);
      const previewOut = q("#preview-out");
      check("a non-backup is refused with an explanation",
            /not a notes backup/.test(previewOut ? previewOut.textContent : ""),
            "said=" + (previewOut ? previewOut.textContent.slice(0, 80) : "n/a"));
      check("...and the restore button stays disabled",
            q("#restore-btn").disabled === true);

      // The real backup: both count lines must state what was exported above
      // against what is on the device now.
      pasteBox.value = freshCsv;
      q("#preview-btn").click();
      await sleep(500);
      const previewText = previewOut ? previewOut.textContent : "";
      check("the preview counts what the backup holds",
            previewText.indexOf("Backup holds") !== -1
            && previewText.indexOf("2 folders, 1 note, 1 reminder") !== -1,
            previewText.slice(0, 140));
      check("...and what the device currently holds",
            previewText.indexOf("This device currently holds") !== -1
            && previewText.indexOf("2 folders, 2 notes, 1 reminder") !== -1,
            previewText.slice(0, 260));
      check("a previewed backup arms the restore button",
            q("#restore-btn").disabled === false);

      // Editing after the preview must revoke the authorisation -- otherwise a
      // preview would be able to authorise bytes that are no longer in the box.
      const confirmsBeforeStale = confirms.length;
      pasteBox.value = freshCsv + "\\n";
      q("#restore-btn").click();
      await sleep(400);
      check("editing after the preview revokes it",
            /changed after it was previewed/.test(
              q("#preview-out") ? q("#preview-out").textContent : ""),
            "said=" + (q("#preview-out")
              ? q("#preview-out").textContent.slice(0, 90) : "n/a"));
      check("...without ever asking for confirmation",
            confirms.length === confirmsBeforeStale,
            "confirms=" + confirms.length);

      // Preview again, then cancel: nothing at all may change.
      pasteBox.value = freshCsv;
      q("#preview-btn").click();
      await sleep(500);
      confirmAnswer = false;
      const confirmsBeforeCancel = confirms.length;
      q("#restore-btn").click();
      await waitFor(() => confirms.length > confirmsBeforeCancel, 3000);
      await sleep(400);
      const cancelWords = confirms[confirms.length - 1] || "";
      check("cancelling the restore keeps every note",
            doc.querySelectorAll("#note-list .item-row").length === 2,
            "rows=" + doc.querySelectorAll("#note-list .item-row").length);
      check("the confirmation states the counts and what is kept",
            cancelWords.indexOf("Replace everything?") !== -1
            && cancelWords.indexOf("2 folders, 2 notes, 1 reminder") !== -1
            && /settings are kept/i.test(cancelWords),
            "said=" + cancelWords);

      // Confirming replaces everything -- and still spares the settings.
      confirmAnswer = true;
      q("#restore-btn").click();
      await waitFor(() => /Restored/.test(
        q("#preview-out") ? q("#preview-out").textContent : ""), 5000);
      check("confirming restores the backup",
            /Restored 2 folders, 1 note, 1 reminder/.test(
              q("#preview-out") ? q("#preview-out").textContent : ""),
            "said=" + (q("#preview-out") ? q("#preview-out").textContent : "n/a"));
      q("#settings-close").click();
      await sleep(300);
    }

    // The restored world: folders back and still nested, keeper present, doomed
    // gone, agenda rebuilt, ratio untouched by the whole exercise.
    const treeRows = [...doc.querySelectorAll("#folder-tree .folder-row")]
      .filter(row => row.dataset.folder);
    check("the folders came back", treeRows.length === 2,
          "rows=" + treeRows.map(r => r.textContent.trim().slice(0, 40)).join(" | "));
    const restoredRoot = treeRows.find(r => /Probe Root/.test(r.textContent));
    const restoredSub = treeRows.find(r => /Probe Sub/.test(r.textContent));
    // Depth renders as inline padding-left (9px + 14px per level), so a child
    // that lost its parentId would come back at the root's indent.
    check("the subfolder is still nested under the root",
          !!restoredRoot && !!restoredSub
          && parseInt(restoredSub.style.paddingLeft, 10)
             > parseInt(restoredRoot.style.paddingLeft, 10),
          "root=" + (restoredRoot ? restoredRoot.style.paddingLeft : "missing")
          + " sub=" + (restoredSub ? restoredSub.style.paddingLeft : "missing"));

    const keeperRows = [...doc.querySelectorAll("#note-list .item-row")];
    check("the keeper note survived and the doomed note did not",
          keeperRows.length === 1 && /Keeper Note/.test(keeperRows[0].textContent)
          && !keeperRows.some(r => /Doomed Note/.test(r.textContent)),
          "rows=" + keeperRows.map(r => r.textContent.trim().slice(0, 30)).join(" | "));

    if (restoredRoot) {
      restoredRoot.querySelector(".folder-select").click();
      await sleep(600);
      const inRoot = doc.querySelectorAll("#note-list .item-row").length;
      check("the restored root folder is empty, as the backup recorded",
            inRoot === 0, "rows=" + inRoot);
    }

    const agendaAfter = q("#agenda-list");
    check("the reminder came back to the agenda",
          !!agendaAfter && /Probe Standup/.test(agendaAfter.textContent),
          "agenda=" + (agendaAfter
            ? agendaAfter.textContent.trim().slice(0, 60) : "n/a"));

    const chipAfter = q("#ratio-value");
    const shareAfter = (() => {
      const top = q(".app-shell > .pane:not(.pane-agenda)");
      const bot = q(".pane-agenda");
      if (!top || !bot) return -1;
      const t = top.getBoundingClientRect().height;
      const b = bot.getBoundingClientRect().height;
      return t + b > 0 ? t / (t + b) : -1;
    })();
    check("the restore did not touch the pane ratio (settings survive)",
          !!chipAfter && chipAfter.textContent.trim() === "60%"
          && Math.abs(shareAfter - 0.6) <= 0.04,
          "chip=" + (chipAfter ? chipAfter.textContent : "n/a")
          + " topShare=" + shareAfter.toFixed(3));

    // --- the choices survive a restart ---
    // A fresh document proves both settings were written to the store rather
    // than merely held in variables: the ratio and the email come back on
    // their own, and the restored data is still there. The document identity
    // check matters -- until navigation actually starts, contentDocument still
    // hands back the old, already-ready document.
    const oldDoc = frame.contentDocument;
    frame.contentWindow.location.reload();
    const restarted = await waitFor(() => {
      const d = frame.contentDocument;
      if (!d || d === oldDoc || !d.body) return false;
      return d.body.dataset.ready === "true" || d.body.dataset.fatal === "true";
    }, 20000);
    const freshBody = frame.contentDocument ? frame.contentDocument.body : null;
    check("the app restarts after a reload",
          restarted && freshBody && freshBody.dataset.ready === "true",
          "ready=" + (freshBody ? freshBody.dataset.ready : "n/a")
          + " fatal=" + (freshBody ? freshBody.dataset.fatal : "n/a"));
    if (restarted && freshBody && freshBody.dataset.ready === "true") {
      const fresh = frame.contentDocument;
      const chip = fresh.querySelector("#ratio-value");
      check("the pane ratio comes back on restart",
            !!chip && chip.textContent.trim() === "60%",
            "chip=" + (chip ? chip.textContent : "missing"));
      const top = fresh.querySelector(".app-shell > .pane:not(.pane-agenda)");
      const bot = fresh.querySelector(".pane-agenda");
      let share = -1;
      if (top && bot) {
        const t = top.getBoundingClientRect().height;
        const b = bot.getBoundingClientRect().height;
        share = t + b > 0 ? t / (t + b) : -1;
      }
      check("...and is applied to the layout",
            Math.abs(share - 0.6) <= 0.04, "topShare=" + share.toFixed(3));
      const email = fresh.querySelector("#backup-email");
      check("the saved email address comes back",
            !!email && email.value === "probe@example.com",
            "value=" + (email ? email.value : "missing"));
      const rowsAfter = [...fresh.querySelectorAll("#folder-tree .folder-row")]
        .filter(row => row.dataset.folder);
      check("the restored data is still there after the restart",
            rowsAfter.length === 2,
            "rows=" + rowsAfter.map(r => r.textContent.trim().slice(0, 30)).join(" | "));
    }
  } catch (error) {
    check("probe ran to completion", false, String(error && error.message || error));
  }

  await fetch("/__result", { method: "POST", body: JSON.stringify(results) });
})();
</script>
"""

# The main frame is 900px wide, so a layout bug that only appears at phone
# width can never reproduce there -- that is exactly how the "one word per
# line" preview squeeze (2026-10-01) shipped green. This pass runs the app
# again in a 380px frame, phone-wide, and asserts what the user actually
# asked for: only Enter keys break lines. Every line below is short enough
# to fit the narrow preview WITHOUT wrapping, so if spaces still wrapped,
# the painted box count would blow past four and the check would fail.
PROBE_NARROW = """<!doctype html>
<meta charset="utf-8">
<title>probe-narrow</title>
<iframe id="app" src="./index.html" width="380" height="740"></iframe>
<script>
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const results = [];
  const check = (name, ok, detail) => { console.log("CHECK:", ok ? "ok" : "FAIL", name); return results.push({ name, ok: !!ok, detail: detail || "" }); };
  async function waitFor(predicate, ms) {
    const deadline = Date.now() + (ms || 15000);
    while (Date.now() < deadline) {
      if (predicate()) return true;
      await sleep(100);
    }
    return false;
  }

  try {
    const frame = document.getElementById("app");
    const appeared = await waitFor(
      () => frame.contentDocument && frame.contentDocument.querySelector(".app-shell"));
    check("narrow: app shell rendered", appeared,
          appeared ? "" : "the iframe never presented the app");
    if (!appeared) throw new Error("no app shell in the narrow frame");

    const doc = frame.contentDocument;
    await waitFor(() => doc.body.dataset.ready === "true" || doc.body.dataset.fatal === "true");
    check("narrow: app settled ready", doc.body.dataset.ready === "true",
          "ready=" + doc.body.dataset.ready + " fatal=" + doc.body.dataset.fatal);
    if (doc.body.dataset.ready !== "true") throw new Error("the narrow app never settled");

    const q = sel => doc.querySelector(sel);
    q("#new-note-btn").click();
    await sleep(800);
    q("#note-title").value = "Narrow Probe";
    q("#note-body").value = "one two three four\\nfive six seven\\neight nine ten\\neleven twelve";
    q("#note-editor").requestSubmit();
    await sleep(800);
    q("#editor-back").click();
    await sleep(600);

    const row = [...doc.querySelectorAll("#note-list .item-row")]
      .find(r => r.textContent.includes("Narrow Probe"));
    const sub = row && row.querySelector(".item-sub");
    check("narrow: the probe note is in the list", !!row);
    if (sub) {
      const box = sub.getBoundingClientRect();
      const rects = sub.getClientRects().length;
      check("narrow: four Enters make exactly four painted lines -- spaces make none",
            rects === 4, "rects=" + rects + " width=" + Math.round(box.width));
      check("narrow: the preview column keeps a real share of the row",
            box.width > 90, "width=" + Math.round(box.width));
      check("narrow: the preview still resolves to pre-line",
            getComputedStyle(sub).whiteSpace === "pre-line",
            getComputedStyle(sub).whiteSpace);
    }

    // --- the reminder row keeps its DATE at phone width ---
    // The user asked for the next-due date on the row, one line. Narrow used to
    // solve the row by hiding the date; now it hides the relative time instead.
    // A real reminder is created (the profile is fresh) so the measurement is
    // of real content, and the row height proves the date did not re-wrap it.
    q("#reminders-tab").click();
    await sleep(500);
    q("#new-reminder-btn").click();
    await sleep(800);
    q("#reminder-title").value = "Narrow Standup";
    q("#reminder-start").value = "2027-01-05T09:00";
    q("#reminder-editor").requestSubmit();
    await sleep(800);
    q("#editor-back").click();
    await sleep(600);

    const reminderRow = doc.querySelector("#reminder-manage-list .reminder-row");
    check("narrow: the reminder row exists", !!reminderRow);
    if (reminderRow) {
      const abs = reminderRow.querySelector(".when-abs");
      const rel = reminderRow.querySelector(".when-rel");
      const computed = (el, pseudo) => frame.contentWindow.getComputedStyle(el, pseudo);
      const absText = abs ? abs.textContent.trim() : "";
      check("narrow: the reminder row shows the date",
            !!abs && absText.length > 0 && /\\d/.test(absText)
            && computed(abs).display !== "none",
            "abs=" + JSON.stringify(absText)
            + " display=" + (abs ? computed(abs).display : "missing"));
      check("narrow: the relative time stands down to make room",
            !!rel && computed(rel).display === "none",
            "rel=" + (rel ? computed(rel).display : "missing"));
      check("narrow: the date carries no leftover separator",
            computed(abs, "::before").content === "none",
            "before=" + computed(abs, "::before").content);
      const titleEl = reminderRow.querySelector(".item-title");
      const lineH = parseFloat(computed(titleEl).lineHeight) || 20;
      const rowH = reminderRow.getBoundingClientRect().height;
      check("narrow: the reminder row is still a single line",
            rowH <= lineH * 1.9, "rowH=" + Math.round(rowH) + " line=" + Math.round(lineH));
    }

    // --- the editor's three buttons share one line at phone width ---
    // Same-line is a layout fact: the tops of Delete, Add photo/video and Save
    // must agree, and the row must be one button tall, not two stacked.
    q("#notes-tab").click();
    await sleep(400);
    q("#new-note-btn").click();
    await sleep(800);
    const buttons = ["#delete-note-btn", "#add-media-btn", "#save-note-btn"]
      .map(sel => q(sel));
    if (buttons.every(b => b)) {
      const tops = buttons.map(b => Math.round(b.getBoundingClientRect().top));
      const spread = Math.max(...tops) - Math.min(...tops);
      const actionBox = q(".editor-actions").getBoundingClientRect();
      const buttonH = buttons[2].getBoundingClientRect().height;
      check("narrow: Delete, Add photo/video and Save sit on one line",
            spread <= 2, "tops=" + tops.join(","));
      check("narrow: the action row is one button tall, not two",
            actionBox.height <= buttonH * 1.35,
            "rowH=" + Math.round(actionBox.height) + " btnH=" + Math.round(buttonH));
      check("narrow: no photo strip shows on a note without photos",
            !q("#media-strip") || q("#media-strip").classList.contains("hidden"),
            "stripVisible=" + (q("#media-strip")
              ? !q("#media-strip").classList.contains("hidden") : "missing"));
    } else {
      check("narrow: the three editor buttons all exist",
            false, "missing=" + buttons.map(b => !!b).join(","));
    }
  } catch (error) {
    check("narrow probe ran to completion", false, String(error && error.message || error));
  }

  await fetch("/__result", { method: "POST", body: JSON.stringify(results) });
})();
</script>
"""

def expected_version() -> str:
    """The APP_VERSION this checkout carries, to pin the probe's chip check."""
    found = re.search(r'export const APP_VERSION = "(\d+)";',
                      (REPO / "view.js").read_text(encoding="utf-8"))
    if not found:
        raise SystemExit("view.js exports no APP_VERSION -- the probe cannot be pinned")
    return found.group(1)


def expected_schema() -> str:
    """The backup SCHEMA_VERSION this checkout writes, to pin the CSV checks."""
    found = re.search(r'export const SCHEMA_VERSION = (\d+);',
                      (REPO / "backup.js").read_text(encoding="utf-8"))
    if not found:
        raise SystemExit("backup.js exports no SCHEMA_VERSION -- the probe cannot be pinned")
    return found.group(1)


# The probe asserts the chip against the version this very checkout carries,
# so a stale published number fails the desktop pass outright. The CSV header
# check is pinned to the schema this checkout writes for the same reason.
PROBE = PROBE.replace("__EXPECTED_VERSION__", expected_version())
PROBE = PROBE.replace("__EXPECTED_SCHEMA__", expected_schema())

HOLDER: dict = {"data": None, "beats": []}


class Probe(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/__result"):
            if self.path.startswith("/__beat"):
                length = int(self.headers.get("Content-Length") or 0)
                HOLDER["beats"].append((time.time(), self.rfile.read(length).decode("utf-8")))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")
                return
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        HOLDER["data"] = self.rfile.read(length).decode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


def build_stage(name: str, app_js: Path) -> Path:
    stage = WORK / name
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for asset in ("index.html", "config.js", "app.css", "view.js", "storage.js",
                  "reminder.js", "backup.js", "sync.js", "drive.js",
                  "manifest.webmanifest"):
        shutil.copyfile(REPO / asset, stage / asset)
    shutil.copyfile(app_js, stage / "app.js")
    (stage / "_probe.html").write_text(PROBE, encoding="utf-8")
    return stage


def serve(directory: Path, port: int) -> tuple[socketserver.TCPServer, threading.Thread]:
    """Serve `directory` on loopback, in a daemon thread, and hand back the pair."""
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port),
                                   functools.partial(Probe, directory=str(directory)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def kill_profile_chrome(profile: Path) -> None:
    """Stop every Chrome process still using this run's profile directory.

    On Windows the chrome.exe named on the command line is only a launcher: it
    spawns the real browser and exits at once, so process.terminate() below
    kills the launcher and ORPHANS the browser -- which goes on running for
    hours and keeps the profile's process-singleton lock (the one Chrome 155's
    2026-10-05 incident showed: a later run's launch was silently handed to the
    living zombie from a previous run and never reached this run's page or
    server at all, so the probe 'never reported back'). Sweeping by profile
    name clears that lock; run before launching and again after terminating.
    Matches only this directory's name -- the user's own browser profile lives
    under a different path and is never touched.
    """
    token = profile.name
    command = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{token}*' }} | "
        "ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", command],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30)
    except subprocess.TimeoutExpired:
        pass


def drive(port: int, browser: Path, httpd, thread, label: str,
          cross_origin: bool = False, page: str = "_probe.html") -> list[dict]:
    """Run Chrome at the probe page and wait for it to report back.

    Chrome is terminated and the server shut down on every path out, including
    the timeout, so a hung browser cannot leave a port held for the next run.
    """
    # A fresh profile every run. IndexedDB lives in the profile, so a leftover
    # one would carry notes and folders into the next run and make any count
    # this probe asserts depend on history rather than on what it just did.
    profile = WORK / f"chrome-profile-{port}"
    kill_profile_chrome(profile)
    if profile.exists():
        shutil.rmtree(profile, ignore_errors=True)

    argv = [
        str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        f"--user-data-dir={profile}",
        "--enable-logging=stderr", "--v=0",
    ]
    if cross_origin:
        # The probe reads and clicks inside a frame served from another origin.
        # Without this its document is opaque and every check fails for a reason
        # that has nothing to do with the app under test. It also needs a
        # non-default profile, which the line above already supplies.
        argv += ["--disable-web-security", "--disable-site-isolation-trials"]
    argv.append(f"http://127.0.0.1:{port}/{page}")

    chrome_log = WORK / 'probe_chrome.log'
    log_handle = open(chrome_log, 'w', encoding='utf-8', errors='replace')
    process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=log_handle)
    deadline = time.time() + 240  # wide enough for the full ~2.5 minute run
    try:
        while time.time() < deadline and HOLDER["data"] is None:
            time.sleep(0.25)
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        log_handle.close()
        kill_profile_chrome(profile)
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    if HOLDER["data"] is None:
        beats = HOLDER.get("beats") or []
        if beats:
            first = beats[0][0]
            print(f"last probe beats ({len(beats)} total) for {label}:")
            for when, body in beats[-8:]:
                print(f"  +{when - first:5.1f}s  {body}")
        else:
            print(f"no /__beat at all for {label}: the probe script never ran")
        raise SystemExit(f"probe never reported back for {label} (browser hung or the page errored)")
    return json.loads(HOLDER["data"])


def run_probe(stage: Path, port: int, browser: Path) -> list[dict]:
    HOLDER["data"] = None
    HOLDER["beats"] = []
    httpd, thread = serve(stage, port)
    return drive(port, browser, httpd, thread, stage.name)


def run_probe_url(url: str, port: int, browser: Path) -> list[dict]:
    """Probe an app that is already served -- the live site, not a local stage."""
    scratch = WORK / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "_probe.html").write_text(
        PROBE.replace('src="./index.html"', f'src="{url}"'), encoding="utf-8")
    HOLDER["data"] = None
    HOLDER["beats"] = []
    httpd, thread = serve(scratch, port)
    return drive(port, browser, httpd, thread, url, cross_origin=True)


def run_probe_narrow(stage: Path, port: int, browser: Path) -> list[dict]:
    """The same stage again, in a phone-width frame (see PROBE_NARROW)."""
    (stage / "_probe_narrow.html").write_text(PROBE_NARROW, encoding="utf-8")
    HOLDER["data"] = None
    HOLDER["beats"] = []
    httpd, thread = serve(stage, port)
    return drive(port, browser, httpd, thread, stage.name + " @380px",
                 page="_probe_narrow.html")


def run_probe_url_narrow(url: str, port: int, browser: Path) -> list[dict]:
    """The narrow pass against an app that is already served."""
    scratch = WORK / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "_probe_narrow.html").write_text(
        PROBE_NARROW.replace('src="./index.html"', f'src="{url}"'), encoding="utf-8")
    HOLDER["data"] = None
    HOLDER["beats"] = []
    httpd, thread = serve(scratch, port)
    return drive(port, browser, httpd, thread, url + " @380px",
                 cross_origin=True, page="_probe_narrow.html")


def find_browser(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f"no browser at {path}")
        return path
    for candidate in CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise SystemExit("no Chrome or Edge found; pass --browser <path>")


def report(label: str, results: list[dict]) -> None:
    print(f"\n=== {label} ===")
    for row in results:
        print(f"  {'PASS' if row['ok'] else 'FAIL':<4}  {row['name']}"
              + (f"\n          {row['detail']}" if row["detail"] else ""))
    print(f"  -> {sum(r['ok'] for r in results)}/{len(results)} passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--compare-stale", metavar="COMMIT",
                        help="also run the app.js from this git revision against the current "
                             "markup, to confirm the harness still detects the fault")
    parser.add_argument("--url", metavar="URL",
                        help="drive an app that is already served -- the live deployment -- "
                             "instead of building local stages")
    parser.add_argument("--browser", help="path to a Chrome or Edge binary")
    args = parser.parse_args()

    if args.url and args.compare_stale:
        parser.error("--url and --compare-stale are different questions; run them separately")

    browser = find_browser(args.browser)
    WORK.mkdir(parents=True, exist_ok=True)

    if args.url:
        results = run_probe_url(args.url, 8190, browser)
        report(args.url, results)
        narrow = run_probe_url_narrow(args.url, 8191, browser)
        report("phone-width frame (380px)", narrow)
        results = results + narrow
        broken = [r["name"] for r in results if not r["ok"]]
        print("\n=== verdict ===")
        if broken:
            print(f"{len(broken)} control(s) do not respond on the deployed app:")
            for name in broken:
                print(f"   {name}")
            return 1
        print(f"the deployed app responds on all {len(results)} checks")
        return 0

    stages: dict[str, Path] = {}
    stale: str | None = None
    if args.compare_stale:
        stale = args.compare_stale
        old_app = WORK / "app_stale.js"
        completed = subprocess.run(["git", "show", f"{stale}:app.js"], cwd=REPO,
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace")
        if completed.returncode != 0:
            raise SystemExit(f"git show {stale}:app.js failed: {completed.stderr.strip()}")
        old_app.write_text(completed.stdout, encoding="utf-8", newline="")
        stages[f"STALE   (current index.html + the app.js from {stale})"] = \
            build_stage("stale", old_app)
    stages["CURRENT (current index.html + the current app.js)"] = \
        build_stage("current", REPO / "app.js")

    verdicts: dict[str, list[dict]] = {}
    for index, (label, stage) in enumerate(stages.items()):
        results = run_probe(stage, 8190 + index, browser)
        report(label, results)
        narrow = run_probe_narrow(stage, 8210 + index, browser)
        report("phone-width frame (380px)", narrow)
        verdicts[label] = results + narrow

    failures = 0
    print("\n=== verdict ===")

    if stale:
        dead = [r["name"] for r in verdicts[f"STALE   (current index.html + the app.js from {stale})"]
                if not r["ok"]]
        if dead:
            print(f"the stale pairing still fails on {len(dead)} control(s), so this harness "
                  f"can still detect the fault it was written for:")
            for name in dead:
                print(f"   {name}")
        else:
            print("WARNING: the stale pairing no longer fails. The harness has gone blind -- "
                  "it would not catch a repeat of this fault.")
            failures += 1

    current = verdicts["CURRENT (current index.html + the current app.js)"]
    broken = [r["name"] for r in current if not r["ok"]]
    if broken:
        print(f"\nBROKEN in the current build -- {len(broken)} control(s) do not respond:")
        for name in broken:
            print(f"   {name}")
        failures += 1
    else:
        print(f"\nthe current build responds on all {len(current)} checks")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
