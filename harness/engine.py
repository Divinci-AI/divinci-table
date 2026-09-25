"""Simplified 4-player Commander engine. CODE owns every rule; agents only pick among the
legal options the engine enumerates ("select instead of generate").

Modelled: 40 life, 21 combat damage from one commander eliminates, command zone + tax,
summoning sickness, land-per-turn, mana rocks/dorks, attacking any opponent, multi-block,
flying/reach, deathtouch, lifelink, trample, vigilance, haste, drawing from an empty library.

NOT modelled (stated so nobody reads a result as a rules-accurate one): the stack and instant
speed (every spell is a sorcery), mulligans, activated abilities beyond mana, damage-order
choice (the engine assigns lethal damage in block order), politics/deals, the 100-card
singleton rule (decks are 40 cards with duplicates).
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

from cards import CARDS, COMMANDERS, BASIC, build_deck

STARTING_LIFE = 40
CMDR_LETHAL = 21
MAX_OPTIONS = 26          # djev-run labels choices a..z
MAX_ACTIONS_PER_PHASE = 12
_ids = itertools.count(1)


@dataclass(eq=False)          # identity semantics: two Goblin tokens are different objects
class Perm:
    name: str
    controller: int
    owner: int
    is_commander: bool = False
    tapped: bool = False
    sick: bool = True
    damage: int = 0
    deathtouched: bool = False
    counters: int = 0
    eot_bonus: int = 0
    eot_trample: bool = False
    id: int = field(default_factory=lambda: next(_ids))

    @property
    def card(self):
        return CARDS[self.name]

    @property
    def kind(self):
        return self.card["kind"]

    @property
    def power(self):
        return self.card["pt"][0] + self.counters + self.eot_bonus

    @property
    def toughness(self):
        return self.card["pt"][1] + self.counters + self.eot_bonus

    def has(self, kw):
        return kw in self.card.get("kw", []) or (kw == "trample" and self.eot_trample)

    @property
    def token(self):
        return bool(self.card.get("token"))

    def label(self):
        """Short unique-ish description used in states and option keys."""
        if self.kind != "creature":
            return f"{self.name}#{self.id}"
        kws = " ".join(k for k in ("flying", "reach", "deathtouch", "lifelink", "trample", "haste",
                                   "vigilance") if self.has(k))
        return f"{self.name}#{self.id} {self.power}/{self.toughness}" + (f" {kws}" if kws else "")


@dataclass
class Player:
    seat: int
    colour: str
    commander: str
    library: list
    hand: list = field(default_factory=list)
    graveyard: list = field(default_factory=list)
    life: int = STARTING_LIFE
    cmdr_in_zone: bool = True
    cmdr_casts: int = 0
    cmdr_damage_taken: dict = field(default_factory=dict)   # attacker seat -> damage
    land_played: bool = False
    alive: bool = True
    drew_from_empty: bool = False
    eliminated_turn: int | None = None
    eliminated_reason: str | None = None

    @property
    def name(self):
        return f"Seat {self.seat + 1} ({self.commander.split(',')[0]})"


class Game:
    def __init__(self, agents, colours=("U", "B", "R", "G"), seed=0, round_cap=25, log_state=True,
                 auto_land=False):
        # auto_land: a rule every seat gets — play a land at the start of each main phase when one
        # is in hand and none was played this turn. Added 2026-09-24 after so1+Qwen3.5-4B passed on
        # its land drop 165 times; it keeps "does the model know Magic basics" out of a comparison
        # meant to measure judgment. Decks are mono-coloured, so which basic is never a choice.
        self.auto_land = auto_land
        self.rng = random.Random(seed)
        self.seed = seed
        self.agents = agents
        self.round_cap = round_cap
        self.players = []
        for s, c in enumerate(colours):
            lib = build_deck(c)
            self.rng.shuffle(lib)
            self.players.append(Player(seat=s, colour=c, commander=COMMANDERS[c], library=lib))
        self.battlefield: list[Perm] = []
        self.round = 0
        self.active = 0
        self.phase = "setup"
        self.log: list[dict] = []
        self.winner = None
        self.log_state = log_state
        for p in self.players:
            for _ in range(7):
                self.draw(p, quiet=True)

    # ── logging ────────────────────────────────────────────────────────────────
    def snapshot(self):
        return {
            "round": self.round, "active": self.active, "phase": self.phase,
            "players": [{
                "seat": p.seat, "name": p.name, "life": p.life, "alive": p.alive,
                "hand": len(p.hand), "library": len(p.library),
                "cmdr_in_zone": p.cmdr_in_zone, "tax": 2 * p.cmdr_casts,
                "cmdr_damage_taken": {str(k): v for k, v in p.cmdr_damage_taken.items() if v},
                "board": [{"id": x.id, "name": x.name, "kind": x.kind, "tapped": x.tapped,
                           "pt": [x.power, x.toughness] if x.kind == "creature" else None,
                           "commander": x.is_commander, "token": x.token}
                          for x in self.battlefield if x.controller == p.seat],
            } for p in self.players],
        }

    def event(self, text):
        entry = {"t": "event", "text": text}
        if self.log_state:
            entry["snap"] = self.snapshot()
        self.log.append(entry)

    # ── helpers ────────────────────────────────────────────────────────────────
    def living(self):
        return [p for p in self.players if p.alive]

    def opponents(self, p):
        return [o for o in self.players if o.alive and o.seat != p.seat]

    def perms(self, seat=None, kind=None):
        return [x for x in self.battlefield
                if (seat is None or x.controller == seat) and (kind is None or x.kind == kind)]

    def draw(self, p, n=1, quiet=False):
        for _ in range(n):
            if not p.library:
                p.drew_from_empty = True
                continue
            p.hand.append(p.library.pop())
            # Sheoldred triggers
            for sh in self.perms(kind="creature"):
                if sh.name != "Sheoldred, the Apocalypse" or not self.players[sh.controller].alive:
                    continue
                if sh.controller != p.seat:
                    p.life -= 1
                    if not quiet:
                        self.event(f"Sheoldred drains {p.name} for 1 on their draw.")

    # ── mana ───────────────────────────────────────────────────────────────────
    def sources(self, p):
        out = []
        for x in self.perms(p.seat):
            if x.tapped:
                continue
            c = x.card
            if x.kind == "land":
                out.append((x, 1, "col"))
            elif "mana" in c and not (x.kind == "creature" and x.sick):
                out.append((x, c["mana"], "C" if c["mana_colour"] == "C" else "col"))
        return out

    def available_mana(self, p):
        return sum(a for _, a, _ in self.sources(p))

    def cost_of(self, p, name, commander=False):
        c = CARDS[name]
        g, col = c["cost"]
        mod = c.get("cost_mod")
        if mod == "per_creature":
            g = max(0, g - len(self.perms(kind="creature")))
        elif mod == "own_power":
            g = max(0, g - sum(x.power for x in self.perms(p.seat, "creature")))
        if commander:
            g += 2 * p.cmdr_casts
        return g, col

    def can_pay(self, p, cost):
        g, col = cost
        src = self.sources(p)
        coloured = sum(a for _, a, t in src if t == "col")
        total = sum(a for _, a, _ in src)
        return coloured >= col and total >= g + col

    def pay(self, p, cost):
        g, col = cost
        src = self.sources(p)
        # colourless first for generic, then coloured for pips, then coloured for the rest
        for x, a, t in sorted(src, key=lambda s: s[2] != "C"):
            if t == "C" and g > 0:
                x.tapped = True
                g -= a
        for x, a, t in src:
            if x.tapped or t != "col":
                continue
            if col > 0:
                x.tapped = True
                col -= a
            elif g > 0:
                x.tapped = True
                g -= a
        assert col <= 0 and g <= 0, "pay() called on an unaffordable cost"

    # ── zone changes ───────────────────────────────────────────────────────────
    def leave_battlefield(self, x, to="graveyard"):
        if x not in self.battlefield:
            return
        self.battlefield.remove(x)
        owner = self.players[x.owner]
        if x.token:
            return
        if x.is_commander:
            owner.cmdr_in_zone = True          # owner always chooses the command zone
            return
        (owner.hand if to == "hand" else owner.graveyard).append(x.name)

    def create(self, name, seat, sick=True):
        x = Perm(name=name, controller=seat, owner=seat, sick=sick)
        self.battlefield.append(x)
        return x

    # ── the decision interface ─────────────────────────────────────────────────
    def ask(self, p, kind, questions, context=None):
        """questions: list of dict(id, prompt, options=[(key, description)]). Returns id -> key.
        Options are capped at MAX_OPTIONS here, in ONE place, so no caller can exceed it."""
        for q in questions:
            q["options"] = q["options"][:MAX_OPTIONS]
        agent = self.agents[p.seat]
        res = agent.decide(self, p, kind, questions, context or {})
        entry = {"t": "decision", "seat": p.seat, "agent": agent.name, "kind": kind,
                 "questions": [{"id": q["id"], "prompt": q["prompt"],
                                "options": [k for k, _ in q["options"]]} for q in questions],
                 **res.log}
        if self.log_state:
            entry["snap"] = self.snapshot()
        self.log.append(entry)
        return res.choices

    # ── turn structure ─────────────────────────────────────────────────────────
    def run(self):
        self.event("Game start. " + ", ".join(p.name for p in self.players) + ".")
        first = True
        while self.round < self.round_cap and len(self.living()) > 1:
            self.round += 1
            for p in self.players:
                if not p.alive or len(self.living()) <= 1:
                    continue
                self.turn(p, skip_draw=first)
                first = False
        alive = self.living()
        if not alive:                                   # everyone left died at once
            self.winner = None
            self.event("All remaining players were eliminated simultaneously — a draw.")
        elif len(alive) == 1:
            self.winner = alive[0].seat
            self.event(f"{alive[0].name} wins — last player standing.")
        else:
            best = max(alive, key=lambda q: q.life)
            tied = [q for q in alive if q.life == best.life]
            self.winner = best.seat if len(tied) == 1 else None
            self.event(f"Round cap ({self.round_cap}) reached. " +
                       (f"{best.name} leads on life." if self.winner is not None else "Tied on life."))
        return self.winner

    def turn(self, p, skip_draw=False):
        self.active = p.seat
        p.land_played = False
        for x in self.perms(p.seat):
            x.tapped = False
            x.sick = False
        self.phase = "draw"
        if not skip_draw:
            self.draw(p)
        self.check_losses()
        if not p.alive:
            return
        self.event(f"Round {self.round}: {p.name}'s turn ({p.life} life, {len(p.hand)} cards).")
        self.main(p, "main1")
        if p.alive:
            self.combat(p)
        if p.alive:
            self.main(p, "main2")
        # cleanup
        self.phase = "cleanup"
        while len(p.hand) > 7:
            worst = max(p.hand, key=lambda n: sum(CARDS[n].get("cost", (0, 0))))
            p.hand.remove(worst)
            p.graveyard.append(worst)
        for x in self.battlefield:
            x.damage = 0
            x.deathtouched = False
            x.eot_bonus = 0
            x.eot_trample = False

    # ── main phase ─────────────────────────────────────────────────────────────
    def main_options(self, p, phase):
        opts = []
        if not p.land_played:
            for n in sorted({n for n in p.hand if CARDS[n]["kind"] == "land"}):
                opts.append((f"play {n}", "Play a land (one per turn)."))
        if p.cmdr_in_zone:
            cost = self.cost_of(p, p.commander, commander=True)
            if self.can_pay(p, cost):
                opts.append((f"cast commander {p.commander}",
                             f"Cost {cost[0]} generic + {cost[1]} {p.colour} (includes tax "
                             f"{2 * p.cmdr_casts}). {CARDS[p.commander]['text']}"))
        seen = set()
        for n in p.hand:
            c = CARDS[n]
            if c["kind"] == "land" or n in seen:
                continue
            seen.add(n)
            cost = self.cost_of(p, n)
            if self.can_pay(p, cost) and self.has_legal_targets(p, n):
                opts.append((f"cast {n}", f"Cost {cost[0]}+{cost[1]}{p.colour}. {c['text']}"))
        opts.append(("go to combat", "Stop casting and move to combat.") if phase == "main1"
                    else ("end turn", "Pass the turn to the next player."))
        return opts

    def has_legal_targets(self, p, name):
        eff = CARDS[name].get("effect")
        if eff == "destroy_creature":
            return bool(self.perms(kind="creature"))
        if eff == "bounce_opp_nonland":
            return any(x.kind != "land" for o in self.opponents(p) for x in self.perms(o.seat))
        if eff == "beast_within":
            return bool(self.battlefield)
        return True

    def main(self, p, phase):
        self.phase = phase
        if self.auto_land and not p.land_played:
            land = next((n for n in p.hand if CARDS[n]["kind"] == "land"), None)
            if land:
                self.do_action(p, f"play {land}")
        for _ in range(MAX_ACTIONS_PER_PHASE):
            opts = self.main_options(p, phase)
            if len(opts) == 1:
                return
            q = dict(id="action", options=opts,
                     prompt=f"It is your {'pre-combat' if phase == 'main1' else 'post-combat'} "
                            f"main phase with {self.available_mana(p)} mana available. Pick your "
                            f"single best next action.")
            choice = self.ask(p, "main", [q])["action"]
            if choice in ("go to combat", "end turn"):
                return
            self.do_action(p, choice)
            self.check_losses()
            if not p.alive or len(self.living()) <= 1:
                return

    def do_action(self, p, choice):
        if choice.startswith("play "):
            n = choice[5:]
            p.hand.remove(n)
            p.land_played = True
            self.create(n, p.seat, sick=False)
            self.event(f"{p.name} plays {n}.")
            return
        if choice.startswith("cast commander "):
            cost = self.cost_of(p, p.commander, commander=True)
            self.pay(p, cost)
            p.cmdr_in_zone = False
            p.cmdr_casts += 1
            x = self.create(p.commander, p.seat, sick=not CARDS[p.commander].get("kw", []).count("haste"))
            x.is_commander = True
            self.event(f"{p.name} casts their commander {p.commander} for {sum(cost)} mana.")
            return
        n = choice[5:]
        c = CARDS[n]
        cost = self.cost_of(p, n)
        self.pay(p, cost)
        p.hand.remove(n)
        self.event(f"{p.name} casts {n}.")
        if c["kind"] in ("creature", "artifact"):
            x = self.create(n, p.seat, sick="haste" not in c.get("kw", []))
            if c.get("etb"):
                self.etb(p, x, c["etb"])
        else:
            self.resolve_sorcery(p, n, c["effect"])
            p.graveyard.append(n)
            for t in self.perms(p.seat, "creature"):
                if t.name == "Talrand, Sky Summoner":
                    self.create("Drake", p.seat)
                    self.event("Talrand's trigger creates a 2/2 flying Drake.")
        self.state_based()

    # ── targeting ──────────────────────────────────────────────────────────────
    def choose_target(self, p, source, candidates, include_players=False):
        """candidates: list of Perm. Returns a Perm, or a Player if include_players."""
        opts, lookup = [], {}
        if include_players:
            for o in self.opponents(p):
                k = f"player {o.name}"
                opts.append((k, f"{o.life} life"))
                lookup[k] = o
        # most relevant first so the 26-cap drops the least interesting targets
        for x in sorted(candidates, key=lambda x: -(x.power if x.kind == "creature" else 2)):
            owner = "yours" if x.controller == p.seat else self.players[x.controller].name
            k = f"{x.label()} ({owner})"
            opts.append((k, "your own permanent" if x.controller == p.seat else f"controlled by {owner}"))
            lookup[k] = x
        if not opts:
            return None
        q = dict(id="target", options=opts,
                 prompt=f"You are resolving {source}: {CARDS[source]['text']} Choose its target.")
        return lookup[self.ask(p, "target", [q], {"source": source})["target"]]

    # ── effects ────────────────────────────────────────────────────────────────
    def etb(self, p, x, etb):
        if etb == "draw2":
            self.draw(p, 2)
        elif etb == "bounce_creature":
            t = self.choose_target(p, x.name, [c for c in self.perms(kind="creature") if c is not x]
                                   or [x])
            if t:
                self.leave_battlefield(t, to="hand")
                self.event(f"{x.name} returns {t.name} to its owner's hand.")
        elif etb == "gary":
            dev = sum(CARDS[y.name]["cost"][1] for y in self.perms(p.seat)
                      if not y.token and "cost" in CARDS[y.name] and y.kind != "land")
            lost = 0
            for o in self.opponents(p):
                o.life -= dev
                lost += dev
            p.life += lost
            self.event(f"Gray Merchant drains each opponent for {dev} ({p.name} gains {lost}).")
        elif etb == "goblin_token":
            self.create("Goblin", p.seat)
        elif etb == "gain5":
            p.life += 5
        elif etb == "hoof":
            mine = self.perms(p.seat, "creature")
            for y in mine:
                y.eot_bonus += len(mine)
                y.eot_trample = True
            self.event(f"Craterhoof gives {len(mine)} creatures +{len(mine)}/+{len(mine)} and trample.")

    def resolve_sorcery(self, p, n, eff):
        if eff in ("draw1", "draw2", "draw3"):
            self.draw(p, int(eff[-1]))
        elif eff == "draw2_lose2":
            self.draw(p, 2)
            p.life -= 2
        elif eff == "destroy_creature":
            t = self.choose_target(p, n, self.perms(kind="creature"))
            self.event(f"{n} destroys {t.name}.")
            self.leave_battlefield(t)
        elif eff == "bounce_opp_nonland":
            t = self.choose_target(p, n, [x for o in self.opponents(p) for x in self.perms(o.seat)
                                          if x.kind != "land"])
            self.event(f"{n} returns {t.name} to its owner's hand.")
            self.leave_battlefield(t, to="hand")
        elif eff == "beast_within":
            t = self.choose_target(p, n, list(self.battlefield))
            self.event(f"Beast Within destroys {t.name}; its controller gets a 3/3 Beast.")
            self.leave_battlefield(t)
            self.create("Beast", t.controller)
        elif eff == "bolt":
            t = self.choose_target(p, n, self.perms(kind="creature"), include_players=True)
            if isinstance(t, Player):
                t.life -= 3
                self.event(f"Lightning Bolt hits {t.name} for 3.")
            else:
                t.damage += 3
                self.event(f"Lightning Bolt hits {t.name} for 3.")
        elif eff == "wrath":
            for x in self.perms(kind="creature"):
                self.leave_battlefield(x)
            self.event("Damnation destroys all creatures.")
        elif eff == "act":
            for x in self.perms(kind="creature"):
                x.damage += 13
            self.event("Blasphemous Act deals 13 damage to each creature.")
        elif eff in ("ramp1", "cultivate"):
            basic = BASIC[p.colour]
            if basic in p.library:
                p.library.remove(basic)
                self.create(basic, p.seat, sick=False).tapped = True
            if eff == "cultivate" and basic in p.library:
                p.library.remove(basic)
                p.hand.append(basic)
            self.rng.shuffle(p.library)

    def state_based(self):
        for x in list(self.perms(kind="creature")):
            if x.damage >= x.toughness or (x.deathtouched and x.damage > 0):
                self.leave_battlefield(x)
                self.event(f"{x.name} ({self.players[x.controller].name}) dies.")

    def check_losses(self):
        for p in self.players:
            if not p.alive:
                continue
            reason = None
            if p.life <= 0:
                reason = f"life reached {p.life}"
            elif p.drew_from_empty:
                reason = "drew from an empty library"
            else:
                for s, d in p.cmdr_damage_taken.items():
                    if d >= CMDR_LETHAL:
                        reason = f"took {d} commander damage from {self.players[s].name}"
            if reason:
                p.alive = False
                p.eliminated_turn = self.round
                p.eliminated_reason = reason
                for x in self.perms(p.seat):
                    self.battlefield.remove(x)
                # things they owned but someone else controls also leave (simplified)
                self.battlefield = [x for x in self.battlefield if x.owner != p.seat]
                self.event(f"☠ {p.name} is eliminated: {reason}.")

    # ── combat ─────────────────────────────────────────────────────────────────
    def combat(self, p):
        self.phase = "combat"
        eligible = [x for x in self.perms(p.seat, "creature") if not x.tapped and not x.sick]
        opps = self.opponents(p)
        if not eligible or not opps:
            return
        questions = []
        for x in eligible:
            opts = [("hold back", "Do not attack; stay untapped to block on opponents' turns.")]
            for o in opps:
                blockers = [b for b in self.perms(o.seat, "creature") if not b.tapped]
                can_block = [b for b in blockers if not x.has("flying") or b.has("flying") or b.has("reach")]
                opts.append((f"attack {o.name}",
                             f"{o.life} life; {len(can_block)} untapped creature(s) able to block it"
                             + (f"; commander damage from you so far {o.cmdr_damage_taken.get(p.seat, 0)}"
                                if x.is_commander else "")))
            questions.append(dict(id=f"atk_{x.id}", options=opts,
                                  prompt=f"Your creature {x.label()}"
                                         f"{' (your COMMANDER)' if x.is_commander else ''} can attack. "
                                         f"Choose which opponent it attacks, or hold it back."))
        choices = {}
        for i in range(0, len(questions), 10):        # stay well inside djev's 128-token canvas
            choices.update(self.ask(p, "attack", questions[i:i + 10], {"attackers": len(eligible)}))
        attacks = {}   # perm -> defender Player
        for x in eligible:
            c = choices.get(f"atk_{x.id}", "hold back")
            if c.startswith("attack "):
                attacks[x] = next(o for o in opps if f"attack {o.name}" == c)
        if not attacks:
            self.event(f"{p.name} does not attack.")
            return
        for x, d in attacks.items():
            if not x.has("vigilance"):
                x.tapped = True
        self.event(f"{p.name} attacks: " + "; ".join(f"{x.label()} → {d.name}" for x, d in attacks.items()))
        # attack triggers
        hellriders = [h for h in self.perms(p.seat, "creature") if h.name == "Hellrider"]
        for x, d in list(attacks.items()):
            if hellriders:
                d.life -= len(hellriders)
            if x.card.get("attack") == "krenko":
                x.counters += 1
                for _ in range(x.power):
                    self.create("Goblin", p.seat)
                self.event(f"Krenko grows to {x.power}/{x.toughness} and makes {x.power} Goblins.")
        if hellriders:
            self.event(f"Hellrider pings each defending player once per attacker.")
        self.check_losses()
        # blocks, per defender
        blocks = {x: [] for x in attacks}
        for d in {d.seat: d for d in attacks.values()}.values():
            if not d.alive:
                continue
            incoming = [x for x, dd in attacks.items() if dd is d and x in self.battlefield]
            blockers = [b for b in self.perms(d.seat, "creature") if not b.tapped]
            qs = []
            for b in blockers:
                legal = [x for x in incoming if not x.has("flying") or b.has("flying") or b.has("reach")]
                if not legal:
                    continue
                opts = [("no block", "Keep this creature out of combat.")]
                for x in legal:
                    opts.append((f"block {x.label()}",
                                 f"attacker from {p.name}" + (" — their COMMANDER (commander damage)"
                                                              if x.is_commander else "")))
                qs.append(dict(id=f"blk_{b.id}", options=opts,
                               prompt=f"You are being attacked. Your untapped {b.label()} may block "
                                      f"one attacker. Choose which attacker to block, or no block."))
            if not qs:
                continue
            total = sum(x.power for x in incoming)
            ans = {}
            for i in range(0, len(qs), 10):
                ans.update(self.ask(d, "block", qs[i:i + 10],
                                    {"incoming": [x.label() for x in incoming], "incoming_power": total}))
            for b in blockers:
                c = ans.get(f"blk_{b.id}", "no block")
                if c.startswith("block "):
                    tgt = next((x for x in incoming if f"block {x.label()}" == c), None)
                    if tgt:
                        blocks[tgt].append(b)
            blk = [f"{b.name} blocks {x.name}" for x, bs in blocks.items() if attacks[x] is d for b in bs]
            if blk:
                self.event(f"{d.name} blocks: " + "; ".join(blk))
        # damage (simultaneous)
        for x, d in attacks.items():
            if x not in self.battlefield:
                continue
            bs = [b for b in blocks[x] if b in self.battlefield]
            dealt = 0
            if bs:
                remaining = x.power
                for b in bs:
                    need = 1 if x.has("deathtouch") else max(0, b.toughness - b.damage)
                    amt = min(remaining, need) if b is not bs[-1] or x.has("trample") else remaining
                    b.damage += amt
                    b.deathtouched |= x.has("deathtouch") and amt > 0
                    dealt += amt
                    remaining -= amt
                    x.damage += b.power
                    x.deathtouched |= b.has("deathtouch") and b.power > 0
                    if b.has("lifelink"):
                        self.players[b.controller].life += b.power
                if x.has("trample") and remaining > 0 and d.alive:
                    d.life -= remaining
                    dealt += remaining
                    if x.is_commander:
                        d.cmdr_damage_taken[p.seat] = d.cmdr_damage_taken.get(p.seat, 0) + remaining
            elif d.alive:
                d.life -= x.power
                dealt = x.power
                if x.is_commander:
                    d.cmdr_damage_taken[p.seat] = d.cmdr_damage_taken.get(p.seat, 0) + x.power
            if x.has("lifelink"):
                p.life += dealt
        summary = ", ".join(f"{d.name} {d.life}" for d in {d.seat: d for d in attacks.values()}.values())
        self.event(f"Combat damage dealt. Life now: {summary}.")
        self.state_based()
        self.check_losses()

    # ── the view an agent gets ─────────────────────────────────────────────────
    def view(self, p):
        """Compact JSON state from p's perspective. Kept small: djev's max_model_len is 4096
        tokens including the system text and every option."""
        def board(seat):
            lands = [x for x in self.perms(seat, "land")]
            out = [f"{len(lands)} lands ({sum(not x.tapped for x in lands)} untapped)"] if lands else []
            for x in self.perms(seat):
                if x.kind == "land":
                    continue
                s = x.label() + (" COMMANDER" if x.is_commander else "") + (" [tapped]" if x.tapped else "")
                s += " [summoning sick]" if x.kind == "creature" and x.sick and seat == p.seat else ""
                out.append(s)
            return out
        return {
            "game": "4-player Commander (simplified). 40 life. 21 combat damage from one commander "
                    "eliminates you. Last player standing wins. All spells are sorcery-speed.",
            "round": self.round,
            "active_player": self.players[self.active].name,
            "you": {
                "name": p.name, "commander": p.commander, "life": p.life,
                "commander_in_command_zone": p.cmdr_in_zone, "commander_tax": 2 * p.cmdr_casts,
                "mana_available": self.available_mana(p), "land_played_this_turn": p.land_played,
                "hand": [f"{n}: {CARDS[n]['text']}" if CARDS[n]["kind"] != "land" else n for n in p.hand],
                "battlefield": board(p.seat), "library": len(p.library),
                "commander_damage_taken": {self.players[s].name: d for s, d in p.cmdr_damage_taken.items() if d},
            },
            "opponents": [{
                "name": o.name, "commander": o.commander, "life": o.life, "hand_size": len(o.hand),
                "battlefield": board(o.seat),
                "commander_damage_taken": {self.players[s].name: d for s, d in o.cmdr_damage_taken.items() if d},
            } for o in self.opponents(p)],
        }
