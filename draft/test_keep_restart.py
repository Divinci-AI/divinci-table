"""A draft server restarted with --keep carries on by itself: no step left hanging, the same keys.        python3 draft/test_keep_restart.py

2026-11-13, a live draft: a player went to bed after the others had made their pick. The host restarted the server with
`--keep --bot Sam`, the saved picks were put back, and the draft sat still: every seat it still waited on had ALREADY picked, and only an
arriving pick ever made it look (restore() refilled `pending` and nothing called _advance()). Someone had to send a pick a second time.

Part 1 (in this process; no server, no voice, no model): the owed step is advanced at restore and saved; each seat gets the card it
had picked; a seat that had picked before a bot took it over keeps its own pick; nothing advances when a seat is still waited on; a
second restart does not advance again; the draft's very last step can be the owed one; a draft nobody is waited on runs by itself.
Part 2 (the real program, twice, on a free port with its state in a scratch folder and DRAFT_BANTER=off): three seats pick over HTTP,
the server is stopped and started again with `--keep --bot Sam`, and with NOTHING sent to it the next pack is on the table.

The same evening: the people's links and the broadcast key survived a --keep restart, the AI seats' tokens did not (they were made new
every time), so claude_seat.py and fusion_seat.py, which read their token once, were refused from then on and had to be restarted too.
Part 3 (in this process): a restart keeps EVERY key; both key files are 0600 and replaced whole; a seat handed to a bot loses its
token; a key file edited by hand can never make two seats share a key or make a seat's key open the broadcast; junk in the file is
not kept and breaks nothing.
Part 4 (the real program): each key opens its own seat before and after `--keep`; then `--keep --bot Fusion`: Claude's token is the
same, Fusion's opens nothing.
"""
import json, os, socket, stat, subprocess, sys, tempfile, time, urllib.error, urllib.request
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
        """Every SEAT's key, by name: the people's from the links file (not its Broadcast line), the AI seats' from their own file."""
        people = {l.split(": ")[0]: l.strip().split("k=")[1] for l in (self.live / "draft-links.txt").read_text().splitlines()
                  if "k=" in l and not l.startswith("Broadcast: ")}
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

print("3. A restart keeps every key (in this process)", flush=True)


def write_keys(dr):
    """The two key files as the program writes them at start-up."""
    if hasattr(S, "write_key_files"):
        return S.write_key_files(dr)
    (S.LIVE / "draft-links.txt").write_text(S.links_text(dr))                                    # (a server from before 2026-11-14)
    (S.LIVE / "draft-agent-tokens.json").write_text(json.dumps({S.NAMES[s]: t for t, s in dr.tokens.items() if s in S.AGENTS}))


def keys_of(dr):
    return {S.NAMES[s]: t for t, s in dr.tokens.items()}


def mode(f):
    return stat.S_IMODE(f.stat().st_mode)


live = fresh("g")
dr = S.Draft(41)
write_keys(dr)
before, bkey = keys_of(dr), dr.broadcast_key
links, agf = live / "draft-links.txt", live / "draft-agent-tokens.json"
check(json.loads(agf.read_text()) == {n: before[n] for n in ("Claude", "Fusion")}, "the AI seats' tokens are in their own file")
check(mode(links) == 0o600 and mode(agf) == 0o600 and not list(live.glob("*.tmp")), "both key files are 0600, and no half-written copy is left", (oct(mode(links)), oct(mode(agf))))
again = S.Draft(41, links)
check({n: keys_of(again)[n] for n in ("Michael", "Sam")} == {n: before[n] for n in ("Michael", "Sam")} and again.broadcast_key == bkey,
      "a restart keeps the people's links and the broadcast key (as before)")
check({n: keys_of(again)[n] for n in ("Claude", "Fusion")} == {n: before[n] for n in ("Claude", "Fusion")},
      "a restart keeps the AI seats' tokens: the same token opens the same seat", "Claude's or Fusion's token changed")
check(list(again.tokens.values()) == list(S.NAMES), "…and the seats are in their usual order")
write_keys(again)
check(json.loads(agf.read_text()) == {n: before[n] for n in ("Claude", "Fusion")} and mode(agf) == 0o600 and mode(links) == 0o600,
      "written again after the restart: the same tokens, still 0600")
S.make_bots(["Fusion"])
third = S.Draft(41, links)
check(before["Fusion"] not in third.tokens and FUSION not in third.tokens.values() and keys_of(third)["Claude"] == before["Claude"],
      "a seat handed to a bot at the restart has no token any more; the other AI seat keeps its own")
write_keys(third)
check(json.loads(agf.read_text()) == {"Claude": before["Claude"]}, "…and its old token is no longer in the file", sorted(json.loads(agf.read_text())))

live = fresh("h")
dr = S.Draft(42)
write_keys(dr)
before, bkey = keys_of(dr), dr.broadcast_key
links, agf = live / "draft-links.txt", live / "draft-agent-tokens.json"
agf.write_text(json.dumps({"Claude": before["Michael"], "Fusion": bkey}))          # a file edited by hand: a person's key, and the broadcast's
again = S.Draft(42, links)
now = keys_of(again)
check(now["Michael"] == before["Michael"] and now["Sam"] == before["Sam"] and again.broadcast_key == bkey,
      "an AI token that is really a person's key or the broadcast key changes nothing for the people or the broadcast")
check(len({*now.values(), again.broadcast_key}) == 5 and now["Claude"] not in (before["Michael"], bkey) and now["Fusion"] not in (before["Michael"], bkey),
      "…the two AI seats get new tokens: five different secrets, and no seat key opens the broadcast", sorted(now))
agf.write_text(json.dumps({"Claude": before["Claude"], "Fusion": before["Claude"]}))
now = keys_of(S.Draft(42, links))
check(now["Claude"] == before["Claude"] and now["Fusion"] != before["Claude"], "the same token for two AI seats: the first keeps it, the second gets a new one")
for junk in ("not json", '["Claude"]', '{"Claude": "short", "Fusion": 7}', json.dumps({"Claude": "has a space " + "x" * 20, "Fusion": None})):
    agf.write_text(junk)
    try:
        now = keys_of(S.Draft(42, links))
        ok = now["Michael"] == before["Michael"] and len(set(now.values())) == 4 and all(len(now[n]) >= 16 and " " not in now[n] for n in ("Claude", "Fusion"))
    except Exception as e:                             # noqa: BLE001
        ok, now = False, repr(e)
    check(ok, f"junk in the AI token file ({junk[:28]}…) is not kept and breaks nothing", now)
agf.unlink()
now = keys_of(S.Draft(42, links))
check(now["Michael"] == before["Michael"] and now["Sam"] == before["Sam"] and len(set(now.values())) == 4, "no AI token file at all (an older server's folder): new AI tokens, the people's links kept")
fresh("i")

print("4. The real program: every key opens its own seat before and after --keep", flush=True)


def opens(base, key):
    c, d = call(base, f"/api/state?k={key}")
    return d.get("you") if c == 200 else c


live = Path(tempfile.mkdtemp(prefix="keep-test-live-"))
prog = Program(live)
try:
    prog.start("--seed", "43")
    k = prog.keys()
    bk = next(l.strip().split("k=")[1] for l in (live / "draft-links.txt").read_text().splitlines() if l.startswith("Broadcast: "))
    check(sorted(k) == ["Claude", "Fusion", "Michael", "Sam"] and all(opens(prog.base, t) == n for n, t in k.items()), "started: each of the four keys opens its own seat")
    check(mode(live / "draft-links.txt") == 0o600 and mode(live / "draft-agent-tokens.json") == 0o600, "the key files are 0600")
    took = {}
    for who in ("Claude", "Fusion"):                   # an AI seat holds its token and has a pick in when the server goes down
        took[who] = call(prog.base, f"/api/state?k={k[who]}")[1]["pack"][0]["id"]
        call(prog.base, f"/api/pick?k={k[who]}", {"id": took[who]})
    prog.stop()
    prog.start("--keep")
    check(prog.keys() == k, "restarted with --keep: the key files hold the same keys", "an AI seat's token changed")
    for who in ("Claude", "Fusion"):
        c, d = call(prog.base, f"/api/state?k={k[who]}")
        check(c == 200 and d.get("you") == who and d.get("my_pick") == took[who],
              f"{who}'s token from BEFORE the restart still opens its seat, with its pick still in (a running seat process is not locked out)", (c, d.get("error")))
    check(opens(prog.base, k["Michael"]) == "Michael" and opens(prog.base, k["Sam"]) == "Sam", "the people's links still open their seats")
    check(call(prog.base, f"/api/broadcast?k={bk}")[0] == 200, "the broadcast key still opens the broadcast")
    check(mode(live / "draft-links.txt") == 0o600 and mode(live / "draft-agent-tokens.json") == 0o600 and not list(live.glob("*.tmp")), "the key files are still 0600")
    prog.stop()
    prog.start("--keep", "--bot", "Fusion")
    k2 = prog.keys()
    check(k2 == {n: t for n, t in k.items() if n != "Fusion"}, "restarted with --keep --bot Fusion: the others' keys are the same and Fusion has none", sorted(k2))
    check(opens(prog.base, k["Claude"]) == "Claude" and opens(prog.base, k["Fusion"]) == 403, "Claude's token still opens Claude's seat; Fusion's old token opens nothing")
finally:
    prog.stop()

print("failures: none" if not fails else f"failures: {fails}")
sys.exit(1 if fails else 0)
