"""The remote player's link is never written, published or copied before the tunnel is PROVED to reach this table (retro M3, 2026-11-14).

    python3 launch/test_tunnels.py            (no network, no cloudflared: a stand-in tunnel process and a stand-in edge, free ports)

The bug, twice (2026-11-09, 2026-11-13): after a relaunch the remote player's link pointed at the OLD, dead tunnel address. The old
launcher kept one log per port, emptied it while the old tunnel process was still writing into it, and the watcher took the last
address named anywhere in that file. Nothing asked the address whether it worked.

  1. the old behaviour, reproduced: a shared log, the old process still writing after the new one's banner, "the last address in the
     file" is the DEAD one. (This is the check that fails on the old code: old_current_host() below is the old watcher's function.)
     host_of() reads the same file right, and `open` never produces such a file in the first place;
  2. one log per tunnel process; `close` waits until the old process is gone; the old process's late lines land in ITS OWN log;
  3. no link without proof: a healthy tunnel -> the link (0600, the key after '#', never on stdout); a dead address -> no link, and the
     stale link file is removed; an address that answers from ANOTHER table -> no link; a table from before /api/instance is proved by
     its game id;
  4. the relaunch that went wrong on the night: a new table, a new tunnel; the link names the new address and only after it is proved;
  5. the watcher is bound to the pid it watches: the process dies -> it says so and stops (it never follows another process); a newer
     launch's tunnel -> it stops; an address that stops answering from this table -> it says so and leaves the link alone;
  6. `check` (asked before a link is published): good while the tunnel reaches this table, not after;
  7. `close --port P` touches only P: a tunnel recorded for another port (the draft server's) is left running;
  8. a REAL table answers GET /api/instance through a tunnel address (a foreign Host name, forwarding headers) with its own word and
     nothing secret, and prove() accepts it.
"""
from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import fake_edge  # noqa: E402
import tunnels  # noqa: E402

FAILS: list[str] = []
PY = sys.executable
TMP = Path(tempfile.mkdtemp(prefix="tunnels-"))
EDGE_DIR = TMP / "edge"
EDGE_DIR.mkdir()
SEEN: list = []
EDGE = fake_edge.serve(EDGE_DIR, seen=SEEN)
STARTED: list[int] = []                                # every pid this test started, so nothing is left behind


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {str(detail)[:500]}"), flush=True)
    if not ok:
        FAILS.append(what)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class StubTable:
    """What a table says about itself, and nothing else: GET /api/instance (or, old=True, only /api/phase with a game id)."""
    def __init__(self, port=None, old=False):
        self.word, self.game, self.old = os.urandom(8).hex(), os.urandom(6).hex(), old
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/api/instance" and not me.old:
                    body = json.dumps({"instance": me.word, "port": me.port}).encode()
                elif path == "/api/phase":
                    body = json.dumps({"game_id": me.game, "player": None}).encode()
                else:
                    self.send_response(404); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
                    return
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body)))
                self.end_headers(); self.wfile.write(body)
        self.srv = ThreadingHTTPServer(("127.0.0.1", port or 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown(); self.srv.server_close()


def env(**extra):
    e = {**os.environ, "NO_CLIPBOARD": "1", "NO_NOTIFY": "1", "PYTHONDONTWRITEBYTECODE": "1",
         "TUNNEL_PROBE_VIA": f"http://127.0.0.1:{EDGE.server_address[1]}", "TUNNEL_PROVE_SECS": "4",
         "TUNNEL_CMD": f"{PY} {HERE / 'fake_tunnel.py'} --port {{port}} --edge-dir {EDGE_DIR} --linger 1.5"}
    e.update(extra)
    return e


def run(*args, **extra):
    r = subprocess.run([PY, str(HERE / "tunnels.py"), *map(str, args)], capture_output=True, text=True, env=env(**extra), timeout=90)
    return r.returncode, r.stdout + r.stderr


def live_dir(seat="Sam", key="KEY-" + "s3cr3t" * 4):
    d = Path(tempfile.mkdtemp(prefix="live-", dir=TMP))
    f = d / f"{seat.lower()}-seat.key"
    f.write_text(key); f.chmod(0o600)
    return d, key


def rec(live, port):
    r = tunnels.read_record(live, port)
    if r and r["pid"] not in STARTED:
        STARTED.append(r["pid"])
    return r


def old_current_host(log: Path):
    """The OLD watcher's function, word for word (tunnel_watch.py before 2026-11-14): the LAST address the log names."""
    found = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com").findall(log.read_text(errors="replace"))
    return found[-1] if found else None


try:
    print("1. The old behaviour, reproduced: one shared log, and the last address in it is the dead one", flush=True)
    shared = TMP / "8800.log"
    shared.write_text("")                              # the old launcher: `: > "$TL"` while the old tunnel process still had it open
    shared.write_text(
        "2026-11-14T09:10:29Z INF Unregistered tunnel connection connIndex=0 event=0 ip=192.0.2.9\n"                        # the OLD process, after the truncate
        "2026-11-14T09:10:30Z INF Requesting new quick Tunnel on trycloudflare.com...\n"                                      # the NEW process
        "2026-11-14T09:10:33Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |\n"
        "2026-11-14T09:10:33Z INF |  https://new-live-address.trycloudflare.com                                                |\n"
        "2026-11-14T09:10:34Z INF Registered tunnel connection connIndex=0 event=0 ip=192.0.2.1 location=test protocol=quic\n"
        '2026-11-14T09:10:35Z ERR Request failed error="context canceled" connIndex=0 dest=https://old-dead-address.trycloudflare.com/api/events?since=876&wait=20 type=http\n'   # the OLD process again
        "2026-11-14T09:10:36Z ERR no more connections active and exiting\n")
    check(old_current_host(shared) == "https://old-dead-address.trycloudflare.com",
          "the old watcher's rule (the last address in the log) picks the DEAD address: this is the bug", old_current_host(shared))
    check(tunnels.host_of(shared) == "https://new-live-address.trycloudflare.com", "host_of() reads the creation banner: the live address", tunnels.host_of(shared))
    only_err = TMP / "err.log"
    only_err.write_text('x ERR Request failed dest=https://first-named.trycloudflare.com/a\nx ERR Request failed dest=https://second-named.trycloudflare.com/b\n')
    check(tunnels.host_of(only_err) == "https://first-named.trycloudflare.com", "with no banner it is the FIRST address named, never the last", tunnels.host_of(only_err))
    check(tunnels.host_of(TMP / "missing.log") is None, "no log, no address")

    print("2. One log per tunnel process, and `close` waits for the old one to be gone", flush=True)
    live, key = live_dir()
    table = StubTable()
    P = table.port
    c, out = run("open", "--live", live, "--port", P)
    r1 = rec(live, P)
    h1 = tunnels.host_of(Path(r1["log"]))
    check(c == 0 and r1 and tunnels.pid_alive(r1["pid"]) and h1 and Path(r1["log"]).stat().st_mode & 0o777 == 0o600,
          "open: a tunnel process, its own 0600 log, a record with its pid", (c, out, r1))
    check("fake_tunnel.py" in tunnels.pid_command(r1["pid"]) and "sh -c" not in tunnels.pid_command(r1["pid"]), "the pid recorded is the tunnel's own (no shell in between)")
    c, out = run("open", "--live", live, "--port", P)
    check(c == 0 and rec(live, P)["pid"] == r1["pid"] and "keeping the tunnel" in out, "open again: the tunnel that is up is kept (the same address)", out)
    c, out = run("open", "--live", live, "--port", P, "--fresh")
    r2 = rec(live, P)
    h2 = tunnels.host_of(Path(r2["log"]))
    check(c == 0 and r2["pid"] != r1["pid"] and r2["log"] != r1["log"] and h2 and h2 != h1, "open --fresh: a new process with a NEW log and a new address", (out, r1, r2))
    check(not tunnels.pid_alive(r1["pid"]), "…and the old process was gone before `open` came back (close waits; the stand-in takes 1.5 s to stop)")
    old_log, new_log = Path(r1["log"]).read_text(), Path(r2["log"]).read_text()
    check(old_log.count("ERR Request failed") >= 2 and "Tunnel server stopped" in old_log, "the old process went on writing after it was told to stop (as cloudflared did)…", old_log[-300:])
    check(h1.split("//")[1] not in new_log and "Request failed" not in new_log, "…into its OWN log: the new tunnel's log never names the old address", new_log[-300:])
    check(old_log.strip().splitlines()[-1][:20] <= new_log.strip().splitlines()[0][:20], "the old process's last line is not later than the new one's first")

    print("3. No link without proof", flush=True)
    c, out = run("link", "--live", live, "--port", P, "--seat", "Sam")
    lf = live / "sam-link.txt"
    link = lf.read_text().strip() if lf.exists() else ""
    check(c == 0 and link.startswith(h2 + "/me?player=Sam#key=") and link.endswith(key) and lf.stat().st_mode & 0o777 == 0o600,
          "a tunnel that answers from this table: the link is written (0600, the key after '#')", (c, out))
    check(key not in out and "#key" not in out and "answers from this table" in out, "the key and the link are never printed", out)
    check(("GET", h2.split("//")[1], "/api/instance") in SEEN, "the proof went THROUGH the tunnel's address (the edge saw the request for that Host)", SEEN[-3:])
    # a dead address: the stand-in never lists itself at the edge, so its address answers 530 (Cloudflare 1033)
    live_d, _ = live_dir()
    t_d = StubTable()
    (live_d / "sam-link.txt").write_text("https://stale-address.trycloudflare.com/me?player=Sam#key=OLDKEY\n")
    run("open", "--live", live_d, "--port", t_d.port, TUNNEL_CMD=f"{PY} {HERE / 'fake_tunnel.py'} --port {{port}} --edge-dir {EDGE_DIR} --no-register --linger 0.2")
    rec(live_d, t_d.port)
    c, out = run("link", "--live", live_d, "--port", t_d.port, "--seat", "Sam", TUNNEL_PROVE_SECS="1.5")
    check(c == 4 and "NO LINK for Sam" in out and "answers 530" in out, "a dead address: exit 4, NO LINK, and it says why", (c, out))
    check(not (live_d / "sam-link.txt").exists(), "…nothing is written, and the stale link file that was lying there is removed")
    # an address that answers, but from ANOTHER table
    other = StubTable()
    hd = tunnels.host_of(Path(tunnels.read_record(live_d, t_d.port)["log"]))
    (EDGE_DIR / (hd.split("//")[1] + ".json")).write_text(json.dumps({"port": other.port}))
    c, out = run("link", "--live", live_d, "--port", t_d.port, "--seat", "Sam", TUNNEL_PROVE_SECS="1")
    check(c == 4 and "ANOTHER table" in out and not (live_d / "sam-link.txt").exists(), "an address that leads to another table: NO LINK", (c, out))
    (EDGE_DIR / (hd.split("//")[1] + ".json")).write_text(json.dumps({"port": t_d.port}))
    c, out = run("link", "--live", live_d, "--port", t_d.port, "--seat", "Sam")
    check(c == 0 and (live_d / "sam-link.txt").exists(), "the same address once it leads to this table: the link", (c, out))
    run("close", "--live", live_d, "--port", t_d.port)
    c, out = run("link", "--live", live_d, "--port", t_d.port, "--seat", "Sam")
    check(c == 4 and "no tunnel process is running" in out and not (live_d / "sam-link.txt").exists(), "no tunnel process: NO LINK, and the link file is removed", (c, out))
    # a table from before /api/instance is proved by its game id
    live_o, _ = live_dir()
    t_o = StubTable(old=True)
    run("open", "--live", live_o, "--port", t_o.port); rec(live_o, t_o.port)
    c, out = run("link", "--live", live_o, "--port", t_o.port, "--seat", "Sam")
    check(c == 0 and "game_id matches" in out, "a table from before /api/instance: proved by its game id (GET /api/phase)", (c, out))
    run("close", "--live", live_o, "--port", t_o.port)
    c, out = run("link", "--live", live, "--port", free_port(), "--seat", "Sam")
    check(c == 4, "a port with no table and no tunnel: NO LINK", (c, out))

    print("4. The relaunch that went wrong: a new table on the port, a new tunnel", flush=True)
    table.stop()
    table = StubTable(port=P)                          # the table was restarted: the same port, a new process, a new word
    c, out = run("open", "--live", live, "--port", P, "--fresh")
    r3 = rec(live, P)
    h3 = tunnels.host_of(Path(r3["log"]))
    c, out = run("link", "--live", live, "--port", P, "--seat", "Sam")
    link = (live / "sam-link.txt").read_text().strip()
    check(c == 0 and link.startswith(h3 + "/") and h2 not in link and h1 not in link, "the link names the NEW address, proved against the NEW table", (c, out))
    check(Path(r2["log"]).read_text().count(h2.split("//")[1]) >= 2 and old_current_host(Path(r2["log"])) == h2,
          "(the old tunnel named its own address again while it shut down: in its own log, where nobody reads it for the new one)")
    c, out = run("open", "--live", live, "--port", P)
    c2, out2 = run("link", "--live", live, "--port", P, "--seat", "Sam")
    check(rec(live, P)["pid"] == r3["pid"] and c2 == 0, "a second launch that keeps the tunnel proves it again before the link is written", (out, out2))

    print("5. The watcher is bound to the pid it watches", flush=True)
    wlog = TMP / "watch.log"

    def watch(live_, port_):
        w = subprocess.Popen([PY, "-u", str(HERE / "tunnels.py"), "watch", "--live", str(live_), "--port", str(port_), "--seat", "Sam"],
                             stdout=open(wlog, "w"), stderr=subprocess.STDOUT, env=env(TUNNEL_WATCH_EVERY="0.3"))
        STARTED.append(w.pid)
        return w

    def until(pred, secs=15):
        end = time.time() + secs
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.1)
        return False
    w = watch(live, P)
    check(until(lambda: f"watching tunnel pid {r3['pid']}" in wlog.read_text()), "it says which pid it watches", wlog.read_text())
    before = (live / "sam-link.txt").read_text()
    (EDGE_DIR / (h3.split("//")[1] + ".json")).rename(TMP / "parked.json")          # the address stops answering (the process is still there)
    check(until(lambda: "no longer reaches this table" in wlog.read_text()), "the address stops answering from this table: it says so", wlog.read_text())
    check((live / "sam-link.txt").read_text() == before, "…and does not touch the link")
    (TMP / "parked.json").rename(EDGE_DIR / (h3.split("//")[1] + ".json"))
    check(until(lambda: "answers from this table again" in wlog.read_text()), "it answers again: it says so")
    os.kill(r3["pid"], signal.SIGKILL)                 # the tunnel process dies (this test started it)
    check(until(lambda: w.poll() is not None) and w.returncode == 3 and "is gone" in wlog.read_text() and "link is dead" in wlog.read_text(),
          "the tunnel process dies: the watcher says the link is dead, and STOPS (exit 3)", (w.poll(), wlog.read_text()))
    c, out = run("open", "--live", live, "--port", P)
    r4 = rec(live, P)
    run("link", "--live", live, "--port", P, "--seat", "Sam")
    w = watch(live, P)
    until(lambda: f"watching tunnel pid {r4['pid']}" in wlog.read_text())
    c, out = run("open", "--live", live, "--port", P, "--fresh")                    # a newer launch: its own tunnel
    r5 = rec(live, P)
    check(until(lambda: w.poll() is not None), "a newer launch opens its own tunnel: the old watcher stops (the launcher stops it; it would stop by itself too)", wlog.read_text())
    check(r5["pid"] != r4["pid"] and not tunnels.pid_alive(r4["pid"]), "…and never follows the new process")

    print("6. `check`, asked before a link is published", flush=True)
    run("link", "--live", live, "--port", P, "--seat", "Sam")
    c, out = run("check", "--live", live, "--port", P, "--seat", "Sam")
    check(c == 0 and out.startswith("ok:") and key not in out, "the link on disk is good: exit 0 (and no key in what it prints)", (c, out))
    table.stop()
    table = StubTable(port=P)                          # the table restarted behind the SAME tunnel: the address now reaches the new table
    c, out = run("check", "--live", live, "--port", P, "--seat", "Sam")
    check(c == 0, "a tunnel points at the port: after a table restart the same address reaches the new table", (c, out))
    os.kill(r5["pid"], signal.SIGTERM)
    until(lambda: not tunnels.pid_alive(r5["pid"]))
    c, out = run("check", "--live", live, "--port", P, "--seat", "Sam")
    check(c == 4 and out.startswith("NOT ok:"), "after the tunnel is gone: exit 4 (the launcher then publishes nothing)", (c, out))

    print("7. `close --port P` touches only P", flush=True)
    draft = StubTable()
    run("open", "--live", live, "--port", draft.port); rd = rec(live, draft.port)
    run("open", "--live", live, "--port", P); rp = rec(live, P)
    legacy = live / "tunnels" / f"{P}.pid"
    legacy.write_text(str(os.getpid()))                # an old-layout pidfile naming a process that is NOT a tunnel (this test)
    c, out = run("close", "--live", live, "--port", P)
    check(c == 0 and not tunnels.pid_alive(rp["pid"]) and tunnels.read_record(live, P) is None, "the table's tunnel is closed and its record removed", out)
    check(tunnels.pid_alive(rd["pid"]) and tunnels.read_record(live, draft.port) is not None, "the tunnel recorded for the OTHER port (the draft server's) is left running")
    check("left alone" in out and not legacy.exists(), "an old pidfile that names something that is not a tunnel: left alone, never signalled", out)
    c, out = run("status", "--live", live)
    check(f":{draft.port}" in out and "running" in out, "status lists what is left", out)
    run("close", "--live", live, "--port", draft.port)

    print("8. A real table, through a tunnel address", flush=True)
    sys.path[:0] = [str(REPO / "table"), str(REPO / "table" / "tests")]
    try:
        import cards_path  # noqa: E402, F401
        import match_bo3_test_lib as L  # noqa: E402
    except SystemExit:                                 # no card data on this machine: the first seven parts stand on their own
        L = None
        print("  ⏭️  skipped: the real-table part needs the set's card data (see table/tests/cards_path.py)", flush=True)
    if L is not None:
        t = L.Table(env={"TABLE_HOSTS": ".trycloudflare.com", "TABLE_BEHIND_CLOUDFLARE": "1"})
        try:
            t.claim()
            live_r, _ = live_dir(key=t.keys[L.M], seat=L.M)
            c, out = run("open", "--live", live_r, "--port", t.port); rr = rec(live_r, t.port)
            hr = tunnels.host_of(Path(rr["log"]))
            import urllib.request
            req = urllib.request.Request(f"http://127.0.0.1:{EDGE.server_address[1]}/api/instance", headers={"Host": hr.split("//")[1], "User-Agent": "x"})
            d = json.loads(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10).read())
            local = json.loads(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(f"http://127.0.0.1:{t.port}/api/instance", timeout=10).read())
            check(d.get("instance") == local.get("instance") and len(d["instance"]) == 16 and set(d) == {"instance", "started", "port", "game_id"},
                  "GET /api/instance through a tunnel address (a foreign Host, forwarding headers): the table's own word, and only public fields", d)
            check(t.token not in json.dumps(d) and not any(k in json.dumps(d) for k in t.keys.values()), "…no token and no seat key in it")
            c, out = run("link", "--live", live_r, "--port", t.port, "--seat", L.M)
            check(c == 0 and "instance matches" in out and (live_r / "michael-link.txt").read_text().strip().endswith(t.keys[L.M]),
                  "prove() accepts the real table through its tunnel, and the link carries the seat's key", (c, out))
            page = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                urllib.request.Request(f"http://127.0.0.1:{EDGE.server_address[1]}/me?player=Michael", headers={"Host": hr.split("//")[1]}), timeout=10)
            check(page.status == 200, "the seat page itself is served through that address")
            run("close", "--live", live_r, "--port", t.port)
        finally:
            t.stop()
finally:
    for pid in STARTED:
        try:
            if tunnels.pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    EDGE.shutdown()

print("\nFAILED: " + "; ".join(FAILS) if FAILS else "\nall good")
sys.exit(1 if FAILS else 0)
