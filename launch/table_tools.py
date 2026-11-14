#!/usr/bin/env python3
"""Round-launcher helpers, shared by play_round.sh and play_four.sh.

    table_tools.py lock     --live L --launcher NAME --pid PID --ports 8800,8801 --game "A:B C:D" [--force]
    table_tools.py unlock   --live L --pid PID            (end of the launch: the record stays, marked running)
    table_tools.py plan     --ports 8800,8801 --pair A:B --pair C:D [--force] [--restart 8801]
                                                          (which tables to start, which to leave alone, and why: one line each)
    table_tools.py save     --live L --port P             (the finished game's public record + snapshot copy, before a table is stopped)
    table_tools.py claim    --live L --port P --seat A --seat B     (claim the seats; each key into live/<seat>-seat.key, 0600)
    table_tools.py begin    --live L --port P --by SEAT   (response windows off, then the game's first step, with that seat's own key)
    table_tools.py helper   --live L --port P --name autopilot-Claude --pid PID     (write down a helper process started for a table)
    table_tools.py spawn    --live L --port P --name table --log FILE [--cwd DIR] -- CMD …   (start a table's process, detached, pid written down)
    table_tools.py helpers-stop --live L --port P         (stop the processes written down for that table, each by its own pid)
    table_tools.py played-by --live L --port P --seat S --kind autopilot|person|model [--label TEXT] [--own]
                                                          (tell the table who plays a seat: the host's token, read from its 0600 file)
    table_tools.py stand-in --live L --seat S on|off      (mid-match: hand a person's seat to the rules autopilot, or stop it again)
    table_tools.py round    --live L --match bo3 --table 8800:A:B[:bots=B][:remote=A][:kept] ...     (write live/round.json)
    table_tools.py round-wait --live L [--poll 5] [--max-secs N]     (report each game and match; exit 0 when the round is done)
    table_tools.py tunnels-close --live L --ports 8800,8801          (the tunnels to THOSE table ports; never another one)
    table_tools.py seats-page --live L --seat PORT:NAME[:bot] ... [--remote NAME]     (live/seats.html, 0600)

Rules: keys and the host's token are read from their 0600 files and written only into 0600 files (a key after '#' in a URL); never
printed, never in argv. Nothing is signalled except a PID this toolkit wrote down AND that is still the process it says it is. Only
127.0.0.1 is called.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tunnels  # noqa: E402

LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 127.0.0.1 never goes through a proxy


def suffix_of(port: int) -> str:
    """A table's own files are named after its port: :8800 the plain names (token, server.log, saves), :8801 "-b", any other "-<port>"."""
    return {8800: "", 8801: "-b"}.get(int(port), f"-{port}")


def write_private(path: Path, data: str | bytes) -> None:
    """Write a file readable by this user only (0600), replacing it atomically."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data.encode() if isinstance(data, str) else data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def port_up(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def call(port: int, method: str, path: str, body=None, headers=None, timeout: float = 10):
    """(status, json) from the table on 127.0.0.1:port. 0 when it does not answer."""
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with LOCAL.open(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return 0, {}


def get(port: int, path: str, timeout: float = 10):
    with LOCAL.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


pid_alive, pid_command = tunnels.pid_alive, tunnels.pid_command


def game_over(port: int) -> bool | None:
    """True when the game on the port has finished (the table says so, or a life total is at 0); None if it can't tell."""
    try:
        ph = get(port, "/api/phase")
        if ph.get("game_over") or ph.get("winner"):
            return True
        life = get(port, "/api/life")
        return any(isinstance(v, int) and v <= 0 for v in life.values())
    except Exception:  # noqa: BLE001
        return None


def table_state(port: int) -> dict:
    """What the table on a port is doing: {"up", "seats", "over", "score", "started"}. A match counts as over when the MATCH is over
    (between two games of a match the table is very much in use); a single game when the game is."""
    if not port_up(port):
        return {"up": False, "seats": [], "over": None, "score": "", "started": False}
    c, m = call(port, "GET", "/api/match")
    c2, life = call(port, "GET", "/api/life")
    c3, ph = call(port, "GET", "/api/phase")
    seats = list(m.get("seats") or []) if c == 200 and m.get("on") else sorted(life) if c2 == 200 else []
    started = bool(ph.get("player") or ph.get("game_over") or (m.get("games") if c == 200 else None) or ph.get("mulligan"))
    if c == 200 and m.get("on"):
        return {"up": True, "seats": seats, "over": bool(m.get("over")), "score": str(m.get("score") or ""), "started": started, "match": True}
    return {"up": True, "seats": seats, "over": game_over(port), "score": "", "started": started, "match": False}


# ---------------------------------------------------------------- the launch lock

def cmd_lock(a) -> int:
    L = Path(a.live)
    lockf, mutex = L / "launch.lock", L / "launch.lock.launching"
    ports = [int(p) for p in a.ports.split(",") if p]
    holder = {}
    try:
        holder = json.loads(lockf.read_text())
    except (OSError, ValueError):
        pass
    who = (f"{holder.get('launcher')} (pid {holder.get('pid')}, started {holder.get('started')}, "
           f"game {holder.get('game')!r} on {','.join(map(str, holder.get('ports') or []))})") if holder else "nobody on record"
    # 1. another launch in progress: its mutex, held by a live process that is still that launcher
    try:
        fd = os.open(mutex, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            m = json.loads(mutex.read_text())
        except (OSError, ValueError):
            m = {}
        mpid = int(m.get("pid") or 0)
        if mpid and mpid != a.pid and pid_alive(mpid) and str(m.get("launcher") or "") in pid_command(mpid):
            if not a.force:
                print(f"REFUSED: {m.get('launcher')} (pid {mpid}) is launching a game right now (since {m.get('started')}). "
                      f"Wait for it, or pass --force.")
                return 3
            print(f"--force: going ahead while {m.get('launcher')} (pid {mpid}) is still launching")
        mutex.unlink(missing_ok=True)          # stale (its launcher is gone) or forced
        fd = os.open(mutex, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    me = {"launcher": a.launcher, "pid": a.pid, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "ports": ports, "game": a.game,
          "state": "launching"}
    with os.fdopen(fd, "w") as fh:
        json.dump(me, fh)
    # 2. a game still running on a port we would replace (the caller names only the ports it is about to restart)
    busy = [p for p in ports if port_up(p) and not a.replace_ok and table_state(p)["over"] is not True]
    if busy and not a.force:
        mutex.unlink(missing_ok=True)
        print(f"REFUSED: a game is still running on :{', :'.join(map(str, busy))} (launched by {who}). "
              f"Finish it, or pass --force to replace it.")
        return 3
    if busy:
        print(f"--force: replacing the running game on :{', :'.join(map(str, busy))} (launched by {who})")
    write_private(lockf, json.dumps(me))
    return 0


def cmd_unlock(a) -> int:
    L = Path(a.live)
    lockf, mutex = L / "launch.lock", L / "launch.lock.launching"
    try:
        rec = json.loads(lockf.read_text())
        if int(rec.get("pid") or 0) == a.pid:
            rec.update(state="running", launched=time.strftime("%Y-%m-%d %H:%M:%S"))
            write_private(lockf, json.dumps(rec))
    except (OSError, ValueError):
        pass
    try:
        if int(json.loads(mutex.read_text()).get("pid") or 0) == a.pid:
            mutex.unlink()
    except (OSError, ValueError):
        pass
    return 0


# ---------------------------------------------------------------- which tables does this round start?

def plan(ports: list[int], pairs: list[tuple[str, str]], force: bool = False, restart=()) -> list[dict]:
    """One decision per pairing (the n-th pairing plays on the n-th port):
        start    nothing is listening there; or the table there has FINISHED another pairing's match; or --force / --restart names it
        keep     the same two players are there and their match is still being played: never restarted unless asked
        done     the same two players are there and their match is OVER: never replayed unless asked
        refuse   other players are still playing there
    2026-11-13: the launcher restarted both tables at every game, so a match that was already decided was played on
    (Claude and Fusion played a game 3 nobody needed)."""
    out = []
    for port, (a, b) in zip(ports, pairs):
        st = table_state(port)
        same = st["up"] and {s.lower() for s in st["seats"]} == {a.lower(), b.lower()}
        asked = force or port in restart
        if not st["up"]:
            act, why = "start", "nothing is running there"
        elif asked:
            act, why = "start", ("--force" if force else f"--restart {port}") + (": replaces a match still being played" if st["over"] is not True else "")
        elif same and st["over"] is True:
            act, why = "done", f"their match is over ({st['score'] or 'game over'}): not replayed (--restart {port} plays it again)"
        elif same:
            act, why = "keep", f"their match is still being played ({st['score'] or 'no game finished yet'}): left alone (--restart {port} replaces it)"
        elif st["over"] is True:
            act, why = "start", f"the finished table of {' and '.join(st['seats']) or 'an earlier game'} is replaced"
        else:
            act, why = "refuse", f"{' and '.join(st['seats']) or 'another game'} are still playing there (--restart {port} or --force replaces it)"
        out.append({"action": act, "port": port, "a": a, "b": b, "why": why})
    return out


def cmd_plan(a) -> int:
    ports = [int(p) for p in a.ports.split(",") if p]
    pairs = [tuple(p.split(":", 1)) for p in a.pair]
    if len(ports) < len(pairs):
        print(f"REFUSED: {len(pairs)} pairings need {len(pairs)} ports, got {len(ports)}")
        return 1
    rows = plan(ports, pairs, a.force, {int(p) for p in (a.restart or "").split(",") if p})
    for r in rows:                                     # one line per pairing, for the shell: action port A B, then the reason
        print(f"{r['action']} {r['port']} {r['a']} {r['b']} {r['why']}")
    return 3 if any(r["action"] == "refuse" for r in rows) else 0


# ---------------------------------------------------------------- save the finished game before a restart

def cmd_save(a) -> int:
    L, port = Path(a.live), a.port
    if not port_up(port):
        print(f"save :{port}: no table listening, nothing to save")
        return 0
    try:
        phase, life = get(port, "/api/phase"), get(port, "/api/life")
    except Exception as e:  # noqa: BLE001
        print(f"save :{port}: the table did not answer ({e}); nothing saved")
        return 0
    players = "-".join(sorted(str(n) for n in life)) or "unknown"
    stamp = time.strftime("%Y%m%d-%H%M")
    out = L / "games" / f"{stamp}-{port}-{players}"
    n = 1
    while out.exists():                        # two saves in one minute: never overwrite the first
        n += 1
        out = L / "games" / f"{stamp}-{port}-{players}-{n}"
    out.mkdir(parents=True, mode=0o700)
    os.chmod(out, 0o700)
    os.chmod(out.parent, 0o700)
    events, since, last = [], 0, None
    for _ in range(200):                       # 300 per page; the table keeps its last 3000 in memory
        d = get(port, f"/api/events?since={since}")
        page = d.get("events") or []
        last = d.get("last")
        if d.get("restarted") or not page:
            break
        events += page
        since = max(int(e["id"]) for e in page)
        if last is not None and since >= int(last):
            break
    write_private(out / "events.json", json.dumps({"last": last, "events": events}))
    write_private(out / "life.json", json.dumps(life))
    write_private(out / "phase.json", json.dumps(phase))
    notes = [f"{len(events)} events (last id {last})"]
    try:
        c, m = call(port, "GET", "/api/match")
        if c == 200 and m.get("on"):                   # the whole match's public record: scores, each game's winner and turns
            write_private(out / "match.json", json.dumps(m))
            notes.append(f"match ({m.get('score')})")
    except Exception:  # noqa: BLE001
        pass
    try:                                       # the research folder this table writes to is named by its fairness game id
        fair = get(port, "/api/fair")
        write_private(out / "fair.json", json.dumps(fair))
        rdir = L / "research" / str(fair.get("game") or "")
        if fair.get("game") and rdir.is_dir():
            for name in ("snapshot.pkl", "events.jsonl"):   # copied byte for byte, never opened here
                if (rdir / name).exists():
                    shutil.copyfile(rdir / name, out / name)
                    os.chmod(out / name, 0o600)
                    notes.append(name)
        else:
            notes.append("no research folder found: no snapshot copied")
    except Exception as e:  # noqa: BLE001
        notes.append(f"no snapshot ({e})")
    print(f"saved :{port} ({players}) -> {out}: " + ", ".join(notes))
    return 0


# ---------------------------------------------------------------- seats: claim, begin

def key_file(L: Path, seat: str) -> Path:
    return L / f"{seat.lower()}-seat.key"


def cmd_claim(a) -> int:
    L = Path(a.live)
    for seat in a.seat:
        c, d = call(a.port, "POST", "/api/seat/claim", {"name": seat})
        k = d.get("key") or ""
        if c != 200 or not k:
            print(f"no key for {seat} on :{a.port} ({d.get('error') or 'HTTP %s' % c})")
            return 1
        write_private(key_file(L, seat), k)
    return 0


def cmd_begin(a) -> int:
    L = Path(a.live)
    k = key_file(L, a.by).read_text().strip()
    call(a.port, "POST", "/api/phase/windows", {"on": False, "by": "host"})
    c, d = call(a.port, "POST", "/api/phase/next", {"by": a.by, "key": k}, headers={"X-Seat-Key": k})
    print(f"game started on http://127.0.0.1:{a.port} : {d.get('player')} {d.get('step')}" if c == 200
          else f"the game on :{a.port} did not start ({d.get('error') or 'HTTP %s' % c})")
    return 0 if c == 200 else 1


# ---------------------------------------------------------------- helper processes, written down per table

def helpers_file(L: Path, port: int) -> Path:
    d = L / "pids"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d / f"{port}.json"


def helpers_read(L: Path, port: int) -> list:
    try:
        rows = json.loads(helpers_file(L, port).read_text())
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def helper_add(L: Path, port: int, name: str, pid: int, sig: str = "") -> None:
    rows = [r for r in helpers_read(L, port) if r.get("name") != name or pid_alive(int(r.get("pid") or 0))]
    rows.append({"name": name, "pid": pid, "sig": sig or script_of(pid), "at": time.strftime("%H:%M:%S")})
    write_private(helpers_file(L, port), json.dumps(rows))


def script_of(pid: int) -> str:
    """What marks this process as itself: the first *.py word of its command line (claude_autopilot.py), else its first word."""
    words = pid_command(pid).split()
    return next((os.path.basename(w) for w in words if w.endswith(".py")), os.path.basename(words[0]) if words else "")


def cmd_helper(a) -> int:
    helper_add(Path(a.live), a.port, a.name, a.pid)
    return 0


def cmd_spawn(a) -> int:
    """Start a process that belongs to a table (the table itself, a bot) in its own session, its output appended to a log, and write its
    pid down for that table. Its own session: it outlives the launcher and is never caught by a signal meant for the launcher's group.
    The environment is the caller's (a key never travels in argv: the command gets the NAME of a key file through its environment)."""
    L = Path(a.live)
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        print("spawn: no command")
        return 1
    with open(a.log, "a") as fh:
        try:
            p = subprocess.Popen(cmd, cwd=a.cwd or None, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            print(f"spawn {a.name}: {e}")
            return 1
    time.sleep(0.2)
    helper_add(L, a.port, a.name, p.pid, a.sig or next((os.path.basename(w) for w in cmd if w.endswith((".py", ".sh"))), os.path.basename(cmd[0])))
    return 0


def helpers_stop(L: Path, port: int, only: str = "") -> int:
    """Stop the helpers written down for a table (all of them, or the one called `only`). Returns how many were stopped."""
    n, keep = 0, []
    for r in helpers_read(L, port):
        pid, sig = int(r.get("pid") or 0), str(r.get("sig") or "")
        if only and r.get("name") != only:
            keep.append(r)
            continue
        if pid and pid_alive(pid) and sig and sig in pid_command(pid):
            os.kill(pid, signal.SIGTERM)
            n += 1
    write_private(helpers_file(L, port), json.dumps(keep))
    return n


def cmd_helpers_stop(a) -> int:
    n = helpers_stop(Path(a.live), a.port)
    if n:
        print(f"stopped {n} helper process{'es' if n != 1 else ''} of the table on :{a.port}")
    return 0


# ---------------------------------------------------------------- who plays a seat; the stand-in

def token_of(L: Path, port: int) -> str:
    """The host's token of the table on a port: the file live/round.json names for it, else the n-th table's usual file."""
    for t in round_read(L).get("tables") or []:
        if int(t.get("port") or 0) == port and t.get("token_file"):
            return (L / t["token_file"]).read_text().strip()
    return (L / f"token{suffix_of(port)}").read_text().strip()


def set_played_by(L: Path, port: int, seat: str, kind: str, label: str = "", stand_in: bool = True, token_file: str = "") -> tuple[int, dict]:
    tok = (Path(token_file).read_text().strip() if token_file else token_of(L, port))
    return call(port, "POST", "/api/seat/played-by", {"seat": seat, "kind": kind, "label": label, "stand_in": stand_in}, headers={"X-Brain-Token": tok})


def cmd_played_by(a) -> int:
    c, d = set_played_by(Path(a.live), a.port, a.seat, a.kind, a.label or "", stand_in=not a.own, token_file=a.token_file or "")
    if c in (403, 404) and "pilot seat" not in str(d.get("error")) and "host" not in str(d.get("error")):
        print(f"the table on :{a.port} is too old to record who plays a seat (no /api/seat/played-by): the pages will not say it")
        return 0
    print(f"{a.seat}'s seat on :{a.port}: {(d.get('played_by') or {}).get('label') or d.get('error') or 'HTTP %s' % c}")
    return 0 if c == 200 else 1


def seat_table(L: Path, seat: str) -> dict | None:
    return next((t for t in round_read(L).get("tables") or [] if seat.lower() in [s.lower() for s in t.get("seats") or []]), None)


def start_autopilot(L: Path, port: int, seat: str, grace: str = "5") -> int:
    """The rules autopilot for one seat, as its own process (its own session: it outlives the launcher), with only that seat's key file."""
    bot = Path(os.environ.get("BOT_DIR") or HERE.parent / "bots") / "claude_autopilot.py"
    log = L / ("autopilot.log" if seat == "Claude" else f"{seat.lower()}-pilot.log")
    env = {**os.environ, "TABLE_URL": f"http://127.0.0.1:{port}", "CLAUDE_SEAT": seat, "CLAUDE_KEY_FILE": str(key_file(L, seat)),
           "AUTOPILOT_GRACE": os.environ.get("AUTOPILOT_GRACE", grace), "PYTHONDONTWRITEBYTECODE": "1"}
    with open(log, "a") as fh:
        fh.write(f"=== autopilot for {seat} on :{port} {time.strftime('%H:%M')} ===\n")
        fh.flush()
        p = subprocess.Popen([os.environ.get("BOT_PY") or sys.executable, "-u", str(bot)], env=env, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                             start_new_session=True)
    helper_add(L, port, f"autopilot-{seat}", p.pid, "claude_autopilot.py")
    return p.pid


def cmd_autopilot(a) -> int:
    pid = start_autopilot(Path(a.live), a.port, a.seat)
    print(f"autopilot for {a.seat} on :{a.port}: pid {pid}")
    return 0


def cmd_stand_in(a) -> int:
    """Mid-match: `on` hands a person's seat to the rules autopilot (the table says so on every page and in the log, and the person takes
    the seat back by playing); `off` stops the autopilot and gives the seat back."""
    L = Path(a.live)
    t = seat_table(L, a.seat)
    port = a.port or (int(t["port"]) if t else 0)
    if not port:
        print(f"{a.seat} is at no table of this round (live/round.json): say which with --port")
        return 1
    seat = next((s for s in (t or {}).get("seats") or [] if s.lower() == a.seat.lower()), a.seat)
    name = f"autopilot-{seat}"
    running = [r for r in helpers_read(L, port) if r.get("name") == name and pid_alive(int(r.get("pid") or 0)) and str(r.get("sig") or "") in pid_command(int(r["pid"]))]
    if a.state == "on":
        if not key_file(L, seat).exists():
            print(f"no key file for {seat} ({key_file(L, seat).name}): the launcher writes it when it claims the seats")
            return 1
        c, d = set_played_by(L, port, seat, "autopilot", stand_in=True)
        if c != 200:
            print(f"the table on :{port} refused: {d.get('error') or 'HTTP %s' % c}")
            return 1
        if running:
            print(f"{seat}'s seat is the autopilot's again (pid {running[0]['pid']} was standing by and plays on)")
        else:
            print(f"{seat}'s seat is handed to the rules autopilot (pid {start_autopilot(L, port, seat)}); the table says so on every page. "
                  f"{seat} takes it back by playing from their own page.")
        return 0
    n = helpers_stop(L, port, only=name)
    c, d = set_played_by(L, port, seat, "person")
    print(f"{seat}'s seat is {seat}'s again" + (f" (autopilot stopped)" if n else " (no autopilot was running for it)")
          + ("" if c == 200 else f"; the table did not record it: {d.get('error') or 'HTTP %s' % c}"))
    return 0 if c == 200 else 1


# ---------------------------------------------------------------- the round: what was started, and when it is done

def round_read(L: Path) -> dict:
    try:
        d = json.loads((L / "round.json").read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def cmd_round(a) -> int:
    """--table PORT:A:B[:bots=X+Y][:remote=Z][:token=FILE][:kept]"""
    tables = []
    for spec in a.table:
        port, sa, sb, *opts = spec.split(":")
        t = {"port": int(port), "seats": [sa, sb], "bots": [], "remote": None, "token_file": f"token{suffix_of(int(port))}", "kept": False}
        for o in opts:
            if o == "kept":
                t["kept"] = True
            elif o.startswith("bots="):
                t["bots"] = [x for x in o[5:].split("+") if x]
            elif o.startswith("remote="):
                t["remote"] = o[7:] or None
            elif o.startswith("token="):
                t["token_file"] = o[6:]
        tables.append(t)
    write_private(Path(a.live) / "round.json", json.dumps({"started": time.strftime("%Y-%m-%d %H:%M:%S"), "match": a.match, "tables": tables}, indent=1))
    return 0


def follow(tables: list, poll: float = 5.0, max_secs: float = 0.0, say=print) -> list:
    """Watch the round's tables (GET /api/match and /api/game/summary on 127.0.0.1: public, no key) until every one has finished. Says each
    game's and each match's result once. Returns one result per table: {"port", "seats", "over", "winner", "wins", "score", "draw"}."""
    seen = {int(t["port"]): {"games": 0, "over": False, "down": 0, "said": None, "single": None, "res": None} for t in tables}
    names = {int(t["port"]): " vs ".join(t.get("seats") or []) for t in tables}
    t0 = time.time()
    while True:
        for port, st in seen.items():
            if st["over"]:
                continue
            c, m = call(port, "GET", "/api/match", timeout=4)
            if c != 200:
                st["down"] += 1
                if st["down"] == 6:
                    say(f"table :{port} ({names[port]}) has not answered for a while: is it still running?")
                continue
            st["down"] = 0
            if not m.get("on"):                        # a single game (a four-player table, --single, or an older table)
                if st["single"] is None:
                    st["single"] = True
                    say(f"table :{port} ({names[port]}) plays a single game")
                c2, g = call(port, "GET", "/api/game/summary", timeout=4)
                if c2 == 200 and g.get("over"):
                    st["over"] = True
                    tot = g.get("totals") or {}
                    st["res"] = {"over": True, "winner": g.get("winner"), "wins": {n: int(n == g.get("winner")) for n in tot or (names[port].split(" vs "))},
                                 "score": f"{g.get('winner') or 'nobody'} wins", "draw": not g.get("winner")}
                    say(f"table :{port}: GAME OVER: {g.get('winner') or 'nobody'} wins")
                continue
            for g in (m.get("games") or [])[st["games"]:]:
                res = "a draw" if g.get("draw") else f"{g.get('winner')} wins"
                say(f"table :{port}: game {g.get('n')}: {res} in {g.get('turns')} turns ({', '.join(f'{n} {v}' for n, v in (g.get('totals') or {}).items())})")
            st["games"] = len(m.get("games") or [])
            if m.get("over"):
                st["over"] = True
                st["res"] = {"over": True, "winner": m.get("winner"), "wins": dict(m.get("wins") or {}), "score": m.get("score"), "draw": not m.get("winner"),
                             "games": st["games"]}
                say(f"table :{port}: MATCH OVER: {m.get('score')}")
            elif m.get("next") and m["next"].get("n") != st["said"]:
                st["said"] = m["next"].get("n")
                say(f"table :{port}: {m.get('score')}; game {m['next'].get('n')} starts by itself on the same table")
        if all(st["over"] for st in seen.values()):
            break
        if max_secs and time.time() - t0 > max_secs:
            break
        time.sleep(poll)
    return [{"port": int(t["port"]), "seats": t.get("seats") or [], **(seen[int(t["port"])]["res"] or {"over": False})} for t in tables]


def cmd_round_wait(a) -> int:
    L = Path(a.live)
    rd = round_read(L)
    tables = rd.get("tables") or []
    if a.ports:
        want = {int(p) for p in a.ports.split(",") if p}
        tables = [t for t in tables if int(t["port"]) in want] or [{"port": p, "seats": []} for p in sorted(want)]
    if not tables:
        print("no round on record (live/round.json): nothing to wait for")
        return 1
    say = lambda s: print(s, flush=True)      # noqa: E731
    say("ROUND: " + "; ".join(f":{t['port']} {' vs '.join(t.get('seats') or [])}" for t in tables))
    res = follow(tables, poll=a.poll, max_secs=a.max_secs, say=say)
    write_private(L / "round-result.json", json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "results": res}, indent=1))
    if all(r.get("over") for r in res):
        say("ROUND DONE: " + "; ".join(f"{' vs '.join(r['seats']) or ':' + str(r['port'])}: {r.get('score')}" for r in res))
        return 0
    say("ROUND NOT DONE (gave up waiting): " + "; ".join(f":{r['port']} {'over' if r.get('over') else 'still playing'}" for r in res))
    return 2


# ---------------------------------------------------------------- tunnels to table ports

def cmd_tunnels_close(a) -> int:
    """Only the tunnels to the table ports named. (Before 2026-11-14 this closed every pidfile in live/tunnels/, the draft server's tunnel
    too: the remote player's draft page, and its "Join the match" button, died at every match start.)"""
    L = Path(a.live)
    for p in [int(x) for x in a.ports.split(",") if x]:
        tunnels.close(L, p)
    return 0


# ---------------------------------------------------------------- one local page with every local seat

def seat_url(port: int, name: str, key: str) -> str:
    return f"http://127.0.0.1:{port}/me?player={urllib.parse.quote(name)}#key={urllib.parse.quote(key)}"


def cmd_seats_page(a) -> int:
    L = Path(a.live)
    rows, ports = [], []
    for spec in a.seat:
        port_s, name, *rest = spec.split(":")
        port = int(port_s)
        kf = key_file(L, name)
        if not kf.exists():
            continue
        url = seat_url(port, name, kf.read_text().strip())
        tag = " (autopilot)" if rest and rest[0] == "bot" else ""
        rows.append(f'<p><button onclick="location.href=this.dataset.u" data-u="{html.escape(url, quote=True)}">'
                    f'{html.escape(name)}{tag} &middot; table :{port}</button></p>')
        if port not in ports:
            ports.append(port)
    watch = "".join(f'<a href="http://127.0.0.1:{p}/watch">watch :{p}</a> ' for p in ports)
    remote = (f"<p>{html.escape(a.remote)}'s link: in live/{html.escape(a.remote.lower())}-link.txt (and on the clipboard) once the tunnel "
              f"is proved to reach the table.</p>") if a.remote else ""
    page = ("<!doctype html><meta charset=\"utf-8\"><meta name=\"referrer\" content=\"no-referrer\"><title>Seats</title>"
            "<style>body{font:16px system-ui;margin:2em}button{font-size:1.2em;padding:.6em 1.2em;min-width:18em}</style>"
            f"<h1>This round's seats</h1><p>{time.strftime('%H:%M')} &middot; an older tab holds last round's key: use these.</p>"
            + "".join(rows) + f"<p>{watch}</p>{remote}")
    write_private(L / "seats.html", page)
    print(f"seats: open {L / 'seats.html'}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def P(name, *args):
        p = sub.add_parser(name)
        for flags, kw in args:
            p.add_argument(*flags, **kw)
        return p
    live = (["--live"], {"required": True})
    port = (["--port"], {"type": int, "required": True})
    P("lock", live, (["--launcher"], {"required": True}), (["--pid"], {"type": int, "required": True}), (["--ports"], {"required": True}),
      (["--game"], {"default": ""}), (["--force"], {"action": "store_true"}), (["--replace-ok"], {"action": "store_true"}))
    P("unlock", live, (["--pid"], {"type": int, "required": True}))
    P("plan", (["--ports"], {"required": True}), (["--pair"], {"action": "append", "default": []}), (["--force"], {"action": "store_true"}),
      (["--restart"], {"default": ""}))
    P("save", live, port)
    P("claim", live, port, (["--seat"], {"action": "append", "default": []}))
    P("begin", live, port, (["--by"], {"required": True}))
    P("helper", live, port, (["--name"], {"required": True}), (["--pid"], {"type": int, "required": True}))
    P("spawn", live, port, (["--name"], {"required": True}), (["--log"], {"required": True}), (["--cwd"], {"default": ""}), (["--sig"], {"default": ""}),
      (["command"], {"nargs": argparse.REMAINDER}))
    P("helpers-stop", live, port)
    P("played-by", live, port, (["--seat"], {"required": True}), (["--kind"], {"required": True, "choices": ["person", "autopilot", "model"]}),
      (["--label"], {"default": ""}), (["--own"], {"action": "store_true"}), (["--token-file"], {"default": ""}))
    P("autopilot", live, port, (["--seat"], {"required": True}))
    P("stand-in", live, (["--seat"], {"required": True}), (["--port"], {"type": int, "default": 0}), (["state"], {"choices": ["on", "off"]}))
    P("round", live, (["--match"], {"default": "bo3"}), (["--table"], {"action": "append", "default": []}))
    P("round-wait", live, (["--ports"], {"default": ""}), (["--poll"], {"type": float, "default": 5.0}), (["--max-secs"], {"type": float, "default": 0.0}))
    P("tunnels-close", live, (["--ports"], {"required": True}))
    P("seats-page", live, (["--seat"], {"action": "append", "default": []}), (["--remote"], {"default": ""}))
    a = ap.parse_args(argv)
    return {"lock": cmd_lock, "unlock": cmd_unlock, "plan": cmd_plan, "save": cmd_save, "claim": cmd_claim, "begin": cmd_begin, "helper": cmd_helper, "spawn": cmd_spawn,
            "helpers-stop": cmd_helpers_stop, "played-by": cmd_played_by, "autopilot": cmd_autopilot, "stand-in": cmd_stand_in, "round": cmd_round,
            "round-wait": cmd_round_wait, "tunnels-close": cmd_tunnels_close, "seats-page": cmd_seats_page}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
