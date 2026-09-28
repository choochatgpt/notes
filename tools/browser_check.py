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
<iframe id="app" src="./index.html" width="900" height="760"></iframe>
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
    const status = () => { const el = q("#storage-status"); return el ? el.textContent : "n/a"; };

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

    // Wait for the app to settle: the status line changes once, whether startup
    // succeeded or threw, and data-fatal is set only on a startup failure.
    const settled = await waitFor(
      () => body.dataset.fatal === "true" || status() !== "Local storage: checking\u2026"
    );
    check("app settled after startup", settled, "status=" + status());

    check("no fatal startup banner", body.dataset.fatal !== "true", "status=" + status());

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

    q("#backup-btn").click();
    await sleep(200);
    check("Backup button responds", alerts.length > 0, "alerts=" + alerts.length);

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
