#!/usr/bin/env python3
"""Email a notes backup CSV from the PC, with the file as a real attachment.

This is the PC half of the export story. The web app hands you the CSV (Copy /
Download / Share); this script SMTPs it from the machine it runs on, so the
mailbox credentials live here -- in an environment variable or a config file
outside any repository -- and never in the app, its source, or GitWay/GitHub.

Usage:
    python tools/email_backup.py notes-backup-2026-09-30.csv --to you@example.com
    python tools/email_backup.py notes-backup-2026-09-30.csv --dry-run
    python tools/email_backup.py --watch "C:\\Users\\me\\Downloads"

One-shot mode emails the named file and exits. --watch polls a directory for
files named notes-backup-*.csv, emails each one, and moves it to a sent/
subfolder so it is never sent twice; a failed send leaves the file in place and
retries on later cycles (the first three failures are logged).

Configuration, in precedence order (later wins):
  1. %APPDATA%\\notes-email\\config.json   (or ~/.config/notes-email/config.json)
  2. --config path/to/config.json
  3. environment variables: NOTES_SMTP_HOST, NOTES_SMTP_PORT,
     NOTES_SMTP_SECURITY, NOTES_SMTP_USER, NOTES_SMTP_PASS, NOTES_MAIL_FROM,
     NOTES_MAIL_TO

Config file fields (keep this file OUTSIDE every GitWay/GitHub repo):
  {
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_security": "starttls",   // ssl | starttls | none
    "smtp_user": "you@example.com",
    "smtp_pass": "app-password-here",
    "mail_from": "you@example.com",
    "default_to": "recipient@example.com"
  }

The password is never printed, never logged, and never written anywhere by
this script. --list-config reports which fields are set without values.

Exit codes: 0 success (or clean dry-run / no files), 1 failure.
"""
from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from pathlib import Path

ENV_FIELDS = {
    "smtp_host": "NOTES_SMTP_HOST",
    "smtp_port": "NOTES_SMTP_PORT",
    "smtp_security": "NOTES_SMTP_SECURITY",
    "smtp_user": "NOTES_SMTP_USER",
    "smtp_pass": "NOTES_SMTP_PASS",
    "mail_from": "NOTES_MAIL_FROM",
    "default_to": "NOTES_MAIL_TO",
}
SECRET_FIELDS = {"smtp_pass"}
REQUIRED_SEND = ("smtp_host",)


def default_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "notes-email" / "config.json"
    return Path.home() / ".config" / "notes-email" / "config.json"


def load_config(explicit: str | None) -> tuple[dict, Path]:
    """Merge file config with environment overrides. Later sources win."""
    path = Path(explicit) if explicit else default_config_path()
    cfg: dict = {}
    if path.is_file():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise SystemExit(f"config file {path} is unreadable: {error}") from error
        if not isinstance(cfg, dict):
            raise SystemExit(f"config file {path} must contain a JSON object")
    elif explicit:
        raise SystemExit(f"--config {path} does not exist")

    for field, env in ENV_FIELDS.items():
        value = os.environ.get(env)
        if value is not None:
            cfg[field] = value

    if "smtp_port" in cfg:
        try:
            cfg["smtp_port"] = int(cfg["smtp_port"])
        except (TypeError, ValueError):
            raise SystemExit("smtp_port must be a number")
    return cfg, path


def describe_config(cfg: dict, path: Path) -> str:
    lines = [f"config file: {path} ({'found' if path.is_file() else 'not found'})"]
    for field in ENV_FIELDS:
        if field in SECRET_FIELDS:
            lines.append(f"  {field}: {'set (hidden)' if cfg.get(field) else 'missing'}")
        else:
            lines.append(f"  {field}: {cfg.get(field, 'missing')}")
    return "\n".join(lines)


def setup_help(path: Path) -> str:
    return (
        "SMTP is not configured yet. Create this file:\n"
        f"  {path}\n"
        "with the fields listed above (see --list-config for names),\n"
        "or export NOTES_SMTP_HOST / NOTES_SMTP_USER / NOTES_SMTP_PASS /\n"
        "NOTES_MAIL_FROM instead. Keep the file outside every repository --\n"
        "credentials must never reach GitWay/GitHub. For Gmail use an\n"
        "app password, not your account password."
    )


def looks_like_backup(path: Path) -> bool:
    """Refuse to email a file that is not a notes backup unless forced."""
    try:
        with path.open("rb") as handle:
            first = handle.readline(200)
    except OSError:
        return False
    return first.lstrip(b"\xef\xbb\xbf").startswith(b"# notes-backup")


def build_message(path: Path, cfg: dict, to_addr: str, subject: str | None,
                  require_sender: bool = True) -> EmailMessage:
    data = path.read_bytes()
    msg = EmailMessage()
    msg["Subject"] = subject or f"Notes backup {path.stem}"
    sender = cfg.get("mail_from") or cfg.get("smtp_user")
    if not sender:
        if require_sender:
            raise SystemExit("no sender address: set mail_from (or smtp_user) in config/env")
        sender = "(not configured yet)"
    msg["From"] = sender
    msg["To"] = to_addr
    msg.set_content(
        "Notes backup attached as a CSV file.\n\n"
        "Restores through Settings -> Import notes/reminders -> Preview -> "
        "Restore (overwrite all). Your settings on the target device are kept.\n"
    )
    msg.add_attachment(data, maintype="text", subtype="csv", filename=path.name)
    return msg


def send_message(msg: EmailMessage, cfg: dict) -> None:
    host = cfg.get("smtp_host")
    if not host:
        raise SystemExit(f"smtp_host is missing.\n{setup_help(default_config_path())}")
    port = int(cfg.get("smtp_port") or (465 if cfg.get("smtp_security") == "ssl" else 587))
    security = cfg.get("smtp_security") or ("ssl" if port == 465 else "starttls")
    user = cfg.get("smtp_user")
    password = cfg.get("smtp_pass")
    if user and not password:
        raise SystemExit("smtp_user is set but smtp_pass is missing; "
                         "provide it via env/config (never in source)")

    context = ssl.create_default_context()
    if security == "ssl":
        with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as server:
            if user:
                server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as server:
            if security == "starttls":
                server.starttls(context=context)
            if user:
                server.login(user, password)
            server.send_message(msg)


def redacted_error(error: Exception, cfg: dict) -> str:
    """Describe a send failure without leaking credentials."""
    detail = str(error)
    password = cfg.get("smtp_pass")
    if password:
        detail = detail.replace(str(password), "<password>")
    return detail


def one_shot(args: argparse.Namespace, cfg: dict) -> int:
    path = Path(args.csv)
    if not path.is_file():
        print(f"error: {path} does not exist", file=sys.stderr)
        return 1
    if not args.force and not looks_like_backup(path):
        print(f"error: {path} does not start with the '# notes-backup' header, "
              "so it is not a notes backup (use --force to send anyway)",
              file=sys.stderr)
        return 1

    to_addr = args.to or cfg.get("default_to")
    if not to_addr:
        print("error: no recipient -- pass --to or set default_to/NOTES_MAIL_TO",
              file=sys.stderr)
        return 1

    msg = build_message(path, cfg, to_addr, args.subject,
                        require_sender=not args.dry_run)
    size = path.stat().st_size
    if args.dry_run:
        print("[dry-run] message built, nothing sent")
        print(f"  to: {to_addr}")
        # Not msg["From"]: the email package normalises an address-less
        # placeholder to an empty header, which would print as nothing.
        print(f"  from: {cfg.get('mail_from') or cfg.get('smtp_user') or '(not configured yet)'}")
        print(f"  subject: {msg['Subject']}")
        print(f"  attachment: {path.name} ({size} bytes)")
        return 0

    try:
        send_message(msg, cfg)
    except SystemExit:
        raise
    except Exception as error:  # SMTP errors vary by server; report without secrets
        print(f"send failed: {redacted_error(error, cfg)}", file=sys.stderr)
        print(f"config: {describe_config(cfg, default_config_path())}", file=sys.stderr)
        return 1
    print(f"sent {path.name} ({size} bytes) to {to_addr}")
    return 0


def wait_until_stable(path: Path, timeout: float = 10.0) -> None:
    """Don't email a file mid-download: wait until its size stops changing."""
    deadline = time.monotonic() + timeout
    last = -1
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size == last:
            return
        last = size
        time.sleep(1.0)


def unique_dest(folder: Path, name: str) -> Path:
    dest = folder / name
    counter = 1
    while dest.exists():
        dest = folder / f"{Path(name).stem}-{counter}{Path(name).suffix}"
        counter += 1
    return dest


def watch(args: argparse.Namespace, cfg: dict) -> int:
    directory = Path(args.watch)
    if not directory.is_dir():
        print(f"error: {directory} is not a directory", file=sys.stderr)
        return 1
    to_addr = args.to or cfg.get("default_to")
    if not to_addr and not args.dry_run:
        print("error: no recipient -- pass --to or set default_to/NOTES_MAIL_TO",
              file=sys.stderr)
        return 1

    sent_dir = directory / "sent"
    signatures: dict[Path, tuple] = {}
    failures: dict[Path, int] = {}
    print(f"watching {directory} for {args.pattern} every {args.interval}s "
          f"({'dry-run' if args.dry_run else 'will email to ' + str(to_addr)})")
    try:
        while True:
            for path in sorted(directory.glob(args.pattern)):
                try:
                    stat = path.stat()
                    sig = (stat.st_size, stat.st_mtime_ns)
                except OSError:
                    continue
                if signatures.get(path) == sig:
                    continue
                if args.force or looks_like_backup(path):
                    wait_until_stable(path)
                if not args.force and not looks_like_backup(path):
                    print(f"skip {path.name}: not a notes backup header "
                          "(use --force to include)")
                    signatures[path] = sig
                    continue

                if args.dry_run:
                    print(f"[dry-run] would send {path.name} ({sig[0]} bytes) to {to_addr}")
                    signatures[path] = sig
                    failures.pop(path, None)
                    continue

                msg = build_message(path, cfg, to_addr, args.subject)
                try:
                    send_message(msg, cfg)
                except SystemExit as error:
                    print(f"send failed: {error}", file=sys.stderr)
                    signatures[path] = sig
                except Exception as error:
                    attempts = failures.get(path, 0) + 1
                    failures[path] = attempts
                    print(f"send failed for {path.name} "
                          f"(attempt {attempts}): {redacted_error(error, cfg)}",
                          file=sys.stderr)
                    if attempts >= 3:
                        print(f"giving up on {path.name} until it changes; "
                              "fix the config and touch the file to retry",
                              file=sys.stderr)
                        signatures[path] = sig
                    continue

                failures.pop(path, None)
                signatures[path] = sig
                sent_dir.mkdir(exist_ok=True)
                try:
                    path.replace(unique_dest(sent_dir, path.name))
                    print(f"sent {path.name} ({sig[0]} bytes) to {to_addr}; moved to {sent_dir}")
                except OSError as error:
                    print(f"sent, but could not move {path.name} to {sent_dir}: {error}",
                          file=sys.stderr)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="email_backup.py",
        description="Email a notes backup CSV as a real attachment "
                    "(credentials stay on this PC).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Dry-run works without any SMTP configuration.")
    parser.add_argument("csv", nargs="?", help="backup .csv to email (one-shot mode)")
    parser.add_argument("--to", help="recipient address (overrides default_to)")
    parser.add_argument("--subject", help="email subject (default: derived from filename)")
    parser.add_argument("--config", help="path to a config JSON (default: %APPDATA%\\notes-email\\config.json)".replace("%APPDATA%", "%%APPDATA%%"))
    parser.add_argument("--watch", metavar="DIR",
                        help="poll DIR for notes-backup-*.csv, email each, move to DIR/sent/")
    parser.add_argument("--pattern", default="notes-backup-*.csv",
                        help="glob for --watch (default: notes-backup-*.csv)")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="seconds between --watch polls (default: 5)")
    parser.add_argument("--dry-run", action="store_true",
                        help="build the message and print it; send nothing")
    parser.add_argument("--force", action="store_true",
                        help="skip the '# notes-backup' header check")
    parser.add_argument("--list-config", action="store_true",
                        help="show where config is read from and which fields are set "
                             "(values except host/port/security are redacted; passwords never shown)")
    args = parser.parse_args(argv)

    cfg, config_path = load_config(args.config)

    if args.list_config:
        print(describe_config(cfg, config_path))
        return 0

    if args.watch:
        return watch(args, cfg)

    if not args.csv:
        parser.error("give a .csv to email, or use --watch DIR / --list-config")
    return one_shot(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
