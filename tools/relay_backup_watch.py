#!/usr/bin/env python3
"""Watch the PRIVATE relay repo and handle each upload automatically.

Two inboxes, two handlers, one poll -- plus the sync branch:

  * the TEXT backup inbox (gitway/transfer_inbox/notes/inbox.csv): the phone
    pastes a backup CSV there (Settings -> 'Share CSV for backup'); when the
    blob on origin stops being the placeholder this runs
    relay_pull_backup.py, which archives, emails and resets the inbox.
  * the MEDIA inbox (gitway/transfer_inbox/notes/media/): the phone uploads
    saved photos there ("Add file" -> "Upload files"); when any real file is
    present this runs relay_pull_media.py, which archives them under
    C:\\notes_backups\\media with SHA256 manifests and clears the folder.
  * the SYNC branch (notes-inbox): the app itself pushes a whole export --
    backup CSV + every photo -- through api.github.com with the user's own
    token (the one-tap route the user asked for on 2026-10-03, modelled on
    JScan's authorised transport). When anything but ack.json is on the
    branch this runs relay_pull_sync_inbox.py, which verifies the bundle,
    archives + emails it, then rewrites the branch so the bytes leave GitHub.

This watcher removes the remembering: it polls the relay clone every --every
seconds and the watched path is exactly the tool a manual run would take --
one code path, one set of gates per inbox.

Design rules:
  * The remote is read with `git fetch` + `git show`/`git ls-tree` on
    FETCH_HEAD -- no checkout, no working-tree churn, and autocrlf cannot
    rewrite the bytes.
  * The repo itself is only ever touched by the pickup tools' own success
    paths (each resets its inbox and pushes after a good pickup).
  * Any failure is logged and retried on the next cycle; a failed pickup
    leaves the payload sitting in its inbox, so nothing is ever lost.
  * Placeholder/garbage/idle states are logged once per change, not once per
    cycle, so an idle watcher does not grow the log.

Runs under pythonw.exe from a scheduled task ("Notes backup watch", at
sign-in): sys.stdout is None there, so the first thing this does is point
stdout/stderr at the log file.

Usage:
    python tools/relay_backup_watch.py              # loop every 300s (default)
    python tools/relay_backup_watch.py --every 60   # poll faster
    python tools/relay_backup_watch.py --once       # a single cycle, then exit

Exit codes: 0 = clean run (--once with nothing waiting, or Ctrl-C), 1 = a
--once cycle that tried a pickup and failed; the loop itself never exits 1.
"""
from __future__ import annotations

import argparse
import datetime
import subprocess
import sys
import time
from pathlib import Path

REPO_DEFAULT = r"C:\python\projects\chat\ask-ai-relay"
ARCHIVE_DEFAULT = r"C:\notes_backups"
REPO_INBOX_PATH = "gitway/transfer_inbox/notes/inbox.csv"
REPO_MEDIA_DIR = "gitway/transfer_inbox/notes/media"
SYNC_BRANCH = "notes-inbox"
MEDIA_PLACEHOLDERS = {"readme.md", "readme.txt", ".gitkeep"}
MAX_BYTES = 10 * 1024 * 1024
BACKUP_HEADER = b"# notes-backup"
PLACEHOLDER_FIRST_LINE = b"# NOTES TRANSFER INBOX"
LOG_FILE = Path(ARCHIVE_DEFAULT) / "relay_backup_watch.log"
TOOLS_DIR = Path(__file__).resolve().parent

# The watcher normally runs under pythonw (no console of its own). Without
# this flag every console child (git, the pull tools, the email sender)
# makes Windows pop a fresh black window -- one per poll cycle, which the
# user rightly called irritating (reported 2026-10-05).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    return result.stdout


def log(line: str) -> None:
    """One timestamped line to the console and the log file. Never raises."""
    text = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {line}"
    print(text)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 512 * 1024:
            LOG_FILE.replace(LOG_FILE.with_name(LOG_FILE.name + ".old"))
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    except OSError:
        pass  # the console line still happened; a log hiccup must not kill the loop


def remote_blob(repo: Path, repo_path: str, head: str) -> bytes | None:
    """The blob exactly as origin holds it (None when the path is absent)."""
    try:
        return run_git(repo, "show", f"FETCH_HEAD:{repo_path}")
    except RuntimeError as error:
        if "exists on disk, but not in" in error.args[0] \
                or "does not exist" in error.args[0] or "fatal" in error.args[0]:
            return None
        raise


def media_step(args: argparse.Namespace, repo: Path, head: str) -> str | None:
    """Run the photo pickup when any real file is waiting. None when idle."""
    try:
        listing = run_git(repo, "ls-tree", "--name-only",
                          f"FETCH_HEAD:{REPO_MEDIA_DIR}").decode("utf-8", "replace")
    except RuntimeError as error:
        if "does not exist" in error.args[0] or "fatal" in error.args[0]:
            return None  # the media inbox has not been created yet
        raise
    waiting = [
        name.strip() for name in listing.splitlines()
        if name.strip() and name.strip().lower() not in MEDIA_PLACEHOLDERS
    ]
    if not waiting:
        return None  # an idle media inbox must not grow the log
    log(f"media pickup: {len(waiting)} file(s) waiting (origin {head}): "
        f"{', '.join(waiting)}")
    done = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "relay_pull_media.py"),
         "--repo", str(repo)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW)
    for line in (done.stdout or "").splitlines():
        log("  | " + line)
    if done.stderr and done.stderr.strip():
        log("  stderr: " + done.stderr.strip()[:400])
    if done.returncode == 0:
        return f"media pickup succeeded: {len(waiting)} file(s) archived, folder cleared"
    return (f"relay_pull_media.py exited {done.returncode}; whatever stayed "
            "behind is retried next cycle")


def sync_step(args: argparse.Namespace, repo: Path, head: str) -> str | None:
    """Run the one-tap sync pickup when anything but ack.json waits. None when
    idle -- including the branch not existing yet, which is the normal state
    until the app's first sync."""
    try:
        listing = run_git(repo, "ls-tree", "-r", "--name-only",
                          f"origin/{SYNC_BRANCH}").decode("utf-8", "replace")
    except RuntimeError as error:
        if "Not a valid object name" in error.args[0] \
                or "does not exist" in error.args[0] or "fatal" in error.args[0]:
            return None
        raise
    waiting = [
        name.strip() for name in listing.splitlines()
        if name.strip() and name.strip() != "ack.json"
    ]
    if not waiting:
        return None  # an empty (acknowledged) branch must not grow the log
    log(f"sync pickup: {len(waiting)} file(s) waiting on {SYNC_BRANCH} "
        f"(origin {head}): {', '.join(waiting[:8])}"
        + (" ..." if len(waiting) > 8 else ""))
    done = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "relay_pull_sync_inbox.py"),
         "--repo", str(repo)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=_NO_WINDOW)
    for line in (done.stdout or "").splitlines():
        log("  | " + line)
    if done.stderr and done.stderr.strip():
        log("  stderr: " + done.stderr.strip()[:400])
    if done.returncode == 0:
        return "sync pickup succeeded: bundle archived (+ emailed if a backup CSV rode along), branch cleared"
    return (f"relay_pull_sync_inbox.py exited {done.returncode}; the bundle is "
            "still on the branch and the next cycle retries it")


def csv_step(args: argparse.Namespace, repo: Path, head: str) -> str:
    """The original text-backup pickup, unchanged in behavior."""
    blob = remote_blob(repo, args.repo_path, head)
    if blob is None:
        return f"origin {head}: no inbox file on origin; nothing to do"
    if blob.startswith(PLACEHOLDER_FIRST_LINE):
        return f"origin {head}: inbox is the placeholder; nothing waiting"
    if not blob.startswith(BACKUP_HEADER):
        return (f"origin {head}: inbox is neither placeholder nor backup "
                "-- left alone (paste the whole CSV, starting at its first line)")
    if len(blob) > MAX_BYTES:
        return (f"origin {head}: payload is {len(blob)} bytes, over the "
                f"{MAX_BYTES} cap -- left alone")

    log(f"pickup: inbox holds a {len(blob)}-byte backup (origin {head})")
    cmd = [sys.executable, str(TOOLS_DIR / "relay_pull_backup.py"),
           "--repo", str(repo), "--repo-path", args.repo_path,
           "--archive-dir", args.archive_dir]
    if args.to:
        cmd += ["--to", args.to]
    done = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          creationflags=_NO_WINDOW)
    for line in (done.stdout or "").splitlines():
        log("  | " + line)
    if done.stderr and done.stderr.strip():
        log("  stderr: " + done.stderr.strip()[:400])
    if done.returncode == 0:
        return "pickup succeeded: archived + emailed, inbox reset"
    return (f"relay_pull_backup.py exited {done.returncode}; the payload is "
            "still in the inbox and the next cycle retries it")


def cycle(args: argparse.Namespace) -> str:
    """One poll: fetch once, then both inboxes. Raises only on infra errors."""
    repo = Path(args.repo)
    if not (repo / ".git").is_dir():
        raise RuntimeError(f"{repo} is not a git clone -- check --repo")
    run_git(repo, "fetch", "origin", "--prune")
    head = run_git(repo, "rev-parse", "--verify", "--short", "FETCH_HEAD") \
        .decode().strip()
    results = [media_step(args, repo, head), csv_step(args, repo, head),
               sync_step(args, repo, head)]
    return "; ".join(result for result in results if result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="relay_backup_watch.py",
        description="Watch the private relay repo; archive + email each pasted "
                    "notes backup automatically.",
    )
    parser.add_argument("--repo", default=REPO_DEFAULT,
                        help=f"local relay clone (default: {REPO_DEFAULT})")
    parser.add_argument("--repo-path", default=REPO_INBOX_PATH,
                        help=f"inbox path inside the repo (default: {REPO_INBOX_PATH})")
    parser.add_argument("--archive-dir", default=ARCHIVE_DEFAULT,
                        help=f"dated copies land here (default: {ARCHIVE_DEFAULT})")
    parser.add_argument("--to", help="email recipient override "
                                     "(default: email_backup.py's default_to)")
    parser.add_argument("--every", type=float, default=300.0, metavar="SECONDS",
                        help="seconds between polls (default: 300)")
    parser.add_argument("--once", action="store_true",
                        help="run one poll cycle and exit")
    args = parser.parse_args(argv)

    # Under pythonw.exe (the scheduled task) sys.stdout is None, and even the
    # first print would throw. Give the prints the log file itself.
    if sys.stdout is None:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = LOG_FILE.open("a", encoding="utf-8", buffering=1)

    log(f"watcher started: repo={args.repo} inbox={args.repo_path} "
        f"media={REPO_MEDIA_DIR} sync={SYNC_BRANCH} archive={args.archive_dir} "
        f"every={args.every:g}s")
    failures = 0
    while True:
        try:
            log(cycle(args))
        except Exception as error:  # infrastructure trouble: log, wait, retry
            failures += 1
            log(f"cycle failed ({failures} so far): {error}")
        if args.once:
            return 1 if failures else 0
        try:
            time.sleep(args.every)
        except KeyboardInterrupt:
            log("watcher stopped")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
