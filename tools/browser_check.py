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
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORK = Path(tempfile.gettempdir()) / "notes_browser_check"

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
<iframe id="app" src="./index.html" allow="web-share" width="900" height="760"></iframe>
<script>
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const results = [];
  const check = (name, ok, detail) => results.push({ name, ok: !!ok, detail: detail || "" });

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
      // STARTS at 50%, and 50% is deliberately not one of the four offered
      // shares -- it is where a fresh device lives until the first click.
      check("the ratio control opens on the default 50%",
            chipText() === "50%" && Math.abs(paneShare() - 0.5) <= 0.04,
            "chip=" + chipText() + " topShare=" + paneShare().toFixed(3));
      // The four requested top shares in order, then one more click to prove
      // the wrap -- which also leaves a non-default share behind for the
      // persistence check at the very end.
      const cycle = [["20%", 0.2], ["40%", 0.4], ["60%", 0.6],
                     ["80%", 0.8], ["20%", 0.2]];
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
      // clicked -- a real mailto hangs headless Chrome), the download, and the
      // copy confirmation.
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
            exportedCsv.indexOf("# notes-backup v1") === 0
            && exportedCsv.indexOf("Probe Standup") !== -1,
            exportedCsv.slice(0, 70));
      check("the CSV carries all three sections",
            ["## folders", "## notes", "## reminders"]
              .every(section => exportedCsv.indexOf(section) !== -1));

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
            decoded.indexOf("# notes-backup v1") !== -1
            && decoded.indexOf("Probe Standup") !== -1,
            "decoded=" + decoded.length + " chars");
      check("the mail link is offered, since this backup fits the safe limit",
            !!mailLink && !mailLink.classList.contains("hidden")
            && mailLink.getAttribute("aria-disabled") !== "true",
            "hidden=" + (mailLink ? mailLink.classList.contains("hidden") : "n/a"));

      const download = q("#download-csv-btn");
      const downloadName = download ? (download.getAttribute("download") || "") : "";
      check("a downloadable CSV is offered",
            !!download && (download.getAttribute("href") || "").indexOf("blob:") === 0
            && downloadName.indexOf("notes-backup-") === 0
            && downloadName.slice(-4) === ".csv",
            "download=" + downloadName);

      q("#copy-csv-btn").click();
      await sleep(400);
      const sizeNote = q("#export-size-note");
      check("Copy CSV confirms or explains itself",
            /Copied to the clipboard|Copying was blocked/.test(
              sizeNote ? sizeNote.textContent : ""),
            "said=" + (sizeNote ? sizeNote.textContent : "n/a"));

      // Web Share hands the real file to another app where the browser
      // supports it (phones). Where it does not, the button must hide itself:
      // a visible control that opens nothing reads as "the feature does not
      // exist", which is the failure this project has already hit twice. The
      // check asserts visibility agrees with the browser's own capability
      // either way, so it holds in headless Chrome regardless of support.
      const shareBtn = q("#share-csv-btn");
      let fileShareable = false;
      try {
        const frameNav = frame.contentWindow.navigator;
        fileShareable = typeof frameNav.canShare === "function"
          && frameNav.canShare({
               files: [new File(["# notes-backup v1"], "probe.csv",
                                { type: "text/csv" })]
             });
      } catch (e) {
        fileShareable = false;
      }
      check("the share button shows only when the browser can share files",
            !!shareBtn && shareBtn.classList.contains("hidden") === !fileShareable,
            "hidden=" + (shareBtn ? shareBtn.classList.contains("hidden") : "n/a")
            + " canShare=" + fileShareable);

      // The button can be visible yet the share still refused: Chrome on
      // Android (Honor Magic V5) in the wild answers canShare(files) yes
      // and then denies share() itself with NotAllowedError. Stub both
      // endings so the two
      // messages are proven rather than assumed, then restore the real
      // method -- the stubs shadow the prototype with an own property, so
      // deleting the own property puts the original back.
      if (shareBtn && !shareBtn.classList.contains("hidden")) {
        const frameNav = frame.contentWindow.navigator;
        Object.defineProperty(frameNav, "share", {
          configurable: true,
          value: () => frame.contentWindow.Promise.resolve()
        });
        shareBtn.click();
        await sleep(350);
        check("a completed share confirms itself",
              (sizeNote ? sizeNote.textContent : "").indexOf("Shared.") !== -1,
              "said=" + (sizeNote ? sizeNote.textContent : "n/a"));
        Object.defineProperty(frameNav, "share", {
          configurable: true,
          value: () => frame.contentWindow.Promise.reject(
                   new frame.contentWindow.DOMException("Permission denied",
                                                        "NotAllowedError"))
        });
        shareBtn.click();
        await sleep(350);
        check("a refused share explains the way out, not a bare denial",
              (sizeNote ? sizeNote.textContent : "").indexOf("refused the share") !== -1
              && (sizeNote ? sizeNote.textContent : "").indexOf("Download") !== -1,
              "said=" + (sizeNote ? sizeNote.textContent : "n/a"));
        delete frameNav.share;
      }

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
          !!chipAfter && chipAfter.textContent.trim() === "20%"
          && Math.abs(shareAfter - 0.2) <= 0.04,
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
            !!chip && chip.textContent.trim() === "20%",
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
            Math.abs(share - 0.2) <= 0.04, "topShare=" + share.toFixed(3));
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

HOLDER: dict[str, str | None] = {"data": None}


class Probe(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/__result"):
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
    for asset in ("index.html", "app.css", "view.js", "storage.js", "reminder.js",
                  "backup.js", "manifest.webmanifest"):
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


def drive(port: int, browser: Path, httpd, thread, label: str,
          cross_origin: bool = False) -> list[dict]:
    """Run Chrome at the probe page and wait for it to report back.

    Chrome is terminated and the server shut down on every path out, including
    the timeout, so a hung browser cannot leave a port held for the next run.
    """
    # A fresh profile every run. IndexedDB lives in the profile, so a leftover
    # one would carry notes and folders into the next run and make any count
    # this probe asserts depend on history rather than on what it just did.
    profile = WORK / f"chrome-profile-{port}"
    if profile.exists():
        shutil.rmtree(profile, ignore_errors=True)

    argv = [
        str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
        "--no-default-browser-check", "--disable-extensions",
        f"--user-data-dir={profile}",
    ]
    if cross_origin:
        # The probe reads and clicks inside a frame served from another origin.
        # Without this its document is opaque and every check fails for a reason
        # that has nothing to do with the app under test. It also needs a
        # non-default profile, which the line above already supplies.
        argv += ["--disable-web-security", "--disable-site-isolation-trials"]
    argv.append(f"http://127.0.0.1:{port}/_probe.html")

    process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 90
    try:
        while time.time() < deadline and HOLDER["data"] is None:
            time.sleep(0.25)
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    if HOLDER["data"] is None:
        raise SystemExit(f"probe never reported back for {label} (browser hung or the page errored)")
    return json.loads(HOLDER["data"])


def run_probe(stage: Path, port: int, browser: Path) -> list[dict]:
    HOLDER["data"] = None
    httpd, thread = serve(stage, port)
    return drive(port, browser, httpd, thread, stage.name)


def run_probe_url(url: str, port: int, browser: Path) -> list[dict]:
    """Probe an app that is already served -- the live site, not a local stage."""
    HOLDER["data"] = None
    # The probe page itself only drives the clicks, so a throwaway directory is
    # enough; the app under test comes from `url`.
    scratch = WORK / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "_probe.html").write_text(
        PROBE.replace('src="./index.html"', f'src="{url}"'), encoding="utf-8")
    httpd, thread = serve(scratch, port)
    return drive(port, browser, httpd, thread, url, cross_origin=True)


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
        verdicts[label] = results
        report(label, results)

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
