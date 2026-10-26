"""End to end, offline: harness/pilot_game.py plays real two-seat games on the real server with a FAKE loopback router
standing in for the Orin. No model, no network beyond 127.0.0.1.

  A. random-legal pilot: the game runs to its turn cap, the model path is exercised, the oracle finds no TABLE bug,
     and no prompt ever contains a card that is only in the OTHER seat's hand (privacy).
  B. a router that returns garbage: the game is flagged (exit 3), the brain stops; nothing plays on with a default.
  C. a router that returns HTTP 500: same.
  D. the STOP file ends a running game quickly.
Takes a few minutes (each game is real time).   python3 table/tests/pilot_e2e_test.py"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness"))
import fake_router  # noqa: E402

ok_all = True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"), flush=True)


def play(mode, port, turns, extra=(), seed=2, stop_file=None, timeout=420):
    srv = fake_router.start(mode, seed)
    run = Path(tempfile.mkdtemp(prefix="pilot-e2e-"))
    stop = stop_file or str(run / "NO-STOP")
    env = {**os.environ, "JETSON_BASE": srv.base}
    t0 = time.time()
    p = subprocess.run([sys.executable, str(REPO / "harness/pilot_game.py"), "--run", str(run), "--port", str(port), "--seed", str(seed),
                        "--turns", str(turns), "--stop-file", stop, *extra], capture_output=True, text=True, env=env, timeout=timeout)
    return p, run, srv, time.time() - t0


def summary(p):
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {}


# A. random legal pilot
p, run, srv, _ = play("random", 8881, 10)
s = summary(p)
check("A: exits 0 (completed, no table finding)", p.returncode == 0, f"rc={p.returncode} {p.stdout[-400:]} {p.stderr[-300:]}")
check("A: ended at the turn cap or by an elimination declared as host", "turn cap" in str(s.get("stopped")) or "game-over" in str(s.get("stopped")), s.get("stopped"))
check("A: the model path was exercised", len(srv.seen) > 0 and s.get("model_calls") == len(srv.seen), f"seen={len(srv.seen)} calls={s.get('model_calls')}")
check("A: every router call carried an enum of legal options and the explicit model name",
      all(r["model"] == "gpt-oss-20b" and r["response_format"]["json_schema"]["strict"] for r in srv.seen))
# privacy: a card only in the OTHER seat's hand must not be in a prompt
games = list((run / "research").glob("*/brain.jsonl"))
brain = [json.loads(l) for l in games[0].read_text().splitlines()] if games else []
decks = {n: {c["name"] for c in json.loads((REPO / f"decks/{n.lower()}.json").read_text())["mainBoard"]} for n in ("Kaust", "Tuvasa")}
leaks = []
for body in srv.seen:
    user = body["messages"][1]["content"]
    me = json.loads(user.split("STATE:\n", 1)[1].split("\nOPTIONS:", 1)[0])["you"]
    other = "Tuvasa" if me == "Kaust" else "Kaust"
    oh = [r["hand_after"] for r in brain if r.get("seat") == other and r.get("hand_after")]
    only = {n for hand in oh[-1:] for n in hand} - decks[me] if oh else set()
    leaks += [n for n in only if n in user]
check("A: no prompt names a card that only the other seat holds", not leaks, leaks[:3])
check("A: no run file is group/world readable", all((f.stat().st_mode & 0o077) == 0 for f in [run / "host-token", run / "server.log"]))

# B/C. a failing router must flag the game, not play on
for mode, port in (("garbage", 8882), ("http500", 8883), ("wrongmodel", 8885)):
    p, run, srv, dt = play(mode, port, 10)
    s = summary(p)
    check(f"{mode}: exit 3 with the pilot named", p.returncode == 3 and s.get("pilot_failed"), f"rc={p.returncode} {s.get('pilot_failed')} {p.stdout[-300:]}")
    check(f"{mode}: the game stopped because a brain exited, well before the cap", "brain exited" in str(s.get("stopped")), s.get("stopped"))

# D. STOP file
stop = Path(tempfile.mkdtemp()) / "STOP"; stop.write_text("")
p, run, srv, dt = play("first", 8884, 30, stop_file=str(stop))
check("D: STOP file ends the game as STOP", "STOP" in str(summary(p).get("stopped")), summary(p).get("stopped"))

left = subprocess.run(["pgrep", "-f", "server.py.*--port 888"], capture_output=True, text=True).stdout.split()
check("no server or brain left running", not left and not subprocess.run(["pgrep", "-f", "fusion_brain.py.*888"], capture_output=True).stdout.strip(), left)
sys.exit(0 if ok_all else 1)
