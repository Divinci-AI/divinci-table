#!/usr/bin/env python3
"""The remote player's tunnel: open it, PROVE it reaches this table, and only then hand out a link (retro M3, 2026-11-14).

    tunnels.py open   --live L --port P                 start a tunnel process for the table on :P (or keep the one that is up)
    tunnels.py link   --live L --port P --seat Sam      probe the tunnel; if it answers from THIS table, write Sam's link (0600) + clipboard
    tunnels.py check  --live L --port P --seat Sam      is the link on disk still good? (exit 0 yes, 4 no): asked before a link is published
    tunnels.py watch  --live L --port P --seat Sam      follow THIS tunnel process until it dies; re-prove the link every 20 s
    tunnels.py close  --live L --port P [--port Q …]    stop the tunnel (and its watcher) for those table ports, and wait until it is gone
    tunnels.py status --live L                          what is recorded, and whether each still answers

What went wrong, twice (2026-11-09 and 2026-11-13). The old launcher kept ONE log per port (live/tunnels/<port>.log). At a relaunch it
sent the old tunnel a SIGTERM, did not wait for it to exit, emptied that log and started the new tunnel appending to the same file. The
old process had the file open and went on writing into it for several seconds (2026-11-13: seven seconds of the old tunnel's shutdown
lines sit in the "new" log, after the new tunnel's registration). The watcher took "the LAST trycloudflare address anywhere in the log"
as the tunnel's address: any later line of the dying process that named its own address (cloudflared names it in request errors) became
"the new host". It then rewrote the player's link to the dead address, copied that to the clipboard, and the launcher published it.
Nothing ever asked the address whether it worked.

What this does instead:
  * One log per tunnel PROCESS (live/tunnels/<port>-<stamp>-<nonce>.log, 0600, created new, never emptied, never shared). The record
    live/tunnels/<port>.json (0600) names the pid, its log and when it started.
  * The address is read from that process's own log: the address in its creation banner, else the first one it names. Never the last.
  * `close` waits for the process to be gone before anything else happens (SIGTERM, then SIGKILL after 15 s). Only a pid this file wrote
    down, and only while that pid is still the command it wrote down, is ever signalled.
  * The watcher is bound to the pid in the record. When that process is gone it says so and stops; it never follows another process.
  * NO LINK WITHOUT PROOF. Before a link is written to a file, copied to the clipboard or published, the table's own word
    (GET /api/instance: a random word made when that table process started) is read on 127.0.0.1 and asked for again through the
    tunnel's address. Only an answer with the same word counts. A dead address (Cloudflare 530/1033), a tunnel that leads to another
    table, or one that leads to last game's table all fail, and then no link is written (and a link file already there is removed).
    A table too old to have /api/instance is proved by its game id (GET /api/phase) the same way.

Keys: a seat key is read from its 0600 file and written only into the 0600 link file, after '#'. Never printed, never in argv.
Env: TUNNEL_CMD (the command, with {port}; default `cloudflared tunnel --no-autoupdate --url http://127.0.0.1:{port}`), TUNNEL_HOST_RE
(what an address looks like; default https://<name>.trycloudflare.com), TUNNEL_WATCH_EVERY (20 s), TUNNEL_PROVE_SECS (how long `link`
keeps asking a new tunnel: 60 s), NO_CLIPBOARD=1, NO_NOTIFY=1. For tests only: TUNNEL_PROBE_VIA=http://127.0.0.1:N sends the probe to
a stand-in edge at that address (the Host header still names the tunnel), so no test ever needs a real tunnel.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_CMD = "cloudflared tunnel --no-autoupdate --url http://127.0.0.1:{port}"
HOST_RE = re.compile(os.environ.get("TUNNEL_HOST_RE") or r"https://[a-z0-9-]+\.trycloudflare\.com")
AGENT = "divinci-table-launcher"                       # (Cloudflare refuses Python's default User-Agent with "error code: 1010")
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 127.0.0.1 never goes through a proxy


# ---------------------------------------------------------------- small helpers

def write_private(path: Path, data: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def tdir(live: Path) -> Path:
    d = live / "tunnels"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                               # a child we started and that has exited stays a zombie until it is reaped
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass
    st = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
    return bool(st) and not st.startswith("Z")


def pid_command(pid: int) -> str:
    """The process's command line (ps -o command: argv only, never its environment)."""
    return subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout.strip()


def same_process(pid: int, sig: str) -> bool:
    """Is that pid still the command we wrote down? (A pid is reused by the system sooner or later: never signal one on its number alone.)
    `sig` is what the record holds: the command's arguments ("tunnel --no-autoupdate --url http://127.0.0.1:8800"), which the process's
    command line must contain; for a command with no arguments, its name."""
    return pid_alive(pid) and bool(sig) and sig.lower() in pid_command(pid).lower()


def signature(cmd: list) -> str:
    return " ".join(cmd[1:]) if len(cmd) > 1 else os.path.basename(cmd[0])


def notify(text: str) -> None:
    print(text, flush=True)
    if os.environ.get("NO_NOTIFY") != "1":
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(text)} with title \"Table tunnel\""], check=False, capture_output=True)


# ---------------------------------------------------------------- the record of one tunnel process

def record_path(live: Path, port: int) -> Path:
    return tdir(live) / f"{port}.json"


def read_record(live: Path, port: int) -> dict | None:
    try:
        r = json.loads(record_path(live, port).read_text())
        return r if isinstance(r, dict) and int(r.get("pid") or 0) > 0 else None
    except (OSError, ValueError):
        return None


def host_of(log: Path) -> str | None:
    """The address of the tunnel whose log this is. The creation banner's address if there is one (a line of the box cloudflared draws:
    `INF |  https://name.trycloudflare.com  |`), else the FIRST address the log names. Never the last: later lines that name an address
    (request errors do) are not announcements."""
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return None
    first = None
    for ln in lines:
        m = HOST_RE.search(ln)
        if not m:
            continue
        if "|" in ln and " ERR " not in ln and "dest=" not in ln:
            return m.group(0)
        first = first or m.group(0)
    return first


# ---------------------------------------------------------------- proof: does this address reach THIS table?

def _get_json(url: str, host_header: str | None = None, timeout: float = 10.0, opener=None) -> tuple[int, dict]:
    h = {"User-Agent": AGENT, "Accept": "application/json"}
    if host_header:
        h["Host"] = host_header
    try:
        with (opener or urllib.request.build_opener()).open(urllib.request.Request(url, headers=h), timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return 0, {}


def _curl_json(url: str, timeout: float = 10.0) -> tuple[int, dict]:
    """The same GET with curl, which uses the system's certificate store. For an interpreter that cannot check a certificate (a Python
    installed without its CA bundle fails every https request, whatever the server): the probe must not call a live tunnel dead for that."""
    exe = shutil.which("curl")
    if not exe:
        return 0, {}
    r = subprocess.run([exe, "-s", "-m", str(int(timeout)), "-A", AGENT, "-H", "Accept: application/json", "-w", "\n%{http_code}", url],
                       capture_output=True, text=True)
    body, _, code = r.stdout.rpartition("\n")
    try:
        return int(code or 0), (json.loads(body) if body.strip().startswith("{") else {})
    except ValueError:
        return 0, {}


def table_word(port: int) -> tuple[str, str, str]:
    """(path, field, value): what this table says about itself on 127.0.0.1, to be asked for again through the tunnel."""
    c, d = _get_json(f"http://127.0.0.1:{port}/api/instance", opener=LOCAL, timeout=5)
    if c == 200 and d.get("instance"):
        return "/api/instance", "instance", str(d["instance"])
    c, d = _get_json(f"http://127.0.0.1:{port}/api/phase", opener=LOCAL, timeout=5)     # a table from before /api/instance
    if c == 200 and d.get("game_id"):
        return "/api/phase", "game_id", str(d["game_id"])
    return "", "", ""


def prove(host: str, port: int) -> tuple[bool, str]:
    """Does `host` (https://name.tunnel) answer from the table on 127.0.0.1:port? (True, how) or (False, why not)."""
    path, field, word = table_word(port)
    if not word:
        return False, f"the table on :{port} does not answer on 127.0.0.1"
    via = os.environ.get("TUNNEL_PROBE_VIA")
    if via:                                            # tests: a stand-in edge on loopback; the Host header names the tunnel
        code, d = _get_json(via.rstrip("/") + path, host_header=urllib.parse.urlsplit(host).netloc, opener=LOCAL)
    else:
        code, d = _get_json(host.rstrip("/") + path)
        if code == 0:                                  # no answer at all through this interpreter: ask once more with curl before saying so
            code, d = _curl_json(host.rstrip("/") + path)
    if code != 200:
        return False, f"{host} answers {code or 'nothing'} on {path}" + (" (Cloudflare: that tunnel is gone)" if code in (502, 530) else "")
    if str(d.get(field) or "") != word:
        return False, f"{host} answers, but from ANOTHER table (its {field} is not this table's)"
    return True, f"{host} answers from this table ({field} matches)"


def prove_until(host: str, port: int, secs: float) -> tuple[bool, str]:
    """A new quick tunnel can take a while to be reachable: keep asking for up to `secs`."""
    end = time.time() + secs
    while True:
        ok, how = prove(host, port)
        if ok or time.time() >= end:
            return ok, how
        time.sleep(min(2.0, max(0.2, end - time.time())))


# ---------------------------------------------------------------- links

def link_file(live: Path, seat: str) -> Path:
    return live / f"{seat.lower()}-link.txt"


def make_link(live: Path, seat: str, host: str) -> str:
    key = (live / f"{seat.lower()}-seat.key").read_text().strip()
    return f"{host}/me?player={urllib.parse.quote(seat)}#key={urllib.parse.quote(key)}"


def write_link(live: Path, seat: str, host: str) -> None:
    """Only ever called after prove() said yes for this host."""
    link = make_link(live, seat, host)
    write_private(link_file(live, seat), link + "\n")
    if os.environ.get("NO_CLIPBOARD") != "1":
        subprocess.run(["pbcopy"], input=link.encode(), check=False)    # stdin, never argv


def drop_link(live: Path, seat: str) -> bool:
    f = link_file(live, seat)
    if f.exists():
        f.unlink()
        return True
    return False


# ---------------------------------------------------------------- open / close

def stop_pid(pid: int, sig: str, what: str, wait: float = 15.0) -> bool:
    """SIGTERM a process we started, and WAIT until it is gone (SIGKILL after `wait` s). True when it is gone (or was never ours to stop)."""
    if not same_process(pid, sig):
        return True
    os.kill(pid, signal.SIGTERM)
    end = time.time() + wait
    while time.time() < end:
        if not pid_alive(pid):
            print(f"closed {what} pid {pid}", flush=True)
            return True
        time.sleep(0.2)
    if same_process(pid, sig):
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)
        print(f"{what} pid {pid} did not stop in {wait:g} s: killed", flush=True)
    return not pid_alive(pid)


def stop_watcher(live: Path, port: int) -> None:
    f = tdir(live) / f"{port}.watch.pid"
    try:
        pid = int(f.read_text().strip() or 0)
    except (OSError, ValueError):
        pid = 0
    if pid and pid != os.getpid() and pid_alive(pid) and "tunnels.py" in pid_command(pid) and " watch" in pid_command(pid):
        stop_pid(pid, "tunnels.py watch", f"the tunnel watcher for :{port}", wait=5)
    f.unlink(missing_ok=True)


def close(live: Path, port: int) -> bool:
    """Stop the watcher and the tunnel for one table port, and wait until the tunnel process is gone. Also the old layout's <port>.pid."""
    stop_watcher(live, port)
    ok = True
    rec = read_record(live, port)
    if rec:
        ok = stop_pid(int(rec["pid"]), str(rec.get("sig") or ""), f"the tunnel to :{port}")
        record_path(live, port).unlink(missing_ok=True)
    legacy = tdir(live) / f"{port}.pid"                # written by the launchers before 2026-11-14
    if legacy.exists():
        try:
            pid = int(legacy.read_text().strip() or 0)
        except (OSError, ValueError):
            pid = 0
        if pid and pid_alive(pid):
            cmdline = pid_command(pid)
            if os.path.basename(cmdline.split(" ", 1)[0]) == "cloudflared" and " tunnel" in cmdline:
                ok = stop_pid(pid, " tunnel", f"the old tunnel to :{port}") and ok
            else:
                print(f"{legacy.name}: pid {pid} is not a cloudflared process; left alone", flush=True)
        legacy.unlink(missing_ok=True)
    return ok


def open_tunnel(live: Path, port: int, reuse: bool = True, wait: float = 40.0) -> tuple[dict | None, str | None]:
    """(record, host) for a tunnel process to the table on :port. A tunnel that is still up is kept when `reuse` (it points at the port, so
    it outlives a table restart and the remote player keeps the same address); otherwise it is closed, and its exit waited for, first."""
    rec = read_record(live, port)
    if rec and reuse and same_process(int(rec["pid"]), str(rec.get("sig") or "")):
        host = host_of(Path(rec["log"]))
        if host:
            print(f"keeping the tunnel to :{port} (pid {rec['pid']}): the same address as before", flush=True)
            return rec, host
    close(live, port)
    cmd = shlex.split((os.environ.get("TUNNEL_CMD") or DEFAULT_CMD).replace("{port}", str(port)))
    log = tdir(live) / f"{port}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.log"
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)   # a NEW file: no other process has ever had it open
    try:
        # its own session: it outlives this launcher, and the pid recorded is the tunnel's own (no shell in between)
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd, start_new_session=True)
    except OSError as e:
        os.close(fd)
        print(f"could not start the tunnel ({cmd[0]}): {e}", flush=True)
        return None, None
    os.close(fd)
    rec = {"port": port, "pid": proc.pid, "sig": signature(cmd), "log": str(log), "started": round(time.time(), 2)}
    write_private(record_path(live, port), json.dumps(rec))
    end = time.time() + wait
    host = None
    while time.time() < end:
        host = host_of(log)
        if host or proc.poll() is not None:
            break
        time.sleep(0.25)
    if not host:
        print(f"the tunnel to :{port} (pid {proc.pid}) has not named an address" + (" and has exited" if proc.poll() is not None else "") + f": see {log}", flush=True)
    return rec, host


# ---------------------------------------------------------------- commands

def cmd_open(a) -> int:
    rec, host = open_tunnel(Path(a.live), a.port, reuse=not a.fresh, wait=a.wait)
    if not rec or not host:
        return 3
    print(f"tunnel to :{a.port}: pid {rec['pid']}, {host} (not yet proved: `link` does that)", flush=True)
    return 0


def cmd_link(a) -> int:
    live = Path(a.live)
    rec = read_record(live, a.port)
    if not rec or not same_process(int(rec["pid"]), str(rec.get("sig") or "")):
        gone = drop_link(live, a.seat)
        print(f"NO LINK for {a.seat}: no tunnel process is running for :{a.port}" + (" (the old link file was removed)" if gone else ""), flush=True)
        return 4
    host = host_of(Path(rec["log"]))
    ok, how = prove_until(host, a.port, a.prove_secs) if host else (False, "the tunnel has not named an address")
    if not ok:
        gone = drop_link(live, a.seat)
        print(f"NO LINK for {a.seat}: {how}. Nothing was written or copied" + (" (the old link file was removed)" if gone else "") + ".", flush=True)
        return 4
    write_link(live, a.seat, host)
    clip = "" if os.environ.get("NO_CLIPBOARD") == "1" else " and on the clipboard"
    print(f"{a.seat}'s table tunnel: {how}. The link is in {link_file(live, a.seat)}{clip}.", flush=True)
    return 0


def cmd_check(a) -> int:
    """Is the link on disk still good? Its address must be this port's recorded tunnel's, and must answer from this table NOW."""
    live = Path(a.live)
    try:
        link = link_file(live, a.seat).read_text().strip()
    except OSError:
        print(f"no link file for {a.seat}", flush=True)
        return 4
    u = urllib.parse.urlsplit(link)
    host = f"{u.scheme}://{u.netloc}"
    ok, how = prove(host, a.port)
    print(("ok: " if ok else "NOT ok: ") + how, flush=True)       # (the address only: never the link, which carries the key)
    return 0 if ok else 4


def cmd_watch(a) -> int:
    live = Path(a.live)
    rec = read_record(live, a.port)
    if not rec:
        print(f"no tunnel recorded for :{a.port}: nothing to watch", flush=True)
        return 3
    pid, sig, log = int(rec["pid"]), str(rec.get("sig") or ""), Path(rec["log"])
    every = float(os.environ.get("TUNNEL_WATCH_EVERY", "20"))
    write_private(tdir(live) / f"{a.port}.watch.pid", str(os.getpid()))
    host = host_of(log)
    print(f"watching tunnel pid {pid} to :{a.port} for {a.seat} every {every:g}s (address {host or 'not named yet'}, log {log.name})", flush=True)
    bad = None
    while True:
        time.sleep(every)
        cur = read_record(live, a.port)
        if not cur or int(cur.get("pid") or 0) != pid:             # a newer launch opened its own tunnel (and its own watcher): this one is done
            print(f"tunnel pid {pid} is no longer the tunnel for :{a.port}: this watcher stops", flush=True)
            return 0
        if not same_process(pid, sig):
            notify(f"{a.seat}'s tunnel process (pid {pid}) is gone: {a.seat}'s link is dead. Open a new one: tunnels.py open, then link.")
            return 3
        h = host_of(log)
        if h and h != host:                                        # this very process announced a new address
            ok, how = prove(h, a.port)
            if ok:
                write_link(live, a.seat, h)
                notify(f"{a.seat}'s link changed: the tunnel's address is now {h}, and it answers from this table. "
                       f"The new link is in {link_file(live, a.seat).name}" + ("" if os.environ.get("NO_CLIPBOARD") == "1" else " and on the clipboard") + ".")
                host, bad = h, None
            elif bad != ("new", h):
                notify(f"the tunnel names a new address for {a.seat} ({h}) but it is not proved: {how}. The link was NOT changed.")
                bad = ("new", h)
            continue
        if host:
            ok, how = prove(host, a.port)
            if not ok and bad != how:
                notify(f"{a.seat}'s tunnel no longer reaches this table: {how}. The link in {link_file(live, a.seat).name} is dead until the tunnel is reopened.")
                bad = how
            elif ok and bad:
                print(f"{a.seat}'s tunnel answers from this table again", flush=True)
                bad = None


def cmd_close(a) -> int:
    ok = True
    for p in a.port:
        ok = close(Path(a.live), p) and ok
    return 0 if ok else 3


def cmd_status(a) -> int:
    live = Path(a.live)
    recs = sorted(tdir(live).glob("*.json"))
    if not recs:
        print("no tunnel recorded")
    for f in recs:
        try:
            r = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        port, pid = int(r.get("port") or 0), int(r.get("pid") or 0)
        up = same_process(pid, str(r.get("sig") or ""))
        host = host_of(Path(r.get("log") or ""))
        ok, how = prove(host, port) if up and host else (False, "not running" if not up else "no address yet")
        print(f":{port} pid {pid} {'running' if up else 'GONE'} {host or '-'}: {how}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("open", "link", "check", "watch"):
        p = sub.add_parser(name)
        p.add_argument("--live", required=True)
        p.add_argument("--port", type=int, required=True)
        if name == "open":
            p.add_argument("--fresh", action="store_true", help="close a tunnel that is still up and open a new one (a new address)")
            p.add_argument("--wait", type=float, default=40.0, help="seconds to wait for the tunnel to name its address")
        else:
            p.add_argument("--seat", required=True)
        if name == "link":
            p.add_argument("--prove-secs", type=float, default=float(os.environ.get("TUNNEL_PROVE_SECS", "60")))
    p = sub.add_parser("close")
    p.add_argument("--live", required=True)
    p.add_argument("--port", type=int, action="append", required=True)
    p = sub.add_parser("status")
    p.add_argument("--live", required=True)
    a = ap.parse_args(argv)
    return {"open": cmd_open, "link": cmd_link, "check": cmd_check, "watch": cmd_watch, "close": cmd_close, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
