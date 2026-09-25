"""How much of what the AI says can the table actually make out? Every card name in its deck (and
its own name) is spoken the way the AI says it — "I cast <card>." — in each candidate voice,
transcribed back by Whisper with no hint, and scored with the same "would a player recognise it"
rule the sensory suite uses. Run it after changing the voice or the pronunciation lexicon.

  ~/.venvs/table/bin/python table/tests/voice_audit.py                    # Moira + 4 others, raw vs lexicon
  ~/.venvs/table/bin/python table/tests/voice_audit.py --voices Moira --fails
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import sense_run as S  # noqa: E402
import pronounce  # noqa: E402


def names(deck_path: Path) -> list[str]:
    d = json.loads(deck_path.read_text())
    out = sorted({c["name"] for c in d["commander"] + d["mainBoard"]})
    return [n for n in out if n not in ("Plains", "Forest", "Island", "Swamp", "Mountain")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", default=str(S.REPO / "decks" / "ellivere.json"))
    ap.add_argument("--voices", default="Moira,Samantha,Daniel,Karen,Tessa")
    ap.add_argument("--ai-name", default="Claude")
    ap.add_argument("--fails", action="store_true", help="list every name that doesn't survive")
    a = ap.parse_args()
    import voice
    S.OUT.mkdir(parents=True, exist_ok=True)
    cards = names(Path(a.deck))
    items = [(a.ai_name, f"{a.ai_name}, turn 3.")] + [(c, f"I cast {c}.") for c in cards]
    print(f"{len(items)} names ({len(cards)} cards + the AI's name)\n")
    report = {}
    for v in a.voices.split(","):
        for mode in ("raw", "lexicon"):
            lost = []
            for name, line in items:
                spoken = line if mode == "raw" else pronounce.for_speech(line)
                heard, _ = voice.transcribe(S.say_wav(spoken, v), "")
                if not S.audible(name, heard):
                    lost.append((name, heard))
            ok = len(items) - len(lost)
            report[f"{v}/{mode}"] = {"audible": ok, "of": len(items), "lost": lost}
            print(f"{v:9} {mode:8} {ok:3}/{len(items)} audible ({100 * ok / len(items):.0f} %)", flush=True)
            if a.fails:
                for n, h in lost:
                    print(f"      ✗ {n:36} heard {h!r}")
    (S.OUT / "voice-audit.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
