"""Can the table tell its players apart by voice? Enroll each voice from 2 lines, then identify
held-out lines — clean, in room noise, across the table — and report accuracy and how often it
(correctly) says "not sure" instead of guessing wrong. Wrong answers are the number that matters:
a wrong speaker puts damage on the wrong player.

  ~/.venvs/table/bin/python table/tests/speaker_eval.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import sense_run as S  # noqa: E402
from speakers import Speakers  # noqa: E402

VOICES = ["Daniel", "Samantha", "Karen", "Rishi", "Tessa", "Moira"]      # Moira = the AI itself
ENROLL = ["This is {n}, I'm playing tonight.", "Okay, my name is {n} and I'm ready."]
TEST = ["I play a Forest and cast Cultivate.", "I'm at thirty-five.", "Ghalta attacks Claude for twelve.",
        "Does anyone want more pizza?", "No blocks, I take four.", "I cast Swords to Plowshares on your commander.",
        "Your turn.", "Wait, what does that card do?"]
CONDITIONS = {"clean": {}, "room 20 dB": {"snr_db": 20, "rt60": 0.3, "drr_db": 8},
              "noisy 12 dB": {"snr_db": 12, "rt60": 0.3, "drr_db": 6}}


def main(table_size=int(sys.argv[1]) if len(sys.argv) > 1 else 4):
    S.OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(3)
    voices = VOICES[:table_size - 1] + ["Moira"]
    for cond, spec in CONDITIONS.items():
        sp = Speakers()
        for v in voices:
            for line in ENROLL:
                sp.enroll(v, S.mix(S.say_wav(line.format(n=v), v), {"pad_s": 0.2, **spec}, rng))
        right = wrong = unsure = 0
        for v in voices:
            for line in TEST:
                who, margin, _ = sp.identify(S.mix(S.say_wav(line, v), {"pad_s": 0.2, **spec}, rng))
                if who is None:
                    unsure += 1
                elif who == v:
                    right += 1
                else:
                    wrong += 1
        n = right + wrong + unsure
        print(f"{cond:12} {len(voices)} voices: right {right}/{n} ({100 * right / n:.0f} %)  "
              f"wrong {wrong}  not sure {unsure}")
        total_wrong = globals().get("TOTAL_WRONG", 0) + wrong
        globals()["TOTAL_WRONG"] = total_wrong
    print(f"speaker ID: {globals().get('TOTAL_WRONG', 0)} wrong — passed" if not globals().get("TOTAL_WRONG") else "speaker ID: WRONG answers")
    sys.exit(1 if globals().get("TOTAL_WRONG") else 0)


if __name__ == "__main__":
    main()
