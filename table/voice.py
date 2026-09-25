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
    backend = os.environ.get("ROUTER", "ollama")       # offline by default; "typesafe" sends transcripts out
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
    if r["kind"] == "turn":
        return who, TURN            # the server plays the turn; the announcements are the reply
    if r["kind"] == "deal":
        return who, ("Deal." if r["accept_deal"] >= 0.5 else "No deal.")
    if r["expects_answer"] < 0.5:
        return None, None
    return who, "Let me think about that." if r["kind"] == "question" else "Good question. Let me check."


TURN = "\x00take-turn"        # sentinel from decide_reply: not text, never spoken


def leaks_hand(text: str, hand_names: list[str]) -> str | None:
    """Code guard, not a prompt: a reply that names a card in the AI's hidden hand is never
    spoken. Returns the offending name, or None."""
    low = text.lower()
    for n in hand_names:
        if re.search(r"\b" + re.escape(n.lower()) + r"\b", low):
            return n
    return None


# ── Offline router + replies: a local Gemma through Ollama ─────────────────────────────────
# Ollama returns probabilities only for the token it GENERATES, so every question is a separate
# call (~0.7 s on gemma4:e2b). The router therefore asks ONE question whose options combine kind
# and addressee ("question for Talrand", "deal for Krenko", "a play", "chatter"), and asks the deal
# verdict only when the answer is a deal. 4 calls (~3 s) → 1–2 calls.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("ROUTER_OLLAMA_MODEL", "gemma4:e2b")
# Keep Gemma resident while the table server runs (Ollama's default unloads it after 5 idle minutes,
# and the reload makes the next line take ~3 s). The server unloads it on shutdown.
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "-1")


def _ollama(body: dict, timeout=30) -> dict:
    import urllib.request
    body = {**body, "keep_alive": _keep_alive_value()}
    req = urllib.request.Request(f"{OLLAMA_URL}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    raw = urllib.request.urlopen(req, timeout=timeout).read().decode()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"Ollama returned non-JSON: {raw[:160]!r}")


def _keep_alive_value():
    v = OLLAMA_KEEP_ALIVE
    return int(v) if v.lstrip("-").isdigit() else v


def ollama_load(models: list[str]) -> None:
    """Load each model now and pin it (keep_alive), so the first real line isn't a cold start."""
    for m in dict.fromkeys(models):
        _ollama({"model": m, "stream": False, "messages": [{"role": "user", "content": "ready"}],
                 "options": {"num_predict": 1}})


def ollama_unload(models: list[str]) -> None:
    """Give the memory back: keep_alive 0 unloads a model immediately."""
    import urllib.request
    for m in dict.fromkeys(models):
        try:
            req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", headers={"Content-Type": "application/json"},
                                         data=json.dumps({"model": m, "keep_alive": 0}).encode())
            urllib.request.urlopen(req, timeout=10).read()
        except Exception:
            pass


def _ollama_choice(context: str, question: str, options: dict[str, str]) -> dict[str, float]:
    """Distribution over `options`, read from the probability of each option LETTER in the
    model's first output token."""
    import math
    keys = list(options)
    letters = [chr(65 + i) for i in range(len(keys))]
    lines = "\n".join(f"{l}. {options[k]}" for l, k in zip(letters, keys))
    d = _ollama({"model": OLLAMA_MODEL, "stream": False, "think": False, "logprobs": True, "top_logprobs": 20,
                 "options": {"temperature": 0, "num_predict": 1},
                 "messages": [{"role": "user", "content": f"{context}\n\n{question}\n{lines}\nReply with only the letter."}]})
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


def _context(text, recent, ai_players, humans):
    return ("A four-player Magic: The Gathering Commander game. AI players: "
            + ", ".join(f"{p['name']} ({p['commander']})" for p in ai_players)
            + ". Human players: " + (", ".join(f"{p['name']} ({p['commander']})" for p in humans or []) or "none")
            + ".\nRecent conversation: " + (" / ".join(recent[-4:]) or "(none)")
            + f'\nSomeone at the table just said: "{text}"')


def hands_turn_over(text: str) -> bool:
    t = text.lower()
    if re.search(r"\b(is it|whose|when|what|who|how|why|which|are you|do you|did you|will you)\b", t):
        return False
    return bool(re.search(r"\b(your turn|you'?re up|you are up|go ahead|your go|take your turn|over to you|"
                          r"it'?s you|you'?re next|your move)\b", t))


def spoken_to(text: str, names: list[str]) -> str | None:
    """The AI player a sentence opens by addressing ("Talrand, …", "Hey Krenko, …", "OK Talrand!"),
    or None. A bare name followed by a verb ("Talrand attacks me") is talk ABOUT them, not TO them."""
    for n in names:
        if re.match(rf"^\s*(?:(?:hey|hi|ok|okay|so|alright|yo|and|but|well)[\s,]+)?{re.escape(n)}\s*[,!?:]", text, re.I):
            return n
    return None


# Words that open a sentence with a comma without addressing anyone.
_NOT_NAMES = {"yes", "no", "yeah", "yep", "nope", "ok", "okay", "alright", "well", "so", "and", "but",
              "hey", "hi", "hello", "sure", "fine", "deal", "wait", "look", "listen", "now", "then",
              "also", "actually", "honestly", "guys", "everyone", "everybody", "folks", "oh", "ah", "um",
              "uh", "right", "cool", "nice", "great", "thanks", "sorry", "please", "first", "second"}


def spoken_to_someone_else(text: str, ai_names: list[str]) -> str | None:
    """A sentence opening by addressing a NON-AI name ("Sam, …", "Krenko, …" when Krenko is not an
    AI here), or None. Such a line is for someone else, so no AI may answer it — measured
    2026-09-24: with one AI at the table, "Krenko, if you leave me alone…" was answered by Talrand."""
    m = re.match(r"^\s*(?:(?:hey|hi|ok|okay|so|alright|yo|and|but|well)[\s,]+)?([A-Za-z][A-Za-z'-]{1,20})\s*[,!:]", text, re.I)
    if not m:
        return None
    word = m.group(1)
    if word.lower() in _NOT_NAMES or any(word.lower() == n.lower() for n in ai_names):
        return None
    return word


def route_local(text: str, recent: list[str], ai_players: list[dict], humans: list[dict] | None = None) -> dict:
    t0 = time.time()
    names = [p["name"] for p in ai_players]
    opts = {"play": "Announcing something they do in the game: casting or playing a card, "
                    "activating an ability, or attacking a player."}
    for n in names:
        opts[f"question:{n}"] = f"Asking the AI player {n} a question."
    for n in names:
        opts[f"deal:{n}"] = (f"Offering or asking the AI player {n} for an agreement: a deal, truce, "
                             f"alliance, or a promise like 'if you do this, I will do that'.")
    # Taking a turn is irreversible (it draws, spends mana, attacks), so it is offered ONLY when the
    # words actually hand the turn over — never for a question. Measured 2026-09-24: "Ellivere, what
    # cards are in your hand?" and "…who are you attacking this turn?" were each routed as "your turn"
    # and made her play a whole extra turn.
    for p in ai_players if hands_turn_over(text) else []:
        if p.get("has_deck"):
            opts[f"turn:{p['name']}"] = (f"Telling the AI player {p['name']} that it is their turn to play now "
                                         f"(for example 'your turn', 'go ahead', 'you're up').")
    opts["question:table"] = "Asking a question of the whole table or a human player, not an AI player."
    opts["chatter"] = "Anything else: side conversation, jokes, food, noise."
    # Direct address is a rule, not a judgment: "Krenko, truce?" IS addressed to Krenko. The model
    # still decides WHAT it is, but can no longer hand it to a different AI (measured 2026-09-24:
    # "Krenko, truce this turn?" routed to Talrand without this).
    vocative = spoken_to(text, names)
    elsewhere = None if vocative else spoken_to_someone_else(text, names)
    if elsewhere:                                     # "Sam, …": for someone who isn't an AI
        opts = {k: v for k, v in opts.items() if k in ("play", "chatter", "question:table")}
    if vocative:
        keep = [f"question:{vocative}", f"deal:{vocative}", f"turn:{vocative}"]
        # Naming an AI player AND asking something ("Talrand, who are you attacking?") is a question
        # or an offer TO them, never an announcement of your own play — the word "attacking" pulled
        # exactly this line to "play" in the 2026-09-24 eval.
        if not text.rstrip().endswith("?"):
            keep += ["play", "chatter"]
        opts = {k: v for k, v in opts.items() if k in keep}
    ctx = _context(text, recent, ai_players, humans)
    p = _ollama_choice(ctx, "What did the speaker just do? A name said at the start of a sentence, "
                            "followed by a comma, marks who is being spoken to.", opts)
    for k in ["play", "chatter", "question:table"] + [f"{t}:{n}" for t in ("question", "deal") for n in names]:
        p.setdefault(k, 0.0)
    top = max(p, key=p.get)
    kind = top.split(":")[0] if top != "question:table" else "question"      # play|question|deal|turn|chatter
    kind_p = {"play": p["play"], "chatter": p["chatter"], "rules": 0.0,
              "turn": sum(v for k, v in p.items() if k.startswith("turn:")),
              "question": sum(v for k, v in p.items() if k.startswith("question:")),
              "deal": sum(v for k, v in p.items() if k.startswith("deal:"))}
    addr_p = {n: p[f"question:{n}"] + p[f"deal:{n}"] + p.get(f"turn:{n}", 0.0) for n in names}
    addr_p["the whole table"] = p["question:table"]
    addr_p["nobody in particular"] = p["play"] + p["chatter"]
    addressee = top.split(":")[1] if ":" in top and top != "question:table" else (
        "the whole table" if top == "question:table" else "nobody in particular")
    accept = 0.0
    if kind == "deal":                                   # only now is the verdict worth a call
        v = _ollama_choice(ctx, f"You are {addressee}. Would accepting this offer help you win the game?",
                           {"yes": "Yes, accept.", "no": "No, decline."})
        accept = v["yes"]
    return {"kind": kind, "kind_p": kind_p, "addressee": addressee, "addressee_p": addr_p,
            "expects_answer": addr_p.get(addressee, 0.0) if addressee in names else 0.0,
            "accept_deal": accept, "ms": round((time.time() - t0) * 1000), "router": f"ollama:{OLLAMA_MODEL}"}


# ── In-character replies ────────────────────────────────────────────────────────────────────
# THE FIREWALL: this function has no parameter through which the AI's hand could arrive. It sees
# only what everyone at the table can see (the conversation and announced public cards) plus the
# decision already taken (e.g. "decline the deal"). leaks_hand() still checks the output as a
# backstop, but a model can't leak what it was never told.
REPLY_MODEL = os.environ.get("REPLY_OLLAMA_MODEL", OLLAMA_MODEL)


def persona_reply(ai: dict, kind: str, heard: str, recent: list[str], public_board: list[str],
                  decision: str, own_board: list[str] | None = None) -> str:
    persona = ai.get("persona") or (
        f"You are {ai['name']}, an AI player piloting {ai['commander']} in a friendly four-player "
        f"Magic: The Gathering Commander game. You have a playful, confident table-talk personality.")
    rules = ("Reply in ONE or TWO short spoken sentences, under 30 words, in character. No lists, no "
             "stage directions, no emoji. Never mention or hint at specific cards in your hand; if asked, "
             "stay coy. You have not planned your next turn yet, so don't promise specific plays.")
    user = (f"Your own battlefield (public): {', '.join(own_board or []) or 'nothing yet'}.\n"
            f"Other players' announced cards: {', '.join(public_board[-8:]) or 'nothing announced'}.\n"
            f"Recent table talk: {' / '.join(recent[-4:]) or '(none)'}\n"
            f'Someone just said to you: "{heard}"\n'
            f"Your decision: {decision}\nSay your reply out loud now.")
    d = _ollama({"model": REPLY_MODEL, "stream": False, "think": False,
                 "options": {"temperature": 0.8, "num_predict": 70},
                 "messages": [{"role": "system", "content": f"{persona} {rules}"},
                              {"role": "user", "content": user}]}, timeout=20)
    out = (d.get("message") or {}).get("content", "").strip().strip('"').replace("\n", " ")
    out = re.sub(r"\*[^*]*\*", "", out).strip()           # drop *stage directions* if any slip in
    return out[:240]


# ── Vision: reading a public card when OCR can't ─────────────────────────────────────────────
def gemma_read_card(image_bytes: bytes) -> str:
    """Ask the local Gemma (it has vision) for the card's name. The caller must validate the answer
    against the real card-name catalog: a model can name a card that does not exist."""
    import base64
    d = _ollama({"model": OLLAMA_MODEL, "stream": False, "think": False,
                 "options": {"temperature": 0, "num_predict": 24},
                 "messages": [{"role": "user", "images": [base64.b64encode(image_bytes).decode()],
                               "content": "What is the exact name of this Magic: The Gathering card? "
                                          "Reply with only the card's name, or NONE if no card is visible."}]},
                timeout=30)
    return (d.get("message") or {}).get("content", "").strip().strip('".')


def persona_react(ai: dict, card: str, shown_by: str, recent: list[str], public_board: list[str]) -> str:
    """The AI's spoken reaction to a card someone SHOWED it. The card is public, so the persona may
    name it; it still never sees the AI's own hand."""
    return persona_reply(ai, "show", f"{shown_by} shows you the card {card}.", recent, public_board,
                         f"React to {card} in character — what you think of it, or of whoever plays it.")


def pick_heard_card(text: str, candidates: list[str]) -> tuple[str | None, float]:
    """Speech-to-text mangled a card name. Ask Gemma which of the close candidates was meant, with a
    'none of these' option; the caller accepts only a confident pick."""
    opts = {c: c for c in candidates}
    opts["none"] = "None of these — no card was named."
    probs = _ollama_choice(f'A player at a Magic: The Gathering table said (as transcribed by speech '
                           f'recognition, which may misspell card names): "{text}"',
                           "Which card did they most likely name?", opts)
    best = max(probs, key=probs.get)
    return (None if best == "none" else best), probs[best]
