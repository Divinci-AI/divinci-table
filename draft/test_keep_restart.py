"""A draft server restarted with --keep carries on by itself: no step left hanging, the same keys.        python3 draft/test_keep_restart.py

2026-11-13, a live draft: a player went to bed after the others had made their pick. The host restarted the server with
`--keep --bot Sam`, the saved picks were put back, and the draft sat still: every seat it still waited on had ALREADY picked, and only an
arriving pick ever made it look (restore() refilled `pending` and nothing called _advance()). Someone had to send a pick a second time.

Part 1 (in this process; no server, no voice, no model): the owed step is advanced at restore and saved; each seat gets the card it
had picked; a seat that had picked before a bot took it over keeps its own pick; nothing advances when a seat is still waited on; a
second restart does not advance again; the draft's very last step can be the owed one; a draft nobody is waited on runs by itself.
Part 2 (the real program, twice, on a free port with its state in a scratch folder and DRAFT_BANTER=off): three seats pick over HTTP,
the server is stopped and started again with `--keep --bot Sam`, and with NOTHING sent to it the next pack is on the table.
"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="keep-test-"))
os.environ["DRAFT_LIVE"] = str(TMP)
os.environ["DRAFT_SAY_CMD"] = "true"
os.environ["DRAFT_BANTER"] = "off"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import draft as D                      # exits 77 (skipped) when the set's card data is missing
import draft_banter
import draft_server as S

D.OUT_DIR = TMP / "drafts"             # a finished draft's record goes to the scratch folder too
draft_banter.HostVoice.speak = lambda *a, **k: None      # nothing is spoken,
draft_banter.Banter.event = lambda *a, **k: None         # and no model is ever asked for a line
draft_banter.Banter._queue = lambda *a, **k: "off"

fails = []
def check(ok, what, d=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {str(d)[:300]}"), flush=True); fails.append(what) if not ok else None

SEATS0 = (dict(S.HUMANS), dict(S.AGENTS), dict(S.NAMES), set(S.REQUIRED), dict(getattr(S, "BOTS", {})))
MICHAEL, SAM, CLAUDE, FUSION = 0, 1, 2, 3
TOTAL = D.PACKS * D.PACK_SIZE
NEVER = {8800, 8801, 8820, 8830, 8883}


def fresh(name):
    """The seats as the program starts with them, and an empty state folder of its own."""
    for d, v in zip((S.HUMANS, S.AGENTS, S.NAMES), SEATS0[:3]):
        d.clear(); d.update(v)
    S.REQUIRED.clear(); S.REQUIRED.update(SEATS0[3])
    if hasattr(S, "BOTS"):
        S.BOTS.clear(); S.BOTS.update(SEATS0[4])
    S.LIVE = TMP / name
    S.LIVE.mkdir()
    return S.LIVE


def stop_and_restart(dr, seed, bots=()):
    """What `--keep [--bot X]` does: the links file as the running server left it, the seats changed, a new Draft, the saved state."""
    live = S.LIVE
    (live / "draft-links.txt").write_text(S.links_text(dr))
    S.make_bots(list(bots))
    again = S.Draft(seed, live / "draft-links.txt")
    again.restore(live / "draft-state.json")
    return again


def first(dr, seat):
    return dr.current[seat][0]["id"]


def saved():
    return json.loads((S.LIVE / "draft-state.json").read_text())


print("1. The step that is owed after a restart (in this process)", flush=True)
fresh("a")
dr = S.Draft(21)
mine = {s: first(dr, s) for s in (MICHAEL, CLAUDE, FUSION)}
for s, cid in mine.items():
    dr.pick(s, cid)
check(dr.pick_no == 0 and set(dr.pending) == {MICHAEL, CLAUDE, FUSION}, "three seats have picked and the draft waits on Sam", (dr.pick_no, dr.pending))
again = stop_and_restart(dr, 21, bots=["Sam"])
check(again.pick_no == 1 and not again.pending and not again.done,
      "restarted with --keep --bot Sam: the step is advanced at once, with no pick sent again", f"pick_no {again.pick_no}, pending {sorted(again.pending)}")
check([again.picks[s][0]["id"] if again.picks[s] else None for s in mine] == list(mine.values()), "each of the three got the card it had picked")
check(len(again.picks[SAM]) == 1 and all(len(p) == 1 for p in again.picks), "a bot picked for Sam's seat (and for the seats nobody was named for)",
      [len(p) for p in again.picks])
st = saved()
check(len(st["history"]) == 1 and st["pending"] == {} and len(again.history) == 1, "the advanced step is saved: one step of history, nothing pending", st.get("pending"))
v = again.view(MICHAEL)
check(v["pick"] == 2 and not v["picked_this_turn"] and len(v["pack"]) == D.PACK_SIZE - 1 and v["waiting_on"] == ["Michael", "Claude", "Fusion"],
      "Michael is looking at the next pack, and the table waits on the three who are still drafting", {k: v[k] for k in ("pick", "picked_this_turn", "waiting_on")})
third = stop_and_restart(again, 21)
check(third.pick_no == 1 and not third.pending and len(saved()["history"]) == 1, "a second restart advances nothing more", (third.pick_no, third.pending))
check([[c["id"] for c in p] for p in third.picks] == [[c["id"] for c in p] for p in again.picks], "…and replays to the same picks for every seat")

fresh("b")
dr = S.Draft(22)
sams = dr.current[SAM][3]["id"]                        # (not the first card: so it cannot be a bot's choice by luck)
for s, cid in ((SAM, sams), (CLAUDE, first(dr, CLAUDE)), (FUSION, first(dr, FUSION))):
    dr.pick(s, cid)
again = stop_and_restart(dr, 22, bots=["Michael"])
check(again.pick_no == 1 and again.picks[SAM][0]["id"] == sams and len(again.picks[MICHAEL]) == 1,
      "a seat that had picked keeps its OWN pick; the seat handed to a bot gets a bot's", (again.pick_no, again.picks[SAM][:1]))

fresh("c")
dr = S.Draft(23)
for s in (MICHAEL, CLAUDE):
    dr.pick(s, first(dr, s))
again = stop_and_restart(dr, 23)
check(again.pick_no == 0 and set(again.pending) == {MICHAEL, CLAUDE} and not saved()["history"],
      "the same seats, two of four picked: nothing is owed, nothing advances, the two picks are still in", (again.pick_no, sorted(again.pending)))
for s in (SAM, FUSION):
    again.pick(s, first(again, s))
check(again.pick_no == 1 and not again.pending, "…and the step advances, as ever, when the last pick arrives")

fresh("d")
dr = S.Draft(24)
while len(dr.history) < TOTAL - 1:
    for s in S.NAMES:
        dr.pick(s, first(dr, s))
last = {s: first(dr, s) for s in (MICHAEL, CLAUDE, FUSION)}
for s, cid in last.items():
    dr.pick(s, cid)
check(not dr.done and dr.rnd == D.PACKS - 1 and dr.pick_no == D.PACK_SIZE - 1 and len(dr.pending) == 3, "the last pick of the draft, three of four in")
again = stop_and_restart(dr, 24, bots=["Sam"])
check(again.done and len(again.history) == TOTAL and all(len(p) == TOTAL for p in again.picks), "when the owed step is the draft's last one, the draft is over after the restart",
      (again.done, len(again.history)))
check((D.OUT_DIR / "draft-live-seed24.json").exists() and len(saved()["history"]) == TOTAL, "…its record is written and the state saved")
check(all(again.picks[s][-1]["id"] == cid for s, cid in last.items()), "…and the three got their last picks")

fresh("e")
S.make_bots(["Michael", "Sam", "Claude", "Fusion"])
dr = S.Draft(25)
ran = dr.catch_up() if hasattr(dr, "catch_up") else None
check(ran == TOTAL and dr.done and all(len(p) == TOTAL for p in dr.picks), "a draft nobody is waited on (every named seat a bot) runs to its end by itself", ran)
fresh("f")

print("2. The real program: stop it, start it again with --keep --bot Sam, send nothing", flush=True)


def free_port():
    while True:
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]
        if port not in NEVER:
            return port


def call(base, path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError):
        return 0, {}


class Program:
    """draft_server.py itself, as a host starts it. Only the process started here is ever stopped."""
    def __init__(self, live):
        self.live, self.port, self.p, self.out = live, free_port(), None, live / "server.out"
        self.base = f"http://127.0.0.1:{self.port}"

    def start(self, *flags):
        env = {**os.environ, "DRAFT_LIVE": str(self.live), "DRAFT_SAY_CMD": "true", "DRAFT_BANTER": "off"}
        self.p = subprocess.Popen([sys.executable, "-u", os.path.join(HERE, "draft_server.py"), "--port", str(self.port), *flags],
                                  env=env, stdout=open(self.out, "w"), stderr=subprocess.STDOUT)
        for _ in range(150):
            if call(self.base, "/api/state")[0] == 403:    # it answers (403: no key): it is up
                return self
            if self.p.poll() is not None:
                break
            time.sleep(0.1)
        raise SystemExit(f"the draft server did not start: {self.out.read_text()[-400:]}")

    def stop(self):
        if self.p is not None and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.p.kill()

    def keys(self):
        people = {l.split(": ")[0]: l.strip().split("k=")[1] for l in (self.live / "draft-links.txt").read_text().splitlines() if "k=" in l}
        return {**people, **json.loads((self.live / "draft-agent-tokens.json").read_text())}


live = Path(tempfile.mkdtemp(prefix="keep-test-live-"))
prog = Program(live)
try:
    prog.start("--seed", "31")
    k = prog.keys()
    took = {}
    for who in ("Michael", "Claude", "Fusion"):
        c, d = call(prog.base, f"/api/state?k={k[who]}")
        took[who] = d["pack"][0]
        check(call(prog.base, f"/api/pick?k={k[who]}", {"id": d["pack"][0]["id"]})[0] == 200, f"{who} picks")
    c, d = call(prog.base, f"/api/state?k={k['Michael']}")
    check(d["pick"] == 1 and d["picked_this_turn"] and d["waiting_on"] == ["Sam"], "the draft waits on Sam", {x: d.get(x) for x in ("pick", "waiting_on")})
    prog.stop()
    prog.start("--keep", "--bot", "Sam")
    c, d = call(prog.base, f"/api/state?k={k['Michael']}")
    check(c == 200 and d["pick"] == 2 and not d["picked_this_turn"] and [x["id"] for x in d["pool"]] == [took["Michael"]["id"]] and len(d["pack"]) == D.PACK_SIZE - 1,
          "after the restart, with nothing sent: pick 2 is on the table and Michael holds the card he picked", {x: d.get(x) for x in ("pick", "picked_this_turn", "waiting_on")})
    check(d.get("waiting_on") == ["Michael", "Claude", "Fusion"], "Sam's seat is no longer waited on", d.get("waiting_on"))
    out = prog.out.read_text()
    check("0 steps replayed; 1 more advanced now" in out, "the server says what it did when it started", out[-300:])
finally:
    prog.stop()

print("failures: none" if not fails else f"failures: {fails}")
sys.exit(1 if fails else 0)
