"""One full turn for a VirtualPlayer, decided by the local Gemma, announced for the table.

Code rules (each one fixed a measured failure in the harness):
  * the land drop is always taken, and code picks the land (small models skip land drops);
  * there is no "stop casting" option on its own turn — Gemma chooses WHICH affordable spell next,
    and the turn keeps going while anything is affordable (a 4B model passed on 66 % of castable
    turns when allowed to);
  * mana and legality are arithmetic in player.py, never the model's call.
Gemma decides: spell order, what an Aura/Role goes on, and whom each creature attacks.
"""
from __future__ import annotations

import re
import time

import voice
from player import VirtualPlayer, _short_text

MAX_CASTS = 10


def _context(p: VirtualPlayer, humans: list[dict], public_board: list[str]) -> str:
    """The DECISION layer's view. It includes the hand; persona replies never get this."""
    st = p.public()
    hand = "; ".join(f"{c['name']} ({c.get('manaCost') or 'land'}, {c.get('type')})" for c in p.hand)
    return (f"You are {p.name}, playing the {p.deck_name} Commander deck (commander {p.commander['name']}) "
            f"in a four-player game against: {', '.join(h['name'] for h in humans) or 'the table'}.\n"
            f"Turn {st['turn']}. Your life {st['life']}. Untapped mana {st['untapped_mana']}.\n"
            f"Your battlefield: {', '.join(st['battlefield']) or 'no creatures or enchantments'}; lands {st['lands']}.\n"
            f"Your hand: {hand}.\n"
            f"Cards the other players have announced: {', '.join(public_board[-12:]) or 'none yet'}.")


def _and(xs):
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def take_turn(p: VirtualPlayer, humans: list[dict], public_board: list[str]) -> dict:
    t0 = time.time()
    said, decisions = [], []
    p.turn += 1
    p.land_played = False
    for perm in p.battlefield:
        perm.tapped = False
        perm.sick = False
    before = len(p.hand)
    p.draw()
    said.append(f"{p.name}, turn {p.turn}." if len(p.hand) > before
                else f"{p.name}, turn {p.turn}. My library is empty.")

    land = p.best_land()
    if land:
        said.append(p.play_land(land))

    p.public_board = list(public_board)

    def choose_target(card, creatures):
        if creatures is None:                           # hostile Aura: one of THEIR announced cards
            opts = {str(i): n for i, n in enumerate(public_board[-26:])}
            probs = voice._ollama_choice(_context(p, humans, public_board),
                                         f"Which of your opponents' cards should get {card['name']} "
                                         f"({card.get('text') or ''})? Pick their most dangerous creature.", opts)
            k = max(probs, key=probs.get)
            decisions.append({"q": f"target for {card['name']}", "choice": opts[k], "p": round(probs[k], 2)})
            return opts[k]
        if len(creatures) == 1:
            return creatures[0]
        opts = {str(c.id): p.describe(c) for c in creatures}
        probs = voice._ollama_choice(_context(p, humans, public_board),
                                     f"Which of your creatures should get {card['name']} ({card.get('text') or ''})?",
                                     opts)
        pick = max(probs, key=probs.get)
        decisions.append({"q": f"target for {card['name']}", "choice": opts[pick], "p": round(probs[pick], 2)})
        return next(c for c in creatures if str(c.id) == pick)

    def choose_modes(card, spec):
        n, modes = spec
        picked = []
        for _ in range(n):
            left = [m for m in modes if m not in picked]
            opts = {str(i): m for i, m in enumerate(left)}
            probs = voice._ollama_choice(_context(p, humans, public_board),
                                         f"You cast {card['name']}. Choose a mode" + (" (another one)" if picked else "") + ":",
                                         opts)
            k = max(probs, key=probs.get)
            picked.append(opts[k])
            decisions.append({"q": f"mode for {card['name']}", "choice": opts[k], "p": round(probs[k], 2)})
        return picked

    for _ in range(MAX_CASTS):
        options = p.castable()
        if not options:
            break
        if len(options) == 1:
            label, card, pay, is_cmdr = options[0]
            pick_p = 1.0
        else:
            opts = {o[0]: f"{o[0]} — {o[1].get('manaCost')}, {o[1].get('type')}: {_short_text(o[1])[:110]}"
                    for o in options[:26]}
            probs = voice._ollama_choice(_context(p, humans, public_board),
                                         "It is your main phase. Which spell do you cast next?", opts)
            pick = max(probs, key=probs.get)
            label, card, pay, is_cmdr = next(o for o in options if o[0] == pick)
            pick_p = probs[pick]
        decisions.append({"q": "cast", "choice": label, "p": round(pick_p, 2), "options": len(options)})
        said.append(p.cast(label, card, pay, is_cmdr, choose_target, choose_modes))

    # combat: every untapped creature that isn't summoning sick may attack
    attackers = [c for c in p.creatures() if not c.tapped and not c.sick]
    assigned: dict[str, list] = {}
    for c in attackers:
        if not humans:
            break
        opts = {"hold": "Hold it back to block."}
        opts.update({h["name"]: f"Attack {h['name']}." for h in humans})
        probs = voice._ollama_choice(_context(p, humans, public_board),
                                     f"Your {p.describe(c)} can attack. Whom does it attack?", opts)
        pick = max(probs, key=probs.get)
        decisions.append({"q": f"attack with {c.name}", "choice": pick, "p": round(probs[pick], 2)})
        if pick != "hold":
            assigned.setdefault(pick, []).append(c)
    for who, cs in assigned.items():
        for c in cs:
            if "Vigilance" not in (c.card.get("keywords") or []):
                c.tapped = True
        for c in cs:                                   # "whenever X attacks" Role/token triggers
            m = re.search(r"Whenever [^.]*?attacks[^,]*, ([^.]+)\.", c.card.get("text") or "")
            if m and "Role token" in m.group(1):
                fake = type(c)({**c.card, "text": f"When this enters, {m.group(1)}."})
                fake.id = c.id
                said += p.enter_effects(fake, choose_target)
        total = sum(p.stats(c)[0] for c in cs)
        said.append(f"I attack {who} with " + _and([c.name for c in cs]) + f", {total} damage.")
    if not assigned and attackers:
        said.append("No attacks this turn.")
    said.append("That's my turn.")
    return {"said": said, "decisions": decisions, "public": p.public(), "ms": round((time.time() - t0) * 1000)}


def decide_block(p: VirtualPlayer, amount: int, attacker: str | None, trample: bool,
                 humans: list[dict], public_board: list[str]) -> dict:
    """Someone attacks this AI. Code owns the arithmetic and one rule — if the hit would be lethal
    and anything can block, something blocks; Gemma decides whether (and with what) otherwise.
    Returns {"said": [...], "blocker": name|None, "life": new life}."""
    blockers = [c for c in p.creatures() if not c.tapped]
    who = attacker or "that"
    choice = None
    # Code owns the combat arithmetic. A block is on the table only if the blocker survives, or the
    # hit really matters (lethal, or would leave it at 10 or less) — measured 2026-09-25: Gemma
    # chump-blocked a 1-power Llanowar Elves at 40 life, trading a creature for one point of life.
    import oracle
    atk_tough = None
    try:
        atk_tough = int((oracle.card(attacker) or {}).get("toughness")) if attacker else None
    except (TypeError, ValueError):
        pass
    matters = p.life - amount <= 10
    blockers = [c for c in blockers if p.stats(c)[1] > amount or matters]
    if blockers:
        opts = {"none": f"Don't block. Take {amount}."}
        for c in blockers:
            pw, tg = p.stats(c)
            fate = "survives" if tg > amount else "dies"
            kill = "" if atk_tough is None else (" and kills it" if pw >= atk_tough else "")
            opts[str(c.id)] = f"Block with {p.describe(c)} — it {fate}{kill}."
        probs = voice._ollama_choice(_context(p, humans, public_board),
                                     f"{who} attacks you for {amount}{' with trample' if trample else ''}. "
                                     f"You are at {p.life} life. Do you block?", opts)
        pick = max(probs, key=probs.get)
        if pick == "none" and p.life - amount <= 5:        # never die (or drop to ≤5) with a blocker available
            pick = str(max(blockers, key=lambda c: p.stats(c)[1]).id)
        choice = next((c for c in blockers if str(c.id) == pick), None)
    return resolve_block(p, choice, amount, trample, attacker)


def resolve_block(p: VirtualPlayer, choice, amount: int, trample: bool, attacker: str | None) -> dict:
    """The arithmetic of one attacker hitting this AI, blocked by `choice` or unblocked."""
    who = attacker or "that"
    said = []
    if choice is None:
        p.life -= amount
        said.append(f"No blocks. I take {amount}; I'm at {p.life}.")
        return {"said": said, "blocker": None, "life": p.life}
    pw, tg = p.stats(choice)
    said.append(f"I block {who} with {choice.name}.")
    import oracle
    try:
        atk_tough = int((oracle.card(attacker) or {}).get("toughness")) if attacker else None
    except (TypeError, ValueError):
        atk_tough = None
    through = max(0, amount - tg) if trample else 0
    if tg <= amount:
        p.move(f"#{choice.id}", "graveyard")
        said.append(f"{choice.name} dies.")
    if through:
        p.life -= through
        said.append(f"{through} tramples over; I'm at {p.life}.")
    died = None
    if pw and atk_tough is not None and pw >= atk_tough:
        said.append(f"{choice.name} deals {pw} back — {who} dies.")
        died = attacker
    elif pw:
        said.append(f"{choice.name} deals {pw} back.")
    return {"said": said, "blocker": choice.name, "life": p.life, "attacker_died": died}
