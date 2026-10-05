#!/usr/bin/env python3
"""Pick up a notes backup pasted into the PRIVATE relay repo and email it.

This is the phone -> PC bridge for backups when the phone's share sheet is
refused (NotAllowedError in Chrome on Android was the case in the wild):
the Web Share path dies, but the clipboard copy ("Share CSV for backup",
renamed from Copy CSV on 2026-10-02) always works, and the relay repo is
reachable from the phone's browser.

Phone flow (the inbox lives in choochatgpt/ask-ai-relay -- PRIVATE, never
the public Pages repo):
    1. Notes -> Settings -> Share CSV for backup
    2. github.com/choochatgpt/ask-ai-relay -> gitway/transfer_inbox/notes/
       -> inbox.csv -> Edit (pencil) -> select all -> paste -> Commit changes
    3. On the PC: python tools/relay_pull_backup.py

PC flow (this script):
    1. fast-forward the relay clone (aborts if the tree is dirty);
    2. read the inbox BLOB from git (`git show`, exact repo bytes -- immune
       to autocrlf checkout rewriting);
    3. refuse anything that does not start with the `# notes-backup` header
       or exceeds the size cap -- the placeholder must never be emailed;
    4. copy it into the GitWay transfer inbox with a manifest (filename,
       size, SHA256, transfer id, source commit -- per gitway_guide.md #15);
    5. hand it to tools/email_backup.py, which SMTPs it as a real
       attachment with credentials read only from env / config outside
       every repository;
    6. on send success, reset the inbox file to its placeholder, commit and
       push, so the notes bytes leave the repo HEAD and the next paste
       starts clean. A failed send leaves the repo untouched -- the payload
       is never lost to a config problem.

Rules this tool exists under (2026-09-30): the user directed backups to
move through the private relay repo. Scope is text backup CSVs only, private
repo only. Media bytes and the public notes repo remain forbidden, and no
credential ever passes through this script -- git auth is the machine's own,
SMTP auth belongs to email_backup.py's config.

Usage:
    python tools/relay_pull_backup.py                 # pick up + email + reset
    python tools/relay_pull_backup.py --to someone@example.com
    python tools/relay_pull_backup.py --dry-run       # build email, send nothing,
                                                      # reset nothing
    python tools/relay_pull_backup.py --no-cleanup    # send, leave inbox as pasted
    python tools/relay_pull_backup.py --archive-dir C:\notes_backups
                                                      # also keep a dated copy on disk
    python tools/relay_pull_backup.py --list-config   # via email_backup.py

Exit codes: 0 = nothing waiting or full success, 1 = failure.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path

REPO_DEFAULT = r"C:\python\projects\chat\ask-ai-relay"
INBOX_ROOT_DEFAULT = r"C:\python\projects\gitway_transfer\inbox\2026-09-27-01-Notes"
PROJECT_ID = "2026-09-27-01-Notes"
REPO_INBOX_PATH = "gitway/transfer_inbox/notes/inbox.csv"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_HEADER = b"# notes-backup"
PLACEHOLDER_FIRST_LINE = b"# NOTES TRANSFER INBOX"
PLACEHOLDER = (
    "# NOTES TRANSFER INBOX - this is a placeholder, not a backup.\n"
    "# From the phone: open this file on github.com, tap the pencil (Edit),\n"
    "# select all, paste the CSV copied from Notes -> Settings ->\n"
    "# 'Share CSV for backup',\n"
    "# then Commit changes. The PC pickup tool (tools/relay_pull_backup.py in\n"
    "# the notes repo) emails it via SMTP and resets this file to the placeholder.\n"
).encode("utf-8")
TOOLS_DIR = Path(__file__).resolve().parent

# May run under the windowless watcher (pythonw). Without this flag every
# console child (git, the email sender) makes Windows pop a fresh black
# window -- one per poll cycle (reported 2026-10-05).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        creationflags=_NO_WINDOW,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"git {' '.join(args)} failed:\n"
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="relay_pull_backup.py",
        description="Pick up a notes backup pasted into the private relay "
                    "repo's transfer inbox and email it from this PC.",
    )
    parser.add_argument("--repo", default=REPO_DEFAULT,
                        help=f"local relay clone (default: {REPO_DEFAULT})")
    parser.add_argument("--inbox-root", default=INBOX_ROOT_DEFAULT,
                        help=f"transfer inbox root (default: {INBOX_ROOT_DEFAULT})")
    parser.add_argument("--repo-path", default=REPO_INBOX_PATH,
                        help=f"inbox path inside the repo (default: {REPO_INBOX_PATH})")
    parser.add_argument("--to", help="recipient (falls back to email_backup.py's "
                                     "default_to config)")
    parser.add_argument("--dry-run", action="store_true",
                        help="build the email and print it; send nothing, reset nothing")
    parser.add_argument("--no-cleanup", action="store_true",
                        help="send, but leave the repo inbox exactly as pasted")
    parser.add_argument("--archive-dir", metavar="DIR",
                        help="also write a dated copy of each picked-up backup "
                             "here (the watcher uses this so every backup lands "
                             "on disk as well as in the mailbox)")
    args = parser.parse_args(argv)

    repo = Path(args.repo)
    if not (repo / ".git").is_dir():
        raise SystemExit(f"{repo} is not a git clone -- check --repo")

    # Refuse on any TRACKED modification; untracked entries are ignored
    # deliberately -- this clone carries standing local-only infra files,
    # and an untracked file can neither block a --ff-only pull of a clean
    # tree nor end up in a commit that adds only the inbox path.
    dirty = [
        line for line in run_git(repo, "status", "--porcelain")
        .decode("utf-8", "replace").splitlines()
        if line.strip() and not line.startswith("??")
    ]
    if dirty:
        raise SystemExit(
            "the relay clone has uncommitted changes -- refusing to touch it.\n"
            + "\n".join(dirty)
            + "\nResolve them first; this tool only runs on a tree with no "
              "tracked modifications."
        )
    run_git(repo, "pull", "--ff-only")
    commit = run_git(repo, "rev-parse", "--short", "HEAD").decode().strip()

    payload = run_git(repo, "show", f"HEAD:{args.repo_path}")
    if payload.startswith(PLACEHOLDER_FIRST_LINE):
        print("no backup waiting: the inbox is still the placeholder.")
        return 0
    if not payload.startswith(BACKUP_HEADER):
        print("no backup waiting: the inbox does not start with "
              f"{BACKUP_HEADER.decode()!r} and is not the placeholder either "
              "(paste the whole CSV, starting at its first line).")
        return 0
    if len(payload) > MAX_BYTES:
        raise SystemExit(f"payload is {len(payload)} bytes, over the "
                         f"{MAX_BYTES} cap -- refusing (gitway_guide.md #15.4)")

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    transfer_id = f"NOTES-{stamp}"
    inbox_dir = Path(args.inbox_root) / transfer_id
    inbox_dir.mkdir(parents=True, exist_ok=True)
    dest = inbox_dir / "inbox.csv"
    dest.write_bytes(payload)

    sha256 = hashlib.sha256(payload).hexdigest()
    manifest = {
        "filename": dest.name,
        "size": len(payload),
        "sha256": sha256,
        "mime_type": "text/csv",
        "project_id": PROJECT_ID,
        "transfer_id": transfer_id,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": f"ask-ai-relay@{commit}:{args.repo_path}",
        "destination": str(dest),
    }
    (inbox_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"extracted {len(payload)} bytes -> {dest}")
    print(f"sha256 {sha256}")

    # The dated on-disk copy, so every backup lands on the PC even if the
    # mailbox is never opened. Written before the send: an email failure then
    # still leaves the bytes safe here, not only in the inbox.
    if args.archive_dir and not args.dry_run:
        archive = Path(args.archive_dir)
        archive.mkdir(parents=True, exist_ok=True)
        dated = archive / f"notes-backup-{stamp}.csv"
        counter = 1
        while dated.exists():
            dated = archive / f"notes-backup-{stamp}-{counter}.csv"
            counter += 1
        dated.write_bytes(payload)
        manifest["archived_to"] = str(dated)
        (inbox_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"archived copy -> {dated}")

    email_cmd = [sys.executable, str(TOOLS_DIR / "email_backup.py"), str(dest)]
    if args.dry_run:
        email_cmd.append("--dry-run")
    if args.to:
        email_cmd += ["--to", args.to]
    mailed = subprocess.run(email_cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                            creationflags=_NO_WINDOW)
    sent = mailed.returncode == 0
    if not sent:
        print("the repo was left untouched -- the payload is still in the "
              "inbox; fix the config (see email_backup.py --list-config) and rerun.")
        if mailed.stderr and mailed.stderr.strip():
            print("email sender said: " + mailed.stderr.strip()[:400])
        return 1
    if args.dry_run:
        print("dry-run: nothing sent, inbox left as pasted.")
        return 0
    if args.no_cleanup:
        print("sent; inbox left as pasted (--no-cleanup).")
        return 0

    (repo / args.repo_path).write_bytes(PLACEHOLDER)
    run_git(repo, "add", args.repo_path)
    run_git(repo, "commit", "-m",
            f"Reset notes transfer inbox after pickup ({transfer_id})")
    run_git(repo, "push")
    print(f"inbox reset to placeholder and pushed (commit "
          f"{run_git(repo, 'rev-parse', '--short', 'HEAD').decode().strip()}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
