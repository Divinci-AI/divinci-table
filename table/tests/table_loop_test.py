"""harness/table_loop.py: every guard stops the loop cleanly, a bad game is never retried, and nothing is left behind.
Uses a stand-in "game" script (no table server, no model): it takes pilot_game's arguments and behaves as scripted.
    python3 table/tests/table_loop_test.py"""
import json, os, signal, socket, subprocess, sys, tempfile, threading, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "harness"))
import table_loop as L  # noqa: E402

ok_all = True


def check(name, ok, detail=""):
    global ok_all
    ok_all &= bool(ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"), flush=True)


# ── 1. Guards, with injected samplers and clock ──────────────────────────────────────────────────
tmp = Path(tempfile.mkdtemp())
now = [1000.0]
vals = {"disk": 50.0, "free_pct": 60.0, "swapouts": 0}
g = L.Guards(tmp, tmp / "STOP", disk_floor_gib=12, min_free_pct=8, swapouts_per_min=30000, hours=1, games=3, max_failures=3,
             sampler={"disk": lambda: vals["disk"], "free_pct": lambda: vals["free_pct"], "swapouts": lambda: vals["swapouts"]},
             clock=lambda: now[0])
check("nothing wrong: no reason", g.check() is None)
vals["disk"] = 11.9; check("disk under the floor stops", "floor" in (g.check() or ""), g.check()); vals["disk"] = 50
vals["free_pct"] = 5; check("low free memory stops", "memory" in (g.check() or "")); vals["free_pct"] = 60
now[0] += 30; vals["swapouts"] = 5000; check("a modest swap-out rate is fine", g.check() is None, g.check())
now[0] += 30; vals["swapouts"] = 5000 + 60000; check("one fast swap-out sample alone does not stop (shared laptop, noisy neighbours)", g.check() is None)
now[0] += 30; vals["swapouts"] = 5000 + 120000; check("a fast swap-out rate twice in a row stops", "swapping" in (g.check() or ""), g.check())
now[0] += 30; vals["swapouts"] = 5000 + 120000; g.check(); vals["swapouts"] = 5000 + 120000
vals["swapouts"] = None; now[0] += 30; check("an unmeasurable sampler ALLOWS (a guard that cannot measure must not ground the loop)", g.check() is None)
(tmp / "STOP").write_text(""); check("the STOP file stops", g.check() == "STOP file"); (tmp / "STOP").unlink()
g.n_games = 3; check("the games limit stops", g.check() == "games limit"); g.n_games = 0
g.fails = 3; check("three failures in a row stop", "3 failed" in (g.check() or "")); g.fails = 0
now[0] += 3600; check("the hours limit stops", g.check() == "hours limit")

# ── 2. refuse to start under the disk floor (nothing is filled: the floor is raised above free space) ──
p = subprocess.run([sys.executable, str(REPO / "harness/table_loop.py"), "--out", str(tmp / "o1"), "--disk-floor-gib", "100000000"], capture_output=True, text=True)
check("a disk floor above free space refuses to start", p.returncode != 0 and "not starting: disk free" in (p.stdout + p.stderr), p.stderr[-200:])
p = subprocess.run([sys.executable, str(REPO / "harness/table_loop.py"), "--out", str(tmp / "o2"), "--backend", "orin"], capture_output=True, text=True)
check("--backend orin refuses without --orin-clear", p.returncode != 0 and "--orin-clear" in (p.stdout + p.stderr))
p = subprocess.run([sys.executable, str(REPO / "harness/table_loop.py"), "--out", str(tmp / "o3"), "--backend", "orin", "--orin-clear"], capture_output=True, text=True,
                   env={**os.environ, "JETSON_BASE": "http://example.com/v1"})
check("...and refuses a JETSON_BASE that is not loopback", p.returncode != 0 and "loopback" in (p.stdout + p.stderr))

# ── 3. the loop with a scripted stand-in game ───────────────────────────────────────────────────
FAKE = tmp / "fakegame.py"
FAKE.write_text('''
import json, os, signal, sys, time, subprocess
from pathlib import Path
a = dict(zip(sys.argv[1::2], sys.argv[2::2]))
run = Path(a["--run"]); run.mkdir(parents=True, exist_ok=True); (run / "brain.jsonl").write_text("private")
plan = json.loads(Path(os.environ["PLAN"]).read_text()); n = int(run.name.split("-")[1]); what = plan[min(n, len(plan) - 1)]
(Path(os.environ["PLAN"]).parent / f"seen-{n}").write_text(json.dumps(a))
def summary(rc, stopped, oracle=None, sigs=None):
    print(json.dumps({"stopped": stopped, "oracle": oracle or {}, "oracle_signatures": sigs or {}, "events": 10})); sys.exit(rc)
if what == "ok": summary(0, "turn cap")
if what == "finding": summary(2, "turn cap", {"leak": 1}, {"leak|say|x": 1})
if what == "fail": summary(3, "a brain exited")
if what == "fail-router-down":
    os.kill(int(os.environ["ROUTER_PID"]), signal.SIGKILL); time.sleep(0.5); summary(3, "a brain exited")
if what == "hang": time.sleep(600)
if what == "wait-stop":
    while not Path(a["--stop-file"]).exists(): time.sleep(0.2)
    summary(0, "STOP file")
if what == "leak-port":
    subprocess.Popen([sys.executable, "-c", "import socket,time;s=socket.socket();s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind(('127.0.0.1',%s));s.listen();time.sleep(60)" % a["--port"]], start_new_session=True)
    time.sleep(1); summary(0, "turn cap")
''')


def loop(plan, extra=(), env_extra=None, timeout=120):
    out = Path(tempfile.mkdtemp(dir=tmp))
    (out / "plan.json").write_text(json.dumps(plan))
    env = {**os.environ, "TABLE_LOOP_GAME_CMD": f"{sys.executable} {FAKE}", "PLAN": str(out / "plan.json"), "TABLE_LOOP_PASS_ENV": "PLAN", **(env_extra or {})}
    p = subprocess.run([sys.executable, str(REPO / "harness/table_loop.py"), "--out", str(out), "--watch-secs", "0.3", "--kill-grace-secs", "1",
                        "--max-secs", "2", "--disk-floor-gib", "0", "--min-free-pct", "0", "--port0", "8950", *extra], capture_output=True, text=True, env=env, timeout=timeout)
    rep = json.loads((out / "report.json").read_text()) if (out / "report.json").exists() else {}
    rows = [json.loads(l) for l in (out / "results.jsonl").read_text().splitlines()] if (out / "results.jsonl").exists() else []
    return p, out, rep, rows


p, out, rep, rows = loop(["ok", "finding", "ok"], ["--games", "3"])
check("3 scripted games run and the loop ends on the games limit", rep.get("games") == 3 and rep.get("stopped") == "games limit", rep)
check("a clean game's folder (private hands) is deleted; the game with a finding is kept", not (out / "game-0000").exists() and (out / "game-0001").exists() and not (out / "game-0002").exists())
check("the new signature is reported with its seed and decks, and rc 2 is a GOOD game", "leak|say|x" in rep.get("new_signatures", {}) and rep.get("failed") == 0, rep)
check("the report carries a repro command for each game", all(r["repro"].startswith("python3 harness/pilot_game.py --seed") for r in rows))
check("decks rotate across games", len({r["decks"] for r in rows}) == 3, [r["decks"] for r in rows])
check("the results folder is mode 700", (out.stat().st_mode & 0o077) == 0)

p, out, rep, rows = loop(["fail", "fail", "fail", "ok", "ok"], ["--games", "10"])
check("three failed games in a row stop the loop, with no fourth game", rep.get("games") == 3 and "3 failed" in rep.get("stopped", ""), rep)
p, out, rep, rows = loop(["fail", "fail", "ok", "fail", "fail", "ok"], ["--games", "6"])
check("a good game resets the failure count", rep.get("games") == 6, rep.get("games"))
p, out, rep, rows = loop(["fail"] * 5, ["--games", "5", "--max-failures", "99"])
check("mutation: with the failure cap raised the loop does NOT stop at 3", rep.get("games") == 5, rep.get("games"))

p, out, rep, rows = loop(["hang", "ok"], ["--games", "2"], timeout=60)
check("a game past max-secs plus grace is killed and counted failed", rows and rows[0]["timed_out"] and rows[0]["failed"], rows[:1])
left = subprocess.run(["pgrep", "-f", str(FAKE)], capture_output=True, text=True).stdout.split()
check("...and its process is gone", not left, left)

out2 = Path(tempfile.mkdtemp(dir=tmp)); (out2 / "plan.json").write_text(json.dumps(["wait-stop"]))
env = {**os.environ, "TABLE_LOOP_GAME_CMD": f"{sys.executable} {FAKE}", "PLAN": str(out2 / "plan.json"), "TABLE_LOOP_PASS_ENV": "PLAN"}
proc = subprocess.Popen([sys.executable, str(REPO / "harness/table_loop.py"), "--out", str(out2), "--watch-secs", "0.3", "--kill-grace-secs", "1", "--max-secs", "30",
                         "--disk-floor-gib", "0", "--min-free-pct", "0", "--port0", "8960", "--hours", "1"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(2); (out2 / "STOP").write_text(""); t0 = time.time()
try:
    outtxt, _ = proc.communicate(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill(); outtxt = "TIMEOUT"
rep = json.loads((out2 / "report.json").read_text()) if (out2 / "report.json").exists() else {}
check("the STOP file stops a running game and the loop within seconds", rep.get("stopped") == "STOP file" and time.time() - t0 < 10, (rep.get("stopped"), round(time.time() - t0, 1)))
left = subprocess.run(["pgrep", "-f", str(FAKE)], capture_output=True, text=True).stdout.split()
check("...with no stray process", not left, left)
sock = socket.socket(); check("...and no port left in use", sock.connect_ex(("127.0.0.1", 8960)) != 0); sock.close()

p, out, rep, rows = loop(["leak-port"], ["--games", "1"])
check("a game that leaves a port listening is counted failed", rows and rows[0]["leftover_port"] and rows[0]["failed"], rows)
sock = socket.socket(); check("...and the loop killed the leftover listener", sock.connect_ex(("127.0.0.1", 8950)) != 0); sock.close()
subprocess.run(["pkill", "-f", "s.bind"], capture_output=True)

# ── 4. orin backend: health failure and pilot failure stop it with no retry ─────────────────────
p, out, rep, rows = loop(["ok"], ["--backend", "orin", "--orin-clear", "--games", "5"], {"JETSON_BASE": "http://127.0.0.1:9/v1"})
check("orin backend: a failed /health stops before any game and does not retry", rep.get("games") == 0 and "health" in rep.get("stopped", ""), rep)
import fake_router
srv = fake_router.start("first", 1)
p, out, rep, rows = loop(["fail", "ok", "ok"], ["--backend", "orin", "--orin-clear", "--games", "5"], {"JETSON_BASE": srv.base})
check("orin backend: the first pilot failure stops the whole loop (no retry loop)", rep.get("games") == 1 and "pilot failed" in rep.get("stopped", ""), rep)

rp = subprocess.Popen([sys.executable, str(REPO / "harness/fake_router.py"), "--mode", "first"], stdout=subprocess.PIPE, text=True)
rbase = rp.stdout.readline().strip()
p, out, rep, rows = loop(["fail-router-down", "ok"], ["--backend", "orin", "--orin-clear", "--games", "5"], {"JETSON_BASE": rbase, "ROUTER_PID": str(rp.pid), "TABLE_LOOP_PASS_ENV": "PLAN,ROUTER_PID"})
check("orin backend: a pilot failure with the router gone is recorded as a network loss, not a game result, and the loop stops",
      rep.get("games") == 1 and rows and rows[0].get("network_lost") is True and "unreachable" in rep.get("stopped", ""), (rep, rows))
rp.wait()
p, out, rep, rows = loop(["fail", "ok"], ["--backend", "orin", "--orin-clear", "--games", "5"], {"JETSON_BASE": srv.base})
check("...while a pilot failure with the router still answering is NOT called a network loss", rows and rows[0].get("network_lost") is False, rows)
check("...and the loop made no model request itself", len(srv.seen) == 0)
sys.exit(0 if ok_all else 1)
