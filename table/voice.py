"""Open-mic pipeline, server side: speech → text → what was said and to whom → what to do.

Jev DECIDES (typed, one request per utterance); code ACTS. Replies are templates for now; the next
step routes them through each AI player's Divinci Release (personality) and Divinci TTS (voice).

Privacy: audio arrives in memory and is never written to disk. While the router is TypeSafe's
jev-latest, the TRANSCRIPT of anything said near the mic goes to TypeSafe; a local router
(djev/so1 on the Jetson) removes that.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import tempfile
import time
import wave

import numpy as np

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "mlx-community/whisper-small-mlx")
_KEY = os.environ.pop("TYPESAFE_API_KEY", None)      # read once; reaches curl on stdin only
MAX_ROUTER_CALLS = int(os.environ.get("JEV_MAX_CALLS", "500"))
_calls = 0

# Whisper invents these from silence or breath noise. Drop them rather than route them.
HALLUCINATIONS = {"", "you", "thank you", "thank you.", "thanks for watching", "thanks for watching!",
                  ".", "bye", "okay", "hmm"}


def wav_to_float32(data: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(data)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
            raise ValueError("expected 16 kHz mono 16-bit WAV")
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


import threading as _threading
_WHISPER_LOCK = _threading.Lock()      # one transcription at a time: the MLX model is shared state


def transcribe(audio: np.ndarray, hint: str) -> tuple[str, float]:
    """Returns (text, mean no-speech probability). The hint (player + card names) is what gets
    'Sheoldred' instead of 'Shealderd': 4/8 → 8/8 names right in the 2026-09-24 check."""
    import mlx_whisper
    with _WHISPER_LOCK:
        r = _transcribe_locked(mlx_whisper, audio, hint)
    segs = r.get("segments") or []
    nsp = sum(s.get("no_speech_prob", 0.0) for s in segs) / len(segs) if segs else 1.0
    return r["text"].strip(), nsp


def _transcribe_locked(mlx_whisper, audio, hint):
    return mlx_whisper.transcribe(audio, path_or_hf_repo=WHISPER_MODEL, language="en",
                               initial_prompt=hint, condition_on_previous_text=False)


def is_noise(text: str, no_speech: float) -> bool:
    return text.lower().strip(" .!?") in {h.strip(" .!?") for h in HALLUCINATIONS} or no_speech > 0.6


KINDS = {
    "play": "Someone announces what they do in the game: casting or playing a card, activating an "
            "ability, or attacking a player.",
    "question": "Someone asks one of the AI players a question.",
    "deal": "Someone offers or asks for an agreement with an AI player: a deal, truce, alliance, or "
            "a promise like 'if you do this, I will do that'.",
    "rules": "Someone asks about the rules or the state of the game.",
    "chatter": "Anything else: side conversation, jokes, food, noise.",
}


def route(text: str, recent: list[str], ai_players: list[dict], humans: list[dict] | None = None) -> dict:
    """One Jev request, four questions answered in parallel. `accept_deal` is speculative: it is
    only used when `kind` comes back as a deal, and costs nothing extra to ask up front."""
    global _calls
    backend = os.environ.get("ROUTER", "so1")          # offline by default; "typesafe" sends transcripts out
    if backend == "ollama":
        return route_local(text, recent, ai_players, humans)
    url = ("https://api.typesafe.ai/v1/systemone" if backend == "typesafe"
           else os.environ.get("SO1_URL", "http://127.0.0.1:8792").rstrip("/") + "/v1/systemone")
    if backend == "typesafe" and not _KEY:
        raise RuntimeError("TYPESAFE_API_KEY not set (or set ROUTER=so1 / ROUTER=ollama for a local router)")
    if _calls >= MAX_ROUTER_CALLS:
        raise RuntimeError(f"router call budget ({MAX_ROUTER_CALLS}) used up; raise JEV_MAX_CALLS")
    _calls += 1
    names = [p["name"] for p in ai_players]
    addressees = {n: f"The AI player {n}." for n in names}
    addressees["the whole table"] = "Everyone at the table, not one specific player."
    addressees["nobody in particular"] = "Not addressed to anyone, or to a human player."
    state = {"game": "A four-player Magic: The Gathering Commander game. Some seats are AI players.",
             "ai_players": ai_players, "human_players": humans or [],
             "recent_conversation": recent[-6:], "utterance": text}
    body = {"model": "jev-latest", "state": state, "questions": {
        "kind": {"type": "choice", "criteria": KINDS,
                 "instructions": "What kind of thing did the speaker just say (`utterance`)?"},
        "addressee": {"type": "choice", "criteria": addressees,
                      "instructions": "Who is the `utterance` addressed to? Names said at the start "
                                      "of a sentence usually mark who is being spoken to."},
        "expects_answer": {"type": "noul",
                           "instructions": "Does the speaker expect a spoken reply from one of the "
                                           "AI players in `ai_players`?"},
        "accept_deal": {"type": "noul",
                        "instructions": "Suppose the `utterance` is a deal offered to the AI player "
                                        "it addresses. Would accepting it help that AI player win?"},
    }}
    fd, path = tempfile.mkstemp(suffix=".json")
    os.write(fd, json.dumps(body).encode())
    os.close(fd)
    try:
        t0 = time.time()
        r = subprocess.run(["curl", "-q", "-sS", "--max-time", "60", "-X", "POST",
                            url, "--config", "-",
                            "-H", "Content-Type: application/json", "--data-binary", f"@{path}",
                            "-w", "\n%{http_code}"],
                           input=(f'header = "Authorization: Bearer {_KEY}"\n' if backend == "typesafe" else ""),
                           capture_output=True, text=True)
        ms = round((time.time() - t0) * 1000)
    finally:
        os.unlink(path)
    out, _, code = r.stdout.rpartition("\n")
    try:
        a = json.loads(out)["answers"]
    except (json.JSONDecodeError, KeyError):
        raise RuntimeError(f"router HTTP {code}: {out[:160]!r}")
    return {"kind": a["kind"]["choice"], "kind_p": a["kind"]["probabilities"],
            "addressee": a["addressee"]["choice"], "addressee_p": a["addressee"]["probabilities"],
            "expects_answer": a["expects_answer"]["noul"], "accept_deal": a["accept_deal"]["noul"],
            "ms": ms, "router": backend}


def decide_reply(r: dict, ai_names: list[str]) -> tuple[str | None, str | None]:
    """Policy lives in code, so thresholds are tunable without re-asking the model.
    Returns (speaker, text) or (None, None). Templates until the Release chat is wired."""
    who = r["addressee"] if r["addressee"] in ai_names else None
    if who is None or r["kind"] in ("chatter", "play"):
        return None, None
    if r["kind"] == "deal":
        return who, ("Deal." if r["accept_deal"] >= 0.5 else "No deal.")
    if r["expects_answer"] < 0.5:
        return None, None
    return who, "Let me think about that." if r["kind"] == "question" else "Good question. Let me check."


def leaks_hand(text: str, hand_names: list[str]) -> str | None:
    """Code guard, not a prompt: a reply that names a card in the AI's hidden hand is never
    spoken. Returns the offending name, or None."""
    low = text.lower()
    for n in hand_names:
        if re.search(r"\b" + re.escape(n.lower()) + r"\b", low):
            return n
    return None


# ── Offline router: a local Gemma through Ollama ─────────────────────────────────────────────
# Same questions as route(), answered by reading the probability of each option LETTER in the
# model's first output token (Ollama returns top_logprobs), so the result has the same shape:
# a choice plus a distribution. Free and offline; nothing leaves this machine.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("ROUTER_OLLAMA_MODEL", "gemma4:e2b")


def _ollama_choice(context: str, question: str, options: dict[str, str]) -> dict[str, float]:
    import math
    import urllib.request
    keys = list(options)
    letters = [chr(65 + i) for i in range(len(keys))]
    lines = "\n".join(f"{l}. {k}: {options[k]}" if options[k] else f"{l}. {k}" for l, k in zip(letters, keys))
    body = {"model": OLLAMA_MODEL, "stream": False, "think": False, "logprobs": True, "top_logprobs": 20,
            "options": {"temperature": 0, "num_predict": 1},
            "messages": [{"role": "user", "content": f"{context}\n\n{question}\n{lines}\nReply with only the letter."}]}
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    raw = urllib.request.urlopen(req, timeout=60).read().decode()
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"Ollama returned non-JSON: {raw[:160]!r}")
    tops = (d.get("logprobs") or [{}])[0].get("top_logprobs") or []
    lp = {}
    for t in tops:
        tok = str(t["token"]).strip()
        if tok in letters and tok not in lp:
            lp[tok] = t["logprob"]
    floor = (min(lp.values()) if lp else -20.0) - 5.0          # letters outside the top 20
    ex = [math.exp(lp.get(l, floor)) for l in letters]
    s = sum(ex)
    return {k: e / s for k, e in zip(keys, ex)}


def route_local(text: str, recent: list[str], ai_players: list[dict], humans: list[dict] | None = None) -> dict:
    t0 = time.time()
    names = [p["name"] for p in ai_players]
    context = ("A four-player Magic: The Gathering Commander game. AI players: "
               + ", ".join(f"{p['name']} ({p['commander']})" for p in ai_players)
               + ". Human players: " + (", ".join(f"{p['name']} ({p['commander']})" for p in humans or []) or "none")
               + ".\nRecent conversation: " + (" / ".join(recent[-4:]) or "(none)")
               + f'\nSomeone at the table just said: "{text}"')
    kind_p = _ollama_choice(context, "What kind of thing did the speaker just say?", KINDS)
    addressees = {n: f"the AI player {n}" for n in names}
    addressees["the whole table"] = "everyone, not one specific player"
    addressees["nobody in particular"] = "not addressed to anyone, or to a human player"
    addr_p = _ollama_choice(context, "Who is it addressed to? A name said at the start of a sentence "
                                     "usually marks who is being spoken to.", addressees)
    exp_p = _ollama_choice(context, "Does the speaker expect a spoken reply from one of the AI players?",
                           {"yes": "", "no": ""})
    deal_p = _ollama_choice(context, "Suppose this is a deal offered to the AI player it addresses. "
                                     "Would accepting it help that AI player win?", {"yes": "", "no": ""})
    kind = max(kind_p, key=kind_p.get)
    addressee = max(addr_p, key=addr_p.get)
    return {"kind": kind, "kind_p": kind_p, "addressee": addressee, "addressee_p": addr_p,
            "expects_answer": exp_p["yes"], "accept_deal": deal_p["yes"],
            "ms": round((time.time() - t0) * 1000), "router": f"ollama:{OLLAMA_MODEL}"}
