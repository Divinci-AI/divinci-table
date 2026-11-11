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
    deck_kind = f"Commander deck (commander {p.commander['name']})" if p.commander else "40-card deck (no commander)"
    return (f"You are {p.name}, playing the {p.deck_name} {deck_kind} "
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

    said += p.impulse_expire()                           # (a window that closed with a turn the table didn't report)
    land = p.best_land()
    xland = next((c for c in p.impulse_cards() if "Land" in (c.get("types") or [])), None)
    if xland:                                            # a land it may play from exile goes first: it won't wait, the hand's will
        said.append(p.play_land(xland, from_exile=True))
    elif land:
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

    for _ in range(MAX_CASTS):                           # cards it may play from exile first (they expire), the biggest it can pay for
        xo = p.exile_castable()
        if not xo:
            break
        label, card, pay, _c = max(xo, key=lambda o: o[1].get("manaValue") or 0)
        decisions.append({"q": "cast from exile", "choice": label, "p": 1.0, "options": len(xo)})
        said.append(p.cast(label, card, pay, False, choose_target, choose_modes, from_exile=True))

    for _ in range(MAX_CASTS):
        options = p.castable(guard_wipes=True)         # (its own choice: it never wipes its own board)
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

    if not p.land_played:                                # a spell above may have exiled a land it may play
        xland = next((c for c in p.impulse_cards() if "Land" in (c.get("types") or [])), None)
        if xland:
            said.append(p.play_land(xland, from_exile=True))

    # combat: every untapped creature that isn't summoning sick may attack
    rep_ = p.attack_report()                           # T16: the engine's rule (summoning sickness, defender, "can't attack alone")
    attackers = [c for c in p.creatures() if (rep_.get(c.id) or {}).get("can_attack")]
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
    lone_ = [c for cs in assigned.values() for c in cs]
    if len(lone_) == 1 and __import__("player").attacks_alone_banned(lone_[0]):
        assigned.clear()                               # T16: it can't attack alone, and everything else was held back
    for who, cs in assigned.items():
        for c in cs:
            if "Vigilance" not in c.keywords:
                c.tapped = True
        for c in cs:                                   # "whenever X attacks" Role/token triggers
            m = re.search(r"Whenever [^.]*?attacks[^,]*, ([^.]+)\.", c.card.get("text") or "")
            if m and "Role token" in m.group(1):
                fake = type(c)({**c.card, "text": f"When this enters, {m.group(1)}."})
                fake.id = c.id
                said += p.enter_effects(fake, choose_target)
        total = sum(p.stats(c)[0] for c in cs)
        said.append(f"I attack {who} with " + _and([p.shown(c) for c in cs]) + f", {total} damage.")
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


def resolve_block(p: VirtualPlayer, choice, amount: int, trample: bool, attacker: str | None, atk=None, atk_owner=None, more=()) -> dict:
    """The arithmetic of one attacker hitting this seat, blocked by `choice` (and `more`: menace) or unblocked. atk/atk_owner: the attacking
    permanent, when the table knows it, so its counters, Equipment, tokens and keywords count (first strike, double strike, deathtouch, lifelink,
    indestructible). Without it (a person's declared attacker) the attacker's printed toughness is the best there is.

    `amount` is the attacker's power: one strike. Combat damage is dealt in steps (rule 510): when anyone in this fight has first strike or
    double strike there is a first-strike step (first strike and double strike deal) and then the regular step (double strike and everyone
    without first strike deal, those still alive). Within a step all damage is simultaneous; deaths are checked between the steps.

    Returned: through (what reached this seat: trample over, or a blocked attacker whose blockers all died before it struck), and
    attacker_lifelink, the attacker's lifelink on what it dealt to BLOCKERS only: what reached the player is a combat hit, and the hit's own
    lifelink is the table's `damage` (player.combat_damage), so counting it here too would gain it twice."""
    who = attacker or "that"
    said = []
    if choice is None:
        p.life -= amount
        said.append(f"No blocks. I take {amount}; I'm at {p.life}.")
        return {"said": said, "blocker": None, "life": p.life, "through": amount, "attacker_lifelink": 0}
    blockers = [choice, *more]
    known = atk is not None and atk_owner is not None
    ak = set(atk_owner.kws(atk, my_turn=True)) if known else set()
    atk_touch, atk_ind, atk_link = "Deathtouch" in ak, "Indestructible" in ak, "Lifelink" in ak
    if known:
        atk_tough = atk_owner.stats(atk)[1]
    else:
        import oracle
        try:
            atk_tough = int((oracle.card(attacker) or {}).get("toughness")) if attacker else None
        except (TypeError, ValueError):
            atk_tough = None
    said.append(f"I block {who} with " + " and ".join(p.shown(b) for b in blockers) + ".")
    bk = {id(b): set(p.kws(b)) for b in blockers}

    def first(k):
        return "First strike" in k or "Double strike" in k

    def deals_in(k, step):
        return first(k) if step == "first" else ("Double strike" in k or "First strike" not in k)

    steps = ["first", "regular"] if first(ak) or any(first(k) for k in bk.values()) else ["regular"]
    alive, atk_alive = list(blockers), True
    atk_hurt, atk_touched = (atk.damage if known else 0), False
    touched, gone = set(), []
    to_blockers = through = back_total = 0
    hit_back_by = None
    for step in steps:
        assigned, step_through = {}, 0
        if atk_alive and deals_in(ak, step):
            left = amount
            if not alive:                               # blocked, and every blocker is gone: only trample carries on to the player (702.19e)
                step_through = left if trample else 0
            else:
                for i, b in enumerate(alive):
                    lethal = 1 if atk_touch else max(0, p.stats(b)[1] - b.damage)
                    hit = left if (i == len(alive) - 1 and not trample) else min(left, lethal)
                    assigned[id(b)] = (b, hit)
                    left -= hit
                step_through = left if trample else 0
        back, back_touch = 0, False
        for b in alive:                                 # the blockers' damage this step (simultaneous with the attacker's)
            pw = max(0, p.stats(b)[0])
            if pw and deals_in(bk[id(b)], step):
                back += pw
                back_touch = back_touch or "Deathtouch" in bk[id(b)]
                hit_back_by = hit_back_by or b
                if "Lifelink" in bk[id(b)]:
                    p.life += pw
                    p.gained_life(pw)                   # "whenever you gain life" hears a blocker's lifelink too
                    said.append(f"Lifelink: I gain {pw}.")
        for b, hit in assigned.values():
            if hit > 0:
                b.damage += hit
                to_blockers += hit
                if atk_touch:
                    touched.add(id(b))
        through += step_through
        if atk_alive and back:
            atk_hurt += back
            back_total += back
            atk_touched = atk_touched or back_touch
            if known:
                atk.damage += back
        for b in list(alive):                           # state-based actions between the steps
            if "Indestructible" not in bk[id(b)] and (b.damage >= p.stats(b)[1] or id(b) in touched):
                saved = p.destroy_saved(b) if p.stats(b)[1] > 0 else None      # Dr. McCoy: the counters come off and the damage heals instead
                if saved is not None:
                    touched.discard(id(b))
                    said += saved
                    if p.stats(b)[1] <= 0:
                        alive.remove(b)
                        gone.append(b)
                    continue
                alive.remove(b)
                gone.append(b)
        if atk_alive and atk_tough is not None and not atk_ind and back_total and (atk_hurt >= atk_tough or atk_touched):
            saved = atk_owner.destroy_saved(atk) if known else None
            if saved is not None:                       # (its controller's McCoy: it lives, healed, with its counters gone)
                said += saved
                atk_hurt, atk_touched = 0, False
                atk_tough = atk_owner.stats(atk)[1]
            else:
                atk_alive = False
    for b in gone:
        name = p.shown(b)
        p.move(f"#{b.id}", "graveyard", cause="combat")
        said.append(f"{name} dies.")
    if through:
        p.life -= through
        said.append(f"{through} tramples over; I'm at {p.life}." if trample else f"{through} gets through; I'm at {p.life}.")
    died = None
    if back_total:
        said.append(f"{p.shown(hit_back_by)} deals {back_total} back" + (f" — {who} dies." if not atk_alive else "."))
        if not atk_alive:
            died = attacker or who
    return {"said": said, "blocker": choice.name, "life": p.life, "attacker_died": died, "through": through,
            "attacker_lifelink": to_blockers if atk_link else 0}
