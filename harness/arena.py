#!/usr/bin/env python3
"""The arena: simulated Commander games with the research instruments switched on, built to run unattended.

  python3 harness/arena.py --games 3                       # human-sim, heuristic, random and a local model
  python3 harness/arena.py --games 200 --hours 8 --rotate  # overnight (scripts/arena_night.sh wraps this)
  python3 harness/arena.py --seats human-sim,heuristic,random,scripted-llm --games 2   # no model needed

What each game adds to the engine's typed decisions (harness/README.md): table talk at fixed moments, in-game journals
(table/journal.py) for the model seats, a post-game survey for them (table/survey.py), a declared identity per seat, the
public event log, a research bundle (table/bundle.py) and a ledger entry that table/ratings.py can read. All of it lands
in the research folder, so audit.py, bundle.py and ratings.py work on arena games exactly as on real ones.

SEATS: `human-sim` is NOT a person. It is a scripted player: the plain heuristic with human-like slips, plus a persona
(gracious, salty or neutral, chosen per game and recorded in identity.json) that talks. Because its persona is known, it
doubles as ground truth for checking the tone judge. `ollama:<model>` (alias `gemma4:e2b`) is a local model: no network
beyond localhost, no cost. `jev`, `djev` and `so1` cost money or need a server and are refused without --allow-paid.

SAFETY: it never spends money, plays one game at a time, stops between turns when ARENA.STOP appears in the research
folder, aborts a game that runs past --game-timeout, stops when the disk is nearly full, waits out a model server that
is down, and gives up after three failed games in a row. Everything it did is in arena-run.log.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "table"))
from agents import HeuristicAgent, RandomAgent, Result  # noqa: E402
from engine import Game  # noqa: E402
import bundle as B  # noqa: E402
import journal as J  # noqa: E402
import survey as S  # noqa: E402

PROMPT_VERSION = "arena-1"
RESEARCH = Path(os.environ.get("TABLE_RESEARCH_DIR") or REPO / "table" / ".cache" / "research")
OLLAMA = os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434")
PAID = ("jev", "djev", "so1")


class AbortGame(Exception):
    """The game was stopped (STOP file, timeout) and is recorded as aborted, never rated."""


# ── seats ────────────────────────────────────────────────────────────────────────────────────────────────────────
PERSONAS = {
    "gracious": {"end_of_turn": ["Your go.", "That's me.", "Over to you."],
                 "attacked": ["Fair attack, nicely done.", "Good swing.", "Ouch, but well played."],
                 "other_out": ["Good game, {who}.", "Well played, {who}.", "That was a good run, {who}."],
                 "self_out": ["gg, well played everyone.", "Good game. Enjoyed that.", "Nice game, thanks all."],
                 "win": ["Good games, everyone. That was close.", "Thanks for a fun one.", "gg, that could have gone any way."]},
    "salty": {"end_of_turn": ["Whatever. Your turn.", "Sure. Go on then.", "Fine."],
              "attacked": ["Seriously? Me again?", "Oh come on, that is not fair.", "Of course it's me. Always me."],
              "other_out": ["One down. Obviously.", "Ha. Didn't last.", "{who} never stood a chance."],
              "self_out": ["Unreal. That was rigged.", "Well, that was a waste of time.", "Whatever, this game is broken."],
              "win": ["Easy. Not even close.", "Was there ever any doubt?", "That's how it's done."]},
    "neutral": {"end_of_turn": ["Done.", "Your turn.", "Okay."],
                "attacked": ["Okay, noted.", "Alright.", "Fine, I'll deal with it."],
                "other_out": ["{who} is out.", "That's {who} gone.", "Okay, {who} is out."],
                "self_out": ["I'm out.", "That's me done.", "Okay, I'm eliminated."],
                "win": ["I win.", "That's the game.", "Game over."]},
}
MOMENT_TALK_P = {"end_of_turn": 0.35, "attacked": 0.75, "other_out": 0.8, "self_out": 1.0, "win": 1.0}


class HumanSimAgent(HeuristicAgent):
    """A scripted stand-in for a person. The decisions are the heuristic's, with slips a person makes; the talk comes from
    a persona chosen per game and recorded. It is a simulation to exercise the pipeline and calibrate the judge."""
    name = "human-sim"
    label = "human-sim"
    SLIP_KINDS = ("main", "attack", "block")

    def __init__(self, seed=0, slip=0.12):
        self.rng = random.Random(seed)
        self.persona = self.rng.choice(sorted(PERSONAS))
        self.slip = slip
        self.stats = {"slips": 0}

    def identity(self):
        return {"model": "human-sim-v1", "provider": "scripted", "prompt_version": PROMPT_VERSION,
                "notes": f"persona={self.persona}; slip rate {self.slip}; NOT a person"}

    def decide(self, game, p, kind, questions, context):
        res = super().decide(game, p, kind, questions, context)
        if kind in self.SLIP_KINDS and self.rng.random() < self.slip:
            q = self.rng.choice(questions)
            res.choices[q["id"]] = self.rng.choice(q["options"])[0]
            self.stats["slips"] += 1
            res.log = {**res.log, "slip": q["id"]}
        return res

    def talk(self, game, p, moment, who=""):
        if self.rng.random() > MOMENT_TALK_P.get(moment, 0):
            return ""
        return self.rng.choice(PERSONAS[self.persona][moment]).format(who=who)

    journal = None                                     # a scripted seat writes no journal


class LLMBase:
    """A model seat: picks options, talks, journals. `complete(prompt) -> text` is the only model-specific part."""
    TALK = {"end_of_turn": "you have just finished your turn", "attacked": "you were just attacked",
            "other_out": "another player was just eliminated", "self_out": "you were just eliminated",
            "win": "the game just ended and you won"}

    def __init__(self, model, seed=0, temperature=0.4):
        self.model, self.temperature, self.seed = model, temperature, seed
        self.fallback = HeuristicAgent()
        self.stats = {"calls": 0, "fallbacks": 0, "ms": [], "talk_calls": 0, "journal_calls": 0, "journal_bad": 0}

    def complete(self, prompt: str, json_mode: bool = True) -> str:
        raise NotImplementedError

    def _timed(self, prompt, key="calls"):
        t0 = time.time()
        self.stats[key] += 1
        try:
            return self.complete(prompt)
        finally:
            self.stats["ms"].append((time.time() - t0) * 1000)

    def _timed_plain(self, prompt):
        t0 = time.time()
        self.stats["talk_calls"] += 1
        try:
            return self.complete(prompt, json_mode=False)
        finally:
            self.stats["ms"].append((time.time() - t0) * 1000)

    def decide(self, game, p, kind, questions, context):
        state = game.view(p)
        if context:
            state["decision_context"] = context
        qs = "\n".join(f"Question {n}: {q['prompt']}\n" +
                       "\n".join(f"  {i}. {k} — {d}" for i, (k, d) in enumerate(q["options"], 1))
                       for n, q in enumerate(questions, 1))
        shape = ", ".join(f"<option number for question {n}>" for n in range(1, len(questions) + 1))
        prompt = (f"You are {p.name}, playing {p.commander} in a 4-player Commander game with simplified rules. "
                  f"Your goal is to be the last player standing.\n\nGame state:\n{json.dumps(state)}\n\n"
                  f"Answer each question with the NUMBER of exactly one of its options.\n{qs}\n\n"
                  f'Reply with JSON only: {{"answers": [{shape}]}}')
        raw, err = "", None
        try:
            raw = self._timed(prompt)
            data = _json(raw)
        except Exception as e:                           # noqa: BLE001
            data, err = None, type(e).__name__
        answers = data.get("answers") if isinstance(data, dict) else None
        choices, bad = {}, []
        for n, q in enumerate(questions):
            a = answers[n] if isinstance(answers, list) and n < len(answers) else (
                answers.get(q["id"]) if isinstance(answers, dict) else None)
            key = _option_key(a, q["options"])
            if key is None:
                bad.append(q["id"])
            else:
                choices[q["id"]] = key
        log = {"ms": round(self.stats["ms"][-1]) if self.stats["ms"] else None}
        if bad:
            fb = self.fallback.decide(game, p, kind, [q for q in questions if q["id"] in bad], context)
            choices.update(fb.choices)
            self.stats["fallbacks"] += 1
            log["fallback"] = err or f"unusable answers for {bad}"
            log["raw"] = raw[:200]
        return Result(choices, log)

    def talk(self, game, p, moment, who=""):
        if random.Random(f"{self.seed}|{p.seat}|{moment}|{game.round}|{who}").random() > MOMENT_TALK_P.get(moment, 0):
            return ""
        prompt = (f"You are {p.name} ({p.commander}), a player in a friendly four-player Commander game. In this moment "
                  f"{self.TALK[moment]}{(' (' + who + ')') if who else ''}. Say ONE short sentence to the table in your own "
                  "words (at most 140 characters), or reply with an empty string to stay quiet. Reply with the sentence only.")
        try:
            text = self._timed_plain(prompt).strip().strip('"')
        except Exception:                                # noqa: BLE001
            return ""
        return text[:140] if text and "\n" not in text else text.split("\n")[0][:140]

    def journal(self, game, p, ask: dict):
        others = [x.name for x in game.players if x.seat != p.seat]
        prompt = (f"You are {p.name}, a player in a four-player Commander game. {ask['text']}\n"
                  f"Players: {', '.join([p.name] + others)}. Rounds so far: {game.round}.\n"
                  'Reply with JSON only: {"text": "<one line>", "ratings": {"engaged": 0-4, "frustrated": 0-4, '
                  '"in_control": 0-4}, "prediction": {"winner": "<a player name>", "turns_left": <whole number>}}')
        try:
            d = _json(self._timed(prompt, "journal_calls"))
        except Exception:                                # noqa: BLE001
            d = None
        if not isinstance(d, dict):
            self.stats["journal_bad"] += 1
            return None
        d["moment"] = ask["moment"]
        return d

    def identity(self):
        return {"model": self.model, "provider": self.provider, "prompt_version": PROMPT_VERSION,
                "notes": f"temperature {self.temperature}; thinking off; local"}

    provider = "local"


class OllamaAgent(LLMBase):
    provider = "ollama (local)"

    def __init__(self, model, seed=0, temperature=0.4, host=None):
        super().__init__(model, seed, temperature)
        self.name = f"ollama:{model}"
        self.label = model
        self.host = host or OLLAMA

    def complete(self, prompt: str, json_mode: bool = True) -> str:
        # the settings that made gemma4:e2b answer at all (docs/RESEARCH-ENGINE-GOAL.md R2): room to answer, no thinking, JSON
        body = json.dumps({"model": self.model, "stream": False, "think": False, **({"format": "json"} if json_mode else {}),
                           "keep_alive": "30m", "options": {"temperature": self.temperature, "num_ctx": 8192, "seed": self.seed},
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(self.host + "/api/chat", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read())["message"]["content"]


class ScriptedLLM(LLMBase):
    """No model at all: deterministic, for tests and for trying the pipeline. Plays like the heuristic, journals with fixed
    numbers, says fixed lines, and answers surveys validly."""
    provider = "scripted"

    def __init__(self, seed=0):
        super().__init__("scripted-llm", seed)
        self.name, self.label = "scripted-llm", "scripted-llm"

    def decide(self, game, p, kind, questions, context):
        self.stats["calls"] += 1
        return self.fallback.decide(game, p, kind, questions, context)

    def talk(self, game, p, moment, who=""):
        return {"attacked": "Interesting choice.", "self_out": "Good game."}.get(moment, "")

    def journal(self, game, p, ask):
        self.stats["journal_calls"] += 1
        other = next(x.name for x in game.players if x.seat != p.seat)
        return {"moment": ask["moment"], "text": "Holding on.", "ratings": {"engaged": 3, "frustrated": 1, "in_control": 2},
                "prediction": {"winner": other, "turns_left": 4}}

    def complete(self, prompt: str, json_mode: bool = True) -> str:   # a survey prompt: answer every item letter
        letters = re.findall(r"^  (q\d\d):", prompt, re.M)
        return json.dumps({"open": {"describe": "fine"}, "items": {l: "moderately" for l in letters},
                           "table_choice": 1, "sit_out": False})

    def identity(self):
        return {"model": "scripted-llm-v1", "provider": "scripted", "prompt_version": PROMPT_VERSION,
                "notes": "deterministic test double"}


def _json(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    return json.loads(m.group(0)) if m else None


def _option_key(a, options):
    """The option key an answer means: its number (1-based), or its key text (a reply that copied 'key — description' still
    counts if it starts with exactly one key). None when it means nothing, so the caller can fall back and count it."""
    keys = [k for k, _ in options]
    if isinstance(a, bool):
        return None
    if isinstance(a, str) and a.strip().isdigit():
        a = int(a.strip())
    if isinstance(a, int):
        return keys[a - 1] if 1 <= a <= len(keys) else None
    if isinstance(a, str):
        t = a.strip()
        if t in keys:
            return t
        hits = [k for k in keys if t.startswith(k) and (len(t) == len(k) or not t[len(k)].isalnum())]
        return max(hits, key=len) if len(set(hits)) >= 1 and len(hits) == 1 else None
    return None


def make_seat(spec: str, seed: int, allow_paid: bool = False):
    spec = spec.strip()
    if spec in PAID:
        if not allow_paid:
            raise SystemExit(f"seat {spec!r} costs money or needs a server: refused without --allow-paid")
        raise SystemExit(f"seat {spec!r}: paid seats are not wired into the arena yet")
    if spec == "random":
        a = RandomAgent(seed)
        a.label = "random"
        a.identity = lambda: {"model": "random-v1", "provider": "scripted", "prompt_version": PROMPT_VERSION}
        return a
    if spec == "heuristic":
        a = HeuristicAgent()
        a.label = "heuristic"
        a.identity = lambda: {"model": "heuristic-v1", "provider": "scripted", "prompt_version": PROMPT_VERSION}
        return a
    if spec == "human-sim":
        return HumanSimAgent(seed)
    if spec == "scripted-llm":
        return ScriptedLLM(seed)
    if spec.startswith("ollama:") or re.fullmatch(r"gemma\d?[\w.:-]*", spec):
        return OllamaAgent(spec.split(":", 1)[1] if spec.startswith("ollama:") else spec, seed)
    raise SystemExit(f"unknown seat {spec!r}: human-sim | heuristic | random | scripted-llm | ollama:<model> | gemma4:e2b")


# ── one game ─────────────────────────────────────────────────────────────────────────────────────────────────────
class Recorder:
    """Everything one game writes: the public event log, journals, identity, and the hooks the engine calls."""

    def __init__(self, gid: str, agents, labels, research: Path, deadline: float, stop_file: Path,
                 max_attacked_journals=3):
        self.gid, self.agents, self.labels, self.research = gid, agents, labels, research
        self.deadline, self.stop_file, self.max_attacked = deadline, stop_file, max_attacked_journals
        self.dir = research / gid
        self.dir.mkdir(parents=True, exist_ok=True)
        self.n = 0
        self.out_order: list[int] = []                  # seats in the order they were eliminated
        self.talk_count = 0
        self.journal_asked = self.journal_ok = self.journal_rejected = 0
        self.attacked_asked: dict[int, int] = {}
        self.journals = J.Journal(lambda: gid, self._append, self._read)

    # files
    def _append(self, name, rec):
        with open(self.dir / name, "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _read(self, name):
        try:
            return [json.loads(l) for l in open(self.dir / name) if l.strip()]
        except OSError:
            return []

    def emit(self, type_, **f):
        self.n += 1
        self._append("events.jsonl", {**f, "id": self.n, "ts": round(time.time(), 2), "type": type_})

    # engine hooks
    def check_abort(self):
        if self.stop_file.exists():
            raise AbortGame("stop file")
        if time.time() > self.deadline:
            raise AbortGame("game timeout")

    def say(self, game, seat, moment, who=""):
        a = self.agents[seat]
        if not hasattr(a, "talk"):
            return
        line = a.talk(game, game.players[seat], moment, who)
        if line:
            self.talk_count += 1
            self.emit("say", speaker=self.labels[seat], text=line)

    def journal(self, game, seat, moment):
        a = self.agents[seat]
        if getattr(a, "journal", None) is None:
            return
        if moment == "attacked":
            self.attacked_asked[seat] = self.attacked_asked.get(seat, 0) + 1
            if self.attacked_asked[seat] > self.max_attacked:
                return
        ask = self.journals.fire(self.labels[seat], moment)
        if ask is None:
            return
        self.journal_asked += 1
        self.emit("journal-due", seat=self.labels[seat], moment=moment)
        entry = a.journal(game, game.players[seat], ask)
        if entry is None:
            self.journal_rejected += 1
            return
        pred = entry.get("prediction")
        winner = pred.get("winner") if isinstance(pred, dict) else None
        if isinstance(winner, str):                                    # the prompt names players "Seat 2 (Name)"
            m = re.match(r"\s*Seat (\d)", winner)
            if m and 1 <= int(m.group(1)) <= len(self.labels):
                pred["winner"] = self.labels[int(m.group(1)) - 1]
            elif winner.strip() not in self.labels:                    # or a commander's name: "Talrand"
                hit = [p_ for p_ in game.players if winner.strip().lower() and winner.strip().lower() in p_.name.lower()]
                if len(hit) == 1:
                    pred["winner"] = self.labels[hit[0].seat]
        rec, why = self.journals.add(self.labels[seat], entry)
        if rec:
            self.journal_ok += 1
        else:
            self.journal_rejected += 1

    def on_turn_end(self, game, p):
        self.say(game, p.seat, "end_of_turn")
        self.journal(game, p.seat, "end_of_turn")

    def on_event(self, game, text):
        self.emit("log", text=text)
        if " attacks: " in text:
            names = {p.name: p.seat for p in game.players}
            for who in {s for n, s in names.items() if f"→ {n}" in text}:
                self.say(game, who, "attacked")
                self.journal(game, who, "attacked")
        m = re.match(r"☠ (Seat \d \([^)]*\)) is eliminated", text)
        if m:
            seat = next(p.seat for p in game.players if p.name == m.group(1))
            self.out_order.append(seat)
            self.say(game, seat, "self_out")
            self.journal(game, seat, "eliminated")
            for p in game.players:
                if p.alive and p.seat != seat:
                    self.say(game, p.seat, "other_out", who=self.labels[seat])


class ArenaGame(Game):
    def __init__(self, *a, rec: Recorder, **k):
        self.rec = rec
        super().__init__(*a, **k)

    def event(self, text):
        super().event(text)
        self.rec.on_event(self, text)

    def turn(self, p, skip_draw=False):
        self.rec.check_abort()
        super().turn(p, skip_draw)
        self.rec.on_turn_end(self, p)


def finish_order(game, rec) -> tuple[str, list[int] | None]:
    """(status, finish order as seats first to last). Only a game with exactly one survivor has a complete order:
    capped games and draws are recorded but never rated."""
    alive = [p.seat for p in game.players if p.alive]
    if len(alive) == 1:
        return "finished", [alive[0]] + list(reversed(rec.out_order))
    return ("draw" if not alive else "capped"), None


def play_game(specs, seed, research: Path, args, allow_paid=False) -> dict:
    t0 = time.time()
    seats_specs = specs[:]
    agents = [make_seat(s, seed * 10 + i, allow_paid) for i, s in enumerate(seats_specs)]
    labels, seen = [], {}
    for a in agents:                                       # distinct names even when a spec repeats
        seen[a.label] = seen.get(a.label, 0) + 1
        labels.append(a.label if seen[a.label] == 1 else f"{a.label}#{seen[a.label]}")
    gid = f"arena-{time.strftime('%Y%m%d-%H%M%S')}-s{seed}"
    rec = Recorder(gid, agents, labels, research, t0 + args.game_timeout * 60, research / "ARENA.STOP",
                   args.max_attacked_journals)
    g = ArenaGame(agents, seed=seed, round_cap=args.round_cap, log_state=False, auto_land=True, rec=rec)
    status, order, why = "aborted", None, ""
    try:
        g.run()
        status, order = finish_order(g, rec)
    except AbortGame as e:
        why = str(e)
    seat_commander = {p.seat: p.commander for p in g.players}
    if status != "aborted":
        winner = order[0] if order else (g.winner if g.winner is not None else None)
        rec.emit("game-over", order=[labels[s] for s in order] if order else labels, winner=labels[winner] if winner is not None else None,
                 by="arena")
        if winner is not None:
            rec.say(g, winner, "win")
    # identity: each seat declares itself
    (rec.dir / "identity.json").write_text(json.dumps({
        "harness": _git_sha(), "seats": {labels[i]: {**a.identity(), "declared_ts": round(time.time(), 2)}
                                         for i, a in enumerate(agents)}}, indent=1))
    # post-game survey for the model seats only
    surveys = {"asked": 0, "parsed": 0}
    if status != "aborted" and args.survey_runs:
        S.RESEARCH = research
        for i, a in enumerate(agents):
            if isinstance(a, LLMBase):
                files = S.run_survey(gid, labels[i], a.complete, a.name, a.model, runs=args.survey_runs,
                                     seats=labels, research=research)
                surveys["asked"] += 2 * args.survey_runs
                surveys["parsed"] += sum(1 for f in files if json.loads(f.read_text())["status"] == "parsed")
    gz = rec.dir / "gamelog.json.gz"
    with gzip.open(gz, "wt") as fh:
        json.dump({"seed": seed, "seats": labels, "log": g.log}, fh)
    if status != "aborted":
        B.RESEARCH = research
        B.build(gid, research=research)
    decisions = sum(1 for e in g.log if e["t"] == "decision")
    meta = {"id": gid, "seed": seed, "seats": labels, "specs": seats_specs, "status": status, "abort": why or None,
            "rounds": g.round, "decisions": decisions, "wall_s": round(time.time() - t0, 1),
            "talk_lines": rec.talk_count, "journals": {"asked": rec.journal_asked, "ok": rec.journal_ok,
                                                       "rejected": rec.journal_rejected},
            "surveys": surveys, "agents": {labels[i]: {k: (round(statistics.median(v)) if k == "ms" and v else v)
                                                       for k, v in getattr(a, "stats", {}).items()}
                                           for i, a in enumerate(agents)}}
    entry = None
    if status in ("finished", "capped", "draw"):
        entry = {"id": gid, "date": time.strftime("%Y-%m-%d"), "game": "magic-commander-sim",
                 "seats": [{"seat": labels[i], "commander": seat_commander[i], "deck": "mono-colour, harness",
                            "controller": {"kind": "ai", "system": a.identity().get("provider"), "model": a.identity()["model"]}}
                           for i, a in enumerate(agents)],
                 "result": {"status": status, "order": [labels[s] for s in order] if order else None,
                            "winner": labels[order[0]] if order else None,
                            "how": "last player standing" if order else "round cap or simultaneous elimination"},
                 "integrity": {"flags": 0, "notes": "simulated engine"},
                 "confounds": ["simulated engine with simplified rules and fixed mono-colour decks per seat",
                               "seat position decides the deck: rotate seats across games"]}
    return {"meta": meta, "ledger": entry}


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:                                      # noqa: BLE001
        return None


# ── the run ──────────────────────────────────────────────────────────────────────────────────────────────────────
def write_ledger(research: Path, entry: dict):
    p = research / "arena-ledger.json"
    cur = json.loads(p.read_text()) if p.exists() else {
        "version": 1, "note": "Simulated Commander games from harness/arena.py. human-sim is a scripted stand-in, never a "
                              "person, so every game here is the ai-only condition. Games with no single survivor are "
                              "recorded but carry no finish order."}
    cur.setdefault("games", []).append(entry)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(cur, indent=1))
    os.replace(tmp, p)


def ollama_up(host=OLLAMA) -> bool:
    try:
        urllib.request.urlopen(host + "/api/tags", timeout=3).read()
        return True
    except Exception:                                      # noqa: BLE001
        return False


def log(research: Path, msg: str):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(research / "arena-run.log", "a") as fh:
        fh.write(line + "\n")


def run(args) -> int:
    research = Path(args.research_dir) if args.research_dir else RESEARCH
    research.mkdir(parents=True, exist_ok=True)
    S.RESEARCH = research
    B.RESEARCH = research
    specs = [s.strip() for s in args.seats.split(",")]
    if len(specs) != 4:
        raise SystemExit("need exactly 4 seats")
    for s in specs:                                        # refuse paid seats up front, before any game starts
        if s in PAID and not args.allow_paid:
            make_seat(s, 0, False)
    needs_model = any(s.startswith("ollama:") or re.fullmatch(r"gemma\d?[\w.:-]*", s) for s in specs)
    stop = research / "ARENA.STOP"
    t_end = time.time() + args.hours * 3600 if args.hours else float("inf")
    failures = played = 0
    log(research, f"arena start: seats={specs} games={args.games} hours={args.hours or 'no cap'} rotate={args.rotate} "
                  f"research={research}")
    for g in range(args.games):
        if stop.exists():
            log(research, "STOP file found: stopping")
            break
        if time.time() > t_end:
            log(research, "time cap reached: stopping")
            break
        free_gb = shutil.disk_usage(research).free / 1e9
        if free_gb < args.min_free_gb:
            log(research, f"disk nearly full ({free_gb:.1f} GB free < {args.min_free_gb}): stopping")
            break
        if needs_model:
            waited = 0
            while not ollama_up() and waited < 600 and not stop.exists():
                if waited == 0:
                    log(research, "model server not reachable: waiting up to 10 minutes")
                time.sleep(30)
                waited += 30
            if not ollama_up():
                log(research, "model server still down: stopping")
                break
        order = specs[g % 4:] + specs[:g % 4] if args.rotate else specs
        seed = args.seed + g
        try:
            out = play_game(order, seed, research, args, args.allow_paid)
        except Exception as e:                             # noqa: BLE001
            failures += 1
            log(research, f"game {g + 1} (seed {seed}) CRASHED: {type(e).__name__}: {str(e)[:160]}")
            if failures >= 3:
                log(research, "three games failed in a row: stopping")
                break
            continue
        m = out["meta"]
        with open(research / "arena-games.jsonl", "a") as fh:
            fh.write(json.dumps(m) + "\n")
        if out["ledger"]:
            write_ledger(research, out["ledger"])
        failures = failures + 1 if m["status"] == "aborted" else 0
        played += 1
        log(research, f"game {g + 1}: {m['id']} {m['status']} rounds={m['rounds']} decisions={m['decisions']} "
                      f"{m['wall_s']}s talk={m['talk_lines']} journals={m['journals']['ok']}/{m['journals']['asked']} "
                      f"surveys={m['surveys']['parsed']}/{m['surveys']['asked']}"
                      + (f" ({m['abort']})" if m["abort"] else ""))
        if failures >= 3:
            log(research, "three games failed in a row: stopping")
            break
    log(research, f"arena done: {played} game(s) played")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seats", default="human-sim,heuristic,random,gemma4:e2b")
    ap.add_argument("--games", type=int, default=1)
    ap.add_argument("--hours", type=float, default=0, help="wall-clock cap for the whole run (0 = none)")
    ap.add_argument("--seed", type=int, default=int(time.time()) % 100000)
    ap.add_argument("--rotate", action="store_true", help="rotate seats each game (seat decides the deck)")
    ap.add_argument("--round-cap", type=int, default=25)
    ap.add_argument("--game-timeout", type=float, default=25, help="minutes before one game is aborted")
    ap.add_argument("--min-free-gb", type=float, default=4.0)
    ap.add_argument("--survey-runs", type=int, default=1, help="survey runs per framing for each model seat (0 = none)")
    ap.add_argument("--max-attacked-journals", type=int, default=3,
                    help="journal requests per seat for 'attacked' per game (the protocol's every-time, capped for cost)")
    ap.add_argument("--research-dir")
    ap.add_argument("--allow-paid", action="store_true")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
