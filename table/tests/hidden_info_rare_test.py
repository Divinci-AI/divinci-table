"""Hidden information in the RARE states (2026-11-10, the face-down leak follow-up): after every set-up below, EVERY route an outsider can read (GET, three
callers: Claude's seat key / no key / remote with no key; the event log read to its end) is searched for the name of every card of Michael's deck that is
not legitimately public (hand, library, face-down, tutored, bottomed ...). "Not public" = not face up on a battlefield, not in a graveyard or exile, not named
by the set-up as shown, and not a name Claude's own state also says (Claude's own hand legitimately names its own cards). Names that appear in the quiet
table's own text before anything happens (static pages) are noise and dropped.

States: the mulligan step (opponent's opening hand and bottom picks), a held spell, impulse-exiled cards, Zefram's top-card panel, combat with a face-down
attacker, the game-over summary, BACK (rewind) and its log text, a search effect (the chooser alone sees the options), "By hand" moves, face-down
permanents of every type (creature / land / artifact / enchantment) in every public view and in what a typed target slot says about them.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/hidden_info_rare_test.py
    HI_ONLY="mulligan|held"       only the sections whose title names one of these
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import retro_game4_test as R  # noqa: E402

check, M, C = R.check, "Michael", "Claude"
R.POOL.update({"Highly Illogical": 6, "Reckless Impulse": 6, "Perils of the Past": 6, "Shields Up!": 6, "Federation Resupply": 6, "First Contact": 6, "Rogue Artificial Intelligence": 6})
R.DECK_MICHAEL = R.deck_file("Michael-hidden")
R.DECK_CLAUDE = R.deck_file("Claude-hidden")
MICHAEL_DECK = {e["name"] for e in json.load(open(R.DECK_MICHAEL))["mainBoard"]}
BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest"}
ONLY = os.environ.get("HI_ONLY")

GET_ROUTES = ["/api/board3d", "/api/events", "/api/game/summary", "/api/game-over", "/api/life", "/api/phase", "/api/spectate", "/api/checklist", "/api/fair",
              "/api/highroll", "/api/history", "/api/placed", "/api/seat/claims", "/api/stage", "/api/todos", "/api/voice-config", "/api/players", "/api/state",
              "/api/board", "/api/lobby", "/api/host/rule", "/api/identity", "/api/captain", "/api/show", "/api/deckdecl", "/api/version",
              "/api/journal?seat=Michael", "/api/journal/due?seat=Michael", "/api/ai/state?seat=Michael", "/api/ai/hand?seat=Michael", "/api/hand?seat=Michael",
              "/api/hand?player=Michael", "/api/deckdecl?seat=Michael", "/api/seat/legal?seat=Michael", "/api/brain/state?seat=Michael",
              "/api/brain/state?seat=Claude", "/api/seat/legal?seat=Claude", "/api/ai/state?seat=Claude",
              "/board", "/watch", "/log", "/me", "/hand", "/table", "/lobby", "/stage", "/show", "/xr", "/3d", "/voice"]
CALLERS = ("Claude's key", "no key", "remote, no key")


class Table(R.Table):
    """The harness table with the decklists declared (the default publishes a seat's deck by design) and the mulligan step on when asked."""
    def __init__(self, mulligan=False, extra=()):
        old = {k: os.environ.get(k) for k in ("TABLE_MULLIGAN", "TABLE_MULLIGAN_SECS")}
        if mulligan:
            os.environ.update(TABLE_MULLIGAN="on", TABLE_MULLIGAN_SECS="0")
        try:
            super().__init__(extra=["--decklists", "declared", *extra])
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def claim(self):
        self.keys = {n: self.call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in (C, M)}
        self.call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)

    def raw(self, path, caller):
        h = {}
        if caller == CALLERS[0]:
            h = {"X-Seat-Key": self.keys[C], "X-Forwarded-For": "203.0.113.5", "User-Agent": "test-" + self.keys[C][:4]}
        elif caller == CALLERS[2]:
            h = {"X-Forwarded-For": "203.0.113.20"}
        try:
            with urllib.request.urlopen(urllib.request.Request(self.b + path, headers=h), timeout=20) as x:
                return x.status, x.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 — a route that hangs up is not a leak
            return 0, repr(e)

    def read_all(self, route, caller):
        """The whole reply. The event log is paged (300 at a time): every page."""
        if route != "/api/events":
            return self.raw(route, caller)
        since, out, code = 0, [], 200
        for _ in range(100):
            code, body = self.raw(f"/api/events?since={since}", caller)
            try:
                evs = json.loads(body).get("events", [])
            except Exception:  # noqa: BLE001
                evs = []
            out.append(body)
            if len(evs) < 300:
                break
            since = max(int(e["id"]) for e in evs)
        return code, "\n".join(out)

    def blobs(self):
        return {(r, w): self.read_all(r, w) for r in GET_ROUTES for w in CALLERS}

    def public_names(self):
        """Names that are public by the rules: a face-up permanent, a graveyard, an exile (face up), anywhere on the board."""
        out = set()
        for s in self.call("GET", "/api/board3d", host=False)[1].get("seats", []):
            out |= {p.get("name") for p in s.get("permanents", []) if p.get("name") and not p.get("face_down")}
            out |= set(s.get("graveyard", [])) | set(s.get("exile", [])) | {e.get("name") if isinstance(e, dict) else e for e in s.get("playable_from_exile", [])}
        return {n for n in out if n}

    def own_names(self, who=C):
        """Every name the seat's OWN state says (its hand, board, graveyard ...) without the block about the other seats."""
        st = self.state(who)
        return {n for n in MICHAEL_DECK if n in json.dumps({k: v for k, v in st.items() if k not in ("opponents", "public_boards", "announced_by_others", "recent_events")})}


QUIET = {}


def quiet_names(t):
    """Names the table's text carries before anything is dealt into view: static pages and code-given words."""
    key = "q"
    if key not in QUIET:
        txt = "\n".join(b for (_r, _w), (_c, b) in t.blobs().items())
        QUIET[key] = {n for n in MICHAEL_DECK if re.search(re.escape(n), txt, re.I)}
    return QUIET[key]


def scan(t, label, allowed=(), noise=None, show=True):
    """Search every route and caller for the hidden names; check()s once. Returns the hits."""
    secret = sorted(n for n in MICHAEL_DECK if len(n) > 4 and n not in BASICS and n not in set(allowed) and n not in t.public_names() and n not in t.own_names()
                    and n not in (noise if noise is not None else quiet_names(t)))
    hits = []
    for (r, w), (code, body) in t.blobs().items():
        got = [n for n in secret if re.search(re.escape(n), body, re.I)]
        if got:
            m = re.search(re.escape(got[0]), body, re.I)
            hits.append(f"{r} as {w} ({code}): {got[:4]} …{body[max(0, m.start() - 60):m.end() + 40]!r}")
    check(not hits, f"{label}: no route shows a hidden card of Michael's ({len(secret)} names x {len(GET_ROUTES)} routes x 3 callers)", "; ".join(hits[:4]))
    return hits


def topdeck(t, seat, name):
    t.hand(seat, name)
    c, d = t.call("POST", "/api/brain/topdeck", {"seat": seat, "name": name, "quiet": True})
    assert c == 200, d


def section(title, fn):
    if ONLY and not re.search(ONLY, title, re.I):
        return
    print(f"\n{title}", flush=True)
    try:
        fn()
    except Exception as e:  # noqa: BLE001 — a section that throws is one failure
        traceback.print_exc()
        check(False, f"{title}: raised {type(e).__name__}", e)


def facedown_dicts(obj, out=None):
    out = [] if out is None else out
    if isinstance(obj, dict):
        if obj.get("face_down") is True:
            out.append(obj)
        for v in obj.values():
            facedown_dicts(v, out)
    elif isinstance(obj, list):
        for v in obj:
            facedown_dicts(v, out)
    return out


# ───────────────────────────────────────── sections ─────────────────────────────────────────
def mulligan():
    t = Table(mulligan=True)
    try:
        t.claim()
        noise = quiet_names(t)
        t.call("POST", "/api/phase/next", {"by": C, "key": t.keys[C]}, key=t.keys[C])
        ph = t.call("GET", "/api/phase")[1]
        check(ph.get("player") is None and (ph.get("mulligan") or {}).get("on"), "the opening-hand step is open", ph.get("mulligan"))
        seen = {c["name"] for c in t.state(M)["hand"]}
        seat = lambda a, **b: t.call("POST", f"/api/brain/{a}", {"seat": M, **b}, key=t.keys[M])  # noqa: E731
        scan(t, "mulligan step, Michael has seven (nothing kept yet)", noise=noise)
        seat("mulligan")
        seen |= {c["name"] for c in t.state(M)["hand"]}
        seat("mulligan")
        names = [c["name"] for c in t.state(M)["hand"]]
        seen |= set(names)
        scan(t, "after two mulligans, before the bottom picks", noise=noise)
        c, d = seat("keep", bottom=[names[0], names[6]])
        check(c == 200, "Michael keeps five, two bottomed", (c, d))
        scan(t, "Michael has kept (bottom picks made), Claude has not", noise=noise)
        ev = t.call("GET", "/api/events?since=0", key=t.keys[C])[1]
        says = [e.get("text") for e in ev.get("events", []) if e.get("type") == "say"]
        check(says == ["Michael mulligans to six.", "Michael mulligans to five.", "Michael keeps five."], "the log says only mulligan / keep, with the size", says)
        bn = [n for n in (names[0], names[6]) if n not in BASICS and n not in t.own_names()]
        blob = "\n".join(b for (_r, _w), (_c, b) in t.blobs().items())
        check(not any(re.search(re.escape(n), blob, re.I) for n in bn if n not in noise), "the two bottomed cards are named nowhere", bn)
        c, d = t.call("POST", "/api/brain/keep", {"seat": C}, key=t.keys[C])
        t.wait(lambda p: p.get("player") is not None)
        scan(t, "game started after the last keep", noise=noise)
    finally:
        t.stop()


def facedown_types():
    t = Table()
    try:
        t.start()
        noise = quiet_names(t)
        t.hand(C, "Perils of the Past")
        want = [("Horta", "creature"), ("Blood Crypt", "land"), ("Combadge", "artifact"), ("The Naked Time", "enchantment")]
        for name, _kind in want:
            topdeck(t, M, name)
            c, d = t.call("POST", "/api/brain/manifest", {"seat": M, "n": 1})
            assert c == 200, d
        fd = [p for p in t.state(M)["permanents"] if p.get("face_down")]
        check(sorted(p["name"] for p in fd) == sorted(n for n, _ in want), "set-up: one face-down card of each type", [p["name"] for p in fd])
        scan(t, "face-down creature / land / artifact / enchantment", noise=noise)
        # no view tells them apart: the same keys, the same words
        shapes = {}
        for (r, w), (code, body) in t.blobs().items():
            if not body.startswith("{") and not body.startswith("["):
                continue
            try:
                j = json.loads(body)
            except Exception:  # noqa: BLE001
                continue
            fds = facedown_dicts(j)
            if len(fds) > 1:
                for f in fds:
                    shapes.setdefault((r, w), set()).add(json.dumps({k: v for k, v in f.items() if k not in ("id", "ref", "name")}, sort_keys=True))
        bad = {k: sorted(v) for k, v in shapes.items() if len(v) > 1}
        check(not bad, "every view lists the four face-down cards identically (no type shows through)", json.dumps(bad)[:500])
        opp = t.state(C)["opponents"][M]
        up_lands = sum(1 for p in t.state(M)["permanents"] if not p.get("face_down") and "Land" in (p.get("type") or ""))
        check(opp["lands"] == up_lands and len(opp["lands_list"]) == up_lands, "Claude's view: lands counts only the face-up ones", (opp["lands"], opp["lands_list"], up_lands))
        check(sum(1 for c_ in opp["creatures"] if c_["name"] == "a face-down 2/2") == 4, "…and every face-down card is a face-down 2/2 creature", opp["creatures"])
        check(all("face-down" in str(x) for x in opp["battlefield"] if x.startswith("a ")) and len(opp["battlefield"]) >= 4, "…and in the battlefield list too", opp["battlefield"])
        # typed target slots: what a "target artifact" / "target creature" slot offers must not depend on the card under a face-down permanent
        hand = {c_["name"]: c_ for c_ in t.state(C)["hand"]}
        pc = hand["Perils of the Past"]
        slots = [sl for ms in (pc.get("mode_slots") or []) for sl in ms] + (pc.get("slots") or [])
        by_kind = {}
        for sl in slots:
            labs = [o["label"] for o in sl.get("options", []) if "face-down" in o["label"]]
            by_kind.setdefault(tuple(sl.get("kinds") or []), []).append(labs)
        art = [x for k, v in by_kind.items() if "Artifact" in k for x in v]
        cre = [x for k, v in by_kind.items() if "Creature" in k and "Artifact" not in k for x in v]
        check(art and all(not x for x in art), "a 'target artifact' slot offers no face-down permanent (they are creatures, whatever is underneath)", by_kind)
        check(cre and all(len(x) == 4 and len(set(re.sub(r"#\d+", "#", y) for y in x)) == 1 for x in cre), "a 'target creature' slot offers all four, worded alike", by_kind)
    finally:
        t.stop()


def zefram():
    t = Table()
    try:
        t.start()
        noise = quiet_names(t)
        t.lands(M, 4, "Island")
        t.onto(M, "Zefram Cochrane, Warp Pioneer")
        t.decline(M)
        topdeck(t, M, "Open Communications")
        check((t.state(M).get("library_top") or {}).get("name") == "Open Communications", "set-up: Michael may look at his top card", t.state(M).get("library_top"))
        check(t.state(C).get("library_top") is None, "Claude's own state has no top card", "")
        scan(t, "Zefram's panel is up (top card known to Michael only)", noise=noise)
        c, d = t.call("GET", f"/api/brain/state?seat={M}", key=t.keys[C])
        check(c == 403, "Claude's key can't read Michael's state (the panel lives there)", c)
    finally:
        t.stop()


def impulse():
    t = Table()
    try:
        t.start()
        noise = quiet_names(t)
        t.hand(M, "Reckless Impulse")
        t.lands(M, 2, "Mountain")
        topdeck(t, M, "Quark, Humble Proprietor")      # the card BELOW the two that get exiled
        topdeck(t, M, "Moopsy")
        topdeck(t, M, "Mugato")
        c, d = t.act(M, "cast", name="Reckless Impulse")
        check(c == 200, "Michael casts Reckless Impulse", (c, d))
        ex = t.board(M).get("exile") or []
        check(len(ex) == 2, "the two exiled cards are public, face up", ex)
        scan(t, "impulse: the exiled two are public, nothing beyond", allowed=set(ex), noise=noise)
        below = [c["name"] if isinstance(c, dict) else str(c) for c in t.call("POST", "/api/brain/peek", {"seat": M, "n": 1})[1].get("private", {}).get("top") or []]
        txt = "\n".join(b for (_r, _w), (_c, b) in t.blobs().items())
        check(below and not any(re.search(re.escape(n), txt, re.I) for n in below if n not in set(ex) | t.public_names() | t.own_names() | noise), "the card below them is named nowhere", below)
    finally:
        t.stop()


def held():
    t = Table()
    try:
        t.start()
        noise = quiet_names(t)
        t.turn(C)
        t.lands(C, 2, "Island")
        t.hand(C, "Highly Illogical")
        t.turn(M)
        t.lands(M, 1, "Plains")
        t.hand(M, "Tricorder")
        topdeck(t, M, "Sickbay")
        c, d = t.call("POST", "/api/brain/cast", {"seat": M, "name": "Tricorder"}, key=t.keys[M])
        ph = t.call("GET", "/api/phase")[1]
        check(c == 200 and (ph.get("held") or {}).get("spell") == "Tricorder", "Michael's spell is held for Claude's answer", (c, ph.get("held"), d.get("error")))
        print("   (the window names who may respond:", ph.get("held"), ")")
        scan(t, "a held spell (Tricorder is public, the rest of Michael's hand is not)", allowed={"Tricorder"}, noise=noise)
        t.call("POST", "/api/brain/pass", {"seat": C}, key=t.keys[C])
        time.sleep(0.5)
        scan(t, "after the window closes", allowed={"Tricorder"}, noise=noise)
    finally:
        t.stop()


def combat_facedown():
    t = Table()
    try:
        t.start()
        noise = quiet_names(t)
        topdeck(t, M, "Troubling Tribbles")
        c, d = t.call("POST", "/api/brain/manifest", {"seat": M, "n": 1})
        fid = [p["id"] for p in t.state(M)["permanents"] if p.get("face_down")][-1]
        t.turn(C)
        t.turn(M)                                       # no longer summoning sick
        c, d = t.act(M, "attack", assign={f"#{fid}": C})
        check(c == 200, "Michael attacks with the face-down creature", (c, d))
        inc = t.state(C).get("incoming") or []
        check(inc and "Tribbles" not in json.dumps(inc), "Claude's incoming-attack list names no card", inc)
        print("   (incoming:", json.dumps(inc)[:200], ")")
        scan(t, "combat: a face-down attacker, blocks not yet declared", noise=noise)
        t.act(C, "block", attacker=f"#{fid}")
        scan(t, "combat: blocks declared (taken), before damage", noise=noise)
        t.act(M, "damage")
        scan(t, "combat: after damage", noise=noise)
    finally:
        t.stop()


def game_over():
    t = Table()
    try:
        t.start()
        noise = quiet_names(t)
        topdeck(t, M, "Neelix, Moral Support")
        t.turn(C)
        t.call("POST", "/api/brain/life", {"seat": C, "player": M, "delta": -30, "text": "Phaser barrage."})
        time.sleep(0.6)
        over = t.call("GET", "/api/game/summary", host=False)[1]
        check(over.get("over") is True, "the game is over", str(over)[:200])
        scan(t, "game over: summary, /api/game-over and every page", noise=noise)
    finally:
        t.stop()


def rewind():
    t = Table()
    try:
        t.start()
        noise = quiet_names(t)
        topdeck(t, M, "Pet Sehlat")
        t.hand(M, "Vexing Drone")
        t.lands(M, 3, "Plains")
        c, d = t.act(M, "cast", name="Vexing Drone")
        t.turn(C)
        c, d = t.call("POST", "/api/phase/back", {"by": "host"})
        t.call("POST", "/api/phase/back", {"by": "host"})
        scan(t, "after BACK (rewind) twice: log, history and every page", allowed={"Vexing Drone"}, noise=noise)
        txt = "\n".join(str(e.get("text")) for e in t.events())
        check("Pet Sehlat" not in txt, "the rewind text names no hidden card", "")
    finally:
        t.stop()


def search_effect():
    t = Table()
    try:
        t.start()
        noise = quiet_names(t)
        t.lands(M, 2, "Plains")
        t.hand(M, "Federation Resupply")
        c, d = t.act(M, "cast", name="Federation Resupply")
        pend = t.state(M).get("pending_choices") or []
        print("   (cast:", c, str(d)[:200], "| pending:", json.dumps(pend)[:300], ")")
        opts = [o.get("label") for p in pend for o in (p.get("slot") or {}).get("options", [])]
        scan(t, "a search effect is waiting on Michael's choice (options on his side only)", noise=noise | {x for x in MICHAEL_DECK if x in str(opts) and False})
        check(not (t.state(C).get("opponents", {}).get(M, {}) and any(x in json.dumps(t.state(C).get("opponents")) for x in MICHAEL_DECK if x in str(opts) and x not in t.public_names() and x not in noise)),
              "Claude's opponents block lists none of the offered cards", "")
        if pend and opts:
            first = pend[0]["slot"]["options"][0]
            c, d = t.call("POST", "/api/brain/choose", {"seat": M, "id": pend[0]["id"], "target": {k: v for k, v in first.items() if k != "label"}}, key=t.keys[M])
            print("   (choose:", c, str(d)[:240], ")")
            said = [str(e.get("text")) for e in t.events()[-8:] if e.get("type") == "say"]
            print("   (log:", said, ")")
            revealed = {n for n in MICHAEL_DECK if any(f"reveal {n}" in x for x in said)}      # "reveal it" is the card's own text: that name is public
            check(len(revealed) <= 1, "the log names at most the one revealed card", revealed)
            scan(t, "after the search resolves (only the card's own reveal names the result)", allowed=revealed, noise=noise)
    finally:
        t.stop()


def by_hand():
    t = Table()
    try:
        t.start()
        noise = quiet_names(t)
        t.hand(M, "Mugato")
        t.hand(M, "Moopsy")
        ev0 = t.events()[-1]["id"] if t.events() else 0
        t.act(M, "manual", op="move", ref="hand:Mugato", to="library-top")
        t.act(M, "manual", op="move", ref="library-top", to="hand")
        t.act(M, "manual", op="move", ref="hand:Moopsy", to="library-bottom")
        said = [str(e.get("text")) for e in t.events() if e["id"] > ev0 and e.get("type") == "say"]
        print("   (log:", said, ")")
        check(said and not any(n in " ".join(said) for n in ("Mugato", "Moopsy")), "hidden-zone moves are said as 'a card'", said)
        scan(t, "after 'By hand' moves between hand and library", noise=noise)
    finally:
        t.stop()


def private_looks():
    t = Table()
    try:
        t.start()
        noise = quiet_names(t)
        for n in ("Moopsy", "Mugato", "Pet Sehlat"):
            topdeck(t, M, n)
        c, d = t.call("POST", "/api/brain/peek", {"seat": M, "n": 3})
        top = [x if isinstance(x, str) else x.get("name") for x in (d.get("private") or {}).get("top") or []]
        check(len(top) == 3, "Michael peeks at his top three", d)
        scan(t, "after a private peek at the top three", noise=noise)
        c, d = t.call("POST", "/api/brain/draw", {"seat": M, "n": 2})
        scan(t, "after drawing two (the draw is said as a count)", noise=noise)
        t.lands(M, 5, "Swamp")
        t.hand(M, "Rogue Artificial Intelligence")
        t.lands(M, 6, "Swamp")
        topdeck(t, M, "Neelix, Moral Support")
        c, d = t.act(M, "cast", name="Rogue Artificial Intelligence")
        pend = t.state(M).get("pending_choices") or []
        print("   (cast", c, str(d.get("error"))[:100], "pending:", json.dumps(pend)[:300], ")")
        if pend:
            opts = (pend[0].get("slot") or {}).get("options") or []
            sur = next((o for o in opts if "urveil" in o.get("label", "")), None)
            if sur:
                t.call("POST", "/api/brain/choose", {"seat": M, "id": pend[0]["id"], "target": {k: v for k, v in sur.items() if k != "label"}}, key=t.keys[M])
        st = t.state(M)
        print("   (pending_surveil:", st.get("pending_surveil"), "surveil_cards:", json.dumps(st.get("surveil_cards"))[:200], ")")
        check(bool(st.get("pending_surveil")), "set-up: Michael has a surveil waiting (the cards are shown to him alone)", st.get("pending_surveil"))
        scan(t, "a surveil is waiting on Michael (the top cards are his alone)", noise=noise)
        sc = [x.get("name") if isinstance(x, dict) else x for x in st.get("surveil_cards") or []]
        c, d = t.act(M, "surveil", graveyard=sc[:1], top_order=sc[1:])
        print("   (surveil:", c, str(d.get("said"))[:200], ")")
        scan(t, "after the surveil (one card to the graveyard is public, the rest stay hidden)", allowed=set(sc[:1]), noise=noise)
    finally:
        t.stop()


def host_rule():
    t = Table()
    try:
        t.start()
        noise = quiet_names(t)
        topdeck(t, M, "Mugato")
        t.call("POST", "/api/brain/manifest", {"seat": M, "n": 1})
        fid = [p["id"] for p in t.state(M)["permanents"] if p.get("face_down")][-1]
        ev0 = t.events()[-1]["id"] if t.events() else 0
        c, d = t.call("POST", "/api/host/rule", {"seat": M, "ref": f"#{fid}", "action": "damage", "n": 1})
        print("   (host rule damage:", c, json.dumps(d)[:240], ")")
        check(c == 200 and "Mugato" not in json.dumps(d), "a house-rule damage to a face-down permanent doesn't name it", (c, d))
        said = " ".join(str(e.get("text")) for e in t.events() if e["id"] > ev0)
        check("Mugato" not in said, "…nor does the log", said[:200])
        scan(t, "after a host rule aimed at a face-down permanent", noise=noise)
    finally:
        t.stop()


def main():
    for title, fn in (("Mulligan step", mulligan), ("Face-down permanents of every type", facedown_types), ("Zefram's top-card panel", zefram),
                      ("Impulse exile", impulse), ("Held spell", held), ("Combat with a face-down attacker", combat_facedown), ("Game over", game_over),
                      ("BACK / rewind", rewind), ("Search effect", search_effect), ("By hand moves", by_hand), ("Private looks", private_looks), ("Host rule", host_rule)):
        section(title, fn)
    print(f"\n{'all passed' if not R.FAILS else f'{len(R.FAILS)} FAILED: {R.FAILS}'}")
    sys.exit(1 if R.FAILS else 0)


if __name__ == "__main__":
    main()
