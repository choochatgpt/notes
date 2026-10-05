#!/usr/bin/env python3
r"""Pick up photos uploaded to the PRIVATE relay repo's media inbox, archive them.

This is the PC half of the photo-backup route the user chose on 2026-10-02
(the "GitWay media inbox"), which deliberately relaxes the 2026-09-30
no-media-bytes rule FOR THE PRIVATE REPO ONLY. The public notes repo and the
backup CSV stay text-only. The flow:

    1. Phone: Notes -> open a note -> tap a photo -> "Save to device"
       (the bytes land in the phone's downloads/gallery);
    2. Phone: github.com/choochatgpt/ask-ai-relay ->
       gitway/transfer_inbox/notes/media/ -> "Add file" -> "Upload files"
       -> pick the saved photo(s) -> Commit changes;
    3. PC: this tool (or relay_backup_watch.py, which runs it automatically).

PC flow (this script):
    1. fast-forward the relay clone (aborts if the tree is dirty -- tracked
       modifications only; standing untracked infra files are ignored);
    2. list the media inbox via `git ls-tree` (exact repo state, no checkout);
    3. for each file: refuse anything over the per-file cap (GitHub's web
       upload refuses files over 25 MB, so anything larger cannot have arrived
       through the sanctioned flow); read the blob with `git cat-file`
       (autocrlf-immune);
    4. copy it into the GitWay transfer inbox with a manifest (filename, size,
       SHA256, transfer id, source commit -- per gitway_guide.md #15), keep a
       dated archive copy under --archive-dir, and skip+report exact duplicates
       (same SHA256 already archived) instead of filing them twice;
    5. after every file is safely on disk, `git rm` exactly the picked-up
       files in ONE commit and push, so the photos leave the repo HEAD. A
       failure on a file leaves that file in the repo -- nothing is lost to a
       half-run, and rerunning resumes.

No email: the archive on this PC is the deliverable (the user's photos do not
need a mailbox copy; the backup CSV keeps its email route).

Usage:
    python tools/relay_pull_media.py                # pick up + archive + clear
    python tools/relay_pull_media.py --dry-run      # list what is waiting
    python tools/relay_pull_media.py --no-cleanup   # archive, leave the repo files
    python tools/relay_pull_media.py --archive-dir D:\somewhere

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
ARCHIVE_DEFAULT = r"C:\notes_backups\media"
PROJECT_ID = "2026-09-27-01-Notes"
REPO_MEDIA_DIR = "gitway/transfer_inbox/notes/media"
# GitHub's web "Upload files" refuses individual files over 25 MB, so a larger
# blob did not arrive through the sanctioned flow -- refuse rather than assume.
MAX_BYTES_PER_FILE = 24 * 1024 * 1024
PLACEHOLDER_NAMES = {"readme.md", "readme.txt", ".gitkeep"}
TOOLS_DIR = Path(__file__).resolve().parent

# May run under the windowless watcher (pythonw). Without this flag every
# console child (git) makes Windows pop a fresh black window -- one per
# poll cycle (reported 2026-10-05).
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


def known_hashes(archive_dir: Path) -> set[str]:
    """SHA256s already archived, read from every run manifest on disk."""
    seen: set[str] = set()
    if not archive_dir.is_dir():
        return seen
    for manifest in archive_dir.glob("*/manifest.json"):
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8")).get("files", [])
        except (OSError, ValueError):
            continue
        for entry in entries:
            if isinstance(entry, dict) and entry.get("sha256"):
                seen.add(entry["sha256"])
    return seen


def run_git_opt(repo: Path, *args: str) -> bytes | None:
    """Like run_git, but returns None when git fails (missing paths etc.)."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        creationflags=_NO_WINDOW,
    )
    return result.stdout if result.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="relay_pull_media.py",
        description="Archive photos uploaded to the private relay repo's "
                    "media inbox, then clear them from the repo.",
    )
    parser.add_argument("--repo", default=REPO_DEFAULT,
                        help=f"local relay clone (default: {REPO_DEFAULT})")
    parser.add_argument("--inbox-root", default=INBOX_ROOT_DEFAULT,
                        help=f"GitWay transfer inbox root (default: {INBOX_ROOT_DEFAULT})")
    parser.add_argument("--repo-dir", default=REPO_MEDIA_DIR,
                        help=f"media inbox path inside the repo (default: {REPO_MEDIA_DIR})")
    parser.add_argument("--archive-dir", default=ARCHIVE_DEFAULT,
                        help=f"dated archive root (default: {ARCHIVE_DEFAULT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what is waiting; archive nothing, reset nothing")
    parser.add_argument("--no-cleanup", action="store_true",
                        help="archive, but leave the repo files in place")
    args = parser.parse_args(argv)

    repo = Path(args.repo)
    if not (repo / ".git").is_dir():
        raise SystemExit(f"{repo} is not a git clone -- check --repo")

    # Same refusal as relay_pull_backup.py: tracked modifications only.
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
    run_git(repo, "pull", "--ff-only")
    commit = run_git(repo, "rev-parse", "--short", "HEAD").decode().strip()

    raw_listing = run_git_opt(repo, "ls-tree", f"HEAD:{args.repo_dir}")
    if raw_listing is None:
        print(f"no media inbox yet: {args.repo_dir} does not exist on origin "
              "(nothing to pick up).")
        return 0
    listing = raw_listing.decode("utf-8", "replace")
    entries = []
    for line in listing.splitlines():
        # <mode> <type> <sha>\t<name>
        meta, _, name = line.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or parts[1] != "blob":
            continue
        if name.strip().lower() in PLACEHOLDER_NAMES:
            continue
        entries.append((parts[2], name.strip()))
    if not entries:
        print("no media waiting: the media inbox holds only its placeholder.")
        return 0

    if args.dry_run:
        print(f"{len(entries)} file(s) waiting in {args.repo_dir}:")
        for sha, name in entries:
            size = int(run_git(repo, "cat-file", "-s", sha).decode().strip())
            print(f"  {name} ({size} bytes)")
        print("dry-run: nothing archived, nothing reset.")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    transfer_id = f"NOTESMEDIA-{stamp}"
    archive_run = Path(args.archive_dir) / transfer_id
    inbox_run = Path(args.inbox_root) / transfer_id
    seen = known_hashes(Path(args.archive_dir))

    manifest_files = []
    picked: list[str] = []
    duplicates: list[str] = []
    refused: list[str] = []
    for sha, name in entries:
        size = int(run_git(repo, "cat-file", "-s", sha).decode().strip())
        if size > MAX_BYTES_PER_FILE:
            refused.append(name)
            print(f"REFUSED {name}: {size} bytes is over the "
                  f"{MAX_BYTES_PER_FILE}-byte per-file cap; left in the repo.")
            continue
        payload = run_git(repo, "cat-file", "blob", sha)
        sha256 = hashlib.sha256(payload).hexdigest()
        if sha256 in seen:
            duplicates.append(name)
            print(f"duplicate skipped (already archived): {name}")
            picked.append(name)  # safe to clear: the bytes are already on disk
            continue

        archive_run.mkdir(parents=True, exist_ok=True)
        dest = archive_run / name
        counter = 1
        while dest.exists():
            dest = archive_run / f"{dest.stem}-{counter}{dest.suffix}"
            counter += 1
        dest.write_bytes(payload)

        inbox_run.mkdir(parents=True, exist_ok=True)
        inbox_copy = inbox_run / name
        inbox_copy.write_bytes(payload)

        manifest_files.append({
            "filename": name,
            "size": len(payload),
            "sha256": sha256,
            "project_id": PROJECT_ID,
            "transfer_id": transfer_id,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "source": f"ask-ai-relay@{commit}:{args.repo_dir}/{name}",
            "destination": str(dest),
            "transfer_copy": str(inbox_copy),
        })
        seen.add(sha256)
        picked.append(name)
        print(f"archived {name} ({len(payload)} bytes) -> {dest}")
        print(f"  sha256 {sha256}")

    manifest_path = archive_run / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({
        "transfer_id": transfer_id,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source_commit": commit,
        "files": manifest_files,
        "duplicates_cleared": duplicates,
    }, indent=2) + "\n", encoding="utf-8")

    if not picked:
        print("nothing was picked up, so nothing is removed from the repo.")
        return 1 if refused else 0

    if args.no_cleanup:
        print(f"archived {len(manifest_files)} file(s); media inbox left as "
              "uploaded (--no-cleanup).")
        return 0

    for name in picked:
        run_git(repo, "rm", "--quiet", f"{args.repo_dir}/{name}")
    run_git(repo, "commit", "-m",
            f"Pick up {len(picked)} photo(s) from the notes media inbox ({transfer_id})")
    run_git(repo, "push")
    print(f"media inbox cleared and pushed (commit "
          f"{run_git(repo, 'rev-parse', '--short', 'HEAD').decode().strip()}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
