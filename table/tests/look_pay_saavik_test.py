"""Three fixes before the Star Trek draft (2026-11-09), each on the engine and then on a table of its own (free port, the seat's own key):

1. Surveil / scry: what stays on top goes back IN ANY ORDER the player sets (top_order), checked to be exactly the kept cards; a scry's bottom
   cards in the order named. No order sent: unchanged (the kept cards stay as they were).
2. Which lands pay: cast with pay_with (the sources the player picks; refused if they can't pay), and `swap`: in the same step, untap a source the
   table tapped for a payment and tap another that makes the same mana. Nothing else untaps: an illegal swap is refused and changes nothing.
3. Saavik, Stoic Student: her enters trigger asks for an attacking or blocking creature an opponent controls, X = the creature types among her
   controller's non-Borg creatures (her own included), the damage is dealt and the creature dies normally; nothing to aim at: said, nothing
   happens. And a virtual seat can flash her in during the opponent's declare attackers and declare blockers steps.

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/look_pay_saavik_test.py
    LPS_ONLY="engine|surveil table"   only the sections whose title names one of these
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "tests"))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import impulse_test as I  # noqa: E402  (its table set-up: free ports never a live game's, decks with the impulse cards)
import player  # noqa: E402

R, check, CARDS, M, C = I.R, I.R.check, I.R.CARDS, "Michael", "Claude"
R.POOL.update({"Saavik, Stoic Student": 6, "Sylvia Tilly, Joy of Discovery": 6})
TEST_SCRY = {"name": "Test Scry", "count": 4, "manaCost": "{U}", "manaValue": 1.0, "type": "Sorcery", "types": ["Sorcery"], "subtypes": [], "supertypes": [],
             "text": "Scry 3.", "power": None, "toughness": None, "colors": ["U"], "colorIdentity": ["U"]}


def deck_with_scry(name):
    f = Path(R.deck_file(name))
    d = json.loads(f.read_text())
    d["mainBoard"].append(TEST_SCRY)                     # (no scry card in the set: a plain one, so the route is exercised)
    f.write_text(json.dumps(d))
    return str(f)


R.DECK_MICHAEL = deck_with_scry("Michael-lps")
R.DECK_CLAUDE = deck_with_scry("Claude-lps")
card, seat = I.card, I.seat


def top_names(v, n):
    return [c["name"] for c in v.library[::-1][:n]]


# ── 1. surveil / scry ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def engine_look():
    print("Engine: surveil and scry, the order of what goes back")
    v = seat(lands=0)
    v.library = [card("Moopsy"), card("Horta"), card("Mugato"), card("Starfleet Security")]   # top: Starfleet Security, Mugato, Horta
    v.run_effects(None, "Surveil 3.")
    check(v.__dict__.get("pending_surveil") == 3, "surveil 3 waits on the player", v.__dict__.get("pending_surveil"))
    try:
        v.finish_look("surveil", 3, ["Mugato"], ["Starfleet Security"])
        bad = False
    except player.IllegalAction:
        bad = True
    check(bad and top_names(v, 3) == ["Starfleet Security", "Mugato", "Horta"] and not v.graveyard, "a top_order that leaves out a kept card is refused, and nothing moved")
    try:
        v.finish_look("surveil", 3, ["Mugato"], ["Horta", "Mugato", "Starfleet Security"])
        bad = False
    except player.IllegalAction:
        bad = True
    check(bad and not v.graveyard, "…so is one naming a card that went to the graveyard")
    v.finish_look("surveil", 3, ["Mugato"], ["Horta", "Starfleet Security"])
    check(top_names(v, 3) == ["Horta", "Starfleet Security", "Moopsy"] and [c["name"] for c in v.graveyard] == ["Mugato"] and not v.__dict__.get("pending_surveil"),
          "surveil: Mugato to the graveyard, the other two back on top in the order chosen", top_names(v, 3))
    v.library = [card("Moopsy"), card("Horta"), card("Mugato"), card("Starfleet Security")]
    v.finish_look("surveil", 2, [], None)
    check(top_names(v, 3) == ["Starfleet Security", "Mugato", "Horta"], "no order sent: unchanged", top_names(v, 3))
    v.library = [card("Moopsy"), card("Horta"), card("Mugato"), card("Starfleet Security")]
    v.run_effects(None, "Scry 3.")
    check(v.__dict__.get("pending_scry") == 3, "scry 3 waits on the player", v.__dict__.get("pending_scry"))
    said = v.finish_look("scry", 3, ["Starfleet Security", "Horta"], ["Mugato"])
    check([c["name"] for c in v.library] == ["Horta", "Starfleet Security", "Moopsy", "Mugato"] and "Horta" not in " ".join(said),
          "scry: two on the bottom in the order named (top to bottom), one on top; the table hears how many, not which", ([c["name"] for c in v.library], said))


def table_look():
    print("Table: surveil (Sylvia Tilly) and scry through the route, top_order checked")
    t = R.Table()
    try:
        t.start()
        t.lands(M, 3, "Forest")
        t.hand(M, "Sylvia Tilly, Joy of Discovery")
        I.stack_top(t, M, ["Horta", "Mugato", "Moopsy"])
        c, d = t.act(M, "cast", name="Sylvia Tilly, Joy of Discovery")
        st = t.state(M)
        check(c == 200 and st.get("pending_surveil") == 2 and st.get("surveil_cards") == ["Horta", "Mugato"], "Sylvia Tilly enters: surveil 2, the two shown only to Michael", (c, st.get("surveil_cards")))
        c, d = t.act(M, "surveil", graveyard=[], top_order=["Mugato"])
        check(c != 200 and t.state(M).get("pending_surveil") == 2, "a top_order missing a kept card is refused; the surveil still waits", (c, d))
        c, d = t.act(M, "surveil", graveyard=[], top_order=["Mugato", "Horta"])
        top = t.call("POST", "/api/brain/peek", {"seat": M, "n": 3})[1].get("private", {}).get("top")
        check(c == 200 and top == ["Mugato", "Horta", "Moopsy"], "both kept, Mugato put first: that's the new top", (c, d, top))
        check(not any("Mugato" in json.dumps(e) for e in t.events()[-6:] if e.get("type") == "say"), "the order of his library is never said aloud", "")
        # scry, the same route
        t.lands(M, 1, "Island")
        t.hand(M, "Test Scry")
        I.stack_top(t, M, ["Horta", "Mugato", "Moopsy"])
        c, d = t.act(M, "cast", name="Test Scry")
        st = t.state(M)
        check(c == 200 and st.get("pending_scry") == 3 and st.get("scry_cards") == ["Horta", "Mugato", "Moopsy"], "scry 3: waiting, shown only to Michael", (c, d, st.get("scry_cards")))
        c, d = t.act(M, "scry", bottom=["Horta"], top_order=["Moopsy", "Horta"])
        check(c != 200, "a top_order naming a card sent to the bottom is refused", (c, d))
        c, d = t.act(M, "scry", bottom=["Horta"], top_order=["Moopsy", "Mugato"])
        top = t.call("POST", "/api/brain/peek", {"seat": M, "n": 2})[1].get("private", {}).get("top")
        lib = t.state(M).get("library")
        check(c == 200 and top == ["Moopsy", "Mugato"] and not t.state(M).get("pending_scry"), "Horta to the bottom, Moopsy then Mugato on top", (c, d, top, lib))
    finally:
        t.stop()


# ── 2. which lands pay ────────────────────────────────────────────────────────────────────────────────────────────────────────────
def engine_pay():
    print("Engine: pay_with, and swapping which land paid")
    v = seat(lands=0)
    m1, m2, pl, fo = (player.Perm(card(n), sick=False) for n in ("Mountain", "Mountain", "Plains", "Forest"))
    v.battlefield += [m1, m2, pl, fo]
    v.hand = [card("Starfleet Security"), card("Reckless Impulse")]
    v.library = [card("Moopsy")] * 3
    try:
        v.manual_cast("Starfleet Security", pay_with=[f"#{pl.id}"])
        bad = False
    except player.IllegalAction as e:
        bad = "can't pay" in str(e)
    check(bad and not any(p.tapped for p in v.battlefield) and len(v.hand) == 2, "pay_with a Plains for {R}: refused, nothing tapped, still in hand")
    v.manual_cast("Starfleet Security", pay_with=[f"#{m2.id}"])
    check(m2.tapped and not m1.tapped, "pay_with the second Mountain: that one is tapped, not the first", (m1.tapped, m2.tapped))
    check(v.paid_now() == [{"id": m2.id, "color": "R"}], "the table remembers what it paid with", v.paid_now())
    for frm, to, why in ((m2, pl, "a Plains can't make the {R} it paid"), (m1, pl, "an untapped land that paid nothing"), (m2, m2, "the same land")):
        try:
            v.swap_payment(f"#{frm.id}", f"#{to.id}")
            ok = False
        except player.IllegalAction:
            ok = True
        check(ok and m2.tapped and not pl.tapped and not m1.tapped, f"swap refused: {why}; nothing changed")
    v.swap_payment(f"#{m2.id}", f"#{m1.id}")
    check(m1.tapped and not m2.tapped and v.paid_now() == [{"id": m1.id, "color": "R"}], "swap the Mountains: the first taps, the second untaps", (m1.tapped, m2.tapped))
    try:
        v.swap_payment(f"#{m2.id}", f"#{fo.id}")
        again = False
    except player.IllegalAction:
        again = True
    check(again and not fo.tapped, "the land that was swapped out paid nothing now: no free untap through it")
    # generic mana can come from anything
    m1.tapped = False
    v.paid_now()
    v.manual_cast("Reckless Impulse", pay_with=[f"#{m2.id}", f"#{pl.id}"])
    check(m2.tapped and pl.tapped and not fo.tapped, "{1}{R} paid with a Mountain and a Plains, as chosen", (m2.tapped, pl.tapped))
    v.swap_payment(f"#{pl.id}", f"#{fo.id}")
    check(fo.tapped and not pl.tapped, "the Plains paid generic: a Forest can pay it instead")
    player.STEP_KEY[0] = lambda: ("Michael", "next step")
    try:
        check(v.paid_now() == [], "in the next step there is nothing left to swap", v.paid_now())
    finally:
        player.STEP_KEY[0] = None


def table_pay():
    print("Table: pay_with and swap through the route")
    t = R.Table()
    try:
        t.start()
        m1 = t.onto(M, "Mountain")
        m2 = t.onto(M, "Mountain")
        pl = t.onto(M, "Plains")
        t.hand(M, "Starfleet Security")
        c, d = t.act(M, "cast", name="Starfleet Security", pay_with=[f"#{pl}"])
        check(c != 200 and not any(p["tapped"] for p in t.state(M)["permanents"]), "pay_with a Plains for {R}: refused, nothing tapped", (c, d))
        c, d = t.act(M, "cast", name="Starfleet Security", pay_with=[f"#{m2}"])
        st = t.state(M)
        tapped = {p["id"] for p in st["permanents"] if p["tapped"]}
        check(c == 200 and tapped == {m2} and st.get("paid_this_step") == [{"id": m2, "color": "R"}], "pay_with the second Mountain: only it is tapped, and the page is told", (c, d, tapped))
        c, d = t.act(M, "swap", **{"from": f"#{m2}", "to": f"#{pl}"})
        check(c != 200 and {p["id"] for p in t.state(M)["permanents"] if p["tapped"]} == {m2}, "an illegal swap (a Plains for {R}) is refused and changes nothing", (c, d))
        c, d = t.act(C, "swap", **{"from": f"#{m2}", "to": f"#{m1}"})
        check(c != 200, "another seat can't swap my lands", (c, d))
        c, d = t.act(M, "swap", **{"from": f"#{m2}", "to": f"#{m1}"})
        check(c == 200 and {p["id"] for p in t.state(M)["permanents"] if p["tapped"]} == {m1}, "swap the Mountains: the first taps, the second untaps", (c, d))
        c, d = t.act(M, "swap", **{"from": f"#{pl}", "to": f"#{m2}"})
        check(c != 200 and not next(p for p in t.state(M)["permanents"] if p["id"] == m2)["tapped"], "a land that paid nothing can't be 'swapped' (no free untap)", (c, d))
        c, d = t.act(M, "next")
        c, d = t.act(M, "swap", **{"from": f"#{m1}", "to": f"#{m2}"})
        check(c != 200, "after the step ends the payment can't be changed", (c, d))
    finally:
        t.stop()


# ── 3. Saavik ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def engine_saavik():
    print("Engine: Saavik's X")
    v = seat(lands=0)
    sv = player.Perm(card("Saavik, Stoic Student"), sick=False)
    v.battlefield += [sv, player.Perm(card("Horta")), player.Perm(card("Starfleet Security")), player.Perm(card("Assault Drone")), player.Perm(card("Mugato"))]
    check(v.creature_types(exclude="Borg") == {"Vulcan", "Advisor", "Elemental", "Human", "Officer", "Ape", "Beast"}, "creature types among non-Borg creatures (Saavik's own, not the Borg drone's)", v.creature_types(exclude="Borg"))
    foe = seat("Claude")
    big = player.Perm(card("Horta"), sick=False)
    foe.battlefield.append(big)
    text = "she deals X damage to target attacking or blocking creature an opponent controls, where X is the number of creature types among non-Borg creatures you control"
    said = v._one_effect(sv, text, [{"perm": big, "owner": foe}])
    check(big not in foe.battlefield and any("X is 7" in x for x in said), "X = 7: Horta (7/6) takes 7 and dies", said)
    slots = player.card_slots(card("Saavik, Stoic Student"))
    check(slots and slots[0].get("in_combat") and slots[0]["side"] == "opp", "her slot: an opponent's creature, attacking or blocking", slots)


def table_saavik():
    print("Table: Saavik flashed in on the opponent's declare attackers and declare blockers steps")
    t = R.Table()
    try:
        t.start()
        for n in ("Horta", "Starfleet Security"):
            t.onto(M, n)
        t.lands(M, 5, "Plains")
        t.hand(M, "Saavik, Stoic Student")
        # Saavik in Michael's own main phase: nothing is attacking or blocking
        ev0 = I.last_event(t)
        c, d = t.act(M, "cast", name="Saavik, Stoic Student")
        st = t.state(M)
        said = " ".join(str(e.get("text")) for e in t.events() if e["id"] > ev0)
        check(c == 200 and not st.get("pending_choices") and "Saavik" in said and ("no legal" in said.lower() or "nothing" in said.lower()),
              "no attacking or blocking creature: she has no legal target, it says so, nothing happens", (c, said[-300:]))
        # Claude attacks with Xindi Surveyors (3/2) and keeps Fugitive Listener home; Michael flashes a second Saavik during declare attackers
        t.turn(C)
        xs_ = t.onto(C, "Xindi Surveyors")
        home = t.onto(C, "Fugitive Listener")
        t.settle_setup()
        t.turn(M)
        t.turn(C)                                      # (summoning sickness gone)
        t.hand(M, "Saavik, Stoic Student")
        c, d = t.act(C, "attack", assign={f"#{xs_}": M})
        check(c == 200 and t.call("GET", "/api/phase")[1].get("step") == "declare attackers", "Claude attacks with Xindi Surveyors", (c, d))
        lg = [a for a in t.legal(M)["actions"] if a["action"] == "cast"]
        check(any(a["body"]["name"].startswith("Saavik") for a in lg), "Michael's legal list (not his turn) offers flashing Saavik in", lg)
        ev1 = I.last_event(t)
        c, d = t.act(M, "cast", name="Saavik, Stoic Student")
        pend = t.state(M).get("pending_choices") or []
        if pend:                                       # (exactly one legal target is taken without asking: the table's rule for every trigger)
            check([o.get("ref") for o in pend[0]["slot"].get("options") or []] == [f"#{xs_}"], "only the attacker is offered", pend[0]["slot"].get("options"))
            t.act(M, "choose", id=pend[0]["id"], target={"at": C, "ref": f"#{xs_}"})
        said = " ".join(str(e.get("text")) for e in t.events() if e["id"] > ev1)
        check(c == 200 and t.perm(C, "Xindi Surveyors") is None and t.perm(C, "Fugitive Listener") is not None and "X is 5" in said,
              "flashed in on his opponent's turn, she hits the attacker (not the creature at home): X = 5 (Vulcan, Advisor, Elemental, Human, Officer), the 3/2 dies", (c, said[-300:]))
        # declare blockers: Claude attacks with Starfleet Security, Michael blocks with Horta, Claude flashes Saavik at the blocker
        t.turn(M)
        t.turn(C)
        ss = t.onto(C, "Starfleet Security")
        t.lands(C, 5, "Plains")
        t.hand(C, "Saavik, Stoic Student")
        c, d = t.act(C, "attack", assign={f"#{ss}": M})
        horta = t.perm(M, "Horta")["id"]
        c2, d2 = t.act(M, "block", blocker=f"#{horta}", attacker=f"#{ss}")
        step = t.call("GET", "/api/phase")[1].get("step")
        check(c == 200 and c2 == 200 and step in ("declare blockers", "combat damage"), "Claude attacks, Michael blocks with Horta", (c, d, c2, d2, step))
        dmg0 = t.perm(M, "Horta")["damage"]
        ev2 = I.last_event(t)
        c, d = t.act(C, "cast", name="Saavik, Stoic Student")
        pend = t.state(C).get("pending_choices") or []
        if pend:
            check(f"#{horta}" in [o.get("ref") for o in pend[0]["slot"].get("options") or []], "the blocking Horta is offered", pend[0]["slot"].get("options"))
            t.act(C, "choose", id=pend[0]["id"], target={"at": M, "ref": f"#{horta}"})
        h = t.perm(M, "Horta")
        said = " ".join(str(e.get("text")) for e in t.events() if e["id"] > ev2)
        # v6c (M1): combat damage is dealt in the damage step, AFTER the window Saavik is cast in. The attacking Starfleet Security is
        # therefore still on the battlefield when she enters (before, the block itself had already killed it), so X counts its Human and
        # Officer too: 6, which kills the blocking Horta (7/6, no damage on it yet).
        check(c == 200 and dmg0 == 0 and h is None and "X is 6" in said and t.perm(C, "Starfleet Security") is not None,
              "Claude flashes Saavik in after the block, before damage, at the blocking Horta: X = 6 (Vulcan, Advisor; El-Aurian, Scout; the attacker's Human, Officer): Horta dies",
              (c, d, h and h["damage"], dmg0, said[-200:]))
    finally:
        t.stop()


def static_page():
    print("hand.html")
    s = (HERE / "hand.html").read_text()
    check("top_order" in s and "data-survup" in s and "data-survdn" in s and 'act(L.kind' in s, "the surveil/scry panel has reorder controls and sends top_order")
    check("pay_with" in s and "data-paypick" in s and 'act("swap"' in s, "the page lets you pick which sources pay, and swap one")


def main():
    sections = [(engine_look, "engine look"), (engine_pay, "engine pay"), (engine_saavik, "engine saavik"), (static_page, "page"),
                (table_look, "surveil table"), (table_pay, "pay table"), (table_saavik, "saavik table")]
    only = [x.strip().lower() for x in os.environ.get("LPS_ONLY", "").split("|") if x.strip()]
    for fn, title in sections:
        if only and not any(x in title for x in only):
            continue
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            check(False, f"section finished: {title}", f"{type(e).__name__}: {e}")
    print("\n" + (f"❌ {len(R.FAILS)} FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all passed"))
    return 1 if R.FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
