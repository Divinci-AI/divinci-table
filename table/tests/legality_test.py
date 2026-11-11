"""T18: every button of a seat's own state says whether it can be pressed now, and why not, from the engine's own checks.
    PYTHONDONTWRITEBYTECODE=1 python3 table/tests/legality_test.py"""
from v6_harness import R, Table, check, run, cast


def ab(t, seat, name, i=0):
    p = t.perm(seat, name)
    return p["abilities"][i]


def table():
    t = Table()
    try:
        t.start()                                          # Michael's main 1
        t.onto("Michael", "Savvy Profiteer")
        a = ab(t, "Michael", "Savvy Profiteer")
        check(a["cost"] == "{3}, {T}" and a["can"] is False and "summoning sick" in a["why_not"], "a creature that just arrived: its {T} ability says summoning sick", a)
        c, d = t.act("Michael", "activate", source="Savvy Profiteer")
        check(c == 400 and "summoning sickness" in d.get("error", ""), "…and the action is refused for the same reason", (c, d))
        t.turn("Michael")
        a = ab(t, "Michael", "Savvy Profiteer")
        check(a["can"] is False and a["why_not"].startswith("needs {3}: you have 0 untapped mana sources"), "tapped out (no mana at all): can is false, with the mana reason", a)
        c, d = t.act("Michael", "activate", source="Savvy Profiteer")
        check(c == 400 and "can't pay" in d.get("error", ""), "…and the action is refused", (c, d))
        t.lands("Michael", 2, "Mountain")
        a = ab(t, "Michael", "Savvy Profiteer")
        check(a["can"] is False and "2 untapped mana sources" in a["why_not"], "two lands for a {3} ability: still no, and it counts them", a)
        t.lands("Michael", 1, "Mountain")
        a = ab(t, "Michael", "Savvy Profiteer")
        check(a["can"] is True and a["why_not"] is None, "with the third land it becomes true", a)
        for p in [x for x in t.state("Michael")["permanents"] if x["name"] == "Mountain"][:1]:
            t.act("Michael", "tap", ref=f"#{p['id']}")
        check(ab(t, "Michael", "Savvy Profiteer")["can"] is False, "tap one of the three lands: false again", "")
        for p in [x for x in t.state("Michael")["permanents"] if x["name"] == "Mountain" and x["tapped"]]:
            t.act("Michael", "untap", ref=f"#{p['id']}")
        check(ab(t, "Michael", "Savvy Profiteer")["can"] is True, "untap it: true again", ab(t, "Michael", "Savvy Profiteer"))
        c, d = t.act("Michael", "activate", source="Savvy Profiteer")
        check(c == 200 and any(p["name"].startswith("Treasure") for p in t.state("Michael")["permanents"]), "and the ability works when the flag says it can", (c, d.get("said") or d))
        a = ab(t, "Michael", "Savvy Profiteer")
        check(a["can"] is False and "tapped" in a["why_not"], "after using it: it is tapped, and says so", a)
        # sorcery-speed ability, and a once-each-turn one
        t.onto("Michael", "DOT-7 Repair Squad")
        t.lands("Michael", 2, "Plains")
        a = ab(t, "Michael", "DOT-7 Repair Squad")
        check(a["can"] is True, "a sorcery-speed ability in my own main phase: yes", a)
        c, d = t.act("Michael", "activate", source="DOT-7 Repair Squad", target=f"#{t.perm('Michael', 'Savvy Profiteer')['id']}", at="Michael")
        check(c == 200 and not t.perm("Michael", "Savvy Profiteer")["tapped"], "it untaps its target", (c, d.get("said") or d))
        a = ab(t, "Michael", "DOT-7 Repair Squad")
        check(a["can"] is False and "once each turn" in a["why_not"], "once each turn: used, so no (with the reason)", a)
        t.lands("Michael", 2, "Plains")
        c, d = t.act("Michael", "activate", source="DOT-7 Repair Squad", target=f"#{t.perm('Michael', 'Savvy Profiteer')['id']}", at="Michael")
        check(c == 400 and "once each turn" in d.get("error", ""), "…and a second activation this turn is refused", (c, d))
        # cards in hand
        t.hand("Michael", "Mugato")
        h = next(c_ for c_ in t.state("Michael")["hand"] if c_["name"] == "Mugato")
        check("can_cast" in h and (h["can_cast"] or "needs" in (h["why_not"] or "")), "a card in hand carries can_cast / why_not", {k: h.get(k) for k in ("can_cast", "why_not", "castable_now")})
        t.turn("Claude")
        a = ab(t, "Michael", "DOT-7 Repair Squad")
        check(a["can"] is False and "sorcery speed" in a["why_not"], "on the opponent's turn the sorcery-speed ability says so (with lands untapped)", a)
        c, d = t.act("Michael", "activate", source="DOT-7 Repair Squad", target=f"#{t.perm('Michael', 'Savvy Profiteer')['id']}", at="Michael")
        check(c == 400 and "only as a sorcery" in d.get("error", ""), "…and the action is refused", (c, d))
        h = next(c_ for c_ in t.state("Michael")["hand"] if c_["name"] == "Mugato")
        check(h["can_cast"] is False and "sorcery speed" in h["why_not"] and h["castable_now"] is False, "a creature card on the opponent's turn: can_cast false, sorcery speed", h.get("why_not"))
        a = ab(t, "Michael", "Savvy Profiteer")
        check(a["can"] in (True, False) and (a["can"] or "sorcery" not in a["why_not"]), "an instant-speed ability isn't blamed on timing", a)
    finally:
        t.stop()


def token_crew():
    """M8: a creature token (and a Spacecraft that is a creature) can be picked as Station crew: its type line says Creature, and the ship's
    own crew list (the engine's rule) names it."""
    t = Table()
    try:
        t.start()
        M = "Michael"
        ship = t.onto(M, "Class-F Shuttlecraft")
        drone = t.onto(M, "Tenacious Drone")
        t.call("POST", "/api/brain/destroy", {"seat": M, "ref": f"#{drone}", "quiet": True})     # its Borg token: made by the engine, with no printed type line
        tok = next(p for p in t.state(M)["permanents"] if p["name"] == "Borg token")
        sh = t.perm(M, "Class-F Shuttlecraft")
        check("Creature" in (tok["type"] or "") and tok["type"].startswith("Token"), "an engine-made token has a type line that says what it is", tok["type"])
        check(sh["station_crew"] == [tok["id"]] and sh["station_can"] is True, "the Spacecraft's crew list is the engine's: the Borg token is on it", (sh.get("station_crew"), sh.get("station_why")))
        c, d = t.act(M, "station", ship=f"#{ship}", crew=f"#{tok['id']}")
        check(c == 200 and t.perm(M, "Class-F Shuttlecraft")["charge"] == 1, "stationing with the token adds its power in charge counters", (c, d.get("said") or d))
        sh = t.perm(M, "Class-F Shuttlecraft")
        check(sh["station_crew"] == [] and (sh["station_can"] is False and "no untapped creature" in sh["station_why"] or sh["station_why"] is not None), "with the token tapped nobody is left to crew it, and it says so", (sh.get("station_crew"), sh.get("station_why")))
        t.lands(M, 3, "Mountain")
        t.onto("Claude", "Mugato")
        t.hand(M, "Plasma Cascade")
        c, d = t.act(M, "cast", name="Plasma Cascade", targets=[{"at": "Claude", "ref": f"#{t.perm('Claude', 'Mugato')['id']}"}])
        check(c == 200 and any("Plasma Cascade deals" in x for x in d.get("said") or [" ".join(d.get("said") or [])]) or "Plasma Cascade deals" in " ".join(d.get("said") or []),
              "M15: a spell's damage line names the spell, not 'This'", d.get("said"))
    finally:
        t.stop()


if __name__ == "__main__":
    run([("abilities and casts: can / why_not", table), ("M8: tokens as Station crew; M15: spell names", token_crew)])
