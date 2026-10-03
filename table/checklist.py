"""Step checklist: before a person passes, what on their own board wants attention in this step.

Rule-based and public-only: it reads the person's recorded permanents (name, rules text, counters,
note) — never a hand. Each item is {"card", "kind", "text"}; the phone shows them before ✋ Pass.
"""
from __future__ import annotations

import re

# (step, whose turn it must be, pattern in the card text, kind)
TRIGGERS = [
    ("upkeep", "own", r"at the beginning of your upkeep", "trigger"),
    ("upkeep", "any", r"at the beginning of each (player's )?upkeep", "trigger"),
    ("draw", "own", r"at the beginning of your draw step", "trigger"),
    ("beginning of combat", "own", r"at the beginning of combat on your turn", "trigger"),
    ("beginning of combat", "any", r"at the beginning of each combat", "trigger"),
    ("end step", "own", r"at the beginning of your end step", "trigger"),
    ("end step", "any", r"at the beginning of (each|the) end step", "trigger"),
]
MAIN = ("main 1", "main 2")


def _sentence(text: str, pattern: str) -> str:
    for part in re.split(r"(?<=[.)])\s+|\n", text or ""):
        if re.search(pattern, part, re.I):
            return part.strip()[:220]
    return ""


def _charge(p: dict) -> int:
    """Charge counters, from the counters field or a note/P-T like '2 charge'."""
    if isinstance(p.get("counters"), int) and "charge" in (p.get("text") or "").lower():
        return p["counters"]
    m = re.search(r"(\d+)\s*charge", f"{p.get('pt') or ''} {p.get('note') or ''}", re.I)
    return int(m.group(1)) if m else 0


def items(perms: list[dict], person: str, active: str, step: str) -> list[dict]:
    own = person == active
    out: list[dict] = []
    seen = set()

    def add(card, kind, text):
        k = (card, kind, text)
        if k not in seen:
            seen.add(k)
            out.append({"card": card, "kind": kind, "text": text})

    creatures = [p for p in perms if p.get("pt") and re.match(r"^\d+/\d+$", str(p.get("pt")))
                 and not p.get("face_down")]
    for p in perms:
        if p.get("face_down"):
            continue
        name, text = p.get("name") or "", p.get("text") or ""
        low = text.lower()
        for st, whose, pat, kind in TRIGGERS:
            if st == step and (whose == "any" or own):
                s = _sentence(text, pat)
                if s:
                    add(name, kind, s)
        if own and step in MAIN:
            if "Planeswalker" in (p.get("type") or "") and step == "main 1":
                add(name, "loyalty", "Use one loyalty ability this turn (sorcery speed)?")
            if "station" in low and "spacecraft" in (p.get("type") or "").lower():
                untapped = [c["name"] for c in creatures if not c.get("tapped") and c.get("name") != name]
                if untapped:
                    add(name, "station", f"Station it? Tap a creature ({', '.join(untapped[:3])}) to add charge counters equal to its power.")
            if name == "Incubator" or (p.get("token") and "transform this token" in low):
                add(name, "transform", "Pay {2} to transform it into a Phyrexian artifact creature?")
        m = re.search(r"remove (three|3|two|2|four|4) charge counters", low)
        if m and not p.get("tapped"):
            need = {"two": 2, "three": 3, "four": 4}.get(m.group(1), None) or int(m.group(1))
            if _charge(p) >= need:
                add(name, "ability", f"It has {_charge(p)} charge counters: its big ability is ready ({_sentence(text, 'remove ' + m.group(1)) or 'remove counters'}).")
    if own and step == "declare attackers":
        ready = [c["name"] for c in creatures if not c.get("tapped")]
        if ready:
            add("Attack", "attack", f"Untapped creatures that could attack: {', '.join(ready[:6])}. Declare with ⚔ (or pass for none).")
    return out
