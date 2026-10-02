#!/usr/bin/env python3
"""Watch the PRIVATE relay repo and handle each pasted notes backup automatically.

The backup loop is: on the phone, Settings -> 'Share CSV for backup' -> paste
into the relay inbox -> Commit. The last leg -- getting the backup onto this PC
and emailed -- used to need someone to remember to run
`python tools/relay_pull_backup.py`.
This watcher removes the remembering: it polls the relay clone every
--every seconds, and the moment the inbox blob on origin stops being the
placeholder it runs relay_pull_backup.py in a subprocess, so the watched path
is exactly the tool a manual run would take -- one code path, one set of
gates (header check, size cap, manifest, SHA256, inbox reset on success).

Design rules:
  * The remote is read with `git fetch` + `git show FETCH_HEAD:PATH` -- no
    checkout, no working-tree churn, and autocrlf cannot rewrite the bytes.
  * The repo itself is only ever touched by relay_pull_backup.py's own
    success path (it resets the inbox and pushes after a good send).
  * Any failure is logged and retried on the next cycle; a failed send leaves
    the payload sitting in the inbox, so nothing is ever lost to a bad config.
  * Placeholder/garbage states are logged once per change, not once per
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
MAX_BYTES = 10 * 1024 * 1024
BACKUP_HEADER = b"# notes-backup"
PLACEHOLDER_FIRST_LINE = b"# NOTES TRANSFER INBOX"
LOG_FILE = Path(ARCHIVE_DEFAULT) / "relay_backup_watch.log"
TOOLS_DIR = Path(__file__).resolve().parent


def run_git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
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


def remote_inbox_bytes(repo: Path, repo_path: str) -> tuple[bytes | None, str]:
    """The inbox blob exactly as origin holds it, and the commit it came from."""
    run_git(repo, "fetch", "origin", "--prune")
    head = run_git(repo, "rev-parse", "--verify", "--short", "FETCH_HEAD") \
        .decode().strip()
    try:
        return run_git(repo, "show", f"FETCH_HEAD:{repo_path}"), head
    except RuntimeError as error:
        if "exists on disk, but not in" in error.args[0] \
                or "does not exist" in error.args[0] or "fatal" in error.args[0]:
            return None, head
        raise


def cycle(args: argparse.Namespace) -> str:
    """One poll. Returns what happened; raises only on infrastructure errors."""
    repo = Path(args.repo)
    if not (repo / ".git").is_dir():
        raise RuntimeError(f"{repo} is not a git clone -- check --repo")

    blob, head = remote_inbox_bytes(repo, args.repo_path)
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
                          encoding="utf-8", errors="replace")
    for line in (done.stdout or "").splitlines():
        log("  | " + line)
    if done.stderr and done.stderr.strip():
        log("  stderr: " + done.stderr.strip()[:400])
    if done.returncode == 0:
        return "pickup succeeded: archived + emailed, inbox reset"
    return (f"relay_pull_backup.py exited {done.returncode}; the payload is "
            "still in the inbox and the next cycle retries it")


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
        f"archive={args.archive_dir} every={args.every:g}s")
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
