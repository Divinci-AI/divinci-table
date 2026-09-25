"""Whole games, by voice only: two simulated humans play a Commander game against the AI.

The humans exist only as speech: every land, spell, attack, damage report, removal spell and
question goes to the server as audio (macOS voices, a little room noise), and everything the AI
does comes back as the words it says. The simulator keeps its OWN ledger of the humans' life
totals from what was said, and checks the table's ledger against it after every line — the
bookkeeping a real table would argue about.

Checked on every line:  no server error · no card from the AI's hand in anything it says
Checked every AI turn:  at most one land · its announced attacks become the humans' damage
Checked continuously:   the table's life totals == the simulator's
Measured:               latency per kind of line (play, question, AI turn, attack on it)

  ~/.venvs/table/bin/python table/tests/game_sim.py                      # 2 games × 8 rounds
  ~/.venvs/table/bin/python table/tests/game_sim.py --games 5 --rounds 12 --seed 3
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import sense_run as S  # noqa: E402

SETUP = {"brain": "gemma", "ai": ["Claude|Ellivere of the Wild Court|Moira"], "ai_deck": "decks/ellivere.json",
         "humans": ["Michael|Ghalta, Primal Hunger", "Sam|Krenko, Mob Boss", "Jess|Atraxa, Praetors' Voice"]}
VOICE = {"Michael": "Daniel", "Sam": "Samantha", "Jess": "Karen"}
SETUP_COMMANDER = {"Michael": "Ghalta", "Sam": "Krenko", "Jess": "Atraxa"}
# Each human's (simplified) deck: what they might announce. Creatures carry their power from Oracle.
DECKS = {
    "Michael": {"lands": ["Forest", "Mountain", "Rugged Highlands", "Cinder Glade"],
                "spells": ["Llanowar Elves", "Cultivate", "Craterhoof Behemoth", "Ghalta, Primal Hunger",
                           "Rampant Growth", "Garruk's Uprising", "Etali, Primal Conqueror", "Kodama's Reach"],
                "removal": ["Beast Within", "Chaos Warp"]},
    "Sam": {"lands": ["Mountain", "Swamp", "Smoldering Marsh", "Bojuka Bog"],
            "spells": ["Goblin Instigator", "Krenko, Mob Boss", "Skirk Prospector", "Goblin Chieftain",
                       "Siege-Gang Commander", "Beetleback Chief", "Goblin Matron", "Legion Warboss"],
            "removal": ["Swords to Plowshares", "Doom Blade", "Lightning Bolt"]},
    "Jess": {"lands": ["Plains", "Island", "Command Tower", "Forest"],
             "spells": ["Atraxa, Praetors' Voice", "Evolution Sage", "Deepglow Skate", "Serra Angel",
                        "Rhystic Study", "Birds of Paradise", "Mulldrifter", "Arcane Signet"],
             "removal": ["Path to Exile", "Swords to Plowshares"]},
}
QUESTIONS = ["Claude, what's your life total?", "Claude, how many cards are in your hand?",
             "Claude, what do you have on the battlefield?", "Claude, what does Ellivere do?",
             "Claude, who's the biggest threat right now?", "Claude, how much mana do you have open?"]


class Sim:
    def __init__(self, run: S.Run, rng: random.Random, nrng, hear: bool):
        self.run, self.rng, self.nrng, self.hear = run, rng, nrng, hear
        self.humans = list(DECKS)
        self.life = {h: 40 for h in self.humans}
        self.board = {h: [] for h in self.humans}           # creatures each human has announced
        self.shown = 0                                      # cards shown to the camera instead of said
        self.errors: list[str] = []
        self.lat: dict[str, list[int]] = {}
        self.lines = 0

    def say(self, who: str, text: str, kind: str) -> dict:
        a = S.mix(S.say_wav(text, VOICE[who]), {"snr_db": 25, "rt60": 0.3, "drr_db": 8, "pad_s": 0.2}, self.nrng)
        hidden_before = self.run.hand()
        t0 = time.time()
        code, r = self.run.post("/api/utterance", S.to_wav(a), "audio/wav")
        ms = round((time.time() - t0) * 1000)
        self.lat.setdefault(kind, []).append(ms)
        self.lines += 1
        if code != 200:
            self.errors.append(f"{who}: {text!r} → HTTP {code} {r.get('error')}")
            return r
        reply = r.get("reply") or ""
        board = {p["name"] for p in self.run.get("/api/brain/state", brain=True).get("permanents", [])}
        hidden = [c for c in self.run.hand() if c in hidden_before and c not in board]   # a Forest in play is public
        played = {m.strip().lower() for m in re.findall(r"I (?:cast|play) ([^.,;!]+)", reply)}
        leak = [c for c in hidden if c.lower() not in played and re.search(r"\b" + re.escape(c.lower()) + r"\b", reply.lower())]
        if leak:
            self.errors.append(f"LEAK {leak} in reply to {text!r}: {reply!r}")
        if self.hear and reply:
            heard, cards, _ = S.hear_back(r.get("speech") or reply, "Moira")
            leak = [c for c in hidden if c in cards and c.lower() not in played]
            if leak:
                self.errors.append(f"AUDIBLE LEAK {leak}: {heard!r}")
        heard = r.get("heard", "")
        note = f"  (heard {heard!r}{', IGNORED: ' + r['ignored'] if r.get('ignored') else ''})" \
            if r.get("ignored") or S._norm(heard) != S._norm(text) else ""
        print(f"  {who:8} {text[:60]:60} → {(reply or '·')[:90]}  [{ms} ms]{note}", flush=True)
        if reply:                                  # people let the AI finish before the next line
            time.sleep(len(reply.split()) / 2.4 + 0.5)
        if kind == "life" and not re.search(r"\d", reply) and not reply.startswith("Who") \
                and not getattr(self, "_repeating", False):
            self._repeating = True                  # no "Jess, 36." back: a person would say it again
            try:
                return self.say(who, text, kind)
            finally:
                self._repeating = False
        if reply.startswith("Who's that?"):          # the table asked who spoke: answer with a name
            self.say(who, f"{who}.", "enroll")
        if ("say that again" in reply.lower() or "for how much" in reply.lower()) and not getattr(self, "_repeating", False):
            self._repeating = True                  # a person would repeat it, a bit more carefully
            try:
                return self.say(who, text, kind)
            finally:
                self._repeating = False
        return r

    def check_life(self, when: str):
        lt = self.run.get("/api/life")
        for h, v in self.life.items():
            if lt.get(h) != v:
                self.errors.append(f"{when}: table has {h} at {lt.get(h)}, the simulator at {v}")
                self.life[h] = lt.get(h)                  # resync so one slip isn't reported forever

    def enroll(self):
        for h in self.humans:
            r = self.say(h, f"Hi everyone, this is {h}, and I'm playing {SETUP_COMMANDER[h]} tonight.", "enroll")
            if r.get("enrolled") != h:
                self.errors.append(f"{h} did not enroll: {r.get('heard')!r} → {r.get('enrolled')}")

    def show(self, who: str, card: str) -> None:
        """Instead of naming it, the player holds the card up to the camera."""
        out = S.show_step(self.run, {"show": {"card": card, "by": who, "frames": 6, "scale": self.rng.choice([0.9, 0.5, 0.35])}},
                          self.nrng)
        self.shown += 1
        got = out["resp"].get("cards") or ([out["resp"]["card"]] if out["resp"].get("card") else [])
        if card not in got and not any(g.split(" // ")[0] == card for g in got):   # a DFC reads by its full name
            self.errors.append(f"{who} showed {card}; the camera read {got or out['resp'].get('status')}")
        print(f"  {who:8} {'[shows ' + card + ' to the camera]':60} → {(out.get('ai_text') or '·')[:80]}", flush=True)

    def human_turn(self, who: str, turn: int):
        d = DECKS[who]
        self.say(who, f"I play {self.rng.choice(d['lands'])}.", "play")
        if turn >= 2:
            card = self.rng.choice(d["spells"])
            if self.rng.random() < 0.3:
                self.show(who, card)
            else:
                self.say(who, f"I cast {card}.", "play")
            if S.oracle_type(card) and "Creature" in S.oracle_type(card):
                self.board[who].append((card, turn))
        import oracle
        other = self.rng.choice([h for h in self.humans if h != who])
        ready = [c for c, t in self.board[who] if t < turn]            # summoning sickness
        if ready and turn >= 3:
            attacker = self.rng.choice(ready)
            pw = oracle.power(attacker) or 1
            short = attacker.split(",")[0]
            target = self.rng.choice(["Claude", other])
            if target == "Claude":
                life0 = self.run.get("/api/ai/state")["life"]
                r = self.say(who, f"{short} attacks Claude for {pw}.", "attacked")
                reply = r.get("reply") or ""
                if r.get("awaiting") == "brain":
                    reply = self.wait_for_brain(r"block", self.brain_wait)
                life1 = self.run.get("/api/ai/state")["life"]
                m = re.search(r"I'm at (\d+)", reply)
                if m and int(m.group(1)) != life1:
                    self.errors.append(f"AI said it's at {m.group(1)} but the table has {life1}")
                reply = r.get("reply") or reply
                if not re.search(r"block", reply, re.I):
                    self.errors.append(f"attack on the AI not acknowledged: heard {r.get('heard')!r}, replied {reply!r}")
                if "No blocks" in reply and life1 != life0 - pw:
                    self.errors.append(f"unblocked {pw} from {short}: AI {life0} → {life1}")
            else:
                self.say(who, f"{short} attacks {other} for {pw}.", "play")
                if self.rng.random() < 0.5:                 # the defender says it — speaker ID decides whose life
                    self.say(other, f"No blocks, I take {pw}.", "life")
                else:
                    self.say(other, f"No blocks. {other} takes {pw}.", "life")
                self.life[other] -= pw
        if self.rng.random() < 0.25 and turn >= 3:
            spell = self.rng.choice(d["removal"])
            before = self.run.get("/api/ai/state")["battlefield"]
            try:
                line = self.run.fill(f"I cast {spell} on Claude's {{ai_creature}}.")
            except S.Skip:
                line = None
            if line:
                r = self.say(who, line, "removal")
                if r.get("awaiting") == "brain":
                    self.wait_for_brain(r".", self.brain_wait)
                after = self.run.get("/api/ai/state")["battlefield"]
                import oracle as O
                if O.effect(spell) in ("exile", "destroy") and len(after) >= len(before) \
                        and not re.search(r"hexproof|shroud|indestructible|isn't on", r.get("reply") or ""):
                    self.errors.append(f"{line!r}: AI board unchanged ({len(before)} → {len(after)})")
        if self.rng.random() < 0.3:
            self.say(who, self.rng.choice(QUESTIONS), "question")
        self.check_life(f"after {who}'s turn {turn}")

    def wait_for_brain(self, until: str, timeout: float) -> str:
        """External brain: the table waits (like people do) until the brain says `until`."""
        mark, t0, said = self.run.last_event(), time.time(), []
        while time.time() - t0 < timeout:
            evs = self.run.events_since(mark)
            if evs:
                mark = evs[-1]["id"]
            for e in evs:
                if e["type"] == "say" and e.get("action") != "filler":
                    said.append(e["text"])
                    print(f"  {'Claude':8} {'':60} ← {e['text'][:100]}", flush=True)
            if re.search(until, " ".join(said), re.I):
                return " ".join(said)
            time.sleep(0.5)
        self.errors.append(f"brain didn't say /{until}/ within {timeout:.0f} s")
        return " ".join(said)

    def ai_turn(self, turn: int):
        before = self.run.get("/api/ai/state")
        if self.run.setup.get("brain") == "external":
            self.say("Michael" if turn % 2 else "Sam", "Claude, your turn.", "ai-turn")
            reply = self.wait_for_brain(r"that'?s my turn|pass the turn|your turn", self.brain_wait)
            r = {"reply": reply}
        else:
            r = self.say("Michael" if turn % 2 else "Sam", "Claude, your turn.", "ai-turn")
        after = self.run.get("/api/ai/state")
        if after["turn"] != before["turn"] + 1:
            self.errors.append(f"'Claude, your turn.' did not start its turn ({before['turn']} → {after['turn']})")
        if after["lands"] - before["lands"] > 1:
            self.errors.append(f"AI played {after['lands'] - before['lands']} lands in one turn")
        for who, n in re.findall(r"I attack (\w+) with [^—]*?(?:,|—) (\d+) damage", r.get("reply") or ""):
            if who in self.life:                             # the table says: no blocks, take it
                self.say(who, f"No blocks, I take {n}." if self.rng.random() < 0.5 else f"No blocks, {who} takes {n}.", "life")
                self.life[who] -= int(n)
        self.check_life(f"after the AI's turn {turn}")


def health(srv, sim, elapsed) -> dict:
    """One soak sample: the server's memory and open sockets, whether Gemma is still pinned, and
    this game's latency."""
    import subprocess
    pid = str(srv.proc.pid)
    rss = int((subprocess.run(["ps", "-o", "rss=", "-p", pid], capture_output=True, text=True).stdout or "0").strip() or 0)
    socks = len(subprocess.run(["lsof", "-nP", "-a", "-p", pid, "-i"], capture_output=True, text=True).stdout.splitlines())
    try:
        import urllib.request
        ps = json.loads(urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=5).read())
        gemma = any("gemma" in m.get("name", "") for m in ps.get("models", []))
    except Exception:
        gemma = False
    all_ms = sorted(v for vs in sim.lat.values() for v in vs) or [0]
    return {"min": elapsed / 60, "rss_mb": rss // 1024, "sockets": socks, "gemma": gemma,
            "p50": all_ms[len(all_ms) // 2], "p95": all_ms[int(0.95 * (len(all_ms) - 1))]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--games", type=int, default=2)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--hear", action="store_true", help="also check every AI line after TTS→STT (slower)")
    ap.add_argument("--brain", choices=["gemma", "external"], default="gemma",
                    help="external: a person (or Claude via tablectl) plays the AI; the table waits for it")
    ap.add_argument("--brain-wait", type=float, default=180, help="seconds to wait for the external brain")
    ap.add_argument("--minutes", type=float, default=0, help="soak: keep playing games for this long and "
                    "track memory, latency, sockets and Gemma drift game by game")
    a = ap.parse_args()
    S.OUT.mkdir(parents=True, exist_ok=True)
    for d in DECKS.values():                               # the Oracle db must know every card we say
        import oracle
        missing = [c for c in d["spells"] + d["removal"] if not oracle.card(c)]
        assert not missing, missing
    SETUP["brain"] = a.brain
    srv = S.Server(SETUP, a.port)
    report, t0 = [], time.time()
    try:
        srv.wait_ready()
        from e2e_offline import EgressWatch
        watch = EgressWatch([a.port, 11434])
        watch.start()
        run = S.Run(srv, SETUP, hear=a.hear)
        soak = []
        g = -1
        while True:
            g += 1
            if a.minutes:
                if time.time() - t0 > a.minutes * 60:
                    break
            elif g >= a.games:
                break
            rng = random.Random(a.seed * 100 + g)
            run.post("/api/reset", {})
            sim = Sim(run, rng, np.random.default_rng(a.seed * 100 + g), a.hear)
            sim.brain_wait = a.brain_wait
            if a.brain == "external":
                print(f"brain: TABLE_URL={srv.base} TABLE_TOKEN_FILE={srv.token_file} table/tablectl.py watch", flush=True)
            print(f"\n══ game {g + 1}", flush=True)
            sim.enroll()
            for t in range(1, a.rounds + 1):
                print(f"— round {t}", flush=True)
                for h in sim.humans:
                    sim.human_turn(h, t)
                sim.ai_turn(t)
                if min(list(sim.life.values()) + [run.get("/api/ai/state")["life"]]) <= 0:
                    print("  (someone is dead — game over)")
                    break
            st = run.get("/api/ai/state")
            print(f"  end: AI turn {st['turn']}, life {st['life']}, lands {st['lands']}, board {st['battlefield'][:5]}")
            print(f"  table life {run.get('/api/life')} · simulator {sim.life}")
            for e in sim.errors:
                print(f"  ❌ {e}")
            report.append({"game": g + 1, "lines": sim.lines, "errors": sim.errors, "latency_ms": sim.lat,
                           "shown": sim.shown, "final": {"table": run.get("/api/life"), "sim": sim.life, "ai": st}})
            soak.append(health(srv, sim, time.time() - t0))
            h = soak[-1]
            print(f"  health: {h['min']:.0f} min · server RSS {h['rss_mb']} MB · sockets {h['sockets']} · "
                  f"Gemma loaded {h['gemma']} · p50 {h['p50']} ms p95 {h['p95']} ms", flush=True)
        watch.stop_flag = True
        watch.join()
        remote = sorted(watch.remote)
    finally:
        srv.stop()
    print("\n" + "═" * 70)
    lat: dict[str, list[int]] = {}
    for r in report:
        for k, v in r["latency_ms"].items():
            lat.setdefault(k, []).extend(v)
    for k, v in sorted(lat.items()):
        v = sorted(v)
        print(f"  {k:10} n={len(v):3}  p50 {statistics.median(v):6.0f} ms  p95 {v[int(0.95 * (len(v) - 1))]:6.0f} ms  max {v[-1]:6.0f}")
    if len(soak) >= 2:
        first, last = soak[0], soak[-1]
        print(f"  drift over {last['min']:.0f} min: RSS {first['rss_mb']} → {last['rss_mb']} MB · sockets "
              f"{first['sockets']} → {last['sockets']} · p95 {first['p95']} → {last['p95']} ms · Gemma loaded {last['gemma']}")
        if last["sockets"] > first["sockets"] + 20:
            report[-1]["errors"].append(f"socket leak: {first['sockets']} → {last['sockets']}")
        if not last["gemma"]:
            report[-1]["errors"].append("Gemma was unloaded during the soak")
        if last["rss_mb"] > first["rss_mb"] * 1.6 + 200:
            report[-1]["errors"].append(f"memory growth: {first['rss_mb']} → {last['rss_mb']} MB")
    errs = sum(len(r["errors"]) for r in report)
    lines = sum(r["lines"] for r in report)
    print(f"{len(report)} games · {lines} spoken lines · {errs} problems · offline: "
          f"{'no non-loopback connection' if not remote else remote} · {time.time() - t0:.0f} s")
    out = S.OUT / f"game-sim-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=1, default=str))
    print(f"report: {out.relative_to(S.REPO)}")
    sys.exit(1 if errs or remote else 0)


if __name__ == "__main__":
    main()
