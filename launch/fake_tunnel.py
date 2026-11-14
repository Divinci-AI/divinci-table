#!/usr/bin/env python3
"""A stand-in for `cloudflared tunnel --url http://127.0.0.1:PORT`, for tests and rehearsals. It opens NO tunnel and touches no network.

    TUNNEL_CMD="python3 launch/fake_tunnel.py --port {port} --edge-dir <dir>" TUNNEL_PROBE_VIA=http://127.0.0.1:<edge port> …

It writes a log the way cloudflared does (the boxed banner with a made-up https://<words>.trycloudflare.com address, a registration
line), and while it runs its address is listed in --edge-dir, where launch/fake_edge.py (the stand-in for Cloudflare's edge) finds the
port to forward to. On SIGTERM it does what the real one did on 2026-11-13: it says it is shutting down, takes --linger seconds over
it, and in that time still writes lines that NAME ITS OWN ADDRESS (a request error), which is what the old watcher mistook for a new
address. Then its address is gone from the edge (the edge answers 530 for it, as Cloudflare does: error 1033)."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import sys
import time
from pathlib import Path


def stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def say(line: str) -> None:
    print(f"{stamp()} {line}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--edge-dir", default=os.environ.get("FAKE_TUNNEL_EDGE_DIR", ""))
    ap.add_argument("--name", default="", help="the first label of the address (default: random words)")
    ap.add_argument("--linger", type=float, default=float(os.environ.get("FAKE_TUNNEL_LINGER", "1.5")), help="seconds between SIGTERM and exit")
    ap.add_argument("--banner-after", type=float, default=float(os.environ.get("FAKE_TUNNEL_BANNER_AFTER", "0.3")))
    ap.add_argument("--no-register", action="store_true", help="never reachable: the address answers 530 from the start")
    a = ap.parse_args()
    name = a.name or "fake-" + "-".join(secrets.choice(["amber", "birch", "cedar", "delta", "ember", "fjord", "grove", "haven"]) for _ in range(3)) + "-" + secrets.token_hex(2)
    host = f"{name}.trycloudflare.com"
    reg = Path(a.edge_dir) / f"{host}.json" if a.edge_dir else None
    stopping = {"at": 0.0}
    signal.signal(signal.SIGTERM, lambda *_: stopping.update(at=stopping["at"] or time.time()))
    say("INF Thank you for trying the STAND-IN tunnel (launch/fake_tunnel.py): nothing leaves this machine.")
    say("INF Requesting new quick Tunnel on trycloudflare.com...")
    time.sleep(a.banner_after)
    say("INF +--------------------------------------------------------------------------------------------+")
    say("INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |")
    say(f"INF |  https://{host:<88}|")
    say("INF +--------------------------------------------------------------------------------------------+")
    say(f"INF Settings: map[ha-connections:1 no-autoupdate:true protocol:quic url:http://127.0.0.1:{a.port}]")
    if reg and not a.no_register:
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps({"port": a.port, "pid": os.getpid()}))
    say("INF Registered tunnel connection connIndex=0 connection=00000000-0000-0000-0000-000000000000 event=0 ip=192.0.2.1 location=test protocol=quic")
    while not stopping["at"]:
        time.sleep(0.1)
    say("INF Initiating graceful shutdown due to signal terminated ...")
    end = stopping["at"] + a.linger
    while time.time() < end:                           # still writing, and still naming its own address, after it was told to stop
        say(f'ERR Request failed error="context canceled" connIndex=0 dest=https://{host}/api/events?since=1&wait=20 event=0 ip=192.0.2.1 type=http')
        time.sleep(min(0.3, max(0.05, end - time.time())))
    if reg:
        reg.unlink(missing_ok=True)
    say("INF Unregistered tunnel connection connIndex=0 event=0 ip=192.0.2.1")
    say("ERR no more connections active and exiting")
    say("INF Tunnel server stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
