"""Which speech-to-text setup hears plays at a real table? Every play × voice × room condition ×
Whisper model × preprocessing, scored the way the table scores it: was the card recognised
(exactly, or as the top close candidate)? Latency is reported with it — a model that hears
everything a second late is a different trade.

  ~/.venvs/table/bin/python table/tests/stt_bench.py                 # all models
  ~/.venvs/table/bin/python table/tests/stt_bench.py --models small,turbo --pre none,norm
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import sense_run as S  # noqa: E402

MODELS = {"small": "mlx-community/whisper-small-mlx", "medium": "mlx-community/whisper-medium-mlx",
          "turbo": "mlx-community/whisper-large-v3-turbo"}
PLAYS = ["Llanowar Elves", "Rhystic Study", "Swords to Plowshares", "Cultivate", "Kodama's Reach", "Smothering Tithe",
         "Sol Ring", "Beast Within", "Goblin Instigator", "Cyclonic Rift", "Arcane Signet", "Beast Whisperer"]
VOICES = ["Daniel", "Samantha", "Karen"]
CONDITIONS = {
    "clean": {"pad_s": 0.3},
    "loud room 5 dB": {"pad_s": 0.4, "snr_db": 5},
    "far field": {"pad_s": 0.3, "rt60": 0.5, "drr_db": 0},
    "far + 12 dB": {"pad_s": 0.3, "rt60": 0.5, "drr_db": 0, "snr_db": 12},
    "whispered": {"pad_s": 0.3, "voice": "Whisper"},
}
HINT = ("Magic: The Gathering Commander game: mana, life, graveyard, library. Names: Claude; Michael; Sam; "
        "Ellivere of the Wild Court; Ghalta, Primal Hunger; Krenko, Mob Boss.")


def preprocess(a: np.ndarray, how: str) -> np.ndarray:
    if how == "none":
        return a
    if how in ("norm", "denoise+norm"):
        pass
    if "denoise" in how:
        a = denoise(a)
    rms = float(np.sqrt(np.mean(a ** 2))) or 1e-4
    return np.clip(a * (0.1 / rms), -1, 1).astype(np.float32)              # ≈ -20 dBFS


def denoise(a: np.ndarray) -> np.ndarray:
    """Spectral subtraction: the noise floor is estimated from the quietest 20 % of frames."""
    n, hop = 512, 128
    win = np.hanning(n).astype(np.float32)
    pad = np.pad(a, (n, n))
    frames = np.lib.stride_tricks.sliding_window_view(pad, n)[::hop] * win
    spec = np.fft.rfft(frames, axis=1)
    mag, ph = np.abs(spec), np.angle(spec)
    energy = mag.sum(1)
    noise = mag[energy <= np.percentile(energy, 20)].mean(0)
    clean = np.maximum(mag - 1.5 * noise, 0.08 * mag)
    out = np.fft.irfft(clean * np.exp(1j * ph), n, axis=1) * win
    y = np.zeros(len(pad), np.float32)
    for i, f in enumerate(out):
        y[i * hop:i * hop + n] += f
    y /= (win ** 2).sum() / hop
    return y[n:n + len(a)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="small,medium,turbo")
    ap.add_argument("--pre", default="none,norm,denoise+norm")
    a = ap.parse_args()
    import mlx_whisper
    from match import name_phrase, recognise_spoken
    cat = S.catalog()
    rng = np.random.default_rng(11)
    S.OUT.mkdir(parents=True, exist_ok=True)
    clips = []                                   # (condition, card, audio) — the same audio for every model
    for cond, spec in CONDITIONS.items():
        for i, card in enumerate(PLAYS):
            v = spec.get("voice") or VOICES[i % len(VOICES)]
            clips.append((cond, card, S.mix(S.say_wav(f"I cast {card}.", v), spec, rng)))
    results = {}
    for m in a.models.split(","):
        for part in m.split("+"):
            mlx_whisper.transcribe(np.zeros(16000, np.float32), path_or_hf_repo=MODELS[part], language="en")   # load
        for pre in a.pre.split(","):
            per, ms = {}, []
            for cond, card, audio in clips:
                x = preprocess(audio, pre)
                t0 = time.time()
                first = m.split("+")[0]
                text = mlx_whisper.transcribe(x, path_or_hf_repo=MODELS[first], language="en", initial_prompt=HINT,
                                              condition_on_previous_text=False)["text"]
                got = recognise_spoken(text, cat)[0]
                if "+" in m and not got and name_phrase(text):            # second pass only on a miss
                    text = mlx_whisper.transcribe(x, path_or_hf_repo=MODELS[m.split("+")[1]], language="en",
                                                  initial_prompt=HINT, condition_on_previous_text=False)["text"]
                    got = recognise_spoken(text, cat)[0]
                ms.append((time.time() - t0) * 1000)
                ok = card in got
                per.setdefault(cond, []).append(ok)
            total = sum(sum(v) for v in per.values())
            results[f"{m}/{pre}"] = {c: sum(v) for c, v in per.items()} | {"total": total,
                                     "p50_ms": round(statistics.median(ms)), "p95_ms": round(sorted(ms)[int(.95 * len(ms))])}
            row = "  ".join(f"{c.split()[0]}:{sum(v):2}/{len(v)}" for c, v in per.items())
            print(f"{m:6} {pre:13} {row}  total {total:2}/{len(clips)}  p50 {statistics.median(ms):4.0f} ms", flush=True)
    (S.OUT / "stt-bench.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
