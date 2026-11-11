"""Impulse draw (2026-11-09, before the Star Trek draft): "exile cards from the top of your library … you may play that card", and quest counters.

Exploratory Mission (Landfall: a quest counter, exile until a nonland card, play it this turn, {R}{R} at five counters), Reckless Impulse (the top
two, until the end of your next turn, lands too), Silicate Surveyor (enters: the top card, until the end of your next turn). The engine first, on
its own (each window, the counter, the pool, an empty library, a game pickled before any of this existed), then on tables of their own on free
ports, through the route a player uses (the seat's own key): what the legal list offers, what another seat may and may not do, what everyone
sees (the exiled cards, face up) and what nobody sees (the rest of the library). Last, a snapshot written by the code before this change
(table-fix, REVIEW_BASE) restores under this one and the new mechanics work on it.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 ~/.venvs/table/bin/python table/tests/impulse_test.py
    IMPULSE_ONLY="engine|mission"   only the sections whose title names one of these
    REVIEW_BASE=<dir>               the pre-change table for the snapshot check (default: the table-fix copy next to this one)
"""
from __future__ import annotations

import json
import os
import pickle
import sys
import tempfile
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import retro_game4_test as R  # noqa: E402
import player  # noqa: E402

check, CARDS = R.check, R.CARDS
M, C = "Michael", "Claude"
R.POOL.update({"Exploratory Mission": 6, "Reckless Impulse": 6, "Silicate Surveyor": 6, "Starfleet Security": 6, "Moopsy": 6,
               "Encounter the Unknown": 2, "Tasha Yar, Out of Time": 2})
_free0 = R.free_port
R.free_port = lambda: next(p for p in iter(_free0, None) if p not in (8800, 8801, 8820, 8830))   # never a live game's port
R.DECK_MICHAEL = R.deck_file("Michael-impulse")      # Table() reads these when it starts
R.DECK_CLAUDE = R.deck_file("Claude-impulse")


def card(n):
    return dict(CARDS[n])


def seat(name="Michael", lands=0):
    v = player.VirtualPlayer(R.DECK_MICHAEL, name, seed=3)
    v.hand.clear()
    v.library.clear()
    v.turn = 1
    for _ in range(lands):
        v.battlefield.append(player.Perm(card("Mountain"), sick=False))
    return v


def untap(v):
    for p in v.battlefield:
        p.tapped = False


def names(cards):
    return [c["name"] for c in cards]


def playable(v):
    return [(e["card"]["name"], e["until"]) for e in v.impulse_zone()]


# ── the engine on its own ────────────────────────────────────────────────────────────────────────────────────────────────────────
def engine_mission():
    print("Engine: Exploratory Mission")
    v = seat(lands=3)
    mission = player.Perm(card("Exploratory Mission"), sick=False)
    v.battlefield.append(mission)
    v.library = [card("Moopsy"), card("Starfleet Security"), card("Plains"), card("Mountain")]       # the END is the top
    v.hand = [card("Mountain")]
    said = " ".join(v.manual_land("Mountain"))
    check(mission.quest == 1, "a land enters: a quest counter on it", mission.quest)
    check(names(v.exile_zone()) == ["Mountain", "Plains", "Starfleet Security"], "exile until a nonland card: the lands on the way are exiled too", names(v.exile_zone()))
    check(playable(v) == [("Starfleet Security", "eot")], "only the nonland card may be played, and only this turn", playable(v))
    check(names(v.library) == ["Moopsy"], "nothing below it is touched", names(v.library))
    check("Moopsy" not in said and "Starfleet Security" in said and not v.todo, "the table hears what was exiled, nothing more, and nothing is left by hand", (said, v.todo))
    check([o[1]["name"] for o in v.exile_castable()] == ["Starfleet Security"] and not v.castable(), "it is castable from exile (and not from the hand)", v.exile_castable())
    c = v.impulse_card("Starfleet Security")
    v2 = seat("Claude", lands=3)
    try:
        v2.manual_cast("Starfleet Security", from_exile=True)
        other = False
    except player.IllegalAction:
        other = True
    check(other, "another seat can't cast it")
    said = v.manual_cast("Starfleet Security", from_exile=True)
    check(any(p.card is c for p in v.battlefield) and not v.impulse_zone() and names(v.exile_zone()) == ["Mountain", "Plains"],
          "cast from exile: it's on the battlefield and gone from exile; the lands stay exiled", (said, names(v.exile_zone())))
    # the window closes with the turn
    untap(v)
    v.land_played = False
    v.library = [card("Moopsy"), card("Starfleet Security")]
    v.hand = [card("Mountain")]
    v.manual_land("Mountain")
    check(playable(v) == [("Starfleet Security", "eot")] and mission.quest == 2, "a second land: a second counter, a second card", (playable(v), mission.quest))
    said = v.clear_until_eot({"Michael"})
    check(not v.impulse_zone() and "Starfleet Security" in names(v.exile_zone()) and said, "end of turn: no longer playable, and it stays in exile (said)", (said, names(v.exile_zone())))
    try:
        v.manual_cast("Starfleet Security", from_exile=True)
        late = False
    except player.IllegalAction as e:
        late = "no longer" in str(e)
    check(late, "casting it after the turn is refused, saying why")
    # five counters: {R}{R} in the pool, spent first
    mission.quest = 3
    v.begin_turn()
    untap(v)
    for k in (4, 5):
        v.land_played = False
        v.library = [card("Moopsy")]
        v.hand = [card("Mountain")]
        v.manual_land("Mountain")
        check(mission.quest == k and [f.card["mana"] for f in v.floating()] == (["R", "R"] if k == 5 else []), f"{k} quest counters: " + ("{R}{R} added" if k == 5 else "no mana yet"), (mission.quest, v.floating()))
        v.clear_until_eot({"Michael"}) if k == 4 else None
    lands_untapped = sum(1 for p in v.battlefield if p.is_("Land") and not p.tapped)
    check(v.available_mana() == lands_untapped + 2, "the pool counts as mana it can spend", (v.available_mana(), lands_untapped))
    v.hand = [card("Starfleet Security")]
    v.manual_cast("Starfleet Security")
    check([f.card["mana"] for f in v.floating()] == ["R"] and sum(1 for p in v.battlefield if p.is_("Land") and not p.tapped) == lands_untapped,
          "a {R} spell is paid from the pool first, tapping no land (one {R} left)", (len(v.floating()), lands_untapped))
    v.land_played = False
    v.library = [card("Moopsy")]
    v.hand = [card("Mountain")]
    v.manual_land("Mountain")
    check(len(v.floating()) == 3, "the sixth counter adds {R}{R} again (five OR MORE), to what is left in the pool", len(v.floating()))
    v.clear_until_eot({"Michael"})
    check(not v.floating(), "the pool empties when the turn ends", v.floating())
    player.STEP_KEY[0] = lambda: ("Michael", 3)
    try:
        v.add_mana("RR")
        k1 = len(v.floating())
        player.STEP_KEY[0] = lambda: ("Michael", 4)
        check(k1 == 2 and not v.floating(), "with a server, the pool empties when the step changes", (k1, v.floating()))
    finally:
        player.STEP_KEY[0] = None
    # an empty library
    v.land_played = False
    v.library = []
    v.hand = [card("Mountain")]
    try:
        said = " ".join(v.manual_land("Mountain"))
        ok = "library is empty" in said and not v.impulse_zone()
    except Exception as e:  # noqa: BLE001
        said, ok = repr(e), False
    check(ok, "an empty library: nothing exiled, nothing to play, no crash", said)
    v.land_played = False
    v.library = [card("Plains"), card("Mountain")]
    v.hand = [card("Mountain")]
    said = " ".join(v.manual_land("Mountain"))
    check(not v.library and not v.impulse_zone() and "nothing to play" in said, "only lands left: all of them exiled, nothing to play", said)


def engine_impulse():
    print("Engine: Reckless Impulse, Silicate Surveyor, Encounter the Unknown, Tasha Yar")
    v = seat(lands=4)
    v.library = [card("Moopsy"), card("Starfleet Security"), card("Mountain")]
    v.hand = [card("Reckless Impulse"), card("Plains")]
    v.manual_cast("Reckless Impulse")
    check(sorted(playable(v)) == [("Mountain", "next"), ("Starfleet Security", "next")] and names(v.library) == ["Moopsy"], "the top two, playable until the end of my next turn", playable(v))
    check([o[1]["name"] for o in v.exile_castable()] == ["Starfleet Security"], "the spell is castable from exile; the land isn't a cast", v.exile_castable())
    v.manual_land("Mountain", from_exile=True)
    check(v.land_played and ("Mountain", "next") not in playable(v), "the exiled land is played as the turn's land drop", playable(v))
    try:
        v.manual_land("Plains")
        again = False
    except player.IllegalAction:
        again = True
    check(again, "…so a land from the hand is refused this turn")
    v.clear_until_eot({"Michael"})
    check(playable(v) == [("Starfleet Security", "next")], "my turn ends: still playable (until the end of my NEXT turn)", playable(v))
    v.clear_until_eot({"Claude"})
    check(playable(v) == [("Starfleet Security", "next")], "the opponent's turn ends: still playable", playable(v))
    v.begin_turn()
    check(playable(v) == [("Starfleet Security", "next")], "my next turn begins: still playable", playable(v))
    said = v.clear_until_eot({"Michael"})
    check(not playable(v) and "Starfleet Security" in names(v.exile_zone()) and said, "the end of my next turn: no longer playable, still in exile", (said, names(v.exile_zone())))
    # Surveyor: enters, the top card
    v = seat(lands=5)
    v.library = [card("Moopsy"), card("Plains")]
    v.hand = [card("Silicate Surveyor")]
    said = " ".join(v.manual_cast("Silicate Surveyor"))
    check(playable(v) == [("Plains", "next")] and names(v.library) == ["Moopsy"] and not v.todo, "Silicate Surveyor enters: the top card, until the end of my next turn", (said, v.todo))
    # Encounter the Unknown's Come in Peace (the same words as Reckless Impulse)
    v = seat(lands=3)
    v.library = [card("Moopsy"), card("Starfleet Security"), card("Mountain")]
    v.hand = [card("Encounter the Unknown")]
    v.manual_cast("Encounter the Unknown", modes=[1])
    check(sorted(playable(v)) == [("Mountain", "next"), ("Starfleet Security", "next")], "Encounter the Unknown, Come in Peace: the same", playable(v))
    # Tasha Yar dies
    v = seat(lands=0)
    v.library = [card("Moopsy"), card("Starfleet Security")]
    t = player.Perm(card("Tasha Yar, Out of Time"), sick=False)
    v.battlefield.append(t)
    v.move(f"#{t.id}", "graveyard")
    check(playable(v) == [("Starfleet Security", "next")], "Tasha Yar dies: the top card, until the end of my next turn", playable(v))
    # the views
    bv = player.brain_view(v)
    pub = v.public()
    check([x["name"] for x in bv["exile_playable"]] == ["Starfleet Security"] and bv["exile_playable"][0]["until"] == "until the end of your next turn",
          "the seat's own view lists it, with when the window closes", bv["exile_playable"])
    check(pub["exile"] == ["Starfleet Security"] and pub["playable_from_exile"] == [{"name": "Starfleet Security", "until": "until the end of your next turn"}]
          and "Moopsy" not in json.dumps(pub), "the public view shows the exiled card (face up) and nothing of the library", pub.get("exile"))


def engine_old_pickle():
    print("Engine: a game pickled before any of this loads")
    v = seat(lands=4)
    mission = player.Perm(card("Exploratory Mission"), sick=False)
    v.battlefield.append(mission)
    for k in ("exile", "impulse", "mana_pool"):
        v.__dict__.pop(k, None)
    mission.__dict__.pop("quest", None)                 # as a Perm pickled before the field existed: the class default answers
    w = pickle.loads(pickle.dumps(v))
    try:
        bv = player.brain_view(w)
        w.public()
        w.clear_until_eot({"Michael"})
        w.library = [card("Moopsy"), card("Starfleet Security")]
        w.hand = [card("Mountain")]
        w.manual_land("Mountain")
        m2 = next(p for p in w.battlefield if p.name == "Exploratory Mission")
        ok = bv["exile_playable"] == [] and m2.quest == 1 and playable(w) == [("Starfleet Security", "eot")]
        detail = (m2.quest, playable(w))
    except Exception as e:  # noqa: BLE001
        ok, detail = False, traceback.format_exc()[-400:]
    check(ok, "an old pickle: empty zones, no quest field, and the new mechanics work on it", detail)


def engine_coverage():
    print("card_coverage")
    import card_coverage as cv
    for n in ("Exploratory Mission", "Reckless Impulse", "Silicate Surveyor", "Tasha Yar, Out of Time"):
        st = [(s, ln[:50]) for ln, sh, s in cv.card_lines(CARDS[n]) if "exile the top" in ln.lower() or "exile cards" in ln.lower()]
        check(st and all(s == "handled" for s, _ in st), f"{n}: its impulse line reports handled", st)
    st = [s for ln, sh, s in cv.card_lines(CARDS["Worf, Defiant Commander"])] if "Worf, Defiant Commander" in CARDS else ["manual"]
    check("manual" in st, "Worf's 'exile the top X' (X = opponents attacked) stays by hand: not claimed", st)


# ── on a table, through the seat's own key ───────────────────────────────────────────────────────────────────────────────────────
def stack_top(t, s, top_first):
    """Put these on top of the seat's library, the first named on top (search shuffles, so this is the last set-up step)."""
    for n in top_first:
        t.hand(s, n)
    for n in reversed(top_first):
        c, d = t.call("POST", "/api/brain/topdeck", {"seat": s, "name": n, "quiet": True})
        assert c == 200, d


def seat_b3(t, s):
    return next(x for x in t.call("GET", "/api/board3d", host=False)[1]["seats"] if x["name"] == s)


def xs(st):
    return [(x["name"], x["until"]) for x in st.get("exile_playable") or []]


def last_event(t):
    ev = t.events()
    return ev[-1]["id"] if ev else 0


def table_mission():
    print("Table: Exploratory Mission, the window ends with the turn, another seat, the library stays hidden, Silicate Surveyor")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 5, "Mountain")
        mid = t.onto(M, "Exploratory Mission")
        t.hand(M, "Mountain")
        stack_top(t, M, ["Mountain", "Plains", "Starfleet Security", "Moopsy"])
        ev0 = last_event(t)
        c, d = t.act(M, "land", name="Mountain")
        check(c == 200, "Michael plays a land (his own key)", (c, d))
        st = t.state(M)
        mp = next(p for p in st["permanents"] if p["id"] == mid)
        check(mp.get("quest") == 1, "Exploratory Mission has a quest counter", mp)
        check(xs(st) == [("Starfleet Security", "this turn")] and st["exile_playable"][0]["castable_now"], "his page lists Starfleet Security, castable from exile, this turn", st.get("exile_playable"))
        b = seat_b3(t, M)
        check(b.get("exile", [])[-3:] == ["Mountain", "Plains", "Starfleet Security"] and b.get("playable_from_exile") == [{"name": "Starfleet Security", "until": "this turn"}],
              "the public board shows all three exiled cards, face up, and which one he may play", (b.get("exile"), b.get("playable_from_exile")))
        lg = [a for a in t.legal(M)["actions"] if a["body"].get("from_exile")]
        check(any(a["action"] == "cast" and a["body"]["name"] == "Starfleet Security" for a in lg), "the legal list offers casting it from exile", lg)
        opp = t.state(C).get("opponents", {}).get(M, {})
        check("Starfleet Security" in opp.get("exile", []) and opp.get("playable_from_exile"), "Claude sees the exiled cards (his opponents view)", opp.get("exile"))
        check(not [a for a in t.legal(C)["actions"] if a["body"].get("from_exile")], "Claude's legal list offers no exile play")
        c, d = t.act(C, "cast", name="Starfleet Security", from_exile=True)
        check(c != 200 and "exile" in str(d.get("error", "")), "Claude can't cast Michael's exiled card", (c, d))
        pub = json.dumps(t.call("GET", "/api/board3d", host=False)[1]) + json.dumps([e for e in t.events() if e["id"] > ev0]) + json.dumps(t.state(C).get("opponents"))
        check("Moopsy" not in pub, "the next card of his library (Moopsy) is named nowhere: board, log, the other seat's view", "Moopsy")
        t.turn(C)
        st = t.state(M)
        check(not xs(st) and "Starfleet Security" in seat_b3(t, M).get("exile", []), "his turn ended: no longer playable, still in exile", (xs(st), seat_b3(t, M).get("exile")))
        check(any("no longer play Starfleet Security" in str(e.get("text")) for e in t.events() if e["id"] > ev0), "…and the table said so")
        t.turn(M)
        c, d = t.act(M, "cast", name="Starfleet Security", from_exile=True)
        check(c != 200 and "no longer" in str(d.get("error", "")), "on his next turn, casting it is refused", (c, d))
        check(not [a for a in t.legal(M)["actions"] if a["body"].get("from_exile")], "…and it is not on his legal list")
        # Silicate Surveyor: enters, the top card; and casting a card from exile through the route
        t.hand(M, "Silicate Surveyor")
        stack_top(t, M, ["Starfleet Security", "Moopsy"])
        c, d = t.act(M, "cast", name="Silicate Surveyor")
        st = t.state(M)
        check(c == 200 and xs(st) == [("Starfleet Security", "until the end of your next turn")], "Silicate Surveyor enters: the top card, until the end of his next turn", (c, d, xs(st)))
        c, d = t.act(M, "cast", name="Starfleet Security", from_exile=True)
        st = t.state(M)
        check(c == 200 and any(p["name"] == "Starfleet Security" for p in st["permanents"]) and not xs(st), "he casts it from exile (paying for it): it's on the battlefield", (c, d))
        check(seat_b3(t, M).get("exile", []).count("Starfleet Security") == 1, "the expired copy is still in exile; the cast one left", seat_b3(t, M).get("exile"))
    finally:
        t.stop()


def table_reckless():
    print("Table: Reckless Impulse, a land from exile, until the end of the next turn")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 3, "Mountain")
        t.hand(M, "Reckless Impulse")
        t.hand(M, "Plains")
        stack_top(t, M, ["Mountain", "Starfleet Security", "Moopsy"])
        c, d = t.act(M, "cast", name="Reckless Impulse")
        st = t.state(M)
        check(c == 200 and sorted(xs(st)) == [("Mountain", "until the end of your next turn"), ("Starfleet Security", "until the end of your next turn")],
              "Reckless Impulse: the top two, until the end of his next turn", (c, d, xs(st)))
        lg = t.legal(M)["actions"]
        check(any(a["action"] == "land" and a["body"].get("from_exile") and a["body"]["name"] == "Mountain" for a in lg), "the legal list offers the exiled Mountain as his land", [a for a in lg if a["action"] == "land"])
        c, d = t.act(M, "land", name="Mountain", from_exile=True)
        st = t.state(M)
        check(c == 200 and st["land_played"] and xs(st) == [("Starfleet Security", "until the end of your next turn")], "he plays it: that was his land for the turn", (c, d, xs(st)))
        c, d = t.act(M, "land", name="Plains")
        check(c != 200, "a land from his hand is now refused", (c, d))
        check(not [a for a in t.legal(M)["actions"] if a["action"] == "land"], "…and no land is offered")
        t.turn(C)
        check(xs(t.state(M)) == [("Starfleet Security", "until the end of your next turn")], "his turn ended: Starfleet Security is still playable", xs(t.state(M)))
        c, d = t.act(C, "cast", name="Starfleet Security", from_exile=True)
        check(c != 200, "Claude, on his own turn, can't cast it", (c, d))
        t.turn(M)
        st = t.state(M)
        lg = [a for a in t.legal(M)["actions"] if a["body"].get("from_exile")]
        check(xs(st) == [("Starfleet Security", "until the end of your next turn")] and any(a["body"]["name"] == "Starfleet Security" for a in lg),
              "his next turn: still playable, and offered", (xs(st), lg))
        t.turn(C)
        st = t.state(M)
        check(not xs(st) and "Starfleet Security" in seat_b3(t, M).get("exile", []), "the end of his next turn: no longer playable, still in exile", (xs(st), seat_b3(t, M).get("exile")))
        t.turn(M)
        c, d = t.act(M, "cast", name="Starfleet Security", from_exile=True)
        check(c != 200 and "no longer" in str(d.get("error", "")), "after that, casting it is refused", (c, d))
    finally:
        t.stop()


def table_rr():
    print("Table: the fifth quest counter adds {R}{R}, and the pool empties with the step")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 2, "Mountain")
        mid = t.onto(M, "Exploratory Mission")
        for _ in range(4):                               # lands entering from a search are lands entering: Landfall each time
            c, d = t.call("POST", "/api/brain/search", {"seat": M, "name": "Mountain", "to": "battlefield", "quiet": True})
            assert c == 200, d
            t.settle_setup()
        t.hand(M, "Mountain")
        st = t.state(M)
        check(next(p for p in st["permanents"] if p["id"] == mid).get("quest") == 4 and not st.get("floating_mana"), "four lands: four quest counters, no mana yet", next(p for p in st["permanents"] if p["id"] == mid))
        c, d = t.act(M, "land", name="Mountain")
        st = t.state(M)
        check(c == 200 and st.get("floating_mana") == ["R", "R"] and sum("mana pool" in x for x in st.get("mana_sources") or []) == 2,
              "the fifth: {R}{R} in his mana pool, counted with his mana", (c, st.get("floating_mana"), st.get("mana_sources")))
        t.hand(M, "Starfleet Security")
        untapped = sum(1 for p in st["permanents"] if "Land" in (p.get("type") or "") and not p["tapped"])
        c, d = t.act(M, "cast", name="Starfleet Security")
        st = t.state(M)
        check(c == 200 and st.get("floating_mana") == ["R"] and sum(1 for p in st["permanents"] if "Land" in (p.get("type") or "") and not p["tapped"]) == untapped,
              "a {R} spell in the same step is paid from the pool, no land tapped", (c, d, st.get("floating_mana")))
        c, d = t.act(M, "next")
        check(c == 200 and not t.state(M).get("floating_mana"), "the step ends: the pool empties", (c, t.state(M).get("floating_mana")))
    finally:
        t.stop()


def table_old_snapshot():
    print("Table: a snapshot from the code before this change (table-fix) restores here, and impulse draw works on it")
    base = Path(os.environ.get("REVIEW_BASE") or HERE.parent / "table-fix").expanduser()
    if not (base / "server.py").exists() or base.resolve() == HERE.resolve() or "def impulse_zone" in (base / "player.py").read_text():
        check(False, "the pre-change table is there (REVIEW_BASE)", str(base))
        return
    o = R.Table(code=base / "server.py")
    try:
        o.start()
        o.lands(M, 3, "Mountain")
        o.onto(M, "Exploratory Mission")
        snap = None
        for _ in range(40):
            snap = next(iter(o.dir.rglob("snapshot.pkl")), None)
            if snap:
                break
            time.sleep(0.5)
        time.sleep(1.5)                                  # the save after the last change
        keys = dict(o.keys)
    finally:
        o.stop()
    check(snap is not None, "the old code saved its game", snap)
    if snap is None:
        return
    t = R.Table(extra=["--restore", str(snap)])
    try:
        t.keys = keys
        st = t.state(M)
        check(not st.get("error") and st.get("exile_playable") == [] and any(p["name"] == "Exploratory Mission" for p in st.get("permanents", [])), "restored: the board is there, with empty exile zones", str(st)[:200])
        t.hand(M, "Reckless Impulse")
        stack_top(t, M, ["Mountain", "Starfleet Security", "Moopsy"])
        c, d = t.act(M, "cast", name="Reckless Impulse")
        st = t.state(M)
        check(c == 200 and sorted(xs(st)) == [("Mountain", "until the end of your next turn"), ("Starfleet Security", "until the end of your next turn")],
              "restored: Reckless Impulse exiles the top two, playable", (c, d, xs(st)))
        c, d = t.act(M, "land", name="Mountain", from_exile=True)
        st = t.state(M)
        mp = next(p for p in st["permanents"] if p["name"] == "Exploratory Mission")
        check(c == 200 and st["land_played"] and mp.get("quest") == 1 and ("Moopsy", "this turn") in xs(st),
              "restored: the exiled Mountain is played as the land drop; Exploratory Mission (from the old save) gets its counter and exiles Moopsy", (c, d, mp.get("quest"), xs(st)))
        c, d = t.act(M, "cast", name="Starfleet Security", from_exile=True)
        st = t.state(M)
        check(c == 200 and any(p["name"] == "Starfleet Security" for p in st["permanents"]), "restored: Starfleet Security is cast from exile", (c, d))
    finally:
        t.stop()


def table_bot():
    print("Table: the reviewed autopilot plays what it may from exile (a land first, then a spell)")
    bot = Path(os.environ.get("AUTOPILOT_REVIEW") or HERE.parent / "claude_autopilot.py").expanduser()
    if not bot.exists():
        check(False, "the reviewed autopilot is there (AUTOPILOT_REVIEW)", str(bot))
        return
    t = R.Table()
    try:
        t.start()
        t.lands(M, 3, "Mountain")
        t.hand(M, "Reckless Impulse")
        stack_top(t, M, ["Mountain", "Starfleet Security"])
        c, d = t.act(M, "cast", name="Reckless Impulse")
        assert c == 200, d
        keyf = Path(tempfile.mkdtemp()) / "k"
        keyf.write_text(t.keys[M])
        keyf.chmod(0o600)
        os.environ.update(CLAUDE_SEAT=M, CLAUDE_KEY_FILE=str(keyf), TABLE_URL=t.b, CLAUDE_TABLE=t.b, AUTOPILOT_GRACE="1")
        sys.path.insert(0, str(bot.parent))
        import importlib.util
        spec = importlib.util.spec_from_file_location("review_autopilot", bot)
        ap = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ap)
        ap.BASE, ap.SEAT, ap.KEY = t.b, M, t.keys[M]
        ev0 = last_event(t)
        ap.play_turn()
        said = [str(e.get("text")) for e in t.events() if e["id"] > ev0 and e.get("speaker") == M]
        check(any("I play Mountain from exile" in x for x in said), "the bot plays the exiled Mountain as its land", said[:6])
        check(any("I cast Starfleet Security from exile" in x for x in said), "…and casts Starfleet Security from exile", said[:6])
    finally:
        t.stop()


def static_page():
    print("hand.html")
    s = (HERE / "hand.html").read_text()
    blk = s[s.index("function drawExile"):s.index("let choiceSig")]
    raw = [x for x in __import__("re").findall(r"\$\{(c\.(?:name|cost|type|text|pt|until|by)|m\.(?:name|text)|t)\}", blk)]
    check("exilepanel" in s and "drawExile(myTurn)" in s and "from_exile: true" in s, "the page has a Playable from exile panel, and its buttons send from_exile")
    check(not raw, "every card field the panel prints goes through esc()", raw)


def main():
    sections = [(engine_mission, "engine mission"), (engine_impulse, "engine impulse"), (engine_old_pickle, "engine old pickle"), (engine_coverage, "engine coverage"),
                (static_page, "page"), (table_mission, "table mission"), (table_reckless, "table reckless"), (table_rr, "table rr"), (table_bot, "table bot"),
                (table_old_snapshot, "table old snapshot")]
    only = [x.strip().lower() for x in os.environ.get("IMPULSE_ONLY", "").split("|") if x.strip()]
    for fn, title in sections:
        if only and not any(x in title for x in only):
            continue
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — recorded, and the next section runs
            traceback.print_exc()
            check(False, f"section finished: {title}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
