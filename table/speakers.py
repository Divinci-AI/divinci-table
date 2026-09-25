"""Who is speaking — offline, no model download: MFCC statistics of a voice, enrolled per player.

A player enrolls by saying their name at the start ("This is Michael."); every later line is
matched to the closest enrolled voice. That resolves "I'm at 35" (whose life?), and the AI's own
voice (enrolled from its TTS) is one more way to recognise an echo.

The embedding is deliberately simple — mean and spread of 20 MFCCs and their deltas over the
voiced frames — and scored with cosine similarity after per-table normalisation. Measured, not
assumed: see table/tests/speaker_eval.py.
"""
from __future__ import annotations

import threading

import numpy as np

SR = 16000
_FB = None


def _mel_fb(n_fft=512, n_mels=40, fmin=60, fmax=7600):
    global _FB
    if _FB is None:
        mel = lambda f: 2595 * np.log10(1 + f / 700)
        imel = lambda m: 700 * (10 ** (m / 2595) - 1)
        pts = imel(np.linspace(mel(fmin), mel(fmax), n_mels + 2))
        bins = np.floor((n_fft + 1) * pts / SR).astype(int)
        fb = np.zeros((n_mels, n_fft // 2 + 1), np.float32)
        for i in range(n_mels):
            a, b, c = bins[i], bins[i + 1], bins[i + 2]
            fb[i, a:b] = (np.arange(a, b) - a) / max(1, b - a)
            fb[i, b:c] = (c - np.arange(b, c)) / max(1, c - b)
        _FB = fb
    return _FB


def mfcc(a: np.ndarray, n=20) -> np.ndarray:
    """Frames × n MFCCs (c1..cn) of the voiced frames only."""
    a = np.asarray(a, np.float32)
    if len(a) < SR // 4:
        return np.zeros((0, n), np.float32)
    a = np.append(a[0], a[1:] - 0.97 * a[:-1])                 # pre-emphasis
    hop, win = 160, 400
    nf = 1 + (len(a) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(nf)[:, None]
    frames = a[idx] * np.hamming(win)[None, :]
    spec = np.abs(np.fft.rfft(frames, 512)) ** 2
    energy = spec.sum(1)
    voiced = energy > np.percentile(energy, 40)                 # drop pauses and room tone
    logmel = np.log(spec[voiced] @ _mel_fb().T + 1e-8)
    k = np.arange(logmel.shape[1])
    dct = np.cos(np.pi / logmel.shape[1] * (k[None, :] + 0.5) * np.arange(1, n + 1)[:, None])
    return (logmel @ dct.T).astype(np.float32)


def embed_mfcc(a: np.ndarray) -> np.ndarray | None:
    m = mfcc(a)
    if len(m) < 20:
        return None
    d = np.diff(m, axis=0)
    return np.concatenate([m.mean(0), m.std(0), d.std(0)])


_ECAPA = None
_ECAPA_LOCK = threading.Lock()


def embed_ecapa(a: np.ndarray) -> np.ndarray | None:
    """SpeechBrain ECAPA-TDNN (VoxCeleb), 192-d, ~20 ms on CPU. Weights come from the local
    Hugging Face cache (downloaded once); HF_HUB_OFFLINE keeps it from ever calling out."""
    global _ECAPA
    if len(a) < SR // 3:
        return None
    import torch
    with _ECAPA_LOCK:
        if _ECAPA is None:
            import logging
            from pathlib import Path
            logging.getLogger("speechbrain").setLevel(logging.ERROR)
            from speechbrain.inference.speaker import EncoderClassifier
            _ECAPA = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb",
                                                    savedir=str(Path(__file__).parent / ".cache" / "ecapa"),
                                                    run_opts={"device": "cpu"})
        with torch.no_grad():
            e = _ECAPA.encode_batch(torch.tensor(np.asarray(a, np.float32))[None, :])
    return e.reshape(-1).numpy()


BACKEND = "ecapa"


def embed(a: np.ndarray) -> np.ndarray | None:
    return embed_ecapa(a) if BACKEND == "ecapa" else embed_mfcc(a)


class Speakers:
    """Enrolled voices for one table. Thread-safe."""

    def __init__(self, threshold: float = 0.3, margin: float = 0.05):
        self.lock = threading.Lock()
        self.samples: dict[str, list[np.ndarray]] = {}
        self.threshold, self.margin = threshold, margin

    def enroll(self, name: str, a: np.ndarray) -> bool:
        e = embed(a)
        if e is None:
            return False
        with self.lock:
            self.samples.setdefault(name, []).append(e)
            del self.samples[name][:-12]                       # keep the latest dozen
        return True

    def names(self):
        with self.lock:
            return sorted(self.samples)

    def _norm(self):  # mfcc only: ECAPA embeddings are compared as they are
        allv = np.array([e for v in self.samples.values() for e in v])
        mu = allv.mean(0)
        sd = allv.std(0) + 1e-3 if len(allv) > 2 else np.ones_like(mu)
        return mu, sd

    def identify(self, a: np.ndarray) -> tuple[str | None, float, dict]:
        """(name or None, confidence margin, all scores). None when unsure — callers then treat
        the speaker as unknown rather than guess."""
        e = embed(a)
        with self.lock:
            if e is None or len(self.samples) < 1:
                return None, 0.0, {}
            mu, sd = self._norm() if BACKEND == "mfcc" else (0.0, 1.0)
            z = (e - mu) / sd
            scores = {}
            for n, v in self.samples.items():
                c = ((np.array(v) - mu) / sd).mean(0)
                scores[n] = float(z @ c / (np.linalg.norm(z) * np.linalg.norm(c) + 1e-9))
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        best, s1 = ranked[0]
        s2 = ranked[1][1] if len(ranked) > 1 else -1.0
        if s1 < self.threshold or s1 - s2 < self.margin:
            return None, s1 - s2, scores
        return best, s1 - s2, scores
