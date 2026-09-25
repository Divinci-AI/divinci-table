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
    said.append(f"{p.name}'s turn {p.turn}. I untap and draw." if len(p.hand) > before
                else f"{p.name}'s turn {p.turn}. I untap; my library is empty.")

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
        said.append(f"I attack {who} with " + ", ".join(p.describe(c).replace(' [tapped]', '') for c in cs)
                    + f" — {total} damage if unblocked.")
    if not assigned and attackers:
        said.append("No attacks this turn.")
    said.append("That's my turn.")
    return {"said": said, "decisions": decisions, "public": p.public(), "ms": round((time.time() - t0) * 1000)}
