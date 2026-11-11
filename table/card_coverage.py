"""What the table really does with each line of a card's text.

    python3 table/card_coverage.py ~/.divinci/star-trek-mtg/cards.json > RETRO-CARD-COVERAGE.md

One list, read against the real engine (player.effect_known runs the engine's own sentence code on a scratch player), so a card's answer here is
the same answer the table acts on. It is used three ways: this report, the "By hand" note the table says when a card lands (server.note_manual),
and the chip on the enlarged card. Statuses:
    handled    the engine does it by itself (it may ask the player to aim it, or to choose)
    announced  the table shows it and offers a button or reminder, and a person decides or finishes it
    manual     the table can't do it. It says so when the card lands: "By hand: …". It never silently does nothing.
"""
from __future__ import annotations

import json
import re
import sys

import player

ACT_COST = re.compile(r"^((?:(?:\{[^}]+\})+|Sacrifice [^:,]+|Tap \w+ untapped [^:,]+)(?:, ?(?:(?:\{[^}]+\})+|Sacrifice [^:,]+|Tap \w+ untapped [^:,]+))*): (.+)$")
KEYWORD_OK = {"Flying", "Reach", "Trample", "Haste", "Vigilance", "Lifelink", "Deathtouch", "First strike", "Double strike", "Menace", "Defender", "Flash",
              "Indestructible", "Hexproof"}


def _plain(line: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", line).strip()


def _sentences_known(text: str, name: str, aimed: bool = True) -> tuple[bool, list[str]]:
    """Which sentences of this text the engine's effect runner can't do."""
    missing = []
    for raw in player.VirtualPlayer._sentences(player.VirtualPlayer._plain(text)):
        s = re.sub(r"^[^.—]{1,40} — ", "", raw.strip()).strip()
        if s and not player.effect_known(s, name, aimed):
            missing.append(s)
    return not missing, missing


def _partial(text: str, name: str, aimed: bool) -> str:
    """handled: every sentence runs. announced: some do, and the table says the rest "By hand" when they come up. manual: none do."""
    ok, missing = _sentences_known(text, name, aimed)
    if ok:
        return "handled"
    total = [x for x in player.VirtualPlayer._sentences(player.VirtualPlayer._plain(text)) if x.strip()]
    return "announced" if len(missing) < len(total) else "manual"


def _modes_after(lines: list[str], i: int) -> list[str]:
    out = []
    for x in lines[i + 1:]:
        if not x.strip().startswith("•"):
            break
        out.append(_plain(x).lstrip("• ").strip())
    return out


def _modal_status(lines: list[str], i: int, name: str) -> str:
    """A "choose one —" the engine asks of its controller: handled when it can carry out every mode, announced when only some (the others are
    listed "(by hand)" in the question), manual when none."""
    ok = [_sentences_known(m_, name)[0] for m_ in _modes_after(lines, i)]
    return "handled" if ok and all(ok) else "announced" if any(ok) else "manual"


def classify_line(card: dict, line: str, lines: list[str], i: int) -> tuple[str, str]:
    """(shape, status) of one line of rules text."""
    name, types = card["name"], card.get("types") or []
    short = name.split(",")[0]
    l_ = _plain(line)
    if l_.startswith("•"):
        return ("mode", "")                     # read with the line that opens it
    # keyword lines
    kws = [w.strip() for w in l_.split(",")]
    if kws and all(any(w.lower() == k.lower() for k in player.EVERY_KEYWORD) or re.fullmatch(r"ward \{[^}]+\}", w, re.I) for w in kws):
        # Ward {N}: paid by whoever aims at it, with the spell or when a target is named (server.ward_due / pay_ward)
        return ("keyword: " + ", ".join(k.split(" {")[0].capitalize() for k in kws), "handled")
    if re.match(r"Ward \{\d+\}", l_):
        return ("keyword: Ward", "handled")
    if re.match(r"Ward\b", l_):
        return ("keyword: Ward", "manual")
    # lands and mana
    if re.fullmatch(r"\{T\}: Add .+", l_) and "only" not in l_:
        return ("mana ability", "handled")
    if re.match(r"\{T\}: Add ", l_):
        return ("mana ability (restricted spend)", "announced")
    if l_ == "This land enters tapped.":
        return ("land enters tapped", "handled")
    if re.match(r"This land enters tapped unless you control", l_):
        return ("land enters tapped unless", "handled")
    if re.match(r"As this land enters, you may pay \d+ life", l_):
        return ("shock land (pay life or tapped)", "handled")
    if re.match(r"When this land enters, you gain \d+ life", l_):
        return ("land enters: gain life", "handled")
    if re.match(r"When this land enters untapped, choose one", l_):
        return ("land enters: choose one", _modal_status(lines, i, short))
    if re.fullmatch(r"This creature can't attack alone\.?", l_):
        return ("can't attack alone", "handled")
    if l_.startswith(player.LOOK_TOP) or l_.startswith(player.CAST_TOP):
        return ("top of library: look / cast an artifact", "handled")
    # Station and Equipment
    if re.match(r"Station\b", l_):
        return ("Station", "handled")
    m = re.match(r"(\d+)\+ \| (.+)$", l_)
    if m:
        what = m.group(2)
        if all(w.strip().lower() in [k.lower() for k in player.STATION_KEYWORDS] for w in re.split(r",\s*", what)):
            return ("station line: keywords", "handled")
        if re.match(r"At the beginning of combat on your turn, create a \d+/\d+ [\w ]+ creature token with haste\. Sacrifice it", what):
            return ("station line: token each combat", "handled")
        if re.match(r"Creatures you control get ", what) or re.match(r"Artifact creatures you control have ", what):
            return ("station line: anthem", "handled")
        return ("station line: other", "manual")
    if re.match(r"Equip \{", l_):
        return ("Equip", "handled")
    if re.match(r"Equipped creature gets ", l_):
        return ("Equipment: stats", "handled")
    if "while you control another creature with a Communicator attached to it" in l_:
        return ("Equipment: Communicator rider", "handled")
    if re.match(r"Whenever equipped creature attacks, surveil", l_):
        return ("Equipment: attack trigger", "handled")
    if re.match(r"When you cast this spell, copy it", l_):
        return ("copy on cast (permanent)", "handled")
    if re.match(r"Enchant ", l_):
        return ("Aura: attach", "handled")
    if re.match(r"Enchanted creature gets ", l_):
        return ("Aura: stats", "handled")
    if re.match(r"Beam me up", l_):
        return ("Beam me up", "handled")
    if re.search(r"landcycling", l_):
        return ("landcycling", "handled")
    # chapters of a Saga
    m = re.match(r"(?:I|II|III|IV)(?:, (?:I|II|III|IV))* — (.+)$", l_)
    if m:
        return ("Saga chapter", _partial(m.group(1), short, False))
    if re.match(r"Kicker\s*(\{[^}]+\})+$|Kicker\s*—\s*Sacrifice an? [\w ]+\.?$", l_):
        return ("kicker", "handled")
    if ("Instant" in types or "Sorcery" in types) and re.search(r"If this spell was kicked", l_):
        ktxt = player.kicked_text(card.get("text") or "", True)
        return ("spell: kicked", "handled" if _sentences_known(ktxt, short)[0] and _sentences_known(player.kicked_text(card.get("text") or "", False), short)[0] else "manual")
    # Instants and sorceries: the sentences themselves
    if "Instant" in types or "Sorcery" in types:
        if re.match(r"Choose (one|two|three)\b", l_):
            modes = [_plain(x).lstrip("• ").strip() for x in lines[i + 1:] if x.strip().startswith("•")]
            ok = [_sentences_known(m_, short)[0] for m_ in modes]
            return ("spell: modal", "handled" if ok and all(ok) else "announced" if any(ok) else "manual")
        ok, _ = _sentences_known(l_, short)
        if ok:
            shape = player.effect_shape(l_)
            return (f"spell: {shape[0]}" if shape else "spell: self-contained", "handled")
        st_ = _partial(l_, short, True)
        return ("spell: part runs" if st_ == "announced" else "spell: other", st_)
    # abilities used from the graveyard or the hand
    m = re.match(r"((?:\{[^}]+\})+), (Exile this card from your graveyard|Put this card from your graveyard on the bottom of your library|Discard this card): (.+)$", l_)
    if m:
        eff = player.ACT_ONLY_RX.sub("", m.group(3)).strip()
        return ("ability from the graveyard / hand", "handled" if _sentences_known(eff, short)[0] else "manual")
    # activated abilities (an ability word in front is only a label: "Strange new worlds — {6}: …")
    l_act = re.sub(r"^[A-Z][\w' ]{2,30} — (?=\{|Tap |Sacrifice )", "", l_)
    m = ACT_COST.match(l_act)
    if m and "Add " not in re.sub(r'"[^"]*"', "", l_act):
        cost, eff = m.group(1), m.group(2)
        eff = player.ACT_ONLY_RX.sub("", player.ACT_LESS_RX.sub("", eff)).strip()        # when and for how much: the engine enforces both
        pay = all(re.fullmatch(r"(\{[^}]+\})+|\{T\}|Tap (an|one|two|three|four|five|six|seven) untapped [\w /]+? you control|Sacrifice (this \w+|another \w+(?: or \w+)?|an? \w+(?: or \w+)?|" + re.escape(name) + "|" + re.escape(short) + ")", c.strip()) for c in re.split(r",\s*(?![^{]*\})", cost))
        probe = player.VirtualPlayer.__new__(player.VirtualPlayer)
        if not pay:
            return ("activated: cost the table can't pay", "manual")
        if re.match(r"(?:choose one)", eff, re.I):
            return ("activated: modal", "handled" if all(_sentences_known(_plain(x).lstrip("• ").strip(), short)[0] for x in lines[i + 1:] if x.strip().startswith("•")) else "manual")
        ok, _ = _sentences_known(eff, short)
        if ok or re.match(r"(draw|scry|surveil|search your library for)", eff, re.I) or re.match(r"Put two charge counters on target Spacecraft", eff):
            return ("activated: runs", "handled")
        return ("activated: effect the table can't run", "manual")
    if re.match(r"\{|Tap (two|three|seven)|Sacrifice|Exile this card|Discard", l_act) and ":" in l_act:
        return ("activated: cost the table can't pay", "manual")
    # enters
    m = re.match(r"(?:[A-Z][\w' ]{2,30} — )?When(?:ever)? (?!(?:A|An|Another|One|You)\b)(?![^,\n]* or another)(?:this \w+|[A-Z][^,\n]*?) enters?(?: or attacks)?(?: the battlefield)?, (.+)$", l_)
    if m:
        clause = m.group(1)
        if re.match(r"choose (one|up to one|two)\s*[—-]", clause, re.I):
            st_ = _modal_status(lines, i, short)             # the engine asks its controller which mode(s), then runs them
            return ("enters or attacks: choose one" if "or attacks" in l_ else "enters: choose one", st_)
        if re.match(r"choose (one|up to one|two)", clause, re.I):
            return ("enters: choose one", "announced")
        if re.search(r"create a colorless Equipment artifact token named", clause):
            return ("enters: Equipment token, attached", "handled")
        if re.search(r"search your library for any number of cards named", clause):
            return ("enters: fetch copies", "handled")
        if re.search(r"destroy target tapped", clause):
            return ("enters: destroy (picker)", "announced")
        if re.search(r"search your library for", clause) and player.effect_shape(clause) and player.effect_shape(clause)[0] == "tutor":
            return ("enters: search", "handled")
        if "or attacks" in l_:
            return ("enters or attacks", "manual")
        ok, _ = _sentences_known(clause, short)
        if ok:
            return ("enters: " + (player.effect_shape(clause)[0] if player.effect_shape(clause) else "self-contained"), "handled")
        st_ = _partial(clause, short, True)
        return ("enters: part runs" if st_ == "announced" else "enters: other", st_)
    # Landfall
    m = re.match(r"(?:Landfall — )?Whenever a land you control enters, (.+)$", l_)
    if m:                                       # the engine runs every sentence (player.landfall); the second-resolution rider waits for its turn
        eff = m.group(1).strip()
        r2 = re.search(r"(?:^|(?<=\.)\s+)If this is the second time this ability has resolved this turn, (.+?)\.?$", eff)
        main = eff[:r2.start()] if r2 else eff
        ok = _sentences_known(main, short, aimed=False)[0] if main.strip() else True
        ok2 = _sentences_known(r2.group(1), short, aimed=False)[0] if r2 else True
        return ("Landfall", "handled" if ok and ok2 else "manual")
    # casting triggers
    m = re.match(r"Whenever you cast an? (enchantment|Aura|noncreature|creature or Spacecraft|Spacecraft) spell, (?:if this Spacecraft isn't a creature, )?(?:you may )?(.+)$", l_)
    if m:
        return ("whenever you cast", "handled" if _sentences_known(m.group(2), short, aimed=False)[0] else "manual")
    # attacks: choose one
    if re.match(r"Whenever this creature attacks, choose (one|two)\s*[—-]", l_):
        return ("attacks: choose one", _modal_status(lines, i, short))
    # dies
    m = re.match(rf"When(?:ever)? (?:this creature|{re.escape(short)}) dies?(?: during your turn)?, (.+)$", l_)
    if m:
        return ("when this dies", "handled" if _sentences_known(m.group(1), short, aimed=False)[0] else "manual")
    if re.match(r"Whenever a creature dies, if [\w' ]+ isn't a creature, put a charge counter", l_):
        return ("a creature dies: charge", "handled")
    if re.match(r"Whenever another creature dies, put a \+1/\+1 counter on", l_):
        return ("another creature dies: counter", "handled")
    m = player.DIES_RX.match(l_)                # Vengeance Mission, Grand Nagus Zek, Excalbian Gamemaster, Skullclamp, Curzon Dax: every death is heard (player.creature_died)
    if m:
        eff = re.sub(r"\s*This ability triggers only once each turn\.?", "", m.group("eff")).strip()
        return ("a creature dies: " + (player.effect_shape(eff)[0] if player.effect_shape(eff) else "self-contained"), "handled" if _sentences_known(eff, short)[0] else "manual")
    m = re.match(r"Whenever you face a dilemma, (.+)$", l_)
    if m:
        return ("dilemma", "handled" if _sentences_known(m.group(1), short, aimed=False)[0] else "manual")
    if re.match(r"At the beginning of combat on your turn, create a \d+/\d+ [\w ]+ creature token with haste\. Sacrifice it", l_):
        return ("each combat: token", "handled")
    m = re.match(r"(?:[\w ]+ — )?At the beginning of (?:your upkeep|each upkeep|your end step|each end step|your first main phase|combat on your turn), (.+)$", l_)
    if m and "choose one" not in m.group(1):
        eff = re.sub(r"^if this creature attacked this turn, ", "", m.group(1))
        return ("beginning-of-step trigger", _partial(eff, short, False))
    if re.match(r"Whenever you (gain life|draw a card), put a \+1/\+1 counter on", l_) or re.match(r"Whenever a player casts a noncreature spell, put a \+1/\+1", l_):
        return ("life, draw or cast trigger", "handled")
    if re.search(r"Other creatures you control enter with an additional \+1/\+1 counter|Spacecraft you control enter with two additional charge|Whenever another creature you control enters, it gets \+1/\+1|Whenever a creature you control enters, you gain 1 life|If you would draw a card, draw two", l_):
        return ("enters/draw replacement", "handled")
    m = re.match(r"Whenever (?:a card|one or more cards) leaves? your graveyard, (.+?)(?: This ability triggers only once each turn\.)?$", l_)
    if m:
        return ("leaves your graveyard", "handled" if _sentences_known(m.group(1), short, aimed=False)[0] else "manual")
    # static stats and keywords
    if re.search(r"(?:^|\s)(Other |Artifact )?[Cc]reatures you control get [+-]\d+/[+-]\d+\.?$", l_) and "until end of turn" not in l_:
        return ("anthem", "handled")
    if re.match(r"(Artifact )?[Cc]reatures you control( with counters on them)? have ([a-z ,]+)\.$", l_) and not re.search(r"hexproof|\"", l_):
        return ("static keyword grant", "handled")
    if re.match(r"This creature gets [+-]\d+/[+-]\d+ for each (Officer token you control|Treasure you control)", l_) or re.match(rf"{re.escape(short)} gets \+\d+/\+\d+ for each Treasure", l_):
        return ("self-counting stats", "handled")
    if re.search(r"power( and toughness are each| is) equal to the number of (differently named lands|artifact creatures) you control", l_):
        return ("*/* from a count", "handled")
    if re.match(r"This spell costs \{X\} less to cast, where X is the number of differently named lands you control|This spell costs \{\d+\} less to cast for each noncreature, nonland card in your graveyard", re.sub(r"^[A-Z][\w' ]{2,30} — ", "", l_)):
        return ("cost reduction the table counts", "handled")
    m = re.match(r"Whenever this creature attacks, (.+)$", l_)
    if m and "choose" not in m.group(1) and _sentences_known(m.group(1), short, aimed=False)[0]:
        return ("attack trigger", "handled")
    if re.search(r"can't be blocked by (creatures with power \d+ or less|artifact creatures)", l_):
        return ("evasion: can't be blocked by", "handled")
    if re.match(r"This (creature|token) can't block", l_):
        return ("can't block", "handled")
    if re.match(r"During your turn, this creature has", l_):
        return ("keyword on your turn", "handled")
    if re.search(r"as though it didn't have defender", l_):
        return ("defender unless", "handled")
    # triggers we handle by name
    if re.search(r"becomes tapped, you gain \d+ life", l_) or re.search(r"becomes tapped, put a promotion counter", l_):
        return ("becomes tapped", "handled")
    m = re.match(r"Whenever [^.\n]*?becomes tapped, (.+)$", l_)
    if m and player.tapped_clause_runs(m.group(1), short):
        return ("becomes tapped", "handled")
    if "total power" in l_ and "put a +1/+1 counter on this creature" in l_:
        return ("total power 8", "handled")
    m = re.match(r"Whenever you attack while creatures you control have total power \d+ or greater, (.+)$", l_)
    if m:                                       # the engine runs the effect (player.total_power_triggers); an aimed one is asked of its controller
        return ("total power 8", _partial(m.group(1), short, True))
    if re.match(r"At the beginning of combat on your turn, create a \d+/\d+ [\w ]+ creature token with haste\. Sacrifice it", l_):
        return ("each combat: token", "handled")
    # the rest, by the kind of thing it is, so the counts say where the work is
    for rx, shape in ((r"becomes tapped", "becomes tapped"), (r"\bdilemma\b", "dilemma"), (r"total power", "total power 8"), (r"^Strange new worlds", "Strange new worlds"), (r"^Federation", "Federation"),
                      (r"At the beginning of", "beginning-of-step trigger"), (r"\bquest counter", "quest counters"), (r"\bdies\b", "dies trigger"), (r"Whenever you attack", "whenever you attack"),
                      (r"attacks", "attack trigger"), (r"combat damage", "combat damage trigger"), (r"Whenever", "other trigger"), (r"\bcopy\b", "copies"), (r"graveyard", "graveyard play"),
                      (r"\bhave\b|\bhas\b", "static grant"), (r"If you would|would", "replacement effect"), (r"\bmay\b", "other static")):
        if re.search(rx, l_):
            return (shape, "manual")
    return ("other", "manual")


_CACHE: dict = {}


def card_lines(card: dict) -> list[tuple[str, str, str]]:
    """[(line, shape, status)] for each ability line of the card, reminder text off."""
    key = (card.get("name"), card.get("text"))
    if key in _CACHE:
        return _CACHE[key]
    lines = [x for x in re.sub(r"\([^)]*\)", "", card.get("text") or "", flags=re.S).split("\n") if x.strip()]   # reminder text can span lines (a Saga's)
    out = []
    for i, line in enumerate(lines):
        shape, status = classify_line(card, line, lines, i)
        if status:
            out.append((_plain(line), shape, status))
    _CACHE[key] = out
    return out


def by_hand_lines(card: dict) -> list[str]:
    """What the table will not do for this card, in the card's own words."""
    return [ln for ln, _, st in card_lines(card) if st == "manual"]


def report(cards_path: str) -> str:
    import make_deck_json_shim as shim
    cards = shim.load(cards_path)
    rows, by_shape = [], {}
    for c in sorted(cards, key=lambda x: x["name"]):
        for ln, shape, st in card_lines(c):
            rows.append((c["name"], ln, shape, st))
            by_shape.setdefault(shape, {"handled": 0, "announced": 0, "manual": 0})[st] += 1
    n = {st: sum(1 for r in rows if r[3] == st) for st in ("handled", "announced", "manual")}
    whole = {}
    for c in cards:
        ss = [st for _, _, st in card_lines(c)]
        whole[c["name"]] = "no rules text" if not ss else "fully handled" if all(s == "handled" for s in ss) else "has manual lines" if "manual" in ss else "needs a person's choice"
    tally = {k: sum(1 for v in whole.values() if v == k) for k in ("fully handled", "needs a person's choice", "has manual lines", "no rules text")}
    out = ["# Star Trek set: what the table really does, card by card", "",
           "Generated by `table/card_coverage.py` from the card data, read against the real engine (`player.effect_known` runs the engine's own code).",
           "Regenerate after any engine change; the same list drives the \"By hand:\" note the table says when a card lands.", "",
           "Status: **handled** = the engine does it (it may ask you to aim or choose). **announced** = the table shows it and offers a button or reminder; a person finishes it.",
           "**manual** = the table can't; it says so at the table.", "",
           f"Cards: {len(cards)} ({', '.join(f'{v} {k}' for k, v in tally.items())}).",
           f"Ability lines: {len(rows)} ({n['handled']} handled, {n['announced']} announced, {n['manual']} manual).", "",
           "## Count by shape", "", "| shape | handled | announced | manual |", "|---|---:|---:|---:|"]
    for shape, d in sorted(by_shape.items(), key=lambda kv: (-sum(kv[1].values()), kv[0])):
        out.append(f"| {shape} | {d['handled'] or ''} | {d['announced'] or ''} | {d['manual'] or ''} |")
    out += ["", "## Every card", "", "| card | ability | shape | status |", "|---|---|---|---|"]
    for name, ln, shape, st in rows:
        out.append(f"| {name} | {ln.replace('|', '/')[:200]} | {shape} | {st} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    print(report(sys.argv[1]))
