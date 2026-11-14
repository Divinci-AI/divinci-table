"""Open the operator's control page in the default browser, with the control token in the URL FRAGMENT (a fragment is never sent to a
server and is not written to any log). The token never goes on a command line either: a private (0600) hand-off page in the state
folder carries it to the browser, and is deleted a few seconds later. Prints nothing secret.

    python3 -m director.ctl [--state-dir ~/.divinci/director] [--port 8930]"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from . import config as cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default=str(Path.home() / ".divinci" / "director"))
    ap.add_argument("--port", type=int, default=8930)
    a = ap.parse_args()
    tok = cfg.read_secret_file(str(Path(a.state_dir).expanduser() / "control.token"), "control token")
    hand = Path(a.state_dir).expanduser() / "open-control.html"
    fd = os.open(hand, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("<!doctype html><script>location.replace(" + json.dumps(f"http://127.0.0.1:{a.port}/control#t={tok}") + ")</script>")
    subprocess.run(["/usr/bin/open", str(hand)], check=False)       # (only a file path is on the command line)
    time.sleep(4)
    hand.unlink(missing_ok=True)
    print("control page opened (keys: 1-9 pin a seat, 0 back to rotation, n next, h HIDE ALL HANDS, u unhide, b blank, c commentary on/off)")


if __name__ == "__main__":
    main()
