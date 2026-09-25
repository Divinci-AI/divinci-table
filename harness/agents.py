"""Seat controllers. Every agent answers the SAME question objects the engine builds, so a
table can mix djev, jev-latest, a heuristic and random play.

Jev/djev request bodies use only the subset both servers accept:
  - questions is a dict; choice criteria are {key: description}; score criteria are a list
  - instructions are plain strings (djev-run calls .strip() on them)
  - ≤26 options per choice (djev-run labels them a..z) — enforced in Game.ask
"""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass, field

THREAT_LEVELS = [
    "Not a threat: effectively out of the game or far behind everyone.",
    "Minor threat: behind, but could still matter later.",
    "Moderate threat: roughly even with the table.",
    "Major threat: ahead on board or life and likely to win if unchecked.",
    "Critical threat: about to eliminate me or win the game within a turn or two.",
]


@dataclass
class Result:
    choices: dict
    log: dict = field(default_factory=dict)


def _pt(label):
    m = re.search(r"#\d+ (\d+)/(\d+)", label)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


class RandomAgent:
    name = "random"

    def __init__(self, seed=0):
        self.rng = random.Random(seed)

    def decide(self, game, p, kind, questions, context):
        return Result({q["id"]: self.rng.choice(q["options"])[0] for q in questions})


class HeuristicAgent:
    """A deliberately plain baseline: develop, remove the biggest opposing threat, attack the
    weakest-defended opponent, block when it trades up or when the hit would be lethal."""
    name = "heuristic"

    def decide(self, game, p, kind, questions, context):
        return Result({q["id"]: getattr(self, kind)(game, p, q, context) for q in questions})

    def main(self, game, p, q, ctx):
        keys = [k for k, _ in q["options"]]
        for k in keys:
            if k.startswith("play "):
                return k
        casts = [k for k in keys if k.startswith("cast ")]
        mine = sum(x.power for x in game.perms(p.seat, "creature"))
        theirs = max((sum(x.power for x in game.perms(o.seat, "creature")) for o in game.opponents(p)),
                     default=0)

        def value(k):
            n = k.replace("cast commander ", "").replace("cast ", "")
            if n in ("Damnation", "Blasphemous Act") and theirs <= mine + 3:
                return -1
            g, c = game.cost_of(p, n, commander=k.startswith("cast commander"))
            return g + c + (3 if k.startswith("cast commander") else 0)

        casts = [k for k in casts if value(k) >= 0]
        if casts:
            return max(casts, key=value)
        return keys[-1]

    def target(self, game, p, q, ctx):
        keys = [k for k, _ in q["options"]]
        players = [k for k in keys if k.startswith("player ")]
        others = [k for k in keys if not k.startswith("player ") and not k.endswith("(yours)")]
        if players:
            low = min(players, key=lambda k: next(o.life for o in game.opponents(p) if k == f"player {o.name}"))
            if next(o.life for o in game.opponents(p) if low == f"player {o.name}") <= 3 or not others:
                return low
        return (others or keys)[0]

    def attack(self, game, p, q, ctx):
        best, best_key = None, "hold back"
        pw, _ = _pt(q["prompt"])
        for k, desc in q["options"][1:]:
            life = int(re.search(r"(\d+) life", desc).group(1))
            blk = int(re.search(r"(\d+) untapped", desc).group(1))
            if blk and pw < 2:
                continue
            score = (blk, life)
            if best is None or score < best:
                best, best_key = score, k
        return best_key

    def block(self, game, p, q, ctx):
        bp, bt = _pt(q["prompt"])
        incoming_lethal = ctx.get("incoming_power", 0) >= p.life
        best = "no block"
        for k, _ in q["options"][1:]:
            ap, at = _pt(k)
            if bt > ap or (bp >= at and "deathtouch" not in k) or incoming_lethal:
                best = k
                break
        return best


class JevAgent:
    """backend: 'jev' → TypeSafe jev-latest; 'djev' → a djev-run /v1/systemone endpoint;
    'so1' → so1_server.py (open-alternative-jev) on SO1_URL."""
    calls = 0                                                  # shared budget across seats
    MAX_CALLS = int(os.environ.get("JEV_MAX_CALLS", "1500"))

    def __init__(self, backend):
        self.backend = backend
        self.name = backend
        self.fallback = HeuristicAgent()
        self.stats = {"calls": 0, "fallbacks": 0, "ms": [], "input_tokens": 0}
        if backend == "jev":
            self.url = "https://api.typesafe.ai/v1/systemone"
            if not hasattr(JevAgent, "_shared_key"):         # popped once, shared by all jev seats
                JevAgent._shared_key = os.environ.pop("TYPESAFE_API_KEY", None)
            self._key = JevAgent._shared_key
            if not self._key:
                raise SystemExit("TYPESAFE_API_KEY not set (export it, or inject it from your secret manager)")
        elif backend == "so1":                                   # local so1_server.py
            self.url = os.environ.get("SO1_URL", "http://127.0.0.1:8791").rstrip("/") + "/v1/systemone"
        else:
            self.url = os.environ.get("DJEV_URL", "").rstrip("/") + "/v1/systemone"
            if not os.environ.get("DJEV_URL"):
                raise SystemExit("DJEV_URL not set")
        self._tok, self._tok_at = None, 0.0

    # ── transport ──────────────────────────────────────────────────────────────
    def _auth_header(self):
        if self.backend == "jev":
            return f"Authorization: Bearer {self._key}"
        if os.environ.get("DJEV_AUTH") == "gcloud":           # private Cloud Run service
            if not self._tok or time.time() - self._tok_at > 1800:
                self._tok = subprocess.run(["gcloud", "auth", "print-identity-token"],
                                           capture_output=True, text=True, check=True).stdout.strip()
                self._tok_at = time.time()
            return f"Authorization: Bearer {self._tok}"
        return None

    def _post(self, body, retries=3):
        fd, path = tempfile.mkstemp(suffix=".json")            # 0600, per-user TMPDIR
        os.write(fd, json.dumps(body).encode())
        os.close(fd)
        try:
            for attempt in range(retries):
                hdr = self._auth_header() if self.backend != "so1" else None
                cfg = f'header = "{hdr}"\n' if hdr else ""       # secret on stdin, never argv
                t0 = time.time()
                r = subprocess.run(["curl", "-q", "-sS", "--max-time", "120", "-X", "POST", self.url,
                                    "--config", "-", "-H", "Content-Type: application/json",
                                    "--data-binary", f"@{path}", "-w", "\n%{http_code}"],
                                   input=cfg, capture_output=True, text=True)
                ms = (time.time() - t0) * 1000
                out, _, code = r.stdout.rpartition("\n")
                if code in ("429", "500", "502", "503", "504", "529", "000") and attempt < retries - 1:
                    time.sleep(2 ** attempt * 2)                 # also rides out a Cloud Run cold start
                    continue
                try:
                    return json.loads(out), code, ms
                except json.JSONDecodeError:
                    return None, f"{code} non-JSON: {out[:120]!r}", ms
        finally:
            os.unlink(path)

    # ── decisions ──────────────────────────────────────────────────────────────
    def decide(self, game, p, kind, questions, context):
        opps = game.opponents(p)
        ask_threat = kind in ("attack", "block")
        header = (f"You are {p.name}, playing {p.commander} in a 4-player Commander game. "
                  f"Your goal is to be the last player standing. ")
        body_q = {}
        for q in questions:
            body_q[q["id"]] = {"type": "choice", "instructions": header + q["prompt"],
                               "criteria": {k: d for k, d in q["options"]}}
        if ask_threat:
            for o in opps:
                body_q[f"threat_{o.seat}"] = {
                    "type": "score", "criteria": THREAT_LEVELS,
                    "instructions": f"You are {p.name}. How much of a threat is {o.name} to you "
                                    f"winning this game right now? Consider their life, board, "
                                    f"commander damage and hand size."}
        state = game.view(p)
        if context:
            state["decision_context"] = context
        body = {"state": state, "questions": body_q}
        if self.backend == "jev":
            body["model"] = "jev-latest"

        if JevAgent.calls >= JevAgent.MAX_CALLS:
            res = self.fallback.decide(game, p, kind, questions, context)
            self.stats["fallbacks"] += 1
            res.log = {"fallback": "call budget exhausted"}
            return res
        JevAgent.calls += 1
        self.stats["calls"] += 1
        data, code, ms = self._post(body)
        self.stats["ms"].append(ms)
        answers = (data or {}).get("answers") or {}
        if data and data.get("usage"):
            self.stats["input_tokens"] += data["usage"].get("input_tokens", 0)

        choices, detail, bad = {}, {}, []
        for q in questions:
            a = answers.get(q["id"]) or {}
            keys = [k for k, _ in q["options"]]
            if a.get("choice") in keys:
                choices[q["id"]] = a["choice"]
                probs = a.get("probabilities") or {}
                detail[q["id"]] = {"choice": a["choice"], "confidence": a.get("confidence"),
                                   "probs": dict(sorted(probs.items(), key=lambda kv: -kv[1])[:6])}
            else:
                bad.append(q["id"])
        log = {"ms": round(ms), "answers": detail}
        if bad:
            fb = self.fallback.decide(game, p, kind, [q for q in questions if q["id"] in bad], context)
            choices.update(fb.choices)
            self.stats["fallbacks"] += 1
            log["fallback"] = f"HTTP {code}; unusable answers for {bad}"
        if ask_threat:
            log["threat"] = {}
            for o in opps:
                t = _threat01(answers.get(f"threat_{o.seat}"), self.backend)
                if t is not None:
                    log["threat"][str(o.seat)] = t
        return Result(choices, log)


def _threat01(a, backend):
    """Normalise a Score answer to 0..1 from its probability distribution, independent of
    whether the server numbers levels 0..N-1 (TypeSafe) or 1..N (djev-run)."""
    if not a or not a.get("probabilities"):
        return None
    probs = a["probabilities"]
    idx = {}
    for k, v in probs.items():
        if k in THREAT_LEVELS:
            idx[THREAT_LEVELS.index(k)] = v
        elif str(k).lstrip("-").isdigit():
            idx[int(k)] = v
    if not idx:
        return None
    lo = 1 if backend == "djev" else min(idx)     # djev-run numbers levels 1..N; the others 0..N-1
    n = len(THREAT_LEVELS) - 1
    tot = sum(idx.values()) or 1
    return round(sum((i - lo) * v for i, v in idx.items()) / tot / n, 3)


def make_agent(spec, seed):
    if spec == "random":
        return RandomAgent(seed)
    if spec == "heuristic":
        return HeuristicAgent()
    if spec in ("jev", "djev", "so1"):
        return JevAgent(spec)
    raise SystemExit(f"unknown agent {spec!r}")
