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

    // --- v36: the agenda steps aside only for NOTE edits. A reminder edit
    // keeps it (body[data-kind] is "reminders" during this edit).
    check("a reminder edit keeps the agenda visible",
          !!q(".pane-agenda") && q(".pane-agenda").getBoundingClientRect().height > 2,
          "agendaH=" + Math.round(q(".pane-agenda").getBoundingClientRect().height));

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
    // v38: the list also renders folder rows (drill-down), so every note-row
    // count and lookup filters [data-item] -- the folder rows are divs without
    // it and must never answer a note-row check.
    q("#notes-tab").click();
    await sleep(500);
    const noteRows = doc.querySelectorAll("#note-list .item-row[data-item]");
    if (noteRows.length) {
      noteRows[0].click();
      await sleep(600);
      check("tapping a note opens the editor in place",
            body.dataset.mode === "edit" && !q("#note-editor").classList.contains("hidden"),
            "mode=" + body.dataset.mode);
      check("back arrow is offered while editing",
            !q("#editor-back").classList.contains("hidden"));

      // --- v38: the breadcrumb bar stays while the note is open ---
      // The editor now lives in the list's panel; the crumbs above must remain
      // on screen and above it, and the list must be the thing that left.
      {
        const crumbBar = q("#crumbs");
        const crumbBox = crumbBar && crumbBar.getBoundingClientRect();
        const editorBox = q("#note-editor").getBoundingClientRect();
        const editorVisible = !q("#note-editor").classList.contains("hidden");
        check("a note edit keeps the breadcrumb bar on screen",
              !!crumbBar && !crumbBar.classList.contains("hidden")
              && crumbBox.height > 0 && editorVisible
              && crumbBox.top < editorBox.top,
              "crumbH=" + (crumbBox ? Math.round(crumbBox.height) : -1)
              + " crumbTop=" + (crumbBox ? Math.round(crumbBox.top) : -1)
              + " editorTop=" + Math.round(editorBox.top)
              + " editorVisible=" + editorVisible);
        check("the open editor replaced the note list in its panel",
              editorVisible && q("#note-list").classList.contains("hidden"));

        // --- v38: ONE action row -- the paperclip menu, Delete, Save ---
        const actionRows = [...doc.querySelectorAll("#note-editor .editor-actions")];
        const actionButtons = ["#attach-menu-btn", "#delete-note-btn", "#save-note-btn"]
          .map(sel => doc.querySelector(sel));
        check("the note editor's actions are one row -- Attach menu, Delete, Save",
              actionRows.length === 1
              && actionButtons.every(b => !!b && actionRows[0].contains(b)),
              "rows=" + actionRows.length
              + " missing=" + actionButtons.map(b => !!b).join(","));
        // The paperclip is a menu now; its row offers no picker buttons at all.
        const pickerButtons = ["#add-gallery-btn", "#add-doc-btn", "#add-camera-btn"]
          .map(sel => doc.querySelector(sel));
        check("the three separate picker buttons are gone from the editor",
              pickerButtons.every(b => !b),
              "left=" + pickerButtons.map(b => !!b).join(","));
        check("an open note editor collapses the agenda",
              editorVisible && q(".pane-agenda").getBoundingClientRect().height <= 2,
              "agendaH=" + Math.round(q(".pane-agenda").getBoundingClientRect().height));
      }
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

    const folderRow = [...doc.querySelectorAll("#note-list .folder-row")]
      .find(row => row.dataset.folder);
    check("the new folder appears in the drill list", !!folderRow,
          "folders=" + doc.querySelectorAll("#note-list .folder-row").length);
    const targetId = folderRow ? folderRow.dataset.folder : "";

    const unfiledRows = doc.querySelectorAll("#note-list .item-row[data-item]");
    check("the note starts out in Unfiled", unfiledRows.length === 1,
          "unfiled rows=" + unfiledRows.length);
    if (unfiledRows.length) {
      // v37: the row's title is the name of the folder the note lives in --
      // here Unfiled, the pseudo-folder's own label, not "Untitled note".
      const untitledTitle = unfiledRows[0].querySelector(".item-title");
      check("an untitled note is titled after its folder (v37)",
            !!untitledTitle && untitledTitle.textContent.trim() === "Unfiled",
            "title=" + (untitledTitle ? untitledTitle.textContent.trim() : "missing"));

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
        const left = doc.querySelectorAll("#note-list .item-row[data-item]").length;
        check("the note has left Unfiled", left === 0, "unfiled rows=" + left);

        // --- v36: the agenda returns when the note editor closes ---
        check("closing the note editor brings the agenda back",
              body.dataset.mode === "browse"
              && q(".pane-agenda").getBoundingClientRect().height > 2,
              "mode=" + body.dataset.mode
              + " agendaH=" + Math.round(q(".pane-agenda").getBoundingClientRect().height));
        // v38: browsing notes shows no context chip at all -- the crumbs bar
        // IS the context, and a chip repeating it would be noise.
        check("browsing notes shows no context chip (the crumbs are it)",
              q("#list-context").textContent === "",
              "chip=" + JSON.stringify(q("#list-context").textContent));

        // v38: drilling in is clicking the folder row itself.
        const dest = doc.querySelector(
          '#note-list .folder-row[data-folder="' + targetId + '"]');
        if (dest) {
          dest.click();
          await sleep(700);
          const arrived = doc.querySelectorAll("#note-list .item-row[data-item]").length;
          check("the note is now in the folder it moved to", arrived === 1,
                "destination rows=" + arrived);
          // v37: the untitled note's label follows its CURRENT folder -- after
          // the move it reads the destination's name, so the label is not a
          // one-time copy of where the note was born.
          const movedRow = doc.querySelector("#note-list .item-row[data-item]");
          const movedTitle = movedRow ? movedRow.querySelector(".item-title") : null;
          check("the moved note's row title follows the new folder (v37)",
                !!movedTitle && movedTitle.textContent.trim() === "Probe Folder",
                "title=" + (movedTitle ? movedTitle.textContent.trim() : "missing"));
          movedOk = arrived === 1;
        } else {
          check("the destination folder can be opened", false, "row not found");
        }
      }
    }

    // --- deleting a folder ---
    // The drill list only shows the browsed folder's children, so the flow
    // first browses back up to the root (the home crumb) where the row is
    // visible again. The delete lives behind the row's ellipsis menu now; it
    // must be on screen at rest, cancelling is honoured, the confirmation
    // states what will be destroyed, and confirming really removes it.
    const homeCrumbDel = doc.querySelector('#crumbs .crumb[data-crumb=""]');
    if (homeCrumbDel) {
      homeCrumbDel.click();
      await sleep(500);
    }
    const rowSel = '#note-list .folder-row[data-folder="' + targetId + '"]';
    const menuBtn = doc.querySelector(rowSel + " .folder-menu");
    check("the folder row offers its actions menu", !!menuBtn,
          "row=" + (!!doc.querySelector(rowSel)));

    if (menuBtn) {
      const style = frame.contentWindow.getComputedStyle(menuBtn);
      const box = menuBtn.getBoundingClientRect();
      check("the ellipsis menu is on screen without hovering",
            box.width > 0 && box.height > 0 && style.opacity !== "0"
            && style.visibility !== "hidden" && style.pointerEvents !== "none",
            "w=" + Math.round(box.width) + " opacity=" + style.opacity
            + " pointerEvents=" + style.pointerEvents);

      menuBtn.click();
      await sleep(400);
      const folderMenu = q("#folder-menu");
      check("the ellipsis opens the folder menu dialog",
            !!(folderMenu && folderMenu.open),
            "open=" + (folderMenu ? folderMenu.open : "no dialog"));

      // Cancel first. A confirmation that removes the folder whichever way it is
      // answered is not a confirmation at all, and that is invisible from the
      // source -- confirm() is stubbed out in every other test here.
      confirmAnswer = false;
      q("#folder-menu-delete").click();
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
      menuBtn.click();
      await sleep(300);
      q("#folder-menu-delete").click();
      await sleep(900);
      check("confirming removes the folder from the list",
            !doc.querySelector(rowSel));
      if (movedOk) {
        // Only meaningful when the note actually made it in there first.
        check("its notes go with it",
              doc.querySelectorAll("#note-list .item-row[data-item]").length === 0,
              "rows=" + doc.querySelectorAll("#note-list .item-row[data-item]").length);
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
    // v34: a minimal one-page PDF. Its xref is sketched rather than exact --
    // the checks assert the tile, the record and the iframe's blob: src, never
    // painted PDF pixels, and Chrome's viewer is lenient about a malformed
    // page table on a one-object file.
    const makePdfFile = name => {
      const body = "%PDF-1.4\\n"
        + "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\\n"
        + "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\\n"
        + "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\\n"
        + "xref\\n0 4\\n0000000000 65535 f \\n"
        + "trailer<</Size 4/Root 1 0 R>>\\nstartxref\\n9\\n%%EOF\\n";
      return new frame.contentWindow.File([body], name, { type: "application/pdf" });
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
      const photoRow = [...doc.querySelectorAll("#note-list .item-row[data-item]")]
        .find(row => row.textContent.includes("Photo Note"));
      if (photoRow) photoRow.click();
      await sleep(800);
      const reopened = doc.querySelector("#media-strip .media-thumb img");
      check("the photo survives save + reopen",
            !!reopened && (reopened.getAttribute("src") || "").startsWith("data:image/"),
            "img=" + !!reopened);
      photosOk = !!reopened;

      // v35: the top of the open panel states what the note carries -- the
      // same badge the rows show, from the same records as the strip.
      {
        const attachLine = doc.querySelector("#editor-attach");
        check("the open note shows its attachment count at the top",
              !!attachLine && !attachLine.classList.contains("hidden")
              && /1 png/.test(attachLine.textContent)
              && /1 attachment:/.test(attachLine.getAttribute("title") || ""),
              "line=" + (attachLine ? attachLine.textContent.trim() : "missing")
              + " title=" + (attachLine ? attachLine.getAttribute("title") : ""));
      }

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

      // v34: documents attach too. The PDF gets a labelled tile instead of a
      // fake picture, and the note row's count line reports all three.
      const pdfTransfer = new frame.contentWindow.DataTransfer();
      pdfTransfer.items.add(makePdfFile("probe.pdf"));
      input.files = pdfTransfer.files;
      input.dispatchEvent(new frame.contentWindow.Event("change", { bubbles: true }));
      await sleep(1500);

      const pdfTile = [...doc.querySelectorAll("#media-strip .media-thumb")]
        .find(cell => cell.querySelector(".media-file-tag"));
      check("a PDF attaches with its own labelled tile, not a broken img",
            !!pdfTile && (/PDF/.test(pdfTile.textContent)),
            "tile=" + (pdfTile ? pdfTile.textContent.trim() : "missing"));
      const rowsNow = await mediaRows();
      check("the PDF record carries its document type",
            rowsNow.some(r => r.type === "application/pdf"),
            "types=" + rowsNow.map(r => r.type).join(","));
      check("...and a PDF with no thumbnail shows no img",
            !pdfTile || !pdfTile.querySelector("img"),
            "img=" + !!(pdfTile && pdfTile.querySelector("img")));
      check("...and its bytes landed in OPFS",
            (await opfsCount()).length === 2, "files=" + (await opfsCount()).length);

      // The attach happened WHILE the editor is open -- the top line must have
      // recounted to the same badge the rows now show, live.
      {
        const attachLine = doc.querySelector("#editor-attach");
        check("the top attach line recounts when a PDF attaches mid-edit",
              !!attachLine && !attachLine.classList.contains("hidden")
              && /2 · 1 pdf · 1 png/.test(attachLine.textContent),
              "line=" + (attachLine ? attachLine.textContent.trim() : "missing"));
      }

      const attachRow = [...doc.querySelectorAll("#note-list .item-row[data-item]")]
        .find(row => row.textContent.includes("Photo Note"));
      const attachLine = attachRow && attachRow.querySelector(".item-attach");
      check("the note row counts its attachments by type",
            attachRow && attachLine
            && /2 · 1 pdf · 1 png/.test(attachLine.textContent)
            && /2 attachments:/.test(attachLine.getAttribute("title") || ""),
            "badge=" + (attachLine
              ? attachLine.textContent.trim() + " | " + attachLine.getAttribute("title")
              : "missing"));

      // The viewer gains the document branch: the iframe shows the OPFS copy
      // through an object URL; the img branch stays hidden (a PDF in <img> is
      // only ever alt text).
      if (pdfTile) pdfTile.click();
      await sleep(900);
      const frameEl = doc.querySelector("#media-frame");
      const imgEl = doc.querySelector("#media-view");
      check("tapping the PDF tile opens the document viewer",
            (doc.querySelector("#media-dialog") || {}).open && frameEl
            && !frameEl.classList.contains("hidden")
            && (frameEl.getAttribute("src") || "").startsWith("blob:")
            && imgEl.classList.contains("hidden")
            && !doc.querySelector("#media-hint").classList.contains("hidden"),
            "src=" + ((frameEl && frameEl.getAttribute("src") || "").slice(0, 5)));
      doc.querySelector("#media-close").click();
      await sleep(400);
      check("closing the document viewer empties the frame",
            !(doc.querySelector("#media-dialog") || {}).open
            && !(doc.querySelector("#media-frame").getAttribute("src")),
            "src=" + ((doc.querySelector("#media-frame").getAttribute("src") || "?")));

      // Deleting the note takes the rest of its media with it. The
      // confirmation must name the attachments that are about to go.
      confirmAnswer = true;
      const confirmsBeforeDelete = confirms.length;
      q("#delete-note-btn").click();
      await sleep(1000);
      const deleteWording = confirms[confirms.length - 1] || "";
      check("deleting a note with attachments warns about them",
            confirms.length > confirmsBeforeDelete
            && /Photo Note/.test(deleteWording) && /2 attachments/.test(deleteWording),
            "said=" + deleteWording.slice(0, 120));
      check("the photo note is gone from the list",
            !([...doc.querySelectorAll("#note-list .item-row[data-item]")]
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
      const keeperRow = [...doc.querySelectorAll("#note-list .item-row[data-item]")]
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

      const rootRow = [...doc.querySelectorAll("#note-list .folder-row")]
        .find(row => /Probe Root/.test(row.textContent));
      check("the export's root folder is created", !!rootRow,
            "rows=" + doc.querySelectorAll("#note-list .folder-row").length);
      if (rootRow) {
        // v38: browsing INTO the row is what makes the next folder its child --
        // the new-folder form parents to the folder being browsed.
        rootRow.click();
        await sleep(500);
        const drillCrumbs = q("#crumbs") ? q("#crumbs").textContent : "";
        check("the crumbs follow the drill into the folder (v38)",
              drillCrumbs.indexOf("Probe Root") !== -1,
              "crumbs=" + drillCrumbs.trim());
        q("#new-folder-btn").click();
        await sleep(400);
        q("#new-folder-name").value = "Probe Sub";
        q("#new-folder-form").requestSubmit();
        await sleep(800);
        const kidRows = [...doc.querySelectorAll("#note-list .folder-row")];
        check("the child folder was created inside the browsed folder (v38)",
              kidRows.length === 1
              && /Probe Sub/.test(kidRows[0] ? kidRows[0].textContent : ""),
              "rows=" + kidRows.map(r => r.textContent.trim().slice(0, 30)).join(" | "));
        // Back to Unfiled so the doomed note below starts out beside its keeper.
        const homeCrumbSub = doc.querySelector('#crumbs .crumb[data-crumb=""]');
        if (homeCrumbSub) {
          homeCrumbSub.click();
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
            doc.querySelectorAll("#note-list .item-row[data-item]").length === 2,
            "rows=" + doc.querySelectorAll("#note-list .item-row[data-item]").length);
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

    // The restored world: the root folder back on the drill list, the keeper
    // present, the doomed gone, agenda rebuilt, ratio untouched.
    const restoredRootRows = [...doc.querySelectorAll("#note-list .folder-row")];
    const restoredRoot = restoredRootRows.find(r => /Probe Root/.test(r.textContent));
    check("the folders came back (the parent visible, the child only inside it)",
          restoredRootRows.length === 1 && !!restoredRoot
          && !restoredRootRows.some(r => /Probe Sub/.test(r.textContent)),
          "rows=" + restoredRootRows.map(r => r.textContent.trim().slice(0, 40)).join(" | "));

    const keeperRows = [...doc.querySelectorAll("#note-list .item-row[data-item]")];
    check("the keeper note survived and the doomed note did not",
          keeperRows.length === 1 && /Keeper Note/.test(keeperRows[0].textContent)
          && !keeperRows.some(r => /Doomed Note/.test(r.textContent)),
          "rows=" + keeperRows.map(r => r.textContent.trim().slice(0, 30)).join(" | "));

    // v38: subfolder rows sort above note rows within the one list.
    check("subfolder rows sort above note rows (v38)",
          !!restoredRoot && keeperRows.length === 1
          && doc.querySelector("#note-list").firstElementChild === restoredRoot,
          "first=" + (doc.querySelector("#note-list").firstElementChild
            ? doc.querySelector("#note-list").firstElementChild.textContent.trim()
                .slice(0, 20)
            : "none"));

    if (restoredRoot) {
      restoredRoot.click();
      await sleep(600);
      // The child came back INSIDE the parent: only browsing into it reveals
      // the sub row, which is the nesting proof at drill width.
      const insideRows = [...doc.querySelectorAll("#note-list .folder-row")];
      const restoredSub = insideRows.find(r => /Probe Sub/.test(r.textContent));
      check("the restored subfolder is nested inside its parent (v38)",
            insideRows.length === 1 && !!restoredSub,
            "rows=" + insideRows.map(r => r.textContent.trim().slice(0, 30)).join(" | "));
      const inRoot = doc.querySelectorAll("#note-list .item-row[data-item]").length;
      check("the restored root folder is empty, as the backup recorded",
            inRoot === 0, "rows=" + inRoot);
      // Browse back out on the crumbs for the checks below.
      const homeCrumbRest = doc.querySelector('#crumbs .crumb[data-crumb=""]');
      if (homeCrumbRest) {
        homeCrumbRest.click();
        await sleep(500);
      }
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
      const rowsAfter = [...fresh.querySelectorAll("#note-list .folder-row")];
      check("the restored data is still there after the restart",
            rowsAfter.length === 1
            && /Probe Root/.test(rowsAfter[0] ? rowsAfter[0].textContent : ""),
            "rows=" + rowsAfter.map(r => r.textContent.trim().slice(0, 30)).join(" | "));
    }

    // --- v38: renaming and moving folders through the ellipsis menu ---
    // A drill row carries one ellipsis button: rename stays the inline editing
    // row the earlier probes already knew, move crosses parents through the
    // sheet (the arrows are gone; the order column survives in storage and
    // the backup), delete keeps the counted confirmation. The list only ever
    // shows the browsed folder's children, so the reload above left the stage
    // at the root with Probe Root visible and Probe Sub only inside it. The
    // reload replaced the document, so everything here re-queries the fresh
    // one -- and the confirm stub lives on the dead window's old contentWindow
    // frame state, which now belongs to the reloaded document again in this
    // variable's window, so restore clicks below re-arm it.
    const fdoc = frame.contentDocument;
    const fq = sel => fdoc.querySelector(sel);
    frame.contentWindow.confirm = message => {
      confirms.push(String(message));
      return confirmAnswer;
    };
    confirmAnswer = false;

    const rowNames = () => {
      // The browsed stage is the root, so every folder row in the list is a
      // root-level folder; a nested one only renders inside its parent.
      const rows = [...fdoc.querySelectorAll("#note-list .folder-row")];
      return rows.map(r => {
        const name = r.querySelector(".item-title");
        return name ? name.textContent.trim() : "?";
      });
    };
    const menuFor = name => {
      const row = [...fdoc.querySelectorAll("#note-list .folder-row")]
        .find(r => {
          const t = r.querySelector(".item-title");
          return t && t.textContent.trim() === name;
        });
      return row ? row.querySelector(".folder-menu") : null;
    };

    // Two more root folders. The reload left the world at Probe Root (browsed
    // root) and Probe Sub (inside it); creation lands at the root next to
    // Probe Root. Nothing has an order yet, so the display must still be
    // plain alphabetical.
    fq("#new-folder-btn").click();
    await sleep(400);
    fq("#new-folder-name").value = "Probe Alpha";
    fq("#new-folder-form").requestSubmit();
    await sleep(800);
    fq("#new-folder-btn").click();
    await sleep(400);
    fq("#new-folder-name").value = "Probe Beta";
    fq("#new-folder-form").requestSubmit();
    await sleep(800);
    check("the three probe root folders start in alphabetical order",
          rowNames().join("|") === "Probe Alpha|Probe Beta|Probe Root",
          "rows=" + rowNames().join(" | "));

    const alphaMenu = menuFor("Probe Alpha");
    check("the folder row carries its ellipsis menu at rest",
          !!alphaMenu, "rows=" + rowNames().join(" | "));
    {
      const box = alphaMenu ? alphaMenu.getBoundingClientRect() : null;
      const style = alphaMenu ? frame.contentWindow.getComputedStyle(alphaMenu) : null;
      check("the ellipsis menu is on screen without hovering",
            !!alphaMenu && box.width > 0 && box.height > 0
            && style.opacity !== "0" && style.visibility !== "hidden"
            && style.pointerEvents !== "none",
            "w=" + Math.round(box.width) + " h=" + Math.round(box.height)
            + " opacity=" + style.opacity);
    }
    if (alphaMenu) {
      // The menu is its own dialog whose target is remembered only while it
      // acts; "move" keeps the memory through the move sheet that follows.
      alphaMenu.click();
      await sleep(400);
      check("the ellipsis opens the folder menu dialog",
            !!(fq("#folder-menu") && fq("#folder-menu").open),
            "open=" + (fq("#folder-menu") ? fq("#folder-menu").open : "no dialog"));
      fq("#folder-menu-move").click();
      await sleep(500);
      const sheet = fq("#folder-move-sheet");
      const sheetRows = sheet ? [...sheet.querySelectorAll("[data-move-target]")] : [];
      const sheetNames = sheetRows.map(b => b.textContent.trim());
      check("the move sheet offers the other folders but never the folder itself (v38)",
            !!(sheet && sheet.open) && sheetNames.some(n => /Unfiled/.test(n))
            && sheetNames.some(n => /Probe Root/.test(n))
            && sheetNames.every(n => n.indexOf("Probe Alpha") === -1),
            "title=" + (fq("#folder-move-title") ? fq("#folder-move-title").textContent : "?")
            + " options=" + sheetNames.join(" | "));
      const rootTarget = sheetRows.find(b => /Probe Root/.test(b.textContent));
      if (rootTarget) {
        rootTarget.click();
        await sleep(800);
        check("a sheet move relocates a folder under another parent (v38)",
              rowNames().join("|") === "Probe Beta|Probe Root",
              "rows=" + rowNames().join(" | "));
      } else {
        check("a sheet move relocates a folder under another parent (v38)",
              false, "options=" + sheetNames.join(" | "));
      }
    }

    // --- the drill: browse INTO the parent and back out on the crumbs ---
    {
      const rootRow2 = [...fdoc.querySelectorAll("#note-list .folder-row")]
        .find(r => {
          const t = r.querySelector(".item-title");
          return t && t.textContent.trim() === "Probe Root";
        });
      if (rootRow2) {
        rootRow2.click();
        await sleep(600);
        const crumbText = fq("#crumbs") ? fq("#crumbs").textContent : "";
        const inside = [...fdoc.querySelectorAll("#note-list .folder-row")]
          .map(r => (r.querySelector(".item-title") || {}).textContent || "?");
        check("browsing into a folder shows its children under the crumb path (v38)",
              crumbText.indexOf("Probe Root") !== -1
              && inside.join("|") === "Probe Alpha|Probe Sub",
              "crumbs=" + crumbText.trim() + " rows=" + inside.join(" | "));
        const homeCrumb2 = fq('#crumbs .crumb[data-crumb=""]');
        check("the home crumb sits above the browsed children (v38)",
              !!homeCrumb2 && !homeCrumb2.disabled
              && homeCrumb2.textContent.trim() === "Notes",
              "crumb=" + (homeCrumb2 ? homeCrumb2.textContent.trim() : "missing"));
        if (homeCrumb2) {
          homeCrumb2.click();
          await sleep(600);
          check("tapping the home crumb browses back to the root (v38)",
                rowNames().join("|") === "Probe Beta|Probe Root",
                "rows=" + rowNames().join(" | "));
        }
      }
    }

    // --- rename through the menu: the inline editing row is unchanged ---
    const betaMenu = menuFor("Probe Beta");
    if (betaMenu) {
      betaMenu.click();
      await sleep(400);
      fq("#folder-menu-rename").click();
      await sleep(500);
      const editInput = fq(".folder-rename-input");
      check("the rename edit opens with the folder's current name",
            !!editInput && editInput.value === "Probe Beta",
            "value=" + (editInput ? editInput.value : "missing"));
      if (editInput) {
        editInput.value = "Probe Renamed";
        const saveForm = fq(".folder-rename-form");
        if (saveForm) saveForm.requestSubmit();
        await waitFor(() => !fq(".folder-rename-input"), 4000);
        check("saving commits the new folder name",
              rowNames()[0] === "Probe Renamed",
              "rows=" + rowNames().join(" | "));
      }
    }

    // --- the crumbs read the folders' live names: a rename reaches the path ---
    const rootMenuRename = menuFor("Probe Root");
    if (rootMenuRename) {
      rootMenuRename.click();
      await sleep(400);
      fq("#folder-menu-rename").click();
      await sleep(500);
      const rootInput = fq(".folder-rename-input");
      if (rootInput) {
        rootInput.value = "Probe Root Too";
        const rootForm = fq(".folder-rename-form");
        if (rootForm) rootForm.requestSubmit();
        await waitFor(() => !fq(".folder-rename-input"), 4000);
        check("the renamed folder shows its new name in the row list",
              rowNames().indexOf("Probe Root Too") !== -1,
              "rows=" + rowNames().join(" | "));
        const renamedRow = [...fdoc.querySelectorAll("#note-list .folder-row")]
          .find(r => /Probe Root Too/.test(r.textContent));
        if (renamedRow) {
          renamedRow.click();
          await sleep(600);
          check("the crumb path reflects the renamed folder (v38)",
                fq("#crumbs").textContent.indexOf("Probe Root Too") !== -1,
                "crumbs=" + (fq("#crumbs") ? fq("#crumbs").textContent.trim() : "n/a"));
          const homeCrumb3 = fq('#crumbs .crumb[data-crumb=""]');
          if (homeCrumb3) {
            homeCrumb3.click();
            await sleep(600);
          }
        }
      }
    }

    const escMenu = menuFor("Probe Renamed");
    if (escMenu) {
      escMenu.click();
      await sleep(400);
      fq("#folder-menu-rename").click();
      await sleep(500);
      const escInput = fq(".folder-rename-input");
      if (escInput) {
        escInput.value = "XXX Wrong";
        escInput.dispatchEvent(new KeyboardEvent("keydown",
          { key: "Escape", bubbles: true }));
        await waitFor(() => !fq(".folder-rename-input"), 3000);
        check("Escape cancels the rename",
              fdoc.querySelector("#note-list").textContent.indexOf("XXX Wrong") === -1
              && rowNames().indexOf("Probe Renamed") !== -1,
              "rows=" + rowNames().join(" | "));

        const emptyMenu = menuFor("Probe Renamed");
        if (emptyMenu) {
          emptyMenu.click();
          await sleep(400);
          fq("#folder-menu-rename").click();
          await sleep(500);
          const emptyInput = fq(".folder-rename-input");
          if (emptyInput) {
            emptyInput.value = "";
            const emptyForm = fq(".folder-rename-form");
            if (emptyForm) emptyForm.requestSubmit();
            await waitFor(() => !fq(".folder-rename-input"), 3000);
            check("an empty name keeps the folder's name silently",
                  !fq(".folder-rename-input")
                  && rowNames().indexOf("Probe Renamed") !== -1,
                  "rows=" + rowNames().join(" | "));
          }
        }
      }
    }

    const settingsAgain = fq("#settings-btn");
    settingsAgain.click();
    await sleep(400);
    fq("#export-btn").click();
    await waitFor(() => {
      const box = fq("#export-csv");
      return box && box.value.indexOf("Probe Renamed") !== -1;
    }, 6000);
    const orderedCsv = fq("#export-csv") ? fq("#export-csv").value : "";
    check("the backup CSV carries the folders' order column",
          orderedCsv.indexOf("id,parentId,name,order,createdAt,updatedAt") !== -1
          && orderedCsv.indexOf("# notes-backup v" + "__EXPECTED_SCHEMA__") === 0
          && /,Probe Renamed,,/.test(orderedCsv)
          && /,Probe Root Too,,/.test(orderedCsv)
          && /,Probe Alpha,/.test(orderedCsv),
          "header=" + orderedCsv.slice(0, 42));

    const orderedBox = fq("#restore-input");
    orderedBox.value = orderedCsv;
    fq("#preview-btn").click();
    await sleep(500);
    confirmAnswer = true;
    fq("#restore-btn").click();
    await waitFor(() => /Restored/.test(
      fq("#preview-out") ? fq("#preview-out").textContent : ""), 6000);
    fq("#settings-close").click();
    await sleep(300);
    check("restoring keeps the folders with their renames",
          rowNames().join("|") === "Probe Renamed|Probe Root Too",
          "rows=" + rowNames().join(" | "));

    // And the move survived the round trip too: the relocated folder came back
    // INSIDE its parent, beside the subfolder the restore itself had made.
    {
      const rootRow3 = [...fdoc.querySelectorAll("#note-list .folder-row")]
        .find(r => /Probe Root Too/.test(r.textContent));
      if (rootRow3) {
        rootRow3.click();
        await sleep(600);
        const inside3 = [...fdoc.querySelectorAll("#note-list .folder-row")]
          .map(r => (r.querySelector(".item-title") || {}).textContent || "?");
        check("...and the moved folder is still nested after the restore (v38)",
              inside3.join("|") === "Probe Alpha|Probe Sub",
              "rows=" + inside3.join(" | "));
        const homeCrumb4 = fq('#crumbs .crumb[data-crumb=""]');
        if (homeCrumb4) {
          homeCrumb4.click();
          await sleep(500);
        }
      }
    }

    const olderDoc = fdoc;
    frame.contentWindow.location.reload();
    const reloaded2 = await waitFor(() => {
      const d = frame.contentDocument;
      if (!d || d === olderDoc || !d.body) return false;
      return d.body.dataset.ready === "true" || d.body.dataset.fatal === "true";
    }, 20000);
    const fdoc2 = frame.contentDocument;
    const fq2 = sel => fdoc2.querySelector(sel);
    const rowNames2 = () => {
      const rows = [...fdoc2.querySelectorAll("#note-list .folder-row")];
      return rows.map(r => {
        const name = r.querySelector(".item-title");
        return name ? name.textContent.trim() : "?";
      });
    };
    check("the renames and the moves survive a reload",
          reloaded2 && rowNames2().join("|") === "Probe Renamed|Probe Root Too",
          "rows=" + rowNames2().join(" | "));

    // --- a long note list scrolls INSIDE its own panel ---
    // The crumbs sit ABOVE the list, so the list must be its own scroller:
    // when it grew past its row (the flex automatic minimum), the scrollable
    // overflow fell to .pane-body and scrolling a long list slid the crumbs
    // aside. Seed enough notes to overflow the panel, scroll the list, and
    // prove the list took the scroll while the crumbs bar's top and
    // .pane-body stayed put. Last on purpose -- the ten notes pollute nothing.
    {
      const seedNote = async label => {
        fq2("#new-note-btn").click();
        await sleep(600);
        fq2("#note-title").value = label;
        fq2("#note-editor").requestSubmit();
        await sleep(500);
        fq2("#editor-back").click();
        await sleep(400);
      };
      for (let i = 0; i < 10; i++) {
        await seedNote("Scroll Probe " + i + " extra words to keep the row tall");
      }
      await sleep(400);
      const scrolledList = fq2("#note-list");
      const crumbBar3 = fq2("#crumbs");
      const paneBody3 = scrolledList ? scrolledList.closest(".pane-body") : null;
      const crumbTop0 = crumbBar3 ? crumbBar3.getBoundingClientRect().top : -1;
      const overflowed = !!scrolledList
        && scrolledList.scrollHeight > scrolledList.clientHeight;
      if (scrolledList) scrolledList.scrollTop = 99999;
      await sleep(300);
      const crumbTop1 = crumbBar3 ? crumbBar3.getBoundingClientRect().top : -1;
      check("a long note list scrolls inside its own panel (crumbs stay put)",
            overflowed
            && !!scrolledList && scrolledList.scrollTop > 0
            && Math.abs(crumbTop1 - crumbTop0) <= 1
            && !!paneBody3 && paneBody3.scrollTop === 0,
            "overflowed=" + overflowed
            + " scrollTop=" + (scrolledList ? scrolledList.scrollTop : "n/a")
            + " crumbTop=" + crumbTop0 + "->" + crumbTop1
            + " paneBody=" + (paneBody3 ? paneBody3.scrollTop : "n/a"));
    }

    // --- v36: the brand mark is a reload button ---
    // Last on purpose: a reload creates a fresh document AND a fresh realm, so
    // every stub wired onto the old window dies (the v32 restore block re-arms
    // its own confirm for the same reason). Nothing after this needs a stub.
    // Queried from frame.contentDocument, NOT the script's opening doc: the
    // flow has restarted the app twice by now, so doc/q are stale by here --
    // the same reason the v32/v33 blocks minted fresh fdoc/fq helpers.
    {
      const liveDoc = frame.contentDocument;
      const brand = liveDoc.querySelector(".brand");
      const reloadBtn = liveDoc.querySelector("#reload-btn");
      check("the brand mark is a reload button",
            !!reloadBtn && !!brand && brand.contains(reloadBtn)
            && reloadBtn.tagName === "BUTTON"
            && reloadBtn.getAttribute("type") === "button"
            && !!reloadBtn.querySelector(".brand-mark")
            && !reloadBtn.contains(liveDoc.querySelector("h1"))
            && !!(reloadBtn.getAttribute("aria-label") || "").trim(),
            "tag=" + (reloadBtn ? reloadBtn.tagName : "missing"));
      const chipVersion = "__EXPECTED_VERSION__";
      const oldDoc = frame.contentDocument;
      reloadBtn.click();
      let freshDoc = null;
      for (let i = 0; i < 100 && !freshDoc; i++) {
        await sleep(200);
        const d = frame.contentDocument;
        freshDoc = d && d !== oldDoc && d.body
          && (d.body.dataset.ready === "true" || d.body.dataset.fatal === "true")
          ? d : null;
      }
      check("the brand-mark button reloads the app into a fresh document",
            !!freshDoc, "deadline=20s");
      check("the header chip refills after the brand-mark reload",
            !!freshDoc && !!freshDoc.querySelector("#home-version")
            && freshDoc.querySelector("#home-version").textContent === "v" + chipVersion,
            "chip=" + (freshDoc && freshDoc.querySelector("#home-version")
              ? freshDoc.querySelector("#home-version").textContent : "missing"));
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

    const row = [...doc.querySelectorAll("#note-list .item-row[data-item]")]
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

    // --- v38: folder rows at phone width ---
    // Placed BEFORE the editor blocks below (flow convenience since v35: the
    // drill list stays visible even while a note is open). The row's ellipsis
    // menu must remain on screen at 380px -- never hover-revealed, and no
    // narrow rule may hide it.
    q("#notes-tab").click();
    await sleep(400);
    q("#new-folder-btn").click();
    await sleep(400);
    q("#new-folder-name").value = "Narrow Folder";
    q("#new-folder-form").requestSubmit();
    await sleep(800);
    const narrowRow = [...doc.querySelectorAll("#note-list .folder-row")]
      .find(r => /Narrow Folder/.test(r.textContent));
    check("narrow: a created folder shows in the drill list", !!narrowRow);
    if (narrowRow) {
      const menuBtn = narrowRow.querySelector(".folder-menu");
      const menuBox = menuBtn && menuBtn.getBoundingClientRect();
      const menuStyle = menuBtn && frame.contentWindow.getComputedStyle(menuBtn);
      check("narrow: the ellipsis menu is on screen without hover",
            !!menuBtn && menuBox.width > 0 && menuBox.height > 0
            && menuStyle.opacity !== "0" && menuStyle.visibility !== "hidden"
            && menuStyle.pointerEvents !== "none"
            && menuStyle.display !== "none",
            "w=" + (menuBox ? Math.round(menuBox.width) : -1)
            + " h=" + (menuBox ? Math.round(menuBox.height) : -1));
      menuBtn.click();
      await sleep(400);
      const narrowMenu = q("#folder-menu");
      check("narrow: the ellipsis opens the folder menu dialog",
            !!(narrowMenu && narrowMenu.open),
            "open=" + (narrowMenu ? narrowMenu.open : "no dialog"));
      if (narrowMenu && typeof narrowMenu.close === "function") narrowMenu.close();
      await sleep(200);
    }

    // --- v34: the attachment-count line at phone width ---
    // The badge's whole point is visibility while browsing, so no narrow rule
    // may stand it down, and it must sit inside the preview's 1fr column (the
    // 2026-10-01 trap: a nowrap item in an auto grid column starved the
    // preview into one word per line).
    q("#notes-tab").click();
    await sleep(400);
    q("#new-note-btn").click();
    await sleep(800);
    q("#note-title").value = "Narrow Attached";
    q("#note-editor").requestSubmit();
    await sleep(600);
    {
      const input = doc.querySelector("#media-input");
      const canvas = doc.createElement("canvas");
      canvas.width = 24;
      canvas.height = 24;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#3d7be0";
      ctx.fillRect(0, 0, 24, 24);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
      const transfer = new frame.contentWindow.DataTransfer();
      transfer.items.add(new frame.contentWindow.File([blob], "n1.png", { type: "image/png" }));
      transfer.items.add(new frame.contentWindow.File([blob], "n2.png", { type: "image/png" }));
      input.files = transfer.files;
      input.dispatchEvent(new frame.contentWindow.Event("change", { bubbles: true }));
      await sleep(1500);
      q("#editor-back").click();
      await sleep(600);
    }
    const narrowAttached = [...doc.querySelectorAll("#note-list .item-row[data-item]")]
      .find(r => r.textContent.includes("Narrow Attached"));
    const narrowBadge = narrowAttached && narrowAttached.querySelector(".item-attach");
    check("narrow: an attached note's row shows its attachment count",
          !!narrowBadge && /2 png/.test(narrowBadge.textContent.trim()),
          "badge=" + (narrowBadge ? narrowBadge.textContent.trim() : "missing"));
    if (narrowBadge) {
      const main = narrowAttached.querySelector(".item-main");
      const badgeBox = narrowBadge.getBoundingClientRect();
      const mainBox = main.getBoundingClientRect();
      const rowBox = narrowAttached.getBoundingClientRect();
      check("narrow: the count line lives inside the preview column",
            narrowBadge.closest(".item-main") === main && badgeBox.width > 0
            && badgeBox.right <= rowBox.right && badgeBox.left >= mainBox.left - 1,
            "badgeW=" + Math.round(badgeBox.width)
            + " mainW=" + Math.round(mainBox.width));
    }

    // --- v38: creating a folder INSIDE the browsed one, at phone width ---
    // Narrow Folder gains a child: the row is browsed into, and the new-folder
    // form parents to the folder on screen. Nothing on the row needs a chip to
    // prove it -- the child is found only after the drill.
    {
      const freshRow = [...doc.querySelectorAll("#note-list .folder-row")]
        .find(r => /Narrow Folder/.test(r.textContent));
      if (freshRow) {
        freshRow.click();
        await sleep(600);
        const narrowCrumbs = q("#crumbs") ? q("#crumbs").textContent : "";
        check("narrow: browsing into the folder updates the crumbs (v38)",
              narrowCrumbs.indexOf("Notes") !== -1
              && narrowCrumbs.indexOf("Narrow Folder") !== -1,
              "crumbs=" + narrowCrumbs.trim());
        q("#new-folder-btn").click();
        await sleep(400);
        q("#new-folder-name").value = "Narrow Kid";
        q("#new-folder-form").requestSubmit();
        await sleep(800);
        const kidRows = [...doc.querySelectorAll("#note-list .folder-row")];
        check("narrow: the child folder was created inside the browsed folder (v38)",
              kidRows.length === 1
              && /Narrow Kid/.test(kidRows[0] ? kidRows[0].textContent : ""),
              "rows=" + kidRows.map(r => r.textContent.trim().slice(0, 30)).join(" | "));
        // Back out to the root for the gallery note below.
        const homeCrumbN = doc.querySelector('#crumbs .crumb[data-crumb=""]');
        if (homeCrumbN) {
          homeCrumbN.click();
          await sleep(500);
        }
      } else {
        check("narrow: the child folder was created inside the browsed folder (v38)",
              false, "Narrow Folder row missing");
      }
    }

    // --- the picture picker feeds the same pipeline ---
    // A fresh note attaches one png through #gallery-input -- the same
    // addNoteMedia ritual as the document input -- and its row badge counts
    // it. The browsed stage is Unfiled: the v38 block above browsed back out
    // of Narrow Folder, so the note and its row land here for the badge check.
    {
      q("#notes-tab").click();
      await sleep(400);
      q("#new-note-btn").click();
      await sleep(800);
      // Title is set and SAVED before the attach: addNoteMedia re-renders the
      // editor as it persists, so an unsaved-title-only editor would feed an
      // empty title into the submit (the note then lists as "Untitled note").
      q("#note-title").value = "Narrow Gallery";
      q("#note-editor").requestSubmit();
      await sleep(600);
      const input = doc.querySelector("#gallery-input");
      const canvas = doc.createElement("canvas");
      canvas.width = 24;
      canvas.height = 24;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#e0533d";
      ctx.fillRect(0, 0, 24, 24);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
      const transfer = new frame.contentWindow.DataTransfer();
      transfer.items.add(new frame.contentWindow.File([blob], "gal.png", { type: "image/png" }));
      input.files = transfer.files;
      input.dispatchEvent(new frame.contentWindow.Event("change", { bubbles: true }));
      await sleep(1500);
      check("narrow: the picture picker attaches through the same pipeline",
            !q("#media-strip").classList.contains("hidden")
            && q("#media-strip").querySelectorAll(".media-thumb").length === 1,
            "thumbs=" + q("#media-strip").querySelectorAll(".media-thumb").length);
      q("#editor-back").click();
      await sleep(600);
      const galleryRow = [...doc.querySelectorAll("#note-list .item-row[data-item]")]
        .find(r => r.textContent.includes("Narrow Gallery"));
      const galleryBadge = galleryRow && galleryRow.querySelector(".item-attach");
      check("narrow: the gallery-attached note's row badge counts it",
            !!galleryBadge && /1 png/.test(galleryBadge.textContent.trim()),
            "rowFound=" + !!galleryRow
            + " titles=" + [...doc.querySelectorAll("#note-list .item-row[data-item]")]
              .map(r => r.textContent.trim().slice(0, 20)).slice(0, 8).join(" | ")
            + " badge=" + (galleryBadge ? galleryBadge.textContent.trim() : "none"));
    }

    // The camera path is device-only (headless cannot open a camera applet),
    // so the probe asserts the input's picker recipe instead of driving it.
    {
      const cam = doc.querySelector("#camera-input");
      const gallery = doc.querySelector("#gallery-input");
      check("narrow: the camera input is hidden and capture-pinned",
            !!cam && cam.tagName === "INPUT"
            && (cam.getAttribute("accept") || "") === "image/*,video/*"
            && cam.getAttribute("capture") === "environment"
            && cam.getAttribute("type") === "file"
            && cam.hasAttribute("hidden"),
            "accept=" + (cam ? cam.getAttribute("accept") : "missing")
            + " capture=" + (cam ? cam.getAttribute("capture") : "missing"));
      check("narrow: the gallery input is media-only and uncaptured",
            !!gallery && gallery.getAttribute("capture") === null
            && (gallery.getAttribute("accept") || "") === "image/*,video/*",
            "accept=" + (gallery ? gallery.getAttribute("accept") : "missing"));
    }

    // --- v38: the editor's actions are one row, and the paperclip is a menu ---
    // Attach (menu), Delete, Save share one line at phone width too, and the
    // paperclip opens the attach menu whose buttons fire the SAME three
    // hidden inputs the flow drove above. One line is still a layout fact:
    // the tops must agree and the row must be one button tall, not two.
    q("#notes-tab").click();
    await sleep(400);
    q("#new-note-btn").click();
    await sleep(800);
    const actionRowsN = [...doc.querySelectorAll("#note-editor .editor-actions")];
    const actionButtonsN = ["#attach-menu-btn", "#delete-note-btn", "#save-note-btn"]
      .map(sel => q(sel));
    if (actionButtonsN.every(b => b)) {
      const topsN = actionButtonsN.map(b => Math.round(b.getBoundingClientRect().top));
      const buttonH = actionButtonsN[1].getBoundingClientRect().height;
      check("narrow: the editor actions are one row -- Attach, Delete, Save",
            actionRowsN.length === 1
            && Math.max(...topsN) - Math.min(...topsN) <= 2
            && actionRowsN[0].getBoundingClientRect().height <= buttonH * 1.35,
            "rows=" + actionRowsN.length + " tops=" + topsN.join(",")
            + " rowH=" + Math.round(actionRowsN[0].getBoundingClientRect().height)
            + " btnH=" + Math.round(buttonH));
      const attachBtn = q("#attach-menu-btn");
      attachBtn.click();
      await sleep(300);
      const attachMenu = q("#attach-menu");
      check("narrow: the paperclip opens the attach menu dialog",
            !!(attachMenu && attachMenu.open)
            && !!q("#menu-gallery-btn") && !!q("#menu-camera-btn")
            && !!q("#menu-doc-btn"),
            "open=" + (attachMenu ? attachMenu.open : "no dialog"));
      // The menu buttons fire the same hidden inputs: spy on the gallery
      // input's click so no file chooser is involved, then assert the menu
      // spent itself and the input was the thing fired.
      let galleryFired = false;
      const galleryInput = q("#gallery-input");
      if (galleryInput) galleryInput.click = () => { galleryFired = true; };
      q("#menu-gallery-btn").click();
      await sleep(300);
      check("narrow: choosing gallery closes the menu and fires the gallery input",
            galleryFired && !!(attachMenu && !attachMenu.open),
            "fired=" + galleryFired
            + " stillOpen=" + (attachMenu ? attachMenu.open : "?"));
      check("narrow: an open editor takes the agenda's half too",
            q(".pane-agenda").getBoundingClientRect().height <= 2,
            "agendaH=" + Math.round(q(".pane-agenda").getBoundingClientRect().height));
      check("narrow: no photo strip shows on a note without photos",
            !q("#media-strip") || q("#media-strip").classList.contains("hidden"),
            "stripVisible=" + (q("#media-strip")
              ? !q("#media-strip").classList.contains("hidden") : "missing"));
      check("narrow: the top attach line stays hidden on a note without photos",
            !q("#editor-attach") || q("#editor-attach").classList.contains("hidden"),
            "visible=" + (q("#editor-attach")
              ? !q("#editor-attach").classList.contains("hidden") : "missing"));
    } else {
      check("narrow: the editor action buttons all exist",
            false, "missing=" + actionButtonsN.map(b => !!b).join(","));
    }

    // --- v37: the untitled note this block created reads its folder ---
    // createNote() persists title:"" immediately, so the note is in the store
    // untitled; back in the list its row must read "Unfiled" -- the name of
    // the folder it lives in -- while Narrow Probe/Attached/Gallery are titled
    // and cannot collide with it.
    q("#editor-back").click();
    await sleep(600);
    const unfiledNamed = [...doc.querySelectorAll("#note-list .item-row[data-item]")]
      .filter(row => {
        const t = row.querySelector(".item-title");
        return t && t.textContent.trim() === "Unfiled";
      }).length;
    check("narrow: an untitled note reads the folder it lives in (v37)",
          unfiledNamed === 1, "rows-titled-Unfiled=" + unfiledNamed);
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
