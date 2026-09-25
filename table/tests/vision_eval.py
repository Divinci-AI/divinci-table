"""Camera reading, by condition: full-frame OCR vs per-card crops (vision.py). Scored as the table
needs it: the right card(s), and NEVER a wrong one.

  ~/.venvs/table/bin/python table/tests/vision_eval.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import sense_run as S  # noqa: E402
import vision  # noqa: E402
from match import identify_any  # noqa: E402
from ocr_mac import read_lines  # noqa: E402

CARDS = ["Counterspell", "Serra Angel", "Lightning Bolt", "Mulldrifter", "Rhystic Study", "Sol Ring", "Hellrider",
         "Birds of Paradise"]
CONDS = {"normal": {"scale": 0.9}, "arm's length 45%": {"scale": 0.45}, "across 35%": {"scale": 0.35},
         "across 28%": {"scale": 0.28}, "far 22%": {"scale": 0.22}, "sideways 35%": {"scale": 0.35, "rotate": 90},
         "upside-down 35%": {"scale": 0.35, "rotate": 180}, "tilted 28%": {"scale": 0.28, "rotate": 12},
         "dim 35%": {"scale": 0.35, "dim": 0.5}}


WRONG = [0]      # per-card crop reads that named a DIFFERENT card: must stay 0


def main():
    cat = S.catalog()
    ident = lambda lines: identify_any(lines, cat)[0]
    rng = np.random.default_rng(5)
    for cond, spec in CONDS.items():
        full = crop = wrong = 0
        ms = []
        for c in CARDS:
            jpg = S.scene({**spec, "card": c}, rng)
            f = ident([l.text for l in read_lines(jpg)])
            full += f == c
            t0 = time.time()
            got = [x["name"] for x in vision.read_cards(jpg, ident, read_lines)]
            ms.append((time.time() - t0) * 1000)
            crop += c in got
            wrong += any(g != c for g in got) + (f is not None and f != c)
            WRONG[0] += any(g != c for g in got)
        print(f"{cond:18} full-frame {full}/{len(CARDS)}  crops {crop}/{len(CARDS)}  wrong {wrong}  "
              f"crop p50 {sorted(ms)[len(ms) // 2]:.0f} ms", flush=True)
    two = ok2 = 0
    for i in range(0, len(CARDS) - 1, 2):
        pair = CARDS[i:i + 2]
        jpg = S.scene({"cards": pair, "scale": 0.55}, rng)
        got = {x["name"] for x in vision.read_cards(jpg, ident, read_lines)}
        two += 1
        ok2 += set(pair) <= got
        print(f"  two cards {pair} → {sorted(got)}")
    print(f"two cards held together: both read {ok2}/{two}")
    print(f"vision: {WRONG[0]} wrong reads — {'passed' if not WRONG[0] else 'FAILED'}")
    sys.exit(1 if WRONG[0] else 0)


if __name__ == "__main__":
    main()
