#!/usr/bin/env python3
"""The dogfood loop: play real games on the real table one after another, run the oracle on each, report new findings.

    scripts/table_loop.sh --out DIR [--backend fake|orin] [--hours 8] [--games N] [--seats 2] ...
    touch DIR/STOP        stop after the game now running (the running game is stopped too, within seconds)

Backends
  fake   a random-legal pilot served by harness/fake_router.py on loopback. No Orin, no network: this is a table fuzzer.
  orin   gpt-oss-20b through an ALREADY OPEN ssh -L tunnel (JETSON_BASE). It needs --orin-clear, which means the `orin`
         session said "clear" for THIS run; the loop never opens a tunnel, checks the router's /health before every game,
         and stops the whole loop at the first pilot failure or health failure (no retry loops).

Guards (all stop the loop cleanly; none is a retry)
  STOP file | --hours | --games | disk floor (--disk-floor-gib, default 12) | memory pressure (--min-free-pct) and swap-out
  RATE (--swapouts-per-min), sampled every 10 s during a game | per-game hard timeout | --max-failures consecutive failed
  games (default 3) | a game that leaves a server or port behind.
A game is FAILED when its exit code is 3 (a pilot failed), 4 (no oracle) or anything unexpected, or it times out. Exit code 2
(a table finding) is a GOOD game with a finding: that is the point of the loop.

The report never prints a server log or a hand: it prints signatures, seeds and deck names, which is enough to replay a game
(`pilot_game.py --seed S --decks D`). Run folders of clean games are deleted (they hold private hands); folders of games with a
finding or a failure are kept (mode 700) for a person to read.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
POOL = "kaust,tuvasa,ellivere,elsha,aminatou,inspirit,nghathrod"
OK_CODES = (0, 2)


# ── samplers (replaceable in tests) ──────────────────────────────────────────────────────────────
def disk_free_gib(path: Path) -> float:
    return shutil.disk_usage(path).free / 2 ** 30


def swapouts() -> int | None:
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            if line.startswith("Swapouts"):
                return int(line.split(":")[1].strip().rstrip("."))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None                                           # unmeasurable: ALLOW (a guard that stops the fleet because it cannot measure is the failure mode)


def mem_free_pct() -> float | None:
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if "free percentage" in line:
                return float(line.split(":")[1].strip().rstrip("%"))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


class Guards:
    """check() returns None when it is fine to go on, else a short reason. Samplers are injected so tests need no real pressure."""

    def __init__(self, out: Path, stop_file: Path, disk_floor_gib=12.0, min_free_pct=8.0, swapouts_per_min=30000.0, hours=None,
                 games=None, max_failures=3, sampler=None, clock=time.time):
        self.out, self.stop_file, self.floor, self.min_free, self.swap_rate = out, stop_file, disk_floor_gib, min_free_pct, swapouts_per_min
        self.deadline = clock() + hours * 3600 if hours else None
        self.games, self.max_failures, self.clock = games, max_failures, clock
        self.s = sampler or {"disk": lambda: disk_free_gib(out), "swapouts": swapouts, "free_pct": mem_free_pct}
        self.n_games, self.fails, self._last, self._breaches = 0, 0, None, 0

    def hard(self):
        """Reasons that apply at any moment, including mid-game."""
        if self.stop_file.exists():
            return "STOP file"
        d = self.s["disk"]()
        if d is not None and d < self.floor:
            return f"disk free {d:.1f} GiB is under the {self.floor:g} GiB floor"
        fp = self.s["free_pct"]()
        if fp is not None and fp < self.min_free:
            return f"memory free {fp:.0f}% is under {self.min_free:g}%"
        so, now = self.s["swapouts"](), self.clock()
        if so is not None:
            if self._last is not None and now - self._last[1] >= 20:
                rate = (so - self._last[0]) / (now - self._last[1]) * 60
                self._last = (so, now)
                self._breaches = self._breaches + 1 if rate > self.swap_rate else 0
                if self._breaches >= 2:                   # one noisy sample on a shared laptop is not a reason to stop
                    return f"swapping out {rate:.0f} pages/min twice in a row (limit {self.swap_rate:g})"
            elif self._last is None:
                self._last = (so, now)
        return None

    def check(self):
        r = self.hard()
        if r:
            return r
        if self.deadline and self.clock() >= self.deadline:
            return "hours limit"
        if self.games is not None and self.n_games >= self.games:
            return "games limit"
        if self.fails >= self.max_failures:
            return f"{self.fails} failed games in a row"
        return None


def port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def kill_port(port: int) -> None:
    """Whatever still listens on one of OUR ports after its game ended is a leftover: stop it."""
    try:
        pids = subprocess.run(["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True, timeout=10).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return
    for pid in pids:
        try:
            os.kill(int(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, ValueError):
            pass


def pick_decks(i: int, seats: int, pool: list[str]) -> str:
    """Every combination of `seats` decks from the pool in turn, and on each lap the seating order shifts by one."""
    from itertools import combinations
    combos = list(combinations(pool, seats))
    combo = list(combos[i % len(combos)])
    shift = (i // len(combos)) % seats
    return ",".join(combo[shift:] + combo[:shift])


def read_summary(stdout: str) -> dict:
    """pilot_game prints one JSON object (pretty-printed) last; take the last top-level object."""
    end = stdout.rfind("}")
    while end != -1:
        start = stdout.rfind("\n{", 0, end)
        start = 0 if start == -1 and stdout.lstrip().startswith("{") else start + 1
        try:
            return json.loads(stdout[start:end + 1])
        except ValueError:
            end = stdout.rfind("}", 0, end)
    return {}


def router_health(base: str) -> bool:
    import urllib.request
    try:
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(base.rstrip("/").removesuffix("/v1") + "/health", timeout=5).read()
        return True
    except Exception:                                      # noqa: BLE001
        return False


def run_loop(a, guards: Guards, game_cmd: list[str], env: dict, log=print) -> dict:
    out = Path(a.out)
    results = out / "results.jsonl"
    pool = [x for x in a.pool.split(",") if x]
    seen: dict[str, dict] = {}
    totals, per_deck, durations = Counter(), Counter(), []
    stop_reason = None
    cur_stop = out / "current.STOP"
    i = 0
    while True:
        stop_reason = guards.check()
        if stop_reason:
            break
        if a.backend == "orin" and not router_health(env["JETSON_BASE"]):
            stop_reason = "the router's /health failed (not retrying: tell the orin session)"
            break
        decks = pick_decks(i, a.seats, pool)
        seed, port = a.seed0 + i, a.port0 + (i % 40)
        if port_busy(port):
            stop_reason = f"port {port} is already in use"
            break
        run = out / f"game-{i:04d}"
        cmd = game_cmd + ["--run", str(run), "--port", str(port), "--seed", str(seed), "--decks", decks,
                          "--turns", str(a.turns_per_seat * a.seats), "--max-secs", str(a.max_secs), "--stop-file", str(cur_stop)]
        cur_stop.unlink(missing_ok=True)
        t0 = time.time()
        outfile = out / "current.out"                     # a FILE, not a pipe: a leftover child must not be able to hold the loop on it
        fh = open(outfile, "w")
        p = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=fh, stderr=subprocess.STDOUT, text=True, start_new_session=True)
        halt = threading.Event()
        why: list = []

        def watch():
            while not halt.wait(a.watch_secs):
                r = guards.hard()
                if r:
                    why.append(r); cur_stop.write_text(r); return
        th = threading.Thread(target=watch, daemon=True); th.start()
        try:
            rc, timed_out = p.wait(timeout=a.max_secs + a.kill_grace_secs), False
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                p.kill()
            rc, timed_out = p.wait(), True
        fh.close()
        stdout = outfile.read_text(errors="replace")
        outfile.unlink(missing_ok=True)
        halt.set(); th.join(1)
        if why:
            stop_reason = why[0]
        secs = round(time.time() - t0, 1)
        s = read_summary(stdout or "")
        leftover = port_busy(port)
        if leftover:
            subprocess.run(["pkill", "-f", f"server.py.*--port {port}"], capture_output=True)
            kill_port(port)
        failed = timed_out or rc not in OK_CODES or leftover or not s
        guards.n_games += 1
        guards.fails = guards.fails + 1 if failed else 0
        rec = {"i": i, "seed": seed, "decks": decks, "seats": a.seats, "rc": rc, "secs": secs, "timed_out": timed_out, "leftover_port": leftover,
               "failed": failed, "stopped": s.get("stopped"), "events": s.get("events"), "oracle": s.get("oracle") or {},
               "signatures": s.get("oracle_signatures") or {}, "pilot_failed": s.get("pilot_failed"), "model_calls": s.get("model_calls"),
               "repro": f"python3 harness/pilot_game.py --seed {seed} --decks {decks} --turns {a.turns_per_seat * a.seats}"}
        with open(results, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        durations.append(secs)
        for k, n in rec["oracle"].items():
            totals[k] += n
        for sig in rec["signatures"]:
            if sig not in seen and not sig.startswith("pilot-error"):
                seen[sig] = {"game": i, "seed": seed, "decks": decks}
        for d in decks.split(","):
            per_deck[d] += 1
        keep = a.keep_all or failed or any(k != "pilot-error" for k in rec["oracle"])
        if not keep and run.exists():
            shutil.rmtree(run, ignore_errors=True)       # holds private hands: only a game with something to read is kept
        log(f"game {i}: rc={rc} {secs}s {rec['stopped']} oracle={rec['oracle']}" + (" FAILED" if failed else ""))
        i += 1
        if a.backend == "orin" and (rc == 3 or timed_out):
            stop_reason = "a pilot failed on the Orin (not retrying: tell the orin session)"
            break
        if stop_reason:
            break
    cur_stop.unlink(missing_ok=True)
    done = [json.loads(l) for l in open(results)] if results.exists() else []
    ok = [r for r in done if not r["failed"]]
    rep = {"stopped": stop_reason, "games": len(done), "completed_ok": len(ok), "failed": len(done) - len(ok),
           "completion_rate": round(len(ok) / len(done), 3) if done else None, "finding_totals": dict(totals),
           "new_signatures": seen, "per_deck_games": dict(per_deck),
           "secs_p50": sorted(durations)[len(durations) // 2] if durations else None, "secs_max": max(durations) if durations else None}
    (out / "report.json").write_text(json.dumps(rep, indent=1))
    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True); ap.add_argument("--backend", choices=("fake", "orin"), default="fake")
    ap.add_argument("--orin-clear", action="store_true", help="the orin session said clear for THIS run; the tunnel is already open")
    ap.add_argument("--hours", type=float, default=8.0); ap.add_argument("--games", type=int)
    ap.add_argument("--seats", type=int, default=2); ap.add_argument("--turns-per-seat", type=int, default=10)
    ap.add_argument("--max-secs", type=int, default=600); ap.add_argument("--pool", default=POOL)
    ap.add_argument("--seed0", type=int, default=100); ap.add_argument("--port0", type=int, default=8920)
    ap.add_argument("--disk-floor-gib", type=float, default=12.0); ap.add_argument("--min-free-pct", type=float, default=8.0)
    ap.add_argument("--swapouts-per-min", type=float, default=30000.0); ap.add_argument("--max-failures", type=int, default=3)
    ap.add_argument("--watch-secs", type=float, default=10.0, help="how often the guards are sampled during a game")
    ap.add_argument("--kill-grace-secs", type=float, default=90.0, help="a game gets --max-secs plus this before it is killed")
    ap.add_argument("--keep-all", action="store_true", help="keep every game folder (logs, decisions, snapshots) for research; they hold private hands, never share them")
    ap.add_argument("--fake-mode", default="random", help="fake_router mode: random | first")
    a = ap.parse_args()
    if not 2 <= a.seats <= 6:
        sys.exit("--seats is 2 to 6")
    out = Path(a.out).resolve(); out.mkdir(parents=True, exist_ok=True); os.chmod(out, 0o700); os.umask(0o077)
    stop_file = out / "STOP"
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV")}
    for k in filter(None, os.environ.get("TABLE_LOOP_PASS_ENV", "").split(",")):   # named extras only (tests); never the whole environment
        if k in os.environ:
            env[k] = os.environ[k]
    srv = None
    if a.backend == "fake":
        sys.path.insert(0, str(HERE))
        import fake_router
        srv = fake_router.start(a.fake_mode, a.seed0)
        env["JETSON_BASE"] = srv.base
    else:
        base = os.environ.get("JETSON_BASE", "")
        if not a.orin_clear:
            sys.exit("--backend orin needs --orin-clear: the `orin` session must say clear for this run, and the tunnel must already be open")
        if not base or not base.startswith(("http://127.0.0.1:", "http://localhost:", "http://[::1]:")):
            sys.exit("JETSON_BASE must be the loopback end of the open ssh -L tunnel (http://127.0.0.1:PORT/v1)")
        env["JETSON_BASE"] = base
    guards = Guards(out, stop_file, a.disk_floor_gib, a.min_free_pct, a.swapouts_per_min, a.hours, a.games, a.max_failures)
    first = guards.hard()
    if first:
        sys.exit(f"not starting: {first}")
    cmd = os.environ.get("TABLE_LOOP_GAME_CMD", "").split() or [sys.executable, str(HERE / "pilot_game.py")]
    print(f"table loop: backend={a.backend} seats={a.seats} hours={a.hours} out={out}", flush=True)
    rep = run_loop(a, guards, cmd, env)
    print(json.dumps(rep, indent=1))
    sys.exit(0 if rep["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
