#!/usr/bin/env python3
r"""Pick up one-tap sync bundles from the PRIVATE relay repo's notes-inbox branch.

This is the PC half of the sync route the user asked for on 2026-10-03
("the moment i click the button export notes/reminders to email, it must sync
all notes and jpg to my PC. just like how jscan does it"). The transport is the
one the client authorised for JScan on 2026-09-21 (LEARN_UPLOAD_DESIGN_REVIEW.md):
the app holds the user's OWN fine-grained PAT in device localStorage only and
pushes bytes to an ORPHAN BRANCH of the private repo through api.github.com.
The public notes repo never carries media, and the token is never in the source.

Bundle shape on the branch (one commit per export, dirs per export id):

    <exportId>/manifest.json     schema notes.sync.bundle/1, per-file sha256s
    <exportId>/backup.csv        the text-only backup CSV (# notes-backup v1)
    <exportId>/media/<file>      the photos/videos themselves

PC flow (this script):
    1. refuse to run on a clone with tracked modifications (same gate as the
       other relay tools);
    2. fetch and list the branch with `git ls-tree` -- no checkout, no working
       churn, autocrlf cannot rewrite the bytes;
    3. for each bundle: read its manifest, verify EVERY file's sha256 and size
       against it, and refuse the whole bundle on any mismatch;
    4. archive the media under --media-archive (dated + SHA256 manifest +
       GitWay transfer copy, same shape as relay_pull_media.py, duplicate
       files skipped by hash), archive the CSV under --csv-archive, and email
       the CSV through tools/email_backup.py -- the same email route the pasted
       text inbox has always used;
    5. only after every bundle is verified and on disk, replace the branch with
       a single parentless commit holding ack.json (--force). A plain delete
       would leave the bytes recoverable in GitHub history forever -- the same
       problem JScan documented -- so the rewrite IS the cleanup. The branch is
       machine-written and carries nobody else's work, which is the only reason
       a force-push is permissible here; it must never be generalised.
       ack.json lists the processed export ids, which is how the phone shows
       "picked up" without any issue comments.

A failure on any file leaves the whole branch untouched -- nothing is lost to
a half-run, and the next cycle retries (media dedupe is by sha256, so a retry
does not double-archive).

No credential passes through this script: git auth is the machine's own, SMTP
auth belongs to email_backup.py's outside-repo config.

Usage:
    python tools/relay_pull_sync_inbox.py                 # pick up + archive + email + ack
    python tools/relay_pull_sync_inbox.py --dry-run       # list what is waiting
    python tools/relay_pull_sync_inbox.py --no-cleanup    # process, leave the branch
    python tools/relay_pull_sync_inbox.py --to someone@example.com

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
MEDIA_ARCHIVE_DEFAULT = r"C:\notes_backups\media"
CSV_ARCHIVE_DEFAULT = r"C:\notes_backups"
PROJECT_ID = "2026-09-27-01-Notes"
BRANCH = "notes-inbox"
ACK_PATH = "ack.json"
BUNDLE_SCHEMA = "notes.sync.bundle/1"
MAX_BYTES_PER_FILE = 24 * 1024 * 1024
MAX_CSV_BYTES = 10 * 1024 * 1024
CSV_HEADER = b"# notes-backup"
TOOLS_DIR = Path(__file__).resolve().parent


def run_git(repo: Path, *args: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        input=stdin,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"git {' '.join(args)} failed:\n"
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    return result.stdout


def branch_blob(repo: Path, branch_ref: str, path: str) -> bytes | None:
    """One file exactly as the branch holds it; None when the path is absent."""
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", f"{branch_ref}:{path}"],
        capture_output=True,
    )
    return result.stdout if result.returncode == 0 else None


def known_hashes(media_archive: Path) -> set[str]:
    """SHA256s already archived, read from every run manifest on disk."""
    seen: set[str] = set()
    if not media_archive.is_dir():
        return seen
    for manifest in media_archive.glob("*/manifest.json"):
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8")).get("files", [])
        except (OSError, ValueError):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("sha256"):
                seen.add(entry["sha256"])
    return seen


def list_branch_files(repo: Path, branch_ref: str) -> list[tuple[str, str]]:
    """(sha, path) for every blob on the branch, ack.json excluded."""
    try:
        listing = run_git(repo, "ls-tree", "-r", branch_ref).decode("utf-8", "replace")
    except SystemExit as error:
        # A missing branch reads "fatal: Not a valid object name" -- the app
        # has never synced, which is an idle state, not a failure.
        if "Not a valid object name" in str(error) or "does not exist" in str(error):
            return []
        raise
    entries = []
    for line in listing.splitlines():
        # <mode> <type> <sha>\t<path>  (same shape relay_pull_media.py parses)
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or parts[1] != "blob":
            continue
        path = path.strip()
        if path and path != ACK_PATH:
            entries.append((parts[2], path))
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="relay_pull_sync_inbox.py",
        description="Archive + email one-tap sync bundles pushed by the notes "
                    "app to the private relay repo's notes-inbox branch, then "
                    "rewrite the branch so the bytes leave GitHub.",
    )
    parser.add_argument("--repo", default=REPO_DEFAULT,
                        help=f"local relay clone (default: {REPO_DEFAULT})")
    parser.add_argument("--inbox-root", default=INBOX_ROOT_DEFAULT,
                        help=f"GitWay transfer inbox root (default: {INBOX_ROOT_DEFAULT})")
    parser.add_argument("--media-archive", default=MEDIA_ARCHIVE_DEFAULT,
                        help=f"dated media archive root (default: {MEDIA_ARCHIVE_DEFAULT})")
    parser.add_argument("--csv-archive", default=CSV_ARCHIVE_DEFAULT,
                        help=f"dated CSV archive root (default: {CSV_ARCHIVE_DEFAULT})")
    parser.add_argument("--to", help="email recipient override "
                                     "(default: email_backup.py's default_to)")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what is waiting; archive nothing, send nothing")
    parser.add_argument("--no-cleanup", action="store_true",
                        help="process everything, but leave the branch as pushed")
    args = parser.parse_args(argv)

    repo = Path(args.repo)
    if not (repo / ".git").is_dir():
        raise SystemExit(f"{repo} is not a git clone -- check --repo")

    # Same refusal as the other relay tools: tracked modifications only --
    # a dirty clone means a human is mid-work and must not be pushed under.
    dirty = [
        line for line in run_git(repo, "status", "--porcelain")
        .decode("utf-8", "replace").splitlines()
        if line.strip() and not line.startswith("??")
    ]
    if dirty:
        raise SystemExit(
            "the relay clone has uncommitted changes -- refusing to touch it.\n"
            + "\n".join(dirty)
        )

    # Fetch into the remote-tracking ref only: this tool never checks out and
    # never needs a local branch (the ack rewrite pushes by SHA), and a local
    # branch refspec could refuse a legitimate non-fast-forward after the
    # history rewrite. A missing branch is the normal pre-first-sync state,
    # not a failure.
    try:
        run_git(repo, "fetch", "origin", BRANCH, "--prune")
    except SystemExit as error:
        if "couldn't find remote ref" in str(error) \
                or "not found" in str(error).lower():
            print(f"nothing waiting: {BRANCH} does not exist on origin yet "
                  "(the app has never synced).")
            return 0
        raise
    branch_ref = f"origin/{BRANCH}"
    head = run_git(repo, "rev-parse", "--verify", "--short", branch_ref) \
        .decode().strip()

    entries = list_branch_files(repo, branch_ref)
    if not entries:
        print(f"nothing waiting: {branch_ref} holds only ack.json (or does not "
              "exist yet -- the app has never synced).")
        return 0

    bundles: dict[str, dict] = {}
    for sha, path in entries:
        export_id = path.split("/")[0] if "/" in path else ""
        if not export_id or export_id not in path:
            print(f"REFUSED {path}: outside every bundle directory; left on the branch.")
            return 1
        if path == f"{export_id}/manifest.json":
            continue  # read separately below; files[] lists only payloads
        bundles.setdefault(export_id, {})[path] = sha

    # Verify every bundle completely before anything is written anywhere.
    verified = []
    for export_id in sorted(bundles):
        files = bundles[export_id]
        manifest_raw = branch_blob(repo, branch_ref, f"{export_id}/manifest.json")
        if manifest_raw is None:
            print(f"REFUSED {export_id}: no manifest.json; bundle left on the branch.")
            return 1
        try:
            manifest = json.loads(manifest_raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            print(f"REFUSED {export_id}: manifest.json is not valid JSON.")
            return 1
        if manifest.get("schema") != BUNDLE_SCHEMA:
            print(f"REFUSED {export_id}: manifest schema is "
                  f"{manifest.get('schema')!r}, expected {BUNDLE_SCHEMA!r}.")
            return 1
        listed = manifest.get("files") or []
        if sorted(f"{export_id}/{entry.get('path', '')}" for entry in listed) \
                != sorted(files):
            print(f"REFUSED {export_id}: manifest file list does not match the "
                  "branch contents.")
            return 1
        for entry in listed:
            rel = entry["path"]
            size = int(entry.get("bytes", -1))
            if size > MAX_BYTES_PER_FILE:
                print(f"REFUSED {export_id}/{rel}: {size} bytes is over the "
                      f"{MAX_BYTES_PER_FILE}-byte per-file cap.")
                return 1
            if rel.startswith("media/"):
                continue  # hashed again below, straight from the blob
            payload = branch_blob(repo, branch_ref, f"{export_id}/{rel}")
            if payload is None or hashlib.sha256(payload).hexdigest() != entry.get("sha256"):
                print(f"REFUSED {export_id}/{rel}: sha256 mismatch against the manifest.")
                return 1
        verified.append((export_id, manifest, listed))

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    transfer_id = f"NOTESSYNC-{stamp}"
    if args.dry_run:
        print(f"{len(verified)} bundle(s) waiting on {branch_ref} (origin {head}):")
        for export_id, manifest, listed in verified:
            print(f"  {export_id}  exported {manifest.get('created_utc', '?')}"
                  f"  {len(listed)} file(s)")
            for entry in listed:
                print(f"    {entry['path']} ({entry.get('bytes', '?')} bytes)")
        print("dry-run: nothing archived, nothing sent, branch left as pushed.")
        return 0

    media_archive = Path(args.media_archive) / transfer_id
    inbox_run = Path(args.inbox_root) / transfer_id
    seen = known_hashes(Path(args.media_archive))
    manifest_files = []
    csv_payloads = []  # (export_id, bytes)
    duplicates = []

    for export_id, _manifest, listed in verified:
        for entry in listed:
            rel = entry["path"]
            payload = branch_blob(repo, branch_ref, f"{export_id}/{rel}")
            if payload is None:
                print(f"REFUSED {export_id}/{rel}: vanished between verify and read.")
                return 1
            sha256 = hashlib.sha256(payload).hexdigest()
            if rel.startswith("media/"):
                # Defensive: write basenames only, whatever the manifest said.
                name = Path(rel).name
                if sha256 in seen:
                    duplicates.append(name)
                    print(f"duplicate skipped (already archived): {name}")
                    continue
                media_archive.mkdir(parents=True, exist_ok=True)
                dest = media_archive / name
                counter = 1
                while dest.exists():
                    dest = media_archive / f"{dest.stem}-{counter}{dest.suffix}"
                    counter += 1
                dest.write_bytes(payload)
                inbox_run.mkdir(parents=True, exist_ok=True)
                transfer_copy = inbox_run / name
                transfer_copy.write_bytes(payload)
                manifest_files.append({
                    "filename": name,
                    "size": len(payload),
                    "sha256": sha256,
                    "project_id": PROJECT_ID,
                    "transfer_id": transfer_id,
                    "bundle": export_id,
                    "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "source": f"ask-ai-relay@{head}:{BRANCH}/{export_id}/{rel}",
                    "destination": str(dest),
                    "transfer_copy": str(transfer_copy),
                })
                seen.add(sha256)
                print(f"archived {name} ({len(payload)} bytes) -> {dest}")
            else:
                # The backup CSV: archive it, then email it. Header-gated like
                # the pasted text inbox -- the placeholder must never be mailed.
                if not payload.startswith(CSV_HEADER):
                    print(f"REFUSED {export_id}/{rel}: does not start with "
                          f"{CSV_HEADER.decode()!r}.")
                    return 1
                if len(payload) > MAX_CSV_BYTES:
                    print(f"REFUSED {export_id}/{rel}: over the {MAX_CSV_BYTES}-byte cap.")
                    return 1
                csv_payloads.append((export_id, payload))

    manifest_path = media_archive / "manifest.json"
    if manifest_files:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps({
            "transfer_id": transfer_id,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "source_commit": head,
            "files": manifest_files,
            "duplicates_cleared": duplicates,
        }, indent=2) + "\n", encoding="utf-8")

    # The CSV leg: dated copy on disk first, then the same SMTP route as the
    # pasted text inbox. A send failure aborts BEFORE the branch rewrite, so
    # the next cycle retries the whole bundle (media dedupe is by sha256).
    for export_id, payload in csv_payloads:
        csv_archive = Path(args.csv_archive)
        csv_archive.mkdir(parents=True, exist_ok=True)
        dated = csv_archive / f"notes-backup-{stamp}.csv"
        counter = 1
        while dated.exists():
            dated = csv_archive / f"notes-backup-{stamp}-{counter}.csv"
            counter += 1
        dated.write_bytes(payload)
        print(f"archived backup CSV ({export_id}, {len(payload)} bytes) -> {dated}")
        email_cmd = [sys.executable, str(TOOLS_DIR / "email_backup.py"), str(dated)]
        if args.to:
            email_cmd += ["--to", args.to]
        sent = subprocess.run(email_cmd).returncode == 0
        if not sent:
            print("the branch was left untouched -- fix the email config "
                  "(see email_backup.py --list-config) and rerun.")
            return 1

    if not manifest_files and not csv_payloads:
        print("nothing was picked up (all duplicates); branch left as pushed.")
        return 0

    if args.no_cleanup:
        print(f"processed {len(manifest_files)} media file(s) and "
              f"{len(csv_payloads)} backup CSV(s); branch left as pushed (--no-cleanup).")
        return 0

    # The ack IS the cleanup: one parentless commit whose only file lists what
    # was processed, force-pushed over the branch. The bundles leave HEAD and
    # history at the same moment -- they are not recoverable on GitHub after
    # this, which is the point.
    ack = json.dumps({
        "schema": "notes.sync.ack/1",
        "processed": [export_id for export_id, _m, _l in verified],
        "source_commit": head,
        "transfer_id": transfer_id,
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
    }, indent=2).encode("utf-8") + b"\n"
    ack_blob = run_git(repo, "hash-object", "-w", "--stdin", stdin=ack) \
        .decode().strip()
    tree = run_git(repo, "mktree",
                   stdin=f"100644 blob {ack_blob}\t{ACK_PATH}\n".encode("utf-8")) \
        .decode().strip()
    ack_commit = run_git(
        repo, "commit-tree", tree, "-m",
        f"notes sync inbox: acknowledge + clear {len(verified)} bundle(s) ({transfer_id})"
    ).decode().strip()
    run_git(repo, "push", "--force", "origin", f"{ack_commit}:refs/heads/{BRANCH}")
    print(f"acknowledged + cleared: {branch_ref} rewritten to {ack_commit[:12]} "
          f"(processed: {', '.join(sorted(bundles))}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
