"""A seat handed to a bot (--bot) is still a seat on the broadcast, and its picks stay hidden unless the host asks.
                                                                                          python3 draft/test_broadcast_bots.py

2026-11-13, the rehearsal draft before the stream: bots in both people's seats, Claude and Fusion drafting. The broadcast showed two
columns: a `--bot` seat was dropped from the page altogether. Proved here:

  1. all four seats are shown, in seat order, the bot seats tagged "bot", with the number of cards each has taken;
  2. by default a bot seat's picks are never shown: not inside a pack, not when a pack ends, not when the draft is over, in any
     reveal mode; its cards are face-down placeholders and nothing it took is named anywhere in the JSON (checked after EVERY
     pick of a whole draft against what this file works out for itself);
  3. with --broadcast-bot-picks a bot seat's picks follow exactly the rule of an AI seat (and under `ai-live` it is treated as an
     AI seat, never as a person); nothing is shown that the rule does not allow yet;
  4. a bot seat regains nothing: no link, no token, no place among the seats the draft waits on or the players who ready up;
  5. the real program started with --bot (and with the flag): four columns, the bot's line absent from both key files.
The broadcast's other guarantees are test_broadcast.py's, which runs unchanged.
"""
import json, os, random, re, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="broadcast-bots-test-"))
os.environ["DRAFT_LIVE"] = str(TMP)
os.environ["DRAFT_SAY_CMD"] = "true"
os.environ["DRAFT_BANTER"] = "off"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import draft as D                      # exits 77 (skipped) when the set's card data is missing
import draft_banter
import draft_server as S

D.OUT_DIR = TMP / "drafts"
draft_banter.HostVoice.speak = lambda *a, **k: None      # nothing is spoken,
draft_banter.Banter.event = lambda *a, **k: None         # and no model is ever asked for a line
draft_banter.Banter._queue = lambda *a, **k: "off"

fails = []
def check(ok, what, d=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {str(d)[:300]}"), flush=True); fails.append(what) if not ok else None

SEATS0 = (dict(S.HUMANS), dict(S.AGENTS), dict(S.NAMES), set(S.REQUIRED))
PACK, TOTAL = D.PACK_SIZE, D.PACKS * D.PACK_SIZE
ORDER = ["Michael", "Sam", "Claude", "Fusion"]
NEVER = {8800, 8801, 8820, 8830, 8883}
_n = [0]


def fresh(bots=()):
    """The seats as the program starts with them, an empty state folder, then `--bot` for each name."""
    for d, v in zip((S.HUMANS, S.AGENTS, S.NAMES), SEATS0[:3]):
        d.clear(); d.update(v)
    S.REQUIRED.clear(); S.REQUIRED.update(SEATS0[3])
    if hasattr(S, "BOTS"):
        S.BOTS.clear()
    _n[0] += 1
    S.LIVE = TMP / f"case{_n[0]}"
    S.LIVE.mkdir()
    S.make_bots(list(bots))


def new_draft(seed, mode="pack-end", delay=3, bot_picks=False):
    try:
        return S.Draft(seed, reveal=mode, delay=delay, bot_picks=bot_picks)
    except TypeError:                                  # a server from before 2026-11-14 has no such option
        return S.Draft(seed, reveal=mode, delay=delay)


def allowed(i, human, steps, mode, delay):
    """The reveal rule, written from the specification (as in test_broadcast.py). i: the pick's index in its seat's draft; steps:
    picks the whole table has finished."""
    if steps >= TOTAL:
        return True
    if mode == "draft-end" or delay == "end":
        return False
    pack_of_pick, pick_in_pack = i // PACK, i % PACK + 1
    if pack_of_pick < steps // PACK:
        return True
    if mode == "pack-end" or (mode == "ai-live" and human):
        return False
    return steps % PACK >= pick_in_pack + delay


def named(body, name):
    return name in body and re.search(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", body) is not None


def rows(dr):
    return {r["name"]: r for r in dr.broadcast()["seats"]}


def run(bots, mode, delay, bot_picks, seed=7):
    """One whole draft. Returns {problem: first detail} and whether a bot pick was ever shown before the end / inside its pack."""
    fresh(bots)
    dr = new_draft(seed, mode, delay, bot_picks)
    people = set(SEATS0[0].values()) - set(bots)
    rng = random.Random(500 + seed)
    bad, early, mid = {}, False, False
    steps = 0

    def look(where):
        nonlocal early, mid
        b = dr.broadcast()
        raw = json.dumps(b, ensure_ascii=False)
        got = [r["name"] for r in b["seats"]]
        if got != ORDER:
            bad.setdefault("all four seats are shown, in seat order", f"{where}: {got}")
            return
        ok_names = set()
        for seat, name in enumerate(ORDER):
            r = b["seats"][seat]
            is_bot = name in bots
            kind = "bot" if is_bot else "human" if name in people else "AI"
            if r["kind"] != kind:
                bad.setdefault("each seat is tagged human, AI or bot", f"{where} {name}: {r['kind']}")
            mine = [(c["name"], c["mana_cost"] or "", c["rarity"]) for c in dr.picks[seat]]
            want = [c for i, c in enumerate(mine) if (bot_picks or not is_bot) and allowed(i, name in people, steps, mode, delay)][::-1]
            ok_names |= {c[0] for c in want}
            shown = [(p["name"], p["cost"], p["rarity"]) for p in r["picks"]]
            if shown != want:
                bad.setdefault("each seat shows exactly the picks its rule allows" if not is_bot else
                               ("a bot seat shows exactly what an AI seat's rule allows" if bot_picks else "a bot seat's picks are never shown"),
                               f"{where} {name}: shown {[x[0] for x in shown][:3]} allowed {[x[0] for x in want][:3]}")
            if r["picked"] != steps or r["hidden"] != steps - len(want):
                bad.setdefault("the picked and face-down counts are right", f"{where} {name}: {r['picked']}/{r['hidden']}")
            if is_bot and not bot_picks and r["colors"]:
                bad.setdefault("a bot seat's colours are not shown either", f"{where} {name}: {r['colors']}")
            if is_bot and r["ready"]:
                bad.setdefault("a bot seat is never 'ready'", where)
            if is_bot and r["picks"] and steps < TOTAL:
                early = True
                mid = mid or any(p["pack"] == steps // PACK + 1 for p in r["picks"])
        every = {c["name"] for p in dr.picks for c in p} | {c["name"] for pk in dr.current for c in pk}
        rest = raw                                     # (a shown name can CONTAIN another card's name, "Warp Core Breach": take the shown ones out first)
        for n in sorted(ok_names, key=len, reverse=True):
            rest = rest.replace(n, "")
        leaked = [n for n in every if n not in ok_names and named(rest, n)]
        if leaked:
            bad.setdefault("no card is named before its seat's rule allows it", f"{where}: {leaked[:4]}")
        if any(x in b["waiting_on"] for x in bots):
            bad.setdefault("the draft never waits on a bot seat", f"{where}: {b['waiting_on']}")

    look("before any pick")
    while not dr.done:
        for seat in sorted(S.REQUIRED):
            pack = dr.current[seat]
            dr.pick(seat, pack[rng.randrange(len(pack))]["id"])
        steps += 1
        look(f"after step {steps}")
    if steps != TOTAL:
        bad.setdefault("the draft ran to its end", steps)
    return bad, early, mid, dr


print("1. An all-AI draft (bots in both people's seats) shows four columns", flush=True)
fresh(["Michael", "Sam"])
dr = new_draft(7)
b = dr.broadcast()
check([(r["name"], r["kind"]) for r in b["seats"]] == [("Michael", "bot"), ("Sam", "bot"), ("Claude", "AI"), ("Fusion", "AI")],
      "four seats, in seat order: Michael and Sam tagged bot, Claude and Fusion tagged AI", [(r["name"], r["kind"]) for r in b["seats"]])
check(b["waiting_on"] == ["Claude", "Fusion"] and b.get("bot_picks") is False, "the draft waits on the two AI seats only; bot picks are off unless asked for", b.get("bot_picks"))
for seat in sorted(S.REQUIRED):
    dr.pick(seat, dr.current[seat][0]["id"])
r = rows(dr)
check(all(r[n]["picked"] == 1 and r[n]["hidden"] == 1 and r[n]["picks"] == [] and r[n]["colors"] == {} for n in ("Michael", "Sam")),
      "after one pick each bot seat has taken 1 card, shown face down, unnamed", {n: (r[n]["picked"], r[n]["hidden"], r[n]["picks"]) for n in ("Michael", "Sam")})

print("2. By default a bot seat's picks are never shown (every reveal mode, checked after every pick)", flush=True)
for bots, mode, delay in ((["Michael", "Sam"], "pack-end", 3), (["Sam"], "delayed", 0), (["Sam"], "ai-live", 2), (["Fusion"], "delayed", 3), (["Michael", "Sam"], "draft-end", 3)):
    bad, early, mid, dr = run(bots, mode, delay, bot_picks=False)
    label = f"--bot {' --bot '.join(bots)}, {mode}, delay {delay}"
    check(not bad, f"[{label}] four seats, the right tags and counts, the other seats exactly as their rule says, nothing of a bot's named", bad)
    check(not early, f"[{label}] no bot pick was shown at any point of the draft")
    r = rows(dr)
    check(dr.done and all(r[n]["picks"] == [] and r[n]["hidden"] == TOTAL for n in bots) and all(len(r[n]["picks"]) == TOTAL for n in ORDER if n not in bots),
          f"[{label}] after the draft: the drafting seats show all {TOTAL} picks, the bot seats still none ({TOTAL} face down)")

print("3. With --broadcast-bot-picks a bot seat follows an AI seat's rule, and never more", flush=True)
seen = {}
for bots, mode, delay in ((["Michael", "Sam"], "pack-end", 3), (["Sam"], "delayed", 3), (["Sam"], "delayed", 0), (["Sam"], "ai-live", 3), (["Michael", "Sam"], "draft-end", 3),
                          (["Sam"], "delayed", "end")):
    bad, early, mid, dr = run(bots, mode, delay, bot_picks=True)
    seen[(mode, delay)] = (early, mid)
    label = f"--bot {' --bot '.join(bots)}, {mode}, delay {delay}, bot picks on"
    check(not bad, f"[{label}] after every pick: a bot seat shows exactly what an AI seat's rule allows, and no card is named early", bad)
    r = rows(dr)
    check(dr.done and all(len(r[n]["picks"]) == TOTAL and r[n]["hidden"] == 0 for n in ORDER), f"[{label}] after the draft every seat shows all {TOTAL} picks")
check(seen[("pack-end", 3)] == (True, False), "pack-end: a bot's picks appeared only once their pack had ended", seen[("pack-end", 3)])
check(seen[("delayed", 3)] == (True, True) and seen[("delayed", 0)] == (True, True), "delayed: they appeared inside the pack, N picks late (so the comparison was not all-or-nothing)")
check(seen[("ai-live", 3)] == (True, True), "ai-live: a bot seat is treated as an AI seat (its picks appear inside the pack, delayed)", seen[("ai-live", 3)])
check(seen[("draft-end", 3)] == (False, False) and seen[("delayed", "end")] == (False, False), "draft-end and delay 'end': nothing of a bot's until the draft is over")
# the same position, by hand: ai-live, five picks into pack 1, delay 3. Michael is a person, Sam's seat is a bot, Claude and Fusion are AI.
fresh(["Sam"])
dr = new_draft(11, "ai-live", 3, bot_picks=True)
for _ in range(5):
    for seat in sorted(S.REQUIRED):
        dr.pick(seat, dr.current[seat][0]["id"])
r = rows(dr)
check([len(r[n]["picks"]) for n in ORDER] == [0, 2, 2, 2], "ai-live, five picks done, delay 3: the person shows nothing; the bot seat and the two AI seats show picks 1 and 2",
      [len(r[n]["picks"]) for n in ORDER])
check([c["name"] for c in dr.picks[1][:2]][::-1] == [p["name"] for p in r["Sam"]["picks"]], "…and what the bot seat shows is what the bot took, newest first")
dr.bc_bot_picks = False
check([len(x["picks"]) for x in dr.broadcast()["seats"]] == [0, 0, 2, 2], "the same position without the flag: the bot seat shows nothing")

print("4. A bot seat regains nothing", flush=True)
fresh(["Sam"])
dr = new_draft(12, "delayed", 0, bot_picks=True)
check(1 not in dr.tokens.values() and len(dr.tokens) == 3, "no token for the bot seat")
check("Sam" not in S.links_text(dr) and 1 not in S.REQUIRED and "Sam" not in S.NAMES.values() and 1 not in S.HUMANS and 1 not in S.AGENTS,
      "no line in the links file, not waited on, not among the people or the AI seats")
while not dr.done:
    for seat in sorted(S.REQUIRED):
        dr.pick(seat, dr.current[seat][0]["id"])
st = dr.host_ready_state()
check(sorted(st["players"]) == ["Claude", "Fusion", "Michael"], "the host's ready list (what match_when_ready.sh reads) has only the seats that drafted", sorted(st["players"]))
check(not next(x for x in dr.broadcast()["seats"] if x["name"] == "Sam")["ready"], "the bot seat is not 'ready' on the broadcast")
for s in S.NAMES:
    dr.set_ready(s, True)
check(dr.host_ready_state()["all_ready"], "…and everyone is ready without it")
rec = json.loads((D.OUT_DIR / "draft-live-seed12.json").read_text())
check(rec.get("bots") == {"1": "Sam"} and "1" not in rec["humans"], "the finished draft's record says which named seat a bot drafted", rec.get("bots"))

print("5. The real program with --bot", flush=True)


def free_port():
    while True:
        with socket.socket() as sk:
            sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]
        if port not in NEVER:
            return port


def call(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except (urllib.error.URLError, OSError):
        return 0, ""


def program(*flags):
    """draft_server.py as a host starts it, on a free port, its state in a scratch folder. No pick is made: nothing is said."""
    live, port = Path(tempfile.mkdtemp(prefix="broadcast-bots-live-")), free_port()
    env = {**os.environ, "DRAFT_LIVE": str(live), "DRAFT_SAY_CMD": "true", "DRAFT_BANTER": "off"}
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "draft_server.py"), "--seed", "5", "--port", str(port), *flags], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base, key = f"http://127.0.0.1:{port}", None
    for _ in range(150):
        try:
            key = re.search(r"^Broadcast: /broadcast\?k=(\S+)$", (live / "draft-links.txt").read_text(), re.M).group(1)
            if call(base, f"/api/broadcast?k={key}")[0] == 200:
                break
        except (OSError, AttributeError):
            pass
        if p.poll() is not None:
            break
        time.sleep(0.1)
    return p, live, base, key


for flags, want_flag in ((("--bot", "Michael", "--bot", "Sam"), False), (("--bot", "Sam", "--broadcast-bot-picks", "--broadcast-reveal", "ai-live"), True)):
    p, live, base, key = program(*flags)
    try:
        code, raw = call(base, f"/api/broadcast?k={key}")
        d = json.loads(raw) if code == 200 else {"seats": []}
        bots = [flags[i + 1] for i, f in enumerate(flags) if f == "--bot"]
        check([x["name"] for x in d["seats"]] == ORDER and [x["name"] for x in d["seats"] if x["kind"] == "bot"] == bots,
              f"started with {' '.join(flags)}: four columns, {' and '.join(bots)} tagged bot", [(x["name"], x["kind"]) for x in d["seats"]])
        check(d.get("bot_picks") is want_flag, f"…bot picks {'on' if want_flag else 'off'}", d.get("bot_picks"))
        lines = (live / "draft-links.txt").read_text().splitlines()
        agents = json.loads((live / "draft-agent-tokens.json").read_text())
        check(not any(l.split(":")[0] in bots for l in lines) and not any(n in bots for n in agents) and len(lines) == 2 - len(bots) + 1,
              "…and no key for a bot seat in either key file", (len(lines), sorted(agents)))
        code, page = call(base, f"/broadcast?k={key}")
        check(code == 200 and ".tag.bot" in page and 's.kind==="bot"' in page and "innerHTML" not in page, "the page knows how to draw a bot seat's tag (and still builds text nodes only)")
    finally:
        p.terminate(); p.wait(timeout=10)              # only the process this file started

print("failures: none" if not fails else f"failures: {fails}")
sys.exit(1 if fails else 0)
