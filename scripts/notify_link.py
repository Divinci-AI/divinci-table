#!/usr/bin/env python3
"""Send one player their seat link, so the host does not paste it into a chat by hand.

    notify_link.py --to Sam --link-file live/sam-link.txt --what "your seat link for round 2"
    notify_link.py --to Sam --link-file live/sam-link.txt --what "..." --dry-run      which channel WOULD be used, and the link's host only

Seen at the table (2026-11-13): the remote player's table link changed at every match start (a new Cloudflare quick tunnel each time)
and the host pasted it into chat by hand four times.

The link carries the seat KEY (after '#'): whoever has it plays that seat. So:
  * the link is read from the FILE (mode 0600, refused otherwise). It is never taken from the command line, never printed, never
    logged, and never put on a command line this script runs (`ps` shows every argv to every user of this laptop).
  * what is printed and logged is only: "sent to <player> via <channel>" and the link's HOST name.
  * contacts: ~/.config/divinci-table/contacts.json, mode 0600 (refused if group- or world-readable):
        {"Sam": {"imessage": "+15555550100", "slack_webhook_file": "/Users/me/.config/divinci-table/sam-webhook.url", "email": "sam@example.org"}}
    The FIRST channel listed for the player that this script can use is the one used.

Channels:
  imessage             `osascript` tells the Messages app to send to that buddy. The AppleScript goes in on stdin and reads the text from a
                       0600 temp file, which is deleted afterwards: the link is not in argv and not in the script text.
  slack_webhook_file   a file (0600) holding one Slack incoming-webhook URL; the text is POSTed to it. An incoming webhook posts to ONE
                       channel and everyone in that channel reads it: use a PRIVATE channel with only that player in it (or a webhook made
                       for a DM-style channel). Never a shared channel: the link is the seat. Slack answers 200 with the body "ok" only
                       when it posted; anything else is a failure here. The webhook URL is a secret too and is never printed.
  email                NOT CONFIGURED: a stub. There are no SMTP credentials on this laptop. A player whose only channel is email gets
                       exit 2 and "email: not configured"; it is skipped when another channel is listed.

Exit codes: 0 sent (or --dry-run found a channel)   1 usage, or a file this script refuses to read   2 no contact / no usable channel for
that player   3 the send failed.   Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONTACTS = Path(os.environ.get("DIVINCI_TABLE_CONTACTS") or Path.home() / ".config" / "divinci-table" / "contacts.json")
OSASCRIPT = os.environ.get("NOTIFY_OSASCRIPT") or "osascript"       # tests point this at a recorder
CHANNELS = ("imessage", "slack_webhook_file", "email")
urlopen = urllib.request.urlopen                                    # tests replace this


class Refused(Exception):
    """A file or an argument this script will not use. Its text never contains a link, a key or a webhook URL."""


def private_text(path: Path, what: str) -> str:
    """The contents of a file that must be the user's own and unreadable by anyone else."""
    try:
        st = path.stat()
    except OSError:
        raise Refused(f"{what} not found: {path}")
    if not stat.S_ISREG(st.st_mode):
        raise Refused(f"{what} is not a regular file: {path}")
    if st.st_mode & 0o077:
        raise Refused(f"{what} is readable by other users (mode {stat.S_IMODE(st.st_mode):04o}): chmod 600 {path}")
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        raise Refused(f"{what} could not be read: {path}")


def read_link(path: Path) -> str:
    link = private_text(path, "the link file").strip()
    u = urllib.parse.urlsplit(link)
    if "\n" in link or len(link) > 2000 or u.scheme not in ("http", "https") or not u.hostname:
        raise Refused(f"the link file does not hold one http(s) link: {path}")
    return link


def link_host(link: str) -> str:
    return urllib.parse.urlsplit(link).hostname or "?"


def pick_channel(entry: dict) -> tuple[str, str] | tuple[None, str]:
    """(channel, its value) for the first usable channel in the order the contacts file lists them, or (None, why)."""
    skipped = []
    for name, value in entry.items():
        if name not in CHANNELS or not isinstance(value, str) or not value.strip():
            continue
        if name == "email":
            skipped.append("email: not configured (a stub: there are no SMTP credentials here)")
            continue
        return name, value.strip()
    return None, "; ".join(skipped) or "no channel listed (imessage, slack_webhook_file)"


def scrub(text, *secrets: str) -> str:
    """Anything a helper or a server said, with every link, seat key and webhook URL we know taken out, cut short. `secrets` may be a link,
    a webhook URL, or the whole message text (every URL in it counts)."""
    out = " ".join(str(text or "").split())
    pieces = set()
    for s in secrets:
        for tok in str(s or "").split():
            if "://" not in tok:
                continue
            u = urllib.parse.urlsplit(tok)
            pieces.update((tok, u.fragment, u.fragment.partition("=")[2], u.path if len(u.path) > 12 else ""))
    for piece in sorted(pieces, key=len, reverse=True):
        if len(piece) >= 6:
            out = out.replace(piece, "<hidden>")
    return out[:160]


def send_imessage(buddy: str, text: str) -> None:
    if not all(ch.isalnum() or ch in "+@._- " for ch in buddy) or len(buddy) > 120:
        raise RuntimeError("the imessage contact has characters this script will not put into AppleScript")
    d = tempfile.mkdtemp(prefix="notify-link-")              # 0700, ours
    f = os.path.join(d, "message.txt")
    try:
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        script = (f'set msgText to (read (POSIX file "{f}") as «class utf8»)\n'
                  'tell application "Messages"\n'
                  '    set svc to 1st account whose service type = iMessage\n'
                  f'    send msgText to participant "{buddy}" of svc\n'
                  'end tell\n')
        r = subprocess.run([OSASCRIPT, "-"], input=script.encode("utf-8"), capture_output=True, timeout=40)   # the script on stdin: argv is "osascript -"
        if r.returncode != 0:
            raise RuntimeError(f"osascript exited {r.returncode}: " + scrub(r.stderr.decode("utf-8", "replace"), text))
    finally:
        for p in (f,):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(d)
        except OSError:
            pass


def send_slack(webhook_file: str, text: str) -> None:
    url = private_text(Path(webhook_file).expanduser(), "the Slack webhook file").strip()
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or not u.hostname or "\n" in url:
        raise Refused("the Slack webhook file does not hold one https URL")
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=20) as r:
            status, body = getattr(r, "status", 200), r.read(200).decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Slack answered HTTP {e.code}: " + scrub(e.read(200).decode("utf-8", "replace"), text, url))
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError("Slack could not be reached: " + scrub(getattr(e, "reason", e), text, url))
    if status != 200 or body != "ok":                        # a webhook says "ok" only when it posted; 200 with another body did not
        raise RuntimeError(f"Slack did not post it (HTTP {status}): " + scrub(body, text, url))


def log_line(path: str | None, line: str) -> None:
    if not path:
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass                                                 # a log that cannot be written never stops a send


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Send one player their seat link by the first channel configured for them. The link is read from a 0600 file, never from the command line.")
    ap.add_argument("--to", required=True, help="the player's name, as in the contacts file")
    ap.add_argument("--link-file", required=True, help="a 0600 file holding the link (e.g. live/sam-link.txt)")
    ap.add_argument("--what", default="your seat link", help='what the link is, e.g. "your seat link for round 2"')
    ap.add_argument("--contacts", default=str(CONTACTS), help="the contacts file (default ~/.config/divinci-table/contacts.json; 0600)")
    ap.add_argument("--log-file", default=None, help="also append the one-line result here (0600)")
    ap.add_argument("--dry-run", action="store_true", help="say which channel would be used and the link's host; send nothing")
    a = ap.parse_args(argv)
    what = " ".join(a.what.split())[:120]
    if "://" in what or "#key=" in what:
        print("--what describes the link; the link itself goes in --link-file (never on the command line)", file=sys.stderr)
        return 1
    try:
        link = read_link(Path(a.link_file).expanduser())
        raw = private_text(Path(a.contacts).expanduser(), "the contacts file")
        try:
            contacts = json.loads(raw)
        except ValueError:
            raise Refused(f"the contacts file is not JSON: {a.contacts}")
        if not isinstance(contacts, dict):
            raise Refused(f"the contacts file must be an object of players: {a.contacts}")
    except Refused as e:
        print(f"notify_link: {e}", file=sys.stderr)
        return 1
    host = link_host(link)
    entry = next((v for k, v in contacts.items() if k.lower() == a.to.lower()), None)
    if not isinstance(entry, dict):
        line = f"not sent to {a.to}: no contact for that player (link host {host})"
        print(line)
        log_line(a.log_file, line)
        return 2
    channel, value = pick_channel(entry)
    if channel is None:
        line = f"not sent to {a.to}: {value} (link host {host})"
        print(line)
        log_line(a.log_file, line)
        return 2
    if a.dry_run:
        print(f"dry run: would send to {a.to} via {channel} (link host {host})")
        return 0
    text = f"Divinci table: {what}. It is yours alone (it carries your seat key): do not forward it.\n{link}"
    try:
        if channel == "imessage":
            send_imessage(value, text)
        else:
            send_slack(value, text)
    except Refused as e:
        print(f"notify_link: {e}", file=sys.stderr)
        return 1
    except (RuntimeError, subprocess.SubprocessError, OSError) as e:
        line = f"FAILED to send to {a.to} via {channel} (link host {host}): " + scrub(e, link, text)
        print(line, file=sys.stderr)
        log_line(a.log_file, line)
        return 3
    line = f"sent to {a.to} via {channel} (link host {host})"
    print(line)
    log_line(a.log_file, line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
