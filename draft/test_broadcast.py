"""The broadcast (a stream's scene) must never show more than its reveal rule allows.        python3 draft/test_broadcast.py

A real server on a free loopback port, its state in a scratch folder, the models and the host voice switched off. For every reveal
mode a seeded draft is driven pick by pick through the seats' own API, and after EVERY pick /api/broadcast is compared with what this
file works out for itself from the picks it made: no card name that is not allowed yet, no pack contents, no card id, no key.

    python3 test_broadcast.py --demo [--reveal delayed] [--delay 3]     a self-driving draft to look at (prints the link; Ctrl-C ends it)
"""
import json, os, random, re, socket, stat, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="broadcast-test-"))
os.environ["DRAFT_LIVE"] = str(TMP)
os.environ["DRAFT_SAY_CMD"] = "true"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import draft as D                      # exits 77 (skipped) when the set's card data is missing
import draft_banter
import draft_server as S

D.OUT_DIR = TMP / "drafts"             # the finished-draft record goes to the scratch folder too
draft_banter.HostVoice.speak = lambda *a, **k: None      # nothing is spoken,
draft_banter.Banter.event = lambda *a, **k: None         # and no model is ever asked for a line
draft_banter.Banter._queue = lambda *a, **k: "off"

fails = []
def check(ok, what, d=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {d}")); fails.append(what) if not ok else None

ALL_NAMES = sorted({c["name"] for c in json.loads(D.CARDS_JSON.read_text()) if c.get("set") == "trk"})
ALL_IDS = {c["id"] for c in json.loads(D.CARDS_JSON.read_text())}
PACK = D.PACK_SIZE


def serve(dr):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), S.make_handler(dr))      # port 0: whatever is free
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def call(base, path, body=None):
    """-> (status, parsed JSON or None, raw text)"""
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            code, raw = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read().decode()
    try:
        return code, json.loads(raw), raw
    except ValueError:
        return code, None, raw


def allowed(i, human, steps, mode, delay):
    """This file's own statement of the rule. i: the pick's index in its seat's draft (0-based); steps: picks the whole table has
    finished. Written from the specification, not from the server's code."""
    total = D.PACKS * PACK
    if steps >= total:
        return True                                    # the draft is over: everything
    if mode == "draft-end" or delay == "end":
        return False
    pack_of_pick, pick_in_pack = i // PACK, i % PACK + 1
    current_pack, finished_in_pack = steps // PACK, steps % PACK
    if pack_of_pick < current_pack:
        return True                                    # its pack has ended
    if mode == "pack-end" or (mode == "ai-live" and human):
        return False
    return finished_in_pack >= pick_in_pack + delay


def named(body, name):
    """Is this card name in the text, as a name (not as part of a longer word)?"""
    return name in body and re.search(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", body) is not None


def run_mode(mode, delay, seed=7):
    """Drive one whole draft; return the number of broadcast snapshots checked. Every problem is counted once per kind."""
    dr = S.Draft(seed, reveal=mode, delay=delay)
    srv, base = serve(dr)
    bk = dr.broadcast_key
    tok = {s: t for t, s in dr.tokens.items()}
    keys = list(tok.values()) + [bk]
    rng = random.Random(1000 + seed)
    mine = {s: [] for s in S.NAMES}                    # what THIS file picked for each seat, in order: (name, cost, rarity)
    bad = {}                                           # kind of problem -> first detail
    snaps = steps = 0
    seen_mid_pack = seen_early = seen_hidden = False   # a pick shown while its pack is going / anything shown before the end

    def look(where):
        nonlocal snaps, seen_mid_pack, seen_early, seen_hidden
        code, d, raw = call(base, f"/api/broadcast?k={bk}")
        snaps += 1
        if code != 200 or d is None:
            bad.setdefault("the broadcast answers", f"{where}: {code}"); return
        ok_names = set()
        for row, seat in zip(d["seats"], S.NAMES):
            human = seat in S.HUMANS
            want = [c for i, c in enumerate(mine[seat]) if allowed(i, human, steps, mode, delay)][::-1]
            got = [(p["name"], p["cost"], p["rarity"]) for p in row["picks"]]
            ok_names |= {c[0] for c in want}
            if got != want:
                bad.setdefault("each seat shows exactly the picks the rule allows, newest first",
                               f"{where} {row['name']}: shown {[g[0] for g in got][:4]} allowed {[w[0] for w in want][:4]}")
            if row["picked"] != steps or row["hidden"] != steps - len(want):
                bad.setdefault("the picked and face-down counts are right", f"{where} {row['name']}: {row['picked']}/{row['hidden']}")
            if [(p["pack"], p["pick"]) for p in row["picks"]] != [(i // PACK + 1, i % PACK + 1) for i in range(len(mine[seat]))
                                                                  if allowed(i, human, steps, mode, delay)][::-1]:
                bad.setdefault("each shown pick carries its own pack and pick number", f"{where} {row['name']}")
            tally = {}
            for p in row["picks"]:
                for col in p["colors"] or ["C"]:
                    tally[col] = tally.get(col, 0) + 1
            if row["colors"] != tally:
                bad.setdefault("the colour tally counts revealed picks only", f"{where} {row['name']}: {row['colors']} vs {tally}")
            if row["kind"] != ("human" if human else "AI") or row["name"] != S.NAMES[seat]:
                bad.setdefault("seats are named and tagged human or AI", f"{where}: {row['name']} {row['kind']}")
            seen_hidden = seen_hidden or row["hidden"] > 0
            if steps < D.PACKS * PACK:
                seen_early = seen_early or bool(row["picks"])
                seen_mid_pack = seen_mid_pack or any(p["pack"] == steps // PACK + 1 for p in row["picks"])
        leaked = [n for n in ALL_NAMES if n not in ok_names and named(raw, n)]
        if leaked:                                     # covers unrevealed picks, every pack's contents and what was passed
            bad.setdefault("no card name appears before the rule allows it", f"{where}: {leaked[:5]}")
        if any(i in raw for i in ALL_IDS):
            bad.setdefault("no card id appears", where)
        if any(k in raw for k in keys):
            bad.setdefault("no key appears", where)
        if len(d["seats"]) != len(S.NAMES):
            bad.setdefault("only the named seats are shown (no bot seat)", f"{where}: {len(d['seats'])}")
        hdr = (d["round"], d["pick"], d["done"])
        if hdr != (min(steps // PACK + 1, D.PACKS), steps % PACK + 1, steps >= D.PACKS * PACK):
            bad.setdefault("the header says which pack and pick the table is on", f"{where}: {hdr}")

    look("before any pick")
    while steps < D.PACKS * PACK:
        order = list(S.NAMES)
        rng.shuffle(order)
        for n, seat in enumerate(order):
            pack = call(base, f"/api/state?k={tok[seat]}")[1]["pack"]
            c = pack[rng.randrange(len(pack))]
            code, out, _ = call(base, f"/api/pick?k={tok[seat]}", {"id": c["id"]})
            if code != 200:
                bad.setdefault("scripted picks go through", f"step {steps}: {out}")
            mine[seat].append((c["name"], c["cost"], c["rarity"]))
            if n < len(order) - 1:                     # the step is not finished: this pick must not show yet in any way
                waiting = call(base, f"/api/broadcast?k={bk}")[1]["waiting_on"]
                if sorted(waiting) != sorted(S.NAMES[s] for s in order[n + 1:]):
                    bad.setdefault("the header says who the table is waiting on", f"step {steps}: {waiting}")
                look(f"step {steps + 1}, {n + 1} of {len(order)} picked")
        steps += 1
        look(f"after step {steps}")
    label = f"{mode}, delay {delay}"
    for what in ("the broadcast answers", "scripted picks go through", "each seat shows exactly the picks the rule allows, newest first",
                 "no card name appears before the rule allows it", "no card id appears", "no key appears",
                 "the picked and face-down counts are right", "each shown pick carries its own pack and pick number", "the colour tally counts revealed picks only",
                 "seats are named and tagged human or AI", "only the named seats are shown (no bot seat)",
                 "the header says which pack and pick the table is on", "the header says who the table is waiting on"):
        check(what not in bad, f"[{label}] {what}", bad.get(what, ""))
    d = call(base, f"/api/broadcast?k={bk}")[1]
    check(d["done"] and all(r["hidden"] == 0 and len(r["picks"]) == D.PACKS * PACK for r in d["seats"]),
          f"[{label}] after the draft everything is revealed: {D.PACKS * PACK} picks a seat, none face down")
    check(seen_hidden == (delay != 0), f"[{label}] picks were face down during the draft" if delay != 0 else f"[{label}] with no delay a finished pick is never face down")
    srv.shutdown(); srv.server_close()
    return snaps, seen_mid_pack, seen_early


def demo(mode, delay):
    dr = S.Draft(random.randrange(1, 10**6), reveal=mode, delay=delay)
    srv, base = serve(dr)
    print(f"broadcast ({mode}, delay {delay}): {base}/broadcast?k={dr.broadcast_key}\nstate in {TMP}; Ctrl-C to stop", flush=True)
    lines = [("Claude", "Fresh pack. Smells like possibility."), ("Michael", "no peeking at the stream"), ("Fusion", "The packs move on, and so must we."),
             ("Sam", "I regret nothing"), ("Claude", "Pass it along, nothing to see here.")]
    try:
        n = 0
        while not dr.done:
            for seat in S.NAMES:
                time.sleep(0.6)
                dr.pick(seat, random.choice(dr.current[seat])["id"])
            n += 1
            if n % 3 == 1:
                dr.banter.add(*lines[(n // 3) % len(lines)])
        print("draft over; the page stays up", flush=True)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__" and "--demo" in sys.argv:
    arg = lambda f, d: sys.argv[sys.argv.index(f) + 1] if f in sys.argv else d
    demo(arg("--reveal", "delayed"), S.broadcast_delay(arg("--delay", "3")))
    sys.exit(0)

# ---- 1. the rule, in every mode, checked after every single pick of a whole draft
total = 0
mid, early = {}, {}
for mode, delay in (("pack-end", 3), ("delayed", 3), ("delayed", 0), ("delayed", "end"), ("ai-live", 3), ("ai-live", "end"), ("draft-end", 3), ("delayed", 20)):
    n, mid[(mode, delay)], early[(mode, delay)] = run_mode(mode, delay)
    total += n
print(f"  ({total} broadcast snapshots compared)")
check(mid[("delayed", 3)] and mid[("delayed", 0)] and mid[("ai-live", 3)],
      "delayed and ai-live showed picks while their pack was still going (so the comparisons above were not all-or-nothing)")
check(not mid[("pack-end", 3)] and early[("pack-end", 3)] and not mid[("delayed", 20)] and early[("delayed", 20)],
      "pack-end (and a delay longer than a pack) showed a pack's picks only once that pack had ended")
check(not any(early[k] for k in (("delayed", "end"), ("ai-live", "end"), ("draft-end", 3))), "delay 'end' and draft-end showed nothing until the draft was over")
check(S.BROADCAST_REVEALS[0] == "pack-end" and S.Draft(7).bc_reveal == "pack-end" and S.Draft(7).bc_delay == 3,
      "the default is pack-end, and the delay for the other modes is 3 picks")

# spot checks of the rule on a hand-made position, so a mistake shared by allowed() above and the server would still show
dr = S.Draft(11, reveal="delayed", delay=3)
for _ in range(5):                                     # five whole picks into pack 1
    for seat in S.NAMES:
        dr.pick(seat, dr.current[seat][0]["id"])
b = dr.broadcast()
check([(len(r["picks"]), r["hidden"]) for r in b["seats"]] == [(2, 3)] * 4, "delay 3, five picks done: picks 1 and 2 are up, 3 to 5 face down",
      str([(len(r["picks"]), r["hidden"]) for r in b["seats"]]))
check([p["pick"] for p in b["seats"][0]["picks"]] == [2, 1], "newest on top")
dr.bc_reveal = "ai-live"
b = dr.broadcast()
check([len(r["picks"]) for r in b["seats"]] == [0, 0, 2, 2], "ai-live at the same point: the two people show nothing, the two AI seats show 2",
      str([len(r["picks"]) for r in b["seats"]]))
dr.bc_reveal = "pack-end"
check(all(not r["picks"] for r in dr.broadcast()["seats"]), "pack-end at the same point: nothing")
for _ in range(PACK - 5 + 1):                          # finish pack 1 and make the first pick of pack 2
    for seat in S.NAMES:
        dr.pick(seat, dr.current[seat][0]["id"])
check([(len(r["picks"]), r["hidden"]) for r in dr.broadcast()["seats"]] == [(PACK, 1)] * 4, "pack-end, one pick into pack 2: all of pack 1, and one face down")
dr.bc_reveal = "draft-end"
check(all(not r["picks"] for r in dr.broadcast()["seats"]), "draft-end at the same point: still nothing")

# a restart (--keep) replays the saved steps: the broadcast is the same, and so is its key
dr.bc_reveal = "delayed"
before = dr.broadcast()
(TMP / "draft-links.txt").write_text(S.links_text(dr))
again = S.Draft(11, TMP / "draft-links.txt", "delayed", 3)
again.restore(TMP / "draft-state.json")
check(again.broadcast()["seats"] == before["seats"], "after a restart the broadcast shows the same picks")
people = lambda x: {t: s for t, s in x.tokens.items() if s in S.HUMANS}
check(again.broadcast_key == dr.broadcast_key and people(again) == people(dr), "a restart keeps the broadcast link and the people's links")

# ---- 2. the keys
srv, base = serve(dr)
bk = dr.broadcast_key
text = S.links_text(dr)
check(f"Broadcast: /broadcast?k={bk}\n" in text and all(f"{S.NAMES[s]}: /?k={t}\n" in text for t, s in dr.tokens.items() if s in S.HUMANS),
      "the links file has a line for each person and a Broadcast line")
check(bk not in dr.tokens and len(bk) >= 20, "the broadcast key is its own secret, not a seat key")
for path in ("/broadcast", "/api/broadcast"):
    check(call(base, path)[0] == 403, f"{path} with no key: 403")
    check(call(base, path + "?k=nope")[0] == 403 and call(base, path + "?k=")[0] == 403, f"{path} with a wrong or empty key: 403")
    check(all(call(base, f"{path}?k={t}")[0] == 403 for t in dr.tokens), f"{path} with any seat key (person or AI): 403")
    check(call(base, f"{path}?k={bk}")[0] == 200, f"{path} with the broadcast key: 200")
refused = call(base, "/api/broadcast?k=nope")
check(refused[1] == {"error": "the broadcast needs its own link"} and not any(n in refused[2] for n in ALL_NAMES), "a refusal says only that")
gets = ("/api/state", "/api/banter", "/api/match", "/art/" + dr.picks[0][0]["id"])
check(all(call(base, f"{p}?k={bk}")[0] == 403 for p in gets), "the broadcast key opens no seat page: state, table talk, match link, art")
posts = {"/api/pick": {"id": dr.current[0][0]["id"]}, "/api/deck": {"main": {}}, "/api/deck/reset": {}, "/api/ready": {"ready": True},
         "/api/banter": {"text": "hello"}, "/api/banter/ask": {"who": "Claude"}, "/api/say": {"on": False}}
check(all(call(base, f"{p}?k={bk}", body)[0] == 403 for p, body in posts.items()), "the broadcast key cannot pick, edit a deck, ready up or talk")
check(not dr.pending and not dr.banter.lines, "and none of those attempts changed anything")
check(call(base, f"/api/state?k={next(iter(dr.tokens))}")[0] == 200, "a seat key still opens its own seat")

# ---- 3. table talk: the lines the seats already see, minus any that names a card
t0, t1 = list(dr.tokens)[:2]
secret = next(c["name"] for c in dr.current[0] if c["name"].lower() not in draft_banter.BASICS)   # a card sitting in a pack right now
call(base, f"/api/banter?k={t0}", {"text": "good luck everyone"})
code, _, raw = call(base, f"/api/banter?k={t1}", {"text": f"I just saw {secret} go by"})
check(code == 400 and not named(raw, secret), "a typed line that names a card is refused at the table itself, and the refusal does not repeat the name", f"{code} {raw[:120]}")
talk = call(base, f"/api/broadcast?k={bk}")
check([e["text"] for e in talk[1]["talk"]] == ["good luck everyone"], "a typed line is shown; a line that names a card is not", str(talk[1]["talk"]))
# The broadcast does not rest on the table's guard: it has its own filter (every card name, whatever its length or last character).
# A line that is ALREADY in the feed and names a card (an older server let typed lines through; a guard can have a hole) is put
# straight into the feed here, the way such a line would be sitting there, and must still never reach the stream.
dr.banter.lines.append({"id": dr.banter.lines[-1]["id"] + 1, "t": time.time(), "who": S.NAMES[1], "text": f"is that {secret.upper()}?", "ai": False, "kind": "chat"})
talk = call(base, f"/api/broadcast?k={bk}")
check(len(talk[1]["talk"]) == 1 and not named(talk[2], secret) and secret.lower() not in talk[2].lower(),
      "a line in the feed that names a card never reaches the stream, whatever the capitals or the punctuation around the name", str(talk[1]["talk"]))
check(len(call(base, f"/api/banter?k={t0}")[1]["lines"]) == 2, "(the seats still see every line that is in their feed, as before)")

# ---- 4. the page
code, _, page = call(base, f"/broadcast?k={bk}")
check(code == 200 and "<!doctype html>" in page, "the page is served")
check(not re.search(r"https?:|//[a-z]|@import|url\(|<link|<img|<iframe|\bsrc\s*=", page, re.I),
      "the page is self-contained: no other host, no font, image, stylesheet or script to fetch")
check(re.findall(r"fetch\(([^,)]*)", page) == ['"api/broadcast?k="+encodeURIComponent(K'], "its only request is its own JSON")
check(bk not in page and not any(t in page for t in dr.tokens) and not any(named(page, n) for n in ALL_NAMES), "the page itself holds no key and no card name")
check("innerHTML" not in page, "the page builds text nodes only (a chat line cannot inject markup)")
srv.shutdown(); srv.server_close()

# ---- 5. the flags and the file
check(S.broadcast_delay("end") == "end" and S.broadcast_delay("END") == "end" and S.broadcast_delay("0") == 0 and S.broadcast_delay("3") == 3, "--broadcast-delay takes a number or 'end'")
for junk in ("-1", "soon", "2.5"):
    try:
        S.broadcast_delay(junk); check(False, f"--broadcast-delay {junk} is refused")
    except ValueError:
        check(True, f"--broadcast-delay {junk} is refused")

# the real program, started the way a host starts it (no pick is made, so nothing is ever said and no model is asked for anything)
NEVER = {8800, 8801, 8820, 8830, 8883}
while True:
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]
    if port not in NEVER:
        break
live2 = Path(tempfile.mkdtemp(prefix="broadcast-test-live-"))
here = os.path.dirname(os.path.abspath(__file__))
env = {**os.environ, "DRAFT_LIVE": str(live2)}
p = subprocess.Popen([sys.executable, os.path.join(here, "draft_server.py"), "--seed", "5", "--port", str(port),
                      "--broadcast-reveal", "ai-live", "--broadcast-delay", "2"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    base, lf, d = f"http://127.0.0.1:{port}", live2 / "draft-links.txt", None
    for _ in range(100):
        try:
            key = re.search(r"^Broadcast: /broadcast\?k=(\S+)$", lf.read_text(), re.M).group(1)
            d = call(base, f"/api/broadcast?k={key}")[1]
            break
        except (OSError, AttributeError):
            time.sleep(0.1)
    check(d is not None and d["reveal"] == {"mode": "ai-live", "delay": 2} and d["round"] == 1 and d["pick"] == 1,
          "started for real with --broadcast-reveal ai-live --broadcast-delay 2: the Broadcast line in the links file opens it", str(d))
    check(stat.S_IMODE(lf.stat().st_mode) == 0o600, "the links file is 0600")
    lines = lf.read_text().splitlines()
    check(len(lines) == len(S.HUMANS) + 1 and lines[-1].startswith("Broadcast: "), "it holds one line per person and the Broadcast line", str(len(lines)))
    seat_key = lines[0].split("k=")[1]
    check(call(base, f"/api/broadcast?k={seat_key}")[0] == 403 and call(base, f"/api/state?k={key}")[0] == 403, "and there too: a seat key cannot open the broadcast, nor the reverse")
finally:
    p.terminate(); p.wait(timeout=10)                  # only the process this file started
bad_flag = subprocess.run([sys.executable, os.path.join(here, "draft_server.py"), "--broadcast-reveal", "live"], env=env, capture_output=True)
check(bad_flag.returncode == 2, "an unknown --broadcast-reveal is refused before anything starts", str(bad_flag.returncode))

print("failures: none" if not fails else f"failures: {fails}")
sys.exit(1 if fails else 0)
