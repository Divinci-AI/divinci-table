"""scripts/notify_link.py: a player is sent their seat link without the link ever being on a command line, on screen or in a log.

Seen at the table (2026-11-13): the remote player's link changed at every match start and the host pasted it into chat by hand four times.

No real sends: `osascript` is a recorder this test writes (it notes its argv, its stdin and what the temp file held), and the HTTP opener is
replaced. Everything lives in a temp folder (the contacts file too: DIVINCI_TABLE_CONTACTS / --contacts). The script did not exist before
this change, so on the old tree every check fails at "the script is there".

    python3 table/tests/notify_link_test.py
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "notify_link.py"
FAILS = []
KEY = "SEAT-KEY-do-not-leak-7f3a9c"
LINK = f"https://quiet-river-1234.trycloudflare.com/me?player=Sam#key={KEY}"
HOOK = "https://hooks.slack.com/services/T000TEST/B000TEST/" + "x" * 24      # not a real webhook
SECRETS = (LINK, KEY, HOOK, HOOK.rsplit("/", 1)[1], "quiet-river-1234.trycloudflare.com/me")


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {str(detail)[:300]}"), flush=True)
    if not ok:
        FAILS.append(what)


def clean(*texts):
    """None of the secrets is in any of these texts (the link's HOST alone is allowed: it is what the script is meant to say)."""
    return not any(s in t for t in texts for s in SECRETS)


def write(path: Path, text: str, mode=0o600):
    path.write_text(text)
    path.chmod(mode)
    return path


def main() -> int:
    check(SCRIPT.is_file(), "the script is there")
    if not SCRIPT.is_file():
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="notify-test-"))
    link_file = write(tmp / "sam-link.txt", LINK + "\n")
    hook_file = write(tmp / "sam-webhook.url", HOOK + "\n")
    rec = tmp / "osascript-calls.jsonl"
    fake = tmp / "fake-osascript"
    write(fake, f"""#!{sys.executable}
import json, re, sys
script = sys.stdin.read()
m = re.search(r'POSIX file "([^"]+)"', script)
held = open(m.group(1)).read() if m else ""
open({str(rec)!r}, "a").write(json.dumps({{"argv": sys.argv[1:], "stdin": script, "file": m.group(1) if m else "", "held": held}}) + "\\n")
sys.exit(int(open({str(tmp / 'osascript-rc')!r}).read() or 0))
""", 0o700)
    write(tmp / "osascript-rc", "0")
    contacts = tmp / "contacts.json"
    log = tmp / "notify.log"

    def run(*args, people=None, mode=0o600):
        write(contacts, json.dumps(people if people is not None else {"Sam": {"imessage": "+15555550100", "slack_webhook_file": str(hook_file), "email": "sam@example.org"}}), mode)
        cmd = [sys.executable, str(SCRIPT), "--contacts", str(contacts), "--log-file", str(log), *args]
        assert not any(s in " ".join(cmd) for s in (KEY, HOOK)), "the test itself must not put a secret on a command line"
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env={**os.environ, "NOTIFY_OSASCRIPT": str(fake), "HOME": str(tmp)})
        return r.returncode, r.stdout, r.stderr

    base = ("--to", "Sam", "--link-file", str(link_file), "--what", "your seat link for round 2")

    print("\n── files it refuses")
    for mode, word in ((0o644, "world-readable"), (0o640, "group-readable")):
        rc, out, err = run(*base, mode=mode)
        check(rc == 1 and "readable by other users" in err and not rec.exists(), f"a {word} contacts file: refused, nothing sent", (rc, err))
        check(clean(out, err), "…without saying the link")
    link_file.chmod(0o644)
    rc, out, err = run(*base)
    check(rc == 1 and "chmod 600" in err and not rec.exists(), "a link file other users can read: refused", (rc, err))
    link_file.chmod(0o600)
    rc, out, err = run("--to", "Sam", "--link-file", str(tmp / "nope.txt"))
    check(rc == 1 and not rec.exists(), "no link file: refused", (rc, err))
    rc, out, err = run("--to", "Sam", "--link-file", str(link_file), "--what", "here it is https://x.example/#key=abc")
    check(rc == 1 and not rec.exists(), "a link typed into --what (the command line): refused", (rc, err))
    bad = write(tmp / "bad-link.txt", "not a link at all\n")
    rc, out, err = run("--to", "Sam", "--link-file", str(bad))
    check(rc == 1 and not rec.exists(), "a link file that holds no link: refused", (rc, err))

    print("\n── which channel")
    rc, out, err = run(*base, "--dry-run")
    check(rc == 0 and "via imessage" in out and "quiet-river-1234.trycloudflare.com" in out, "dry run: the first channel listed (imessage), and the link's host", (rc, out, err))
    check(clean(out, err) and not rec.exists() and not log.exists(), "…and nothing sent, nothing logged, no link shown", (out, err))
    rc, out, err = run(*base, "--dry-run", people={"Sam": {"slack_webhook_file": str(hook_file), "imessage": "+15555550100"}})
    check(rc == 0 and "via slack_webhook_file" in out, "the order in the contacts file decides (Slack listed first)", out)
    rc, out, err = run(*base, "--dry-run", people={"Sam": {"email": "sam@example.org", "imessage": "+15555550100"}})
    check(rc == 0 and "via imessage" in out, "email is a stub: skipped when another channel is listed", out)
    rc, out, err = run(*base, people={"Sam": {"email": "sam@example.org"}})
    check(rc == 2 and "email: not configured" in out and not rec.exists(), "email only: exit 2, 'not configured'", (rc, out, err))
    rc, out, err = run(*base, people={"Ann": {"imessage": "+15555550101"}})
    check(rc == 2 and "no contact" in out and not rec.exists(), "no contact for that player: exit 2", (rc, out, err))
    rc, out, err = run("--to", "sam", "--link-file", str(link_file), "--dry-run")
    check(rc == 0, "the player's name matches whatever its case", (rc, out, err))
    check(clean(out, err, log.read_text() if log.exists() else ""), "none of those said the link")

    print("\n── iMessage through osascript")
    rc, out, err = run(*base)
    calls = [json.loads(x) for x in rec.read_text().splitlines()] if rec.exists() else []
    check(rc == 0 and len(calls) == 1, "sent: exit 0, osascript ran once", (rc, out, err, len(calls)))
    if calls:
        c = calls[0]
        check(c["argv"] == ["-"], "osascript's command line is just '-': the script comes on stdin", c["argv"])
        check(clean(" ".join(c["argv"]), c["stdin"]), "neither its command line nor the AppleScript text holds the link or the key")
        check('participant "+15555550100"' in c["stdin"] and "Messages" in c["stdin"], "the AppleScript tells Messages to send to that buddy", c["stdin"][:200])
        check(LINK in c["held"] and "round 2" in c["held"], "the temp file it reads held the text with the link", c["held"][:60])
        check(c["file"] and not os.path.exists(c["file"]) and not os.path.exists(os.path.dirname(c["file"])), "the temp file and its folder are gone afterwards", c["file"])
    check(out.strip() == "sent to Sam via imessage (link host quiet-river-1234.trycloudflare.com)", "stdout is exactly who, how, and the host", out)
    check(clean(out, err, log.read_text()), "stdout, stderr and the log never hold the link, the key or its path")
    check(stat.S_IMODE(log.stat().st_mode) == 0o600 and "sent to Sam via imessage" in log.read_text(), "the log is 0600 and has the one line")
    write(tmp / "osascript-rc", "1")
    rc, out, err = run(*base)
    check(rc == 3 and "FAILED to send to Sam via imessage" in err, "osascript failing: exit 3, said plainly", (rc, out, err))
    check(clean(out, err, log.read_text()), "…and still no link anywhere")
    write(tmp / "osascript-rc", "0")
    rc, out, err = run(*base, people={"Sam": {"imessage": 'x" & (do shell script "id") & "'}})
    check(rc == 3 and len([1 for _ in rec.read_text().splitlines()]) == 2, "a contact that is not a phone number or an address is never put into AppleScript", (rc, err))

    print("\n── Slack incoming webhook (the opener is replaced: nothing leaves this machine)")
    spec = importlib.util.spec_from_file_location("notify_link", SCRIPT)
    N = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(N)
    seen = []

    class Reply:
        def __init__(self, status, body):
            self.status, self._b = status, body

        def read(self, n=-1):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def slack(reply, people=None):
        write(contacts, json.dumps(people or {"Sam": {"slack_webhook_file": str(hook_file)}}))
        seen.clear()
        N.urlopen = lambda req, timeout=0: (seen.append(req), reply)[1]
        o, e = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(o), contextlib.redirect_stderr(e):
            rc = N.main(["--contacts", str(contacts), "--log-file", str(log), *base])
        return rc, o.getvalue(), e.getvalue()

    rc, out, err = slack(Reply(200, b"ok"))
    check(rc == 0 and len(seen) == 1 and seen[0].full_url == HOOK and seen[0].get_method() == "POST", "POSTed once, to the URL in the webhook file", (rc, out, err))
    check(seen and LINK in json.loads(seen[0].data)["text"], "the body carries the link")
    check(out.strip() == "sent to Sam via slack_webhook_file (link host quiet-river-1234.trycloudflare.com)" and clean(out, err, log.read_text()), "what it says: who, how, the host; never the link or the webhook URL", (out, err))
    rc, out, err = slack(Reply(200, b"invalid_token"))
    check(rc == 3 and "did not post" in err, "HTTP 200 with a body that is not 'ok' is a FAILED send (exit 3)", (rc, err))
    rc, out, err = slack(Reply(200, (HOOK + " " + LINK).encode()))
    check(rc == 3 and clean(out, err, log.read_text()), "a reply that echoes the URL and the link back is not repeated", (out, err))
    hook_file.chmod(0o644)
    rc, out, err = slack(Reply(200, b"ok"))
    check(rc == 1 and not seen and "readable by other users" in err, "a webhook file other users can read: refused, nothing posted", (rc, err))
    hook_file.chmod(0o600)
    check(clean(log.read_text()), "after everything, the log holds no link, key or webhook URL", log.read_text()[-300:])

    for p in sorted(tmp.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    tmp.rmdir()
    print("\n" + (f"❌ {len(FAILS)} FAILED: " + "; ".join(FAILS) if FAILS else "all passed"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
