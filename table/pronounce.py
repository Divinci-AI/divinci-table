"""How the AI's words should SOUND, separate from what they mean.

Rules, logs and the leak guard all work on the real text; only the audio uses for_speech(). The
lexicon is measured, not guessed: table/tests/voice_audit.py speaks every name in the deck in
the AI's voice, transcribes it back with no hint, and each entry here fixed a name the table
could not make out.
"""
from __future__ import annotations

import re

LEXICON: dict[str, str] = {
    # voice_audit.py, Moira, 2026-09-25 — heard back with no hint:
    "Castle Ardenvale": "Castle Arden Vale",       # was "Castle Out and Veil"
    "Knickknack Ouphe": "Knick-knack Oof",         # was "Nignac Oath"
    "Aura Gnarlid": "Aura Narlid",                 # was "Orinulid" (still hard for Moira; fine for Samantha)
}

_PATTERN = None


def for_speech(text: str) -> str:
    global _PATTERN
    if not LEXICON or not text:
        return text
    if _PATTERN is None:
        _PATTERN = re.compile("|".join(r"\b" + re.escape(k) + r"\b" for k in sorted(LEXICON, key=len, reverse=True)))
    return _PATTERN.sub(lambda m: LEXICON[m.group(0)], text)
