#!/usr/bin/env python3
"""Drive the real app in headless Chrome and check that its controls respond.

    python tools/browser_check.py
    python tools/browser_check.py --compare-stale 3db9b76

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
    const alerts = [];
    frame.contentWindow.alert = message => alerts.push(message);
    frame.contentWindow.confirm = () => false;

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

    q("#backup-btn").click();
    await sleep(200);
    check("Backup button responds", alerts.length > 0, "alerts=" + alerts.length);

    // --- the lower pane ---
    const agenda = q("#agenda-list");
    check("agenda pane rendered", !!agenda && agenda.children.length > 0,
          "children=" + (agenda ? agenda.children.length : "n/a"));

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


def run_probe(stage: Path, port: int, browser: Path) -> list[dict]:
    HOLDER["data"] = None
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", port), functools.partial(Probe, directory=str(stage)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    profile = WORK / f"chrome-profile-{port}"
    process = subprocess.Popen(
        [
            str(browser), "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions",
            f"--user-data-dir={profile}",
            f"http://127.0.0.1:{port}/_probe.html",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

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
        raise SystemExit(f"probe never reported back for {stage.name} (Chrome hung or the page errored)")
    return json.loads(HOLDER["data"])


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
    parser.add_argument("--browser", help="path to a Chrome or Edge binary")
    args = parser.parse_args()

    browser = find_browser(args.browser)
    WORK.mkdir(parents=True, exist_ok=True)

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
