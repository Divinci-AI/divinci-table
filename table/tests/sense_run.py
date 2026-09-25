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
            if "Gemma loaded" in txt or "could not preload" in txt:
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
        subprocess.run(cmd, check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ar", str(SR), "-ac", "1",
                        "-sample_fmt", "s16", str(wav)], check=True)
        with wave.open(str(wav)) as w:
            a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    np.save(cache, a)
    return a


def mix(speech: np.ndarray, spec: dict, rng: np.random.Generator) -> np.ndarray:
    a = speech.copy()
    if spec.get("pad_s"):                                   # silence around the words, as a live mic hears
        pad = np.zeros(int(SR * spec["pad_s"]), np.float32)
        a = np.concatenate([pad, a, pad])
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
def card_image(name: str) -> Image.Image:
    p = FIX / (re.sub(r"[^A-Za-z0-9]+", "", name) + ".jpg")
    if not p.exists():
        url = "https://api.scryfall.com/cards/named?format=image&version=normal&exact=" + urllib.parse.quote(name)
        req = urllib.request.Request(url, headers={"User-Agent": "divinci-table-tests/0.1", "Accept": "*/*"})
        p.write_bytes(urllib.request.urlopen(req, timeout=30).read())
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
    for i, name in enumerate(items):
        c = card_image(name)
        w = int(488 * spec.get("scale", 0.9))
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


def audible(card: str, heard: str, cutoff: float = 0.78) -> bool:
    """Would a player at the table recognise this card name in what they heard? Exact, or a close
    sound-alike ("planes" for Plains, "Sarah Angel" for Serra Angel) — not "or annul" for Aura Gnarlid."""
    from difflib import SequenceMatcher
    want, words = _norm(card.split(" // ")[0]), _norm(heard).split()
    n = len(want.split())
    for k in {max(1, n - 1), n, n + 1}:
        for i in range(0, max(1, len(words) - k + 1)):
            if SequenceMatcher(None, want.replace(" ", ""), "".join(words[i:i + k])).ratio() >= cutoff:
                return True
    return False


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

    def get(self, path, brain=False):
        return http(self.srv.base, "GET", path, headers={"X-Brain-Token": self.token} if brain else None)[1]

    def post(self, path, body, ctype="application/json", headers=None):
        return http(self.srv.base, "POST", path, body, ctype, headers)

    def hand(self):
        return self.get("/api/ai/hand", brain=True).get("hand", [])


def utter_step(run: Run, step: dict, rng) -> dict:
    s = step["say"]
    a = mix(say_wav(s["text"], s.get("voice", "Samantha"), s.get("rate")), s, rng)
    before = run.get("/api/ai/state")
    t0 = time.time()
    code, r = run.post("/api/utterance", to_wav(a), "audio/wav")
    ms = round((time.time() - t0) * 1000)
    after = run.get("/api/ai/state")
    return {"code": code, "resp": r, "ms": ms, "before": before, "after": after,
            "ai_text": r.get("reply"), "speaker": r.get("speaker")}


def show_step(run: Run, step: dict, rng) -> dict:
    s = step["show"]
    frames, final, t0 = s.get("frames", 4), None, time.time()
    for _ in range(frames):
        code, r = run.post("/api/show", scene(s, rng), "image/jpeg",
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
    return {"code": code, "resp": final, "ms": round((time.time() - t0) * 1000),
            "ai_text": final.get("reply"), "speaker": final.get("speaker")}


def check_step(run: Run, step: dict, out: dict, hidden: list[str]) -> list[tuple[bool, str]]:
    e, r, res = step.get("expect", {}), out["resp"], []
    add = lambda ok, what: res.append((bool(ok), what))
    ai_text = out.get("ai_text") or ""
    add(out["code"] == 200, f"HTTP {out['code']}" + (f" {r.get('error')}" if r.get("error") else ""))
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
        add(any(c.lower() in ai_text.lower() for c in own), f"answer names something it controls {own[:4]}")
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
    hidden = [c for c in hidden if c.lower() not in played]
    leaked = [c for c in hidden if re.search(r"\b" + re.escape(c.lower()) + r"\b", ai_text.lower())]
    add(not leaked, "no hidden card named" + (f": NAMED {leaked}" if leaked else ""))
    if ai_text and run.hear:
        heard, cards, secs = hear_back(ai_text, run.ai_voice.get(out.get("speaker") or "", "Samantha"))
        out["heard_back"], out["heard_cards"], out["spoken_s"] = heard, cards, secs
        leaked = [c for c in hidden if c in cards]
        add(not leaked, "no hidden card audible after TTS→STT" + (f": HEARD {leaked}" if leaked else ""))
        if "hear_back" in e:
            add(re.search(e["hear_back"], heard, re.I), f"the table hears /{e['hear_back']}/ in {heard[:90]!r}")
        if "hear_cards" in e:
            want = e["hear_cards"] if isinstance(e["hear_cards"], list) else [c for c in re.findall(
                r"I (?:cast|play) ([^.,;!]+)", ai_text) if c.strip() in catalog()]
            missed = [c for c in want if c not in cards and not audible(c, heard)]
            add(not missed, f"card names survive the speaker: {want}" + (f" — LOST {missed}" if missed else ""))
        if "max_spoken_s" in e:
            add(secs <= e["max_spoken_s"], f"spoken in ≤{e['max_spoken_s']} s (took {secs})")
    return res


def run_scenario(run: Run, sc: dict, rng) -> dict:
    run.post("/api/reset", {})
    for _ in range(sc.get("start", {}).get("ai_turns", 0)):
        run.post("/api/ai/turn", {})                          # setup only: get a board on the table
    for card in sc.get("start", {}).get("announced", []):     # setup: cards already on the table
        run.post("/api/utterance", to_wav(say_wav(f"I cast {card}.", "Daniel")), "audio/wav")
    steps, all_ok = [], True
    for step in sc["steps"]:
        if "say" in step:
            out = utter_step(run, step, rng)
        elif "show" in step:
            out = show_step(run, step, rng)
        elif "wait" in step:
            time.sleep(step["wait"])
            continue
        else:
            raise ValueError(f"{sc['id']}: unknown step {list(step)}")
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
            for c in (st.get("show", {}).get("cards") or [st.get("show", {}).get("card")]):
                if c:
                    card_image(c)
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
                print(f"  {'PASS' if res['ok'] else 'FAIL'}", flush=True)
                report.append(res)
            watch.stop_flag = True
            watch.join()
            remote |= set(watch.remote)
        finally:
            srv.stop()

    reg = [r for r in report if r["tier"] == "regression"]
    goal = [r for r in report if r["tier"] == "goal"]
    print("\n" + "═" * 70)
    print(f"regressions: {sum(r['ok'] for r in reg)}/{len(reg)} pass")
    for r in reg:
        if not r["ok"]:
            print(f"  ❌ {r['id']}")
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
