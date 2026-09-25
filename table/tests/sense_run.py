"""Sensory scenario runner: play the humans' side of a Commander game through the AI's senses only.

Every input reaches the server the way it would at a real table:
  - speech: text rendered by a macOS voice (`say`), optionally sped up, with room noise or a
    second talker mixed in, sent to /api/utterance as audio;
  - camera: a Scryfall card image composited onto a table-coloured frame (scaled, rotated, blurred,
    glared, partly covered), sent to /api/show frame by frame.
Every output is judged the way a player would hear it: the AI's words are rendered in ITS voice
and transcribed back by Whisper with no hint, so a card name that doesn't survive the speaker
fails the test even when the text was right.

It starts its own server (default port 8801, its own token file) so a live game on :8800 is left
alone, and watches every socket of that server and Ollama for a non-loopback connection.

  ~/.venvs/table/bin/python table/tests/sense_run.py                      # all scenarios
  ~/.venvs/table/bin/python table/tests/sense_run.py --only hear- --tier regression
  ~/.venvs/table/bin/python table/tests/sense_run.py --list

Scenarios live in table/tests/scenarios/*.yaml. Tier "regression" must pass today; tier "goal"
describes what the player should do and is expected to fail until it is built. A goal that starts
passing is reported so it can be promoted.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).parent
TABLE = HERE.parent
REPO = TABLE.parent
FIX = HERE / "fixtures"
OUT = FIX / "sense"
SCEN = HERE / "scenarios"
sys.path.insert(0, str(TABLE))
sys.path.insert(0, str(HERE))
PY = sys.executable
SR = 16000
os.environ.setdefault("HF_HUB_OFFLINE", "1")             # the runner's own Whisper (hear-back) stays offline too
os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD", "1")


# ── server ───────────────────────────────────────────────────────────────────────────────────
class Server:
    def __init__(self, setup: dict, port: int):
        self.port, self.base = port, f"http://127.0.0.1:{port}"
        self.token_file = OUT / f".token-{port}"
        cmd = [PY, str(TABLE / "server.py"), "--any-card", "--port", str(port), "--token-file", str(self.token_file),
               "--brain", setup.get("brain", "gemma")]
        for a in setup.get("ai", []):
            cmd += ["--ai", a]
        if setup.get("ai_deck"):
            cmd += ["--ai-deck", str(REPO / setup["ai_deck"])]
        for h in setup.get("humans", []):
            cmd += ["--human", h]
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
        env["HF_HUB_OFFLINE"] = "1"
        self.log = open(OUT / f"server-{port}.log", "w")
        self.proc = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=self.log, stderr=subprocess.STDOUT)

    def wait_ready(self, timeout=180):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                sys.exit(f"test server exited: see {self.log.name}")
            txt = Path(self.log.name).read_text()
            if "voices:" in txt or "speaker ID unavailable" in txt:
                return
            time.sleep(1)
        sys.exit("test server not ready in time")

    def stop(self):
        # SIGTERM unpins Gemma in Ollama. If a live game is running on :8800 it needs Gemma pinned,
        # so leave it loaded then (SIGKILL skips the unpin).
        live = socket.socket()
        live_up = live.connect_ex(("127.0.0.1", 8800)) == 0
        live.close()
        self.proc.send_signal(signal.SIGKILL if live_up else signal.SIGTERM)
        self.proc.wait(timeout=20)


def http(base, method, path, body=None, ctype="application/json", headers=None):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    r = urllib.request.Request(base + path, data=data, method=method,
                               headers={"Content-Type": ctype, **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"error": raw[:200].decode(errors="replace")}


# ── inputs: speech ───────────────────────────────────────────────────────────────────────────
def say_wav(text: str, voice: str = "Samantha", rate: int | None = None) -> np.ndarray:
    key = re.sub(r"[^A-Za-z0-9]+", "_", f"{voice}_{rate}_{text}")[:120]
    cache = OUT / "speech" / f"{key}.npy"
    if cache.exists():
        return np.load(cache)
    cache.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        aiff, wav = Path(d) / "a.aiff", Path(d) / "a.wav"
        cmd = ["say", "-v", voice, "-o", str(aiff)] + (["-r", str(rate)] if rate else []) + [text]
        for attempt in (1, 2):                  # macOS speech can stall (seen: one `say` hung 38 min)
            try:
                subprocess.run(cmd, check=True, timeout=30)
                break
            except subprocess.TimeoutExpired:
                if attempt == 2:
                    raise
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ar", str(SR), "-ac", "1",
                        "-sample_fmt", "s16", str(wav)], check=True, timeout=30)
        with wave.open(str(wav)) as w:
            a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    np.save(cache, a)
    return a


def mix(speech: np.ndarray, spec: dict, rng: np.random.Generator) -> np.ndarray:
    a = speech.copy()
    if spec.get("pad_s"):                                   # silence around the words, as a live mic hears
        pad = np.zeros(int(SR * spec["pad_s"]), np.float32)
        a = np.concatenate([pad, a, pad])
    if spec.get("rt60"):                                    # far-field: the laptop across the table
        n = int(SR * spec["rt60"])
        tail = rng.standard_normal(n).astype(np.float32) * np.exp(-6.9 * np.arange(n) / n).astype(np.float32)
        tail[: int(SR * 0.004)] = 0                           # early gap before reflections
        tail = np.convolve(tail, np.ones(6) / 6, mode="same")  # walls and furniture absorb the highs
        drr = spec.get("drr_db", 0)                           # direct-to-reverberant ratio; ~0 dB at 2–3 m in a room
        ir = tail / (np.sqrt(np.sum(tail ** 2)) or 1) * 10 ** (-drr / 20)
        ir[0] = 1.0
        m = len(a) + len(ir) - 1                               # FFT convolution: 100× faster than np.convolve
        size = 1 << (m - 1).bit_length()
        a = np.fft.irfft(np.fft.rfft(a, size) * np.fft.rfft(ir, size), size)[: len(a) + n // 2].astype(np.float32)
        a /= (np.abs(a).max() or 1) / 0.5
    rms = float(np.sqrt(np.mean(a ** 2))) or 1e-4
    if spec.get("over"):                                    # a second talker at the same time
        o = spec["over"]
        b = say_wav(o["text"], o.get("voice", "Daniel"))
        start = int(SR * o.get("at_s", 0.3))
        b = np.concatenate([np.zeros(start, np.float32), b])
        n = max(len(a), len(b))
        a, b = np.pad(a, (0, n - len(a))), np.pad(b, (0, n - len(b)))
        b *= rms * 10 ** (-o.get("db_below", 6) / 20) / (float(np.sqrt(np.mean(b[b != 0] ** 2))) or 1e-4)
        a = a + b
    if spec.get("snr_db") is not None:                      # room noise: pink-ish hum + babble
        n = rng.standard_normal(len(a)).astype(np.float32)
        n = np.convolve(n, np.ones(8) / 8, mode="same")
        babble = sum(np.resize(say_wav(t, v), len(a)) for t, v in
                     (("so anyway the drive over was fine", "Karen"), ("pass the chips please", "Fred")))
        noise = n / (np.sqrt(np.mean(n ** 2)) or 1) + 0.6 * babble / (np.sqrt(np.mean(babble ** 2)) or 1)
        noise *= rms * 10 ** (-spec["snr_db"] / 20) / (np.sqrt(np.mean(noise ** 2)) or 1)
        a = a + noise
    return np.clip(a, -1, 1)


def to_wav(a: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes((a * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


# ── inputs: camera ───────────────────────────────────────────────────────────────────────────
def card_image(name: str, query: str | None = None) -> Image.Image:
    """A card's Scryfall image. `query`: a Scryfall search instead of a name (a token, a Japanese
    printing: 'name:Goblin t:token', '!"Sol Ring" lang:ja')."""
    p = FIX / (re.sub(r"[^A-Za-z0-9]+", "", query or name) + ".jpg")
    if not p.exists():
        hdr = {"User-Agent": "divinci-table-tests/0.1", "Accept": "*/*"}
        if query:
            q = urllib.parse.quote(query)
            meta = json.loads(urllib.request.urlopen(urllib.request.Request(
                f"https://api.scryfall.com/cards/search?unique=prints&include_multilingual=true&q={q}", headers=hdr), timeout=30).read())
            c = meta["data"][0]
            url = (c.get("image_uris") or c["card_faces"][0]["image_uris"])["normal"]
        else:
            url = "https://api.scryfall.com/cards/named?format=image&version=normal&exact=" + urllib.parse.quote(name)
        p.write_bytes(urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=30).read())
        time.sleep(0.15)
    return Image.open(p).convert("RGB")


def scene(spec: dict, rng: np.random.Generator) -> bytes:
    """A 1280x720 camera frame: the card on a table, degraded the way a real one is."""
    bg = np.full((720, 1280, 3), (58, 44, 34), np.float32) + rng.normal(0, 9, (720, 1280, 3))
    frame = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8))
    items = spec.get("cards") or ([spec["card"]] if spec.get("card") else [])
    if spec.get("fake_text"):                                     # a non-card with print on it
        items = []
        page = Image.new("RGB", (480, 620), (236, 232, 222))
        d = ImageDraw.Draw(page)
        for i, line in enumerate(spec["fake_text"].split("\n")):
            d.text((24, 30 + 34 * i), line, fill=(20, 20, 20), font_size=26)
        frame.paste(page.rotate(spec.get("rotate", 0), expand=True, fillcolor=(58, 44, 34)), (400, 50))
    if spec.get("layout"):                                        # an overhead board: cards placed by hand
        for it in spec["layout"]:
            c = card_image(it["card"], it.get("query"))
            w = int(488 * it.get("scale", 0.3))          # ≥ ~150 px per card: what an overhead 4K camera gives
            c = c.resize((w, int(w * c.height / c.width))).rotate(it.get("rotate", 0), expand=True, fillcolor=(58, 44, 34))
            frame.paste(c, (int(it["x"] * 1280 - c.width / 2), int(it["y"] * 720 - c.height / 2)))
        if spec.get("hand_over"):                                # a hand passing over part of the board
            x, y, r = spec["hand_over"]
            ImageDraw.Draw(frame).ellipse((x * 1280 - r, y * 720 - r * 0.7, x * 1280 + r, y * 720 + r * 0.7), fill=(196, 150, 120))
    for i, name in enumerate(items):
        c = card_image(name, spec.get("query"))
        w = int(488 * spec.get("scale", 0.9))
        if spec.get("foil"):                                    # rainbow foil sheen
            g = np.linspace(0, 1, c.width)[None, :, None] * np.ones((c.height, 1, 1))
            rainbow = np.concatenate([np.sin(6 * g + k) * 0.5 + 0.5 for k in (0, 2.1, 4.2)], axis=2) * 255
            c = Image.blend(c, Image.fromarray(rainbow.astype(np.uint8)), spec["foil"])
        c = c.resize((w, int(w * c.height / c.width)))
        if spec.get("blur"):
            c = c.filter(ImageFilter.GaussianBlur(spec["blur"]))
        if spec.get("glare"):                                   # sleeve glare: a bright ellipse
            g = Image.new("L", c.size, 0)
            ImageDraw.Draw(g).ellipse((c.width * 0.15, c.height * 0.1, c.width * 0.85, c.height * 0.45), fill=255)
            g = g.filter(ImageFilter.GaussianBlur(c.width / 10)).point(lambda v: int(v * spec["glare"]))
            c = Image.composite(Image.new("RGB", c.size, (255, 255, 255)), c, g)
        if spec.get("occlude"):                                 # a thumb over the lower part
            ImageDraw.Draw(c).rectangle((0, int(c.height * (1 - spec["occlude"])), c.width, c.height), fill=(196, 150, 120))
        if spec.get("phone"):                                   # the card on a phone screen: black bezel
            c = c.resize((int(c.width * 0.8), int(c.height * 0.8)))
            ph = Image.new("RGB", (c.width + 40, c.height + 110), (12, 12, 14))
            ph.paste(c, (20, 55))
            ImageDraw.Draw(ph).rounded_rectangle((0, 0, ph.width - 1, ph.height - 1), radius=40, outline=(60, 60, 64), width=6)
            c = ph
        c = c.rotate(spec.get("rotate", 0) + (i * 7 if len(items) > 1 else 0), expand=True, fillcolor=(58, 44, 34))
        x = (1280 - c.width) // 2 + (i - (len(items) - 1) / 2) * 360
        frame.paste(c, (int(x), max(0, (720 - c.height) // 2)))
    if spec.get("dim"):
        frame = Image.fromarray((np.asarray(frame, np.float32) * spec["dim"]).astype(np.uint8))
    buf = io.BytesIO()
    frame.save(buf, "JPEG", quality=spec.get("jpeg", 85))
    return buf.getvalue()


# ── outputs: what the table actually hears ──────────────────────────────────────────────────
_CATALOG = None


def catalog():
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = json.loads((TABLE / ".cache" / "card-names.json").read_text())["data"]
    return _CATALOG


def _norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", t.lower().replace("-", " "))


def _sound(t: str) -> str:
    """A crude sound key: spelling differences that sound the same ("Kor"/"core", "Clawd"/"Claude",
    "Ristik"/"Rhystic") collapse; different sounds don't."""
    t = re.sub(r"[^a-z]", "", t.lower())
    t = re.sub(r"s$", "", t)                                # plural / possessive
    for a, b in (("ph", "f"), ("ck", "k"), ("qu", "kw"), ("wh", "w"), ("gh", "g"), ("kn", "n"), ("gn", "n"),
                 ("c", "k"), ("q", "k"), ("x", "ks"), ("z", "s")):
        t = t.replace(a, b)
    t = re.sub(r"(?<=[aeiouy])[wy]", "", t)                 # "aw", "ey": part of the vowel
    t = re.sub(r"([a-z])\1+", r"\1", t)                   # doubled letters
    t = re.sub(r"(?<!^)[aeiouy]+", "a", t)                  # vowels are the least reliable part
    return re.sub(r"(?<=.)a$", "", t)


def _words_sound_alike(want: list[str], heard: list[str]) -> bool:
    """Every word of the name matches, in order, one or two consecutive heard words by sound."""
    from difflib import SequenceMatcher
    for start in range(len(heard)):
        i, ok = start, True
        for w in want:
            best = 0
            for k in (1, 2):
                if i + k <= len(heard):
                    r = SequenceMatcher(None, _sound(w), _sound("".join(heard[i:i + k]))).ratio()
                    if r > (best and best[0] or 0):
                        best = (r, k)
            if not best or best[0] < 0.8:
                ok = False
                break
            i += best[1]
        if ok:
            return True
    return False


def audible(card: str, heard: str, cutoff: float = 0.78) -> bool:
    """Would a player at the table recognise this card name in what they heard? Exact, a close
    spelling ("planes" for Plains, "Sarah Angel" for Serra Angel), or the same sounds word by word
    ("Core Spirit Denser" for Kor Spiritdancer) — not "or annul" for Aura Gnarlid, "Lana or Elvis"
    for Llanowar Elves, or "Lightning Greaves" for Lightning Bolt."""
    from difflib import SequenceMatcher
    want, words = _norm(card.split(" // ")[0]).split(), _norm(heard).split()
    n, w_raw = len(want), "".join(want)
    for k in {max(1, n - 1), n, n + 1}:
        for i in range(0, max(1, len(words) - k + 1)):
            got = words[i:i + k]
            if SequenceMatcher(None, w_raw, "".join(got)).ratio() >= cutoff and got[:1] and \
                    SequenceMatcher(None, want[-1], got[-1]).ratio() >= 0.6:
                return True
    return _words_sound_alike(want, words)


def hear_back(text: str, voice_name: str) -> tuple[str, list[str], float]:
    """Render the AI's words in its own voice and transcribe them with no hint, like a player
    who has never seen the card. Returns (heard, card names recognised in it, seconds spoken)."""
    import voice
    from match import find_cards_in_text
    a = say_wav(text, voice_name)
    heard, _ = voice.transcribe(a, "")
    return heard, find_cards_in_text(heard, catalog()), round(len(a) / SR, 1)


# ── scenario execution ──────────────────────────────────────────────────────────────────────
class Run:
    def __init__(self, srv: Server, setup: dict, hear: bool):
        self.srv, self.setup, self.hear = srv, setup, hear
        self.token = srv.token_file.read_text().strip()
        self.ai_voice = {a.split("|")[0]: a.split("|")[2] for a in setup.get("ai", [])}
        self.last_spoken, self.last_spoken_at = None, 0.0
        self.filled: dict[str, str] = {}

    def get(self, path, brain=False):
        return http(self.srv.base, "GET", path, headers={"X-Brain-Token": self.token} if brain else None)[1]

    def post(self, path, body, ctype="application/json", headers=None):
        return http(self.srv.base, "POST", path, body, ctype, headers)

    def hand(self):
        return self.get("/api/ai/hand", brain=True).get("hand", [])

    def last_event(self):
        return self.get("/api/events?since=latest").get("last", 0)

    def events_since(self, n):
        return self.get(f"/api/events?since={n}").get("events", [])

    def fill(self, text: str) -> str:
        """{ai_creature}: a creature the AI controls right now; {ai_card}: any nonland permanent."""
        if "{" not in text:
            return text
        from match import find_cards_in_text
        bf = self.get("/api/ai/state").get("battlefield", [])
        names = [find_cards_in_text(x, catalog())[:1] for x in bf]
        creatures = [n[0] for n, x in zip(names, bf) if n and re.search(r"\d+/\d+", x)]
        anything = [n[0] for n in names if n]
        vals = {"ai_creature": creatures[0] if creatures else None, "ai_card": anything[0] if anything else None}
        if "{hand_card}" in text:
            h = [x for x in self.get("/api/ai/hand", brain=True).get("hand", [])
                 if (oracle_type(x) or "").find("Land") < 0]
            vals["hand_card"] = h[0] if h else None
        for k, v in vals.items():
            if "{" + k + "}" in text:
                if v is None:
                    raise Skip(f"needs {{{k}}} but the AI has none on the battlefield")
                text = text.replace("{" + k + "}", v)
                self.filled[k] = v
        return text


class Skip(Exception):
    pass


def oracle_type(name: str) -> str | None:
    import oracle
    return (oracle.card(name) or {}).get("type")


def _finish(run: Run, step: dict, out: dict, mark: int) -> dict:
    """Collect what the AI said through the event stream during the step (external brain, rules
    answers, fillers), wait `settle` seconds for late ones, and merge it into what it said."""
    if step.get("settle"):
        time.sleep(step["settle"])
    evs = run.events_since(mark)
    out["events"] = evs
    said = [e for e in evs if e["type"] == "say"]
    parts = [out.get("ai_text") or ""] + [e["text"] for e in said]
    out["ai_text"] = " ".join(p for p in parts if p).strip() or None
    sp = [out.get("ai_speech") or ""] + [e.get("speech") or e["text"] for e in said]
    out["ai_speech"] = " ".join(p for p in sp if p).strip() or None
    if said and not out.get("speaker"):
        out["speaker"] = said[0]["speaker"]
    out["after"] = run.get("/api/ai/state")
    return out


def utter_step(run: Run, step: dict, rng) -> dict:
    s = dict(step["say"])
    if s.get("wav"):                              # a real recording (16 kHz mono WAV) instead of `say`
        with wave.open(str(REPO / s["wav"])) as w:
            base = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        s.setdefault("text", Path(s["wav"]).stem)
    else:
        s["text"] = run.fill(s["text"])
        base = say_wav(s["text"], s.get("voice", "Samantha"), s.get("rate"))
    a = mix(base, s, rng)
    before, mark = run.get("/api/ai/state"), run.last_event()
    t0 = time.time()
    code, r = run.post("/api/utterance", to_wav(a), "audio/wav")
    ms = round((time.time() - t0) * 1000)
    speech = " ".join((r.get("turn") or {}).get("speech") or []) or r.get("speech")
    if r.get("reply"):
        run.last_spoken, run.last_spoken_at = (r.get("reply"), r.get("speaker")), time.time()
    return _finish(run, step, {"code": code, "resp": r, "ms": ms, "before": before,
                               "ai_text": r.get("reply"), "ai_speech": speech, "speaker": r.get("speaker")}, mark)


def echo_step(run: Run, step: dict, rng) -> dict:
    """The laptop hears itself: the AI's last line, in its own voice, back into the mic."""
    e = step["echo"] if isinstance(step["echo"], dict) else {}
    text, who = run.last_spoken or (e.get("text"), next(iter(run.ai_voice), None))
    if not (e.get("text") or text):
        raise Skip("the AI hasn't said anything to echo yet")
    line = e.get("text") or text
    a = mix(say_wav(line, run.ai_voice.get(who, "Moira")), {"rt60": e.get("rt60", 0.35), "snr_db": e.get("snr_db", 20)}, rng)
    # the mic only finishes hearing it once the speaker has finished saying it
    if run.last_spoken_at:
        time.sleep(max(0.0, run.last_spoken_at + len(a) / SR + 0.6 - time.time()))
    before, mark = run.get("/api/ai/state"), run.last_event()
    t0 = time.time()
    code, r = run.post("/api/utterance", to_wav(a), "audio/wav")
    return _finish(run, step, {"code": code, "resp": r, "ms": round((time.time() - t0) * 1000), "before": before,
                               "ai_text": r.get("reply"), "ai_speech": r.get("speech"), "speaker": r.get("speaker")}, mark)


def garbage_step(run: Run, step: dict, rng) -> dict:
    """Things a real mic and camera produce that aren't speech or cards."""
    kind = step["garbage"]
    t0, mark = time.time(), run.last_event()
    if kind == "random-bytes":
        code, r = run.post("/api/utterance", rng.integers(0, 255, 4000, dtype=np.uint8).tobytes(), "audio/wav")
    elif kind == "empty-wav":
        code, r = run.post("/api/utterance", to_wav(np.zeros(0, np.float32)), "audio/wav")
    elif kind == "silence":
        code, r = run.post("/api/utterance", to_wav(np.zeros(SR * 3, np.float32)), "audio/wav")
    elif kind == "noise":
        code, r = run.post("/api/utterance", to_wav(np.clip(rng.normal(0, 0.1, SR * 2), -1, 1).astype(np.float32)), "audio/wav")
    elif kind == "wrong-rate":
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(2), w.setsampwidth(2), w.setframerate(44100)
            w.writeframes(np.zeros(44100 * 2, np.int16).tobytes())
        code, r = run.post("/api/utterance", buf.getvalue(), "audio/wav")
    elif kind == "huge-audio":
        code, r = run.post("/api/utterance", b"\0" * 3_000_000, "audio/wav")
    elif kind == "image-bytes":
        code, r = run.post("/api/show", rng.integers(0, 255, 50_000, dtype=np.uint8).tobytes(), "image/jpeg")
    elif kind == "black-frame":
        buf = io.BytesIO()
        Image.new("RGB", (1280, 720)).save(buf, "JPEG")
        code, r = run.post("/api/show", buf.getvalue(), "image/jpeg")
    elif kind == "bad-json":
        code, r = run.post("/api/life", b"{not json", "application/json")
    else:
        raise ValueError(f"unknown garbage {kind}")
    return _finish(run, step, {"code": code, "resp": r, "ms": round((time.time() - t0) * 1000),
                               "ai_text": r.get("reply"), "speaker": r.get("speaker"), "garbage": True}, mark)


def concurrent_step(run: Run, step: dict, rng) -> dict:
    """Several people talking in quick succession: utterances arrive while others are processing."""
    import threading
    texts = step["concurrent"]
    wavs = [to_wav(mix(say_wav(t["text"], t.get("voice", "Samantha")), t, rng)) for t in texts]
    res = [None] * len(wavs)
    mark, t0 = run.last_event(), time.time()

    def go(i):
        res[i] = run.post("/api/utterance", wavs[i], "audio/wav")
    th = [threading.Thread(target=go, args=(i,)) for i in range(len(wavs))]
    for t in th:
        t.start()
        time.sleep(step.get("gap_s", 0.15))
    for t in th:
        t.join()
    codes = [c for c, _ in res]
    cards = sorted({c for _, r in res for c in (r.get("cards") or [])})
    out = {"code": max(codes), "n_replies": sum(1 for _, r in res if r.get("reply")),
           "resp": {"cards": cards, "codes": codes, "heard": " | ".join(str(r.get("heard")) for _, r in res)}, "ms": round((time.time() - t0) * 1000),
           "ai_text": " ".join(r.get("reply") or "" for _, r in res).strip() or None, "speaker": None}
    return _finish(run, step, out, mark)


def board_step(run: Run, step: dict, rng) -> dict:
    """An overhead frame of the whole table to /api/board."""
    b = step["board"]
    t0, mark = time.time(), run.last_event()
    code, r = run.post("/api/board", scene(b, rng), "image/jpeg")
    return _finish(run, step, {"code": code, "resp": r, "ms": round((time.time() - t0) * 1000),
                               "ai_text": None, "speaker": None}, mark)


def brain_step(run: Run, step: dict, rng) -> dict:
    """What the external brain (Claude via tablectl) would do, sent straight to the brain API."""
    b = dict(step["brain"])
    action = b.pop("action")
    for k, v in list(b.items()):
        if isinstance(v, str):
            b[k] = run.fill(v)
    if b.pop("castable", None):                              # cast whatever is castable now
        st = run.get("/api/brain/state", brain=True)
        opts = [h for h in st.get("hand", []) if h.get("castable_now") and not h.get("land")
                and (h.get("aura_targets") is None or (h["aura_targets"] and h["aura_targets"][0].startswith("#")))]
        if not opts:
            raise Skip("nothing castable")
        b["name"] = opts[0]["name"]
        if opts[0].get("aura_targets"):
            b["on"] = opts[0]["aura_targets"][0].split(" ")[0]
    if b.pop("any_land", None):
        st = run.get("/api/brain/state", brain=True)
        land = next((h["name"] for h in st.get("hand", []) if h.get("land")), None)
        if not land:
            raise Skip("no land in hand")
        b["name"] = land
    before, mark, t0 = run.get("/api/ai/state"), run.last_event(), time.time()
    code, r = http(run.srv.base, "POST", f"/api/brain/{action}", b, headers={"X-Brain-Token": run.token})
    return _finish(run, step, {"code": code, "resp": r, "ms": round((time.time() - t0) * 1000), "before": before,
                               "ai_text": None, "speaker": None, "brain": True}, mark)


def show_step(run: Run, step: dict, rng) -> dict:
    s = step["show"]
    # a real camera streams: the empty table is in view before the card comes up, which is what
    # re-arms the reader after the previous card (the server wants ~1 s of nothing in view)
    blank = scene({}, rng)
    for _ in range(2):
        run.post("/api/show", blank, "image/jpeg")
        time.sleep(0.6)
    seq = s.get("sequence")                        # per-frame overrides: a hand passing in front, etc.
    frames, final, t0 = len(seq) if seq else s.get("frames", 4), None, time.time()
    for fi in range(frames):
        fs = {**s, **seq[fi]} if seq else s
        code, r = run.post("/api/show", scene(fs, rng), "image/jpeg",
                           {"X-Show-To": s.get("to", ""), "X-Shown-By": s.get("by", "Michael")})
        if r.get("status") in ("seen", "unreadable"):
            final = r
            break
        time.sleep(0.05)
    blank = scene({}, rng)                                  # card leaves the camera: re-arm
    for _ in range(3):
        run.post("/api/show", blank, "image/jpeg")
        time.sleep(0.4)
    final = final or r
    if final.get("reply"):
        run.last_spoken, run.last_spoken_at = (final["reply"], final.get("speaker")), time.time()
    return {"code": code, "resp": final, "ms": round((time.time() - t0) * 1000),
            "ai_text": final.get("reply"), "ai_speech": final.get("speech"), "speaker": final.get("speaker")}


def check_step(run: Run, step: dict, out: dict, hidden: list[str]) -> list[tuple[bool, str]]:
    e, r, res = step.get("expect", {}), out["resp"], []
    add = lambda ok, what: res.append((bool(ok), what))
    ai_text = out.get("ai_text") or ""
    want_code = e.get("http", 400 if out.get("garbage") else 200)
    if out.get("garbage"):
        add(out["code"] != 500 and (out["code"] < 400 or out["code"] == want_code or out["code"] in (400, 413)),
            f"handled without a server error (HTTP {out['code']}: {str(r)[:80]})")
    else:
        add(out["code"] == want_code, f"HTTP {out['code']} (want {want_code})" + (f" {r.get('error')}" if r.get("error") else ""))
    if "ignored" in e:
        add(r.get("ignored") == e["ignored"], f"ignored as {e['ignored']} (got {r.get('ignored')!r}, heard {r.get('heard')!r})")
    if "not_ignored" in e:
        add(not r.get("ignored"), f"treated as a real line (ignored={r.get('ignored')!r}, heard {r.get('heard')!r})")
    evs = out.get("events") or []
    if "attention" in e:
        got = [x for x in evs if x["type"] == "attention"]
        add(any(x.get("kind") == e["attention"] for x in got), f"attention for the brain: {e['attention']} "
                                                               f"(got {[x.get('kind') for x in got]})")
    if e.get("no_attention"):
        got = [x.get("kind") for x in evs if x["type"] == "attention"]
        add(not got, f"no attention event (got {got})")
    if "attention_fields" in e:
        a_ = next((x for x in evs if x["type"] == "attention"), {})
        add(all(a_.get(k) == v for k, v in e["attention_fields"].items()), f"attention carries {e['attention_fields']} (got "
            f"{ {k: a_.get(k) for k in e['attention_fields']} })")
    if "event_say" in e:
        said = " / ".join(x["text"] for x in evs if x["type"] == "say")
        add(re.search(e["event_say"], said, re.I), f"the table hears /{e['event_say']}/ from the event stream: {said[:100]!r}")
    if "life_in" in e:
        lt = run.get("/api/life")
        add(all(lt.get(k) in v for k, v in e["life_in"].items()), f"life within {e['life_in']} (table: {lt})")
    if e.get("alive"):
        add(run.get("/api/ai/state")["life"] > 0 or not [b for b in out["before"]["battlefield"] if re.search(r"\d+/\d+", b) and "[tapped]" not in b],
            f"never dies with an untapped blocker available (life {run.get('/api/ai/state')['life']})")
    if "ai_creatures_max" in e and "after" in out:
        n = len([b for b in out["after"]["battlefield"] if re.search(r"\d+/\d+", b)])
        add(n <= e["ai_creatures_max"], f"≤{e['ai_creatures_max']} creatures left (has {n})")
    if e.get("ai_board_same") and "after" in out:
        add(sorted(out["before"]["battlefield"]) == sorted(out["after"]["battlefield"]),
            f"its board is unchanged ({len(out['before']['battlefield'])} → {len(out['after']['battlefield'])})")
    if "gone" in e and "after" in out:                     # gone: ai_creature — the card that line named
        name = run.filled.get(e["gone"])
        add(name and not any(x.startswith(name) for x in out["after"]["battlefield"]), f"{name} has left its board")
    if "kept" in e and "after" in out:
        name = run.filled.get(e["kept"])
        add(name and any(x.startswith(name) for x in out["after"]["battlefield"]), f"{name} is still on its board")
    if "resp" in e:
        add(all(r.get(k) == v for k, v in e["resp"].items()), f"response has {e['resp']} (got "
            f"{ {k: r.get(k) for k in e['resp']} })")
    if "event_type" in e:
        add(any(x["type"] == e["event_type"] for x in evs), f"a '{e['event_type']}' event (got {[x['type'] for x in evs]})")
    if "replies_min" in e:
        add(out.get("n_replies", 0) >= e["replies_min"], f"≥{e['replies_min']} answered (got {out.get('n_replies')})")
    said_lines = (r.get("turn") or {}).get("said") or []
    if "max_words_per_line" in e and said_lines:
        sentences = [x for l in said_lines for x in re.split(r"(?<=[.!?])\s+", l) if x]
        long = [l for l in sentences if len(l.split()) > e["max_words_per_line"]]
        add(not long, f"every turn line ≤ {e['max_words_per_line']} words" + (f": {long[:2]}" if long else ""))
    if e.get("names_targets") and said_lines:
        players = list(run.get("/api/life"))
        bad = [l for l in said_lines if l.startswith("I attack") and not any(p in l for p in players)]
        bad += [l for l in said_lines if re.match(r"I cast .*", l) and "Aura" in (oracle_type(re.match(r"I cast ([^.]+?)(?: on | from |\.)", l).group(1)) or "")
                and " on " not in l]
        add(not bad, "every attack names its player and every Aura names what it enchants" + (f": {bad[:2]}" if bad else ""))
    if "board_reads" in e:
        got = sorted(c["name"] for c in r.get("cards", []))
        want = sorted(e["board_reads"])
        missing = [w for w in want if got.count(w) < want.count(w)]
        extra = [g for g in got if g not in want]
        add(not missing and not extra, f"board reads {want}" + (f" — MISSED {missing}" if missing else "")
            + (f" — WRONG {extra}" if extra else ""))
    if "board_tapped" in e:
        tapped = sorted(c["name"] for c in r.get("cards", []) if c.get("tapped"))
        add(tapped == sorted(e["board_tapped"]), f"tapped: {sorted(e['board_tapped'])} (got {tapped})")
    if "board_left" in e:
        add(sorted(r.get("left", [])) == sorted(e["board_left"]), f"left the board: {e['board_left']} (got {r.get('left')})")
    if "board_entered" in e:
        add(sorted(r.get("entered", [])) == sorted(e["board_entered"]), f"entered: {e['board_entered']} (got {r.get('entered')})")
    if "error" in e:
        add(re.search(e["error"], str(r.get("error", "")), re.I), f"refused with /{e['error']}/ (got {r.get('error')!r})")
    if "heard" in e:
        add(re.search(e["heard"], r.get("heard", ""), re.I), f"STT heard /{e['heard']}/ in {r.get('heard')!r}")
    if "kind" in e:
        kind = (r.get("route") or {}).get("kind")
        add(kind in (e["kind"] if isinstance(e["kind"], list) else [e["kind"]]), f"routed {e['kind']} (got {kind})")
    if "cards" in e:
        got = r.get("cards", [])
        add(all(c in got for c in e["cards"]), f"board gets {e['cards']} (got {got})")
    if "no_cards" in e:
        add(not r.get("cards"), f"nothing added to the board (got {r.get('cards')})")
    if "silent" in e:
        add((not ai_text) == e["silent"], ("AI stays silent" if e["silent"] else "AI answers") + f" (said {ai_text!r})")
    if "speaker" in e:
        add(out.get("speaker") == e["speaker"], f"{e['speaker']} answers (got {out.get('speaker')})")
    if "seen" in e:
        got = r.get("card") if r.get("status") == "seen" else None
        add(got == e["seen"], f"camera reads {e['seen']} (got {got}, status {r.get('status')}, via {r.get('via')})")
    if "seen_in" in e:
        got = r.get("card") if r.get("status") == "seen" else None
        add(got in e["seen_in"], f"camera reads one of {e['seen_in']} — never a wrong card (got {got})")
    if "board_has" in e:
        got = r.get("board", []) + r.get("announced", [])
        add(all(c in got for c in e["board_has"]), f"table board has {e['board_has']} (got {got[-6:]})")
    if "via" in e:
        add(r.get("via") == e["via"], f"read via {e['via']} (got {r.get('via')})")
    if "turn_advanced" in e:
        adv = out["after"]["turn"] > out["before"]["turn"]
        add(adv == e["turn_advanced"], f"AI turn {'advanced' if e['turn_advanced'] else 'unchanged'} "
                                       f"({out['before']['turn']} → {out['after']['turn']})")
    if "max_lands_delta" in e and "after" in out:
        d = out["after"]["lands"] - out["before"]["lands"]
        add(d <= e["max_lands_delta"], f"≤{e['max_lands_delta']} land this turn (played {d})")
    if "names_own_board" in e and "after" in out:
        from match import find_cards_in_text
        own = find_cards_in_text(" ; ".join(out["after"]["battlefield"]), catalog())
        add(any(c.lower() in ai_text.lower() for c in own) if own else re.search(r"no creatures|nothing", ai_text, re.I),
            f"answer names something it controls {own[:4]}" if own else "answer says it has no creatures yet")
    if "ai_board_shrinks" in e and "after" in out:
        b, a_ = len(out["before"]["battlefield"]), len(out["after"]["battlefield"])
        add(a_ < b, f"its board reflects the removal ({b} → {a_} permanents)")
    if "max_ms" in e:
        add(out["ms"] <= e["max_ms"], f"answered within {e['max_ms']} ms (took {out['ms']})")
    if "ai_text" in e:
        add(re.search(e["ai_text"], ai_text, re.I), f"AI's words match /{e['ai_text']}/: {ai_text[:90]!r}")
    if "ai_text_not" in e:
        add(not re.search(e["ai_text_not"], ai_text, re.I), f"AI's words avoid /{e['ai_text_not']}/: {ai_text[:90]!r}")
    if "life" in e:
        lt = run.get("/api/life")
        add(all(lt.get(k) == v for k, v in e["life"].items()), f"life {e['life']} (table: {lt})")
    # always: the hand never reaches the table, in text or in sound. A name it just announced
    # playing is public (a second Plains still in hand is not a leak when it says "I play Plains").
    played = {m.strip().lower() for m in re.findall(r"I (?:cast|play) ([^.,;!]+)", ai_text)}
    # a name that is ALSO on its battlefield is public ("…Fertile Ground on Forest" with a Forest in hand)
    board = {p["name"].lower() for p in run.get("/api/brain/state", brain=True).get("permanents", [])}
    hidden = [c for c in hidden if c.lower() not in played and c.lower() not in board]
    leaked = [c for c in hidden if re.search(r"\b" + re.escape(c.lower()) + r"\b", ai_text.lower())]
    add(not leaked, "no hidden card named" + (f": NAMED {leaked}" if leaked else ""))
    if ai_text and run.hear:
        heard, cards, secs = hear_back(out.get("ai_speech") or ai_text, run.ai_voice.get(out.get("speaker") or "", "Moira"))
        out["heard_back"], out["heard_cards"], out["spoken_s"] = heard, cards, secs
        leaked = [c for c in hidden if c in cards]
        add(not leaked, "no hidden card audible after TTS→STT" + (f": HEARD {leaked}" if leaked else ""))
        if "hear_back" in e:
            add(re.search(e["hear_back"], heard, re.I), f"the table hears /{e['hear_back']}/ in {heard[:90]!r}")
        if "hear_cards" in e:
            want = e["hear_cards"] if isinstance(e["hear_cards"], list) else [c for c in re.findall(
                r"I (?:cast|play) ([^.,;!]+)", ai_text) if c.strip() in catalog()]
            import pronounce                       # the lexicon's respelling IS the intended sound
            missed = [c for c in want if c not in cards and not audible(c, heard)
                      and not audible(pronounce.for_speech(c), heard)]
            add(not missed, f"card names survive the speaker: {want}" + (f" — LOST {missed}" if missed else ""))
        if "max_spoken_s" in e:
            add(secs <= e["max_spoken_s"], f"spoken in ≤{e['max_spoken_s']} s (took {secs})")
    return res


def run_scenario(run: Run, sc: dict, rng) -> dict:
    """A scenario with `repeat: N, min_pass: K` is a RATE: N fresh draws (noise, shuffle), and it
    passes when at least K of them do — the honest measure for anything probabilistic."""
    if sc.get("repeat"):
        tries = [_run_once(run, sc, rng) for _ in range(sc["repeat"])]
        passed = sum(1 for t in tries if t["ok"])
        steps = [st for t in tries for st in t["steps"]]
        res = {**tries[-1], "steps": steps, "ok": passed >= sc.get("min_pass", sc["repeat"]),
               "rate": f"{passed}/{sc['repeat']}"}
        res["steps"].append({"step": {"rate": True}, "checks": [(res["ok"], f"passed {passed}/{sc['repeat']} "
                             f"(needs {sc.get('min_pass', sc['repeat'])})")], "ms": 0, "heard": None})
        return res
    return _run_once(run, sc, rng)


def _run_once(run: Run, sc: dict, rng) -> dict:
    run.post("/api/reset", {})
    run.last_spoken, run.last_spoken_at = None, 0.0
    time.sleep(0.2)
    for _ in range(sc.get("start", {}).get("ai_turns", 0)):
        run.post("/api/ai/turn", {})                          # setup only: get a board on the table
    for card in sc.get("start", {}).get("announced", []):     # setup: cards already on the table
        run.post("/api/utterance", to_wav(say_wav(f"I cast {card}.", "Daniel")), "audio/wav")
    steps, all_ok = [], True
    for step in sc["steps"]:
        try:
            if "say" in step:
                out = utter_step(run, step, rng)
            elif "show" in step:
                out = show_step(run, step, rng)
            elif "echo" in step:
                out = echo_step(run, step, rng)
            elif "garbage" in step:
                out = garbage_step(run, step, rng)
            elif "concurrent" in step:
                out = concurrent_step(run, step, rng)
            elif "board" in step:
                out = board_step(run, step, rng)
            elif "brain" in step:
                out = brain_step(run, step, rng)
            elif "wait" in step:
                time.sleep(step["wait"])
                continue
            else:
                raise ValueError(f"{sc['id']}: unknown step {list(step)}")
        except Skip as why:
            if step.get("optional"):                         # e.g. "cast whatever is castable" on a turn with nothing
                steps.append({"step": step, "checks": [], "ms": 0, "skipped": str(why)})
                continue
            return {"id": sc["id"], "tier": sc.get("tier", "regression"), "about": sc.get("about", ""),
                    "ok": None, "skipped": str(why), "steps": steps}
        # what is STILL hidden after the step: a card it cast this turn is public now, one it drew is not
        hidden = [h if isinstance(h, str) else h.get("card", h.get("name", "")) for h in run.hand()]
        checks = check_step(run, step, out, hidden)
        all_ok &= all(ok for ok, _ in checks)
        steps.append({"step": step, "checks": checks, "ms": out["ms"],
                      "heard": out["resp"].get("heard"), "route": out["resp"].get("route"), "ai_text": out.get("ai_text"),
                      "heard_back": out.get("heard_back"), "heard_cards": out.get("heard_cards")})
    return {"id": sc["id"], "tier": sc.get("tier", "regression"), "about": sc.get("about", ""), "ok": all_ok, "steps": steps}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="run scenarios whose id starts with this (comma-separated)")
    ap.add_argument("--tier", choices=["regression", "goal"])
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--no-hear", action="store_true", help="skip the TTS→STT round trip (faster)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    suites = [yaml.safe_load(p.read_text()) | {"_file": p.name} for p in sorted(SCEN.glob("*.yaml"))]
    picked = []
    for s in suites:
        for sc in s["scenarios"]:
            if a.only and not any(sc["id"].startswith(o) for o in a.only.split(",")):
                continue
            if a.tier and sc.get("tier", "regression") != a.tier:
                continue
            picked.append((s, sc))
    if a.list:
        for s, sc in picked:
            print(f"{sc.get('tier', 'regression'):10} {sc['id']:34} {sc.get('about', '')}")
        print(f"\n{len(picked)} scenarios")
        return
    if not picked:
        sys.exit("no scenarios matched")

    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(a.seed)
    # Build every fixture BEFORE the egress watch: only this touches the network (Scryfall images).
    for _, sc in picked:
        for st in sc["steps"]:
            sh = st.get("show") or st.get("board") or {}
            for c in (sh.get("cards") or [sh.get("card")]):
                if c:
                    card_image(c, sh.get("query"))
            for it in sh.get("layout") or []:
                card_image(it["card"], it.get("query"))
            for f in sh.get("sequence") or []:
                if f.get("card"):
                    card_image(f["card"], f.get("query"))
    from e2e_offline import EgressWatch

    report, t0 = [], time.time()
    by_setup: dict[str, list] = {}
    for s, sc in picked:
        by_setup.setdefault(json.dumps(s["setup"], sort_keys=True), []).append((s, sc))
    remote = set()
    for key, group in by_setup.items():
        setup = json.loads(key)
        srv = Server(setup, a.port)
        try:
            srv.wait_ready()
            watch = EgressWatch([a.port, 11434])
            watch.start()
            run = Run(srv, setup, hear=not a.no_hear)
            for s, sc in group:
                print(f"\n▶ {sc['id']} [{sc.get('tier', 'regression')}] — {sc.get('about', '')}", flush=True)
                try:
                    res = run_scenario(run, sc, rng)
                except Exception as e:                        # one broken scenario must not end the run
                    res = {"id": sc["id"], "tier": sc.get("tier", "regression"), "ok": False,
                           "steps": [], "error": f"{type(e).__name__}: {e}"}
                    print(f"  ❌ crashed: {res['error']}")
                for st in res["steps"]:
                    for ok, what in st["checks"]:
                        if not ok or os.environ.get("SENSE_VERBOSE"):
                            print(("  ✅ " if ok else "  ❌ ") + what, flush=True)
                    if st.get("heard_back"):
                        print(f"     🔊 heard back: {st['heard_back'][:110]!r}", flush=True)
                print(f"  {'SKIP — ' + res['skipped'] if res.get('skipped') else 'PASS' if res['ok'] else 'FAIL'}", flush=True)
                report.append(res)
            watch.stop_flag = True
            watch.join()
            remote |= set(watch.remote)
        finally:
            srv.stop()

    skipped = [r for r in report if r.get("skipped")]
    reg = [r for r in report if r["tier"] == "regression" and not r.get("skipped")]
    goal = [r for r in report if r["tier"] == "goal" and not r.get("skipped")]
    print("\n" + "═" * 70)
    print(f"regressions: {sum(bool(r['ok']) for r in reg)}/{len(reg)} pass")
    for r in reg:
        if not r["ok"]:
            print(f"  ❌ {r['id']}")
    if skipped:
        print(f"skipped (the game state didn't allow it — rerun): {', '.join(r['id'] for r in skipped)}")
    print(f"goals met:   {sum(r['ok'] for r in goal)}/{len(goal)}")
    for r in goal:
        print(f"  {'🎯 MET — promote to regression' if r['ok'] else '·  not yet'}  {r['id']}")
    print(f"offline: {'no non-loopback connection' if not remote else 'REMOTE: ' + str(sorted(remote))}")
    print(f"session {time.time() - t0:.0f} s")
    rp = OUT / f"report-{time.strftime('%Y%m%d-%H%M%S')}.json"
    rp.write_text(json.dumps({"results": report, "remote": sorted(remote)}, indent=1, default=str))
    print(f"report: {rp.relative_to(REPO)}")
    sys.exit(1 if remote or not all(r["ok"] for r in reg) else 0)


if __name__ == "__main__":
    main()
