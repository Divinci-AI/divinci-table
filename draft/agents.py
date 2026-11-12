"""Drafter interface. Any seat can be a Drafter; the OpenRouter models plug in here.

A Drafter returns an index into the pack. The panel asks several drafters, keeps only answers
that are legal indexes, and takes the most-voted card (ties go to the first drafter listed, so
the order of the panel is its tiebreak). If no answer is legal the panel falls back to the
heuristic bot rather than guessing.
"""
from __future__ import annotations

import collections
import random
from pathlib import Path
from typing import Protocol

from draft import bot_pick


class Drafter(Protocol):
    name: str

    def pick(self, pack: list[dict], pool: list[dict], context: dict) -> int | None: ...


class HeuristicDrafter:
    name = "heuristic"

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)

    def pick(self, pack, pool, context):
        return bot_pick(pack, pool, self.rng)


KEY_FILE = Path.home() / ".divinci" / "star-trek-mtg" / "openrouter.env"


def load_openrouter_key() -> str:
    """Return the OpenRouter key, refusing a file with the wrong mode or a non-OpenRouter key.
    Never prints the value; errors name the problem only."""
    if not KEY_FILE.exists():
        raise RuntimeError("no key file; see OPENROUTER-SETUP.md")
    if KEY_FILE.stat().st_mode & 0o077:
        raise RuntimeError("key file is readable by others; chmod 600 it")
    lines = [l for l in KEY_FILE.read_text().splitlines() if l.strip()]
    if len(lines) != 1 or not lines[0].startswith("OPENROUTER_API_KEY="):
        raise RuntimeError("key file must be one line: OPENROUTER_API_KEY=sk-or-...")
    key = lines[0].split("=", 1)[1]
    if not key.startswith("sk-or-"):
        raise RuntimeError("that key is not an OpenRouter key (OpenRouter keys start with sk-or-)")
    return key


PANEL_MODELS = [
    "openai/gpt-4.1-mini",
    "deepseek/deepseek-v4.1-flash-1017",
    "mistralai/mistral-small-3.2-24b-instruct",
]


class OpenRouterDrafter:
    """Asks one OpenRouter model to pick a card. Any failure returns None so the panel ignores it."""

    def __init__(self, model: str, timeout: float = 60.0):
        self.name = model
        self.timeout = timeout

    def _prompt(self, pack, pool):
        lines = []
        for i, c in enumerate(pack, 1):
            lines.append(f"{i}. {c['name']} {c['mana_cost'] or ''} ({c['rarity']}) {c['type_line']}: "
                         f"{c['oracle_text'][:200].replace(chr(10), ' | ')}")
        mine = ", ".join(c["name"] for c in pool) or "(nothing yet)"
        return ("You are drafting a Magic: The Gathering Star Trek booster draft (2-headed giant "
                "prerelease, 40-card decks). Pick the single best card for your deck.\n\n"
                f"Your picks so far: {mine}\n\nPack:\n" + "\n".join(lines) +
                '\n\nReply with only JSON: {"pick": <number from the pack list>}')

    def pick(self, pack, pool, context):
        import json as _json
        import urllib.request
        key = load_openrouter_key()
        body = _json.dumps({
            "model": self.name,
            "messages": [{"role": "user", "content": self._prompt(pack, pool)}],
            "temperature": 0.2,
            "max_tokens": 800,
        }).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions", data=body, method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            reply = ""
            for _attempt in range(2):  # some reasoning models return an empty content field once
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    reply = _json.loads(r.read())["choices"][0]["message"].get("content") or ""
                if reply.strip():
                    break
            start, end = reply.find("{"), reply.rfind("}")
            n = _json.loads(reply[start:end + 1])["pick"]
            return int(n) - 1
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            return None


def panel_pick(drafters: list[Drafter], pack: list[dict], pool: list[dict], context: dict) -> tuple[int, str]:
    votes: collections.Counter[int] = collections.Counter()
    first_voter: dict[int, int] = {}
    for order, d in enumerate(drafters):
        try:
            idx = d.pick(pack, pool, context)
        except Exception:
            idx = None
        if isinstance(idx, int) and 0 <= idx < len(pack):
            votes[idx] += 1
            first_voter.setdefault(idx, order)
    if not votes:
        fallback = HeuristicDrafter(seed=context.get("seed", 0)).pick(pack, pool, context)
        return fallback, "fallback:heuristic"
    best = max(votes, key=lambda i: (votes[i], -first_voter[i]))
    return best, f"panel:{votes[best]}/{len(drafters)}"
