"""End-to-end test of the table server as a simulated game session, and proof it runs offline.

Needs a running table server (with decks/example.txt, AI players Talrand + Krenko, humans Sam +
Michael) and, for ROUTER=so1, the so1 server. Fixtures are built on first run and cached in
table/tests/fixtures/ (gitignored): card photos from Scryfall's public image API, and speech made
with macOS `say`. Building fixtures is the ONLY step that touches the network, and it runs before
the egress watch starts. The watch then asserts that the server processes open no non-loopback
connection for the whole session.

  ~/.venvs/table/bin/python table/tests/e2e_offline.py            # server on :8800, so1 on :8792
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8800"
HERE = Path(__file__).parent
FIX = HERE / "fixtures"
TOKEN_FILE = HERE.parent / ".brain-token"

CARDS = ["Island", "Sol Ring", "Mulldrifter", "Cyclonic Rift", "Man-o'-War", "Talrand, Sky Summoner",
         "Lightning Bolt"]                   # Lightning Bolt is NOT in the example deck: a negative case
SPEECH = {
    "sam_play": "I play a Swamp and cast Sheoldred, the Apocalypse.",
    "ask_talrand": "Talrand, who are you attacking this turn?",
    "deal_krenko": "Krenko, if you leave me alone this turn I won't attack your goblins.",
    "pizza": "Does anyone want pizza?",
    "michael_attack": "Ghalta attacks Sam for twelve with trample.",
    "rules": "Talrand, how much mana do you have open right now?",
}

results: list[tuple[bool, str]] = []


def check(ok: bool, what: str):
    results.append((bool(ok), what))
    print(("  ✅ " if ok else "  ❌ ") + what, flush=True)


# ── fixtures ────────────────────────────────────────────────────────────────────────────────
def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", name)


def build_fixtures():
    FIX.mkdir(exist_ok=True)
    for name in CARDS:
        p = FIX / f"{slug(name)}.jpg"
        if not p.exists():
            url = ("https://api.scryfall.com/cards/named?format=image&version=normal&exact="
                   + urllib.parse.quote(name))
            req = urllib.request.Request(url, headers={"User-Agent": "divinci-table-tests/0.1", "Accept": "*/*"})  # Scryfall 400s without Accept
            p.write_bytes(urllib.request.urlopen(req, timeout=30).read())
            time.sleep(0.15)                  # Scryfall asks for ≤10 requests/s
    blank = FIX / "blank.png"
    if not blank.exists():
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=0x202020:s=640x360", "-frames:v", "1", str(blank)], check=True)
    for key, text in SPEECH.items():
        w = FIX / f"{key}.wav"
        if not w.exists():
            aiff = FIX / f"{key}.aiff"
            subprocess.run(["say", "-o", str(aiff), text], check=True)
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ar", "16000",
                            "-ac", "1", "-sample_fmt", "s16", str(w)], check=True)
            aiff.unlink()
    silence = FIX / "silence.wav"
    if not silence.exists():
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "anullsrc=r=16000:cl=mono", "-t", "2", "-sample_fmt", "s16", str(silence)], check=True)


# ── HTTP ────────────────────────────────────────────────────────────────────────────────────
def call(method, path, body=None, ctype="application/json", headers=None):
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": ctype, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            code, raw = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read().decode()
    try:
        return code, json.loads(raw)
    except json.JSONDecodeError:
        raise AssertionError(f"{path} returned non-JSON (HTTP {code}): {raw[:200]}")


def scan_card(name: str, clear_secs=1.1):
    """Hold a card in view for 3 frames, then take it away (blank frames past clear_secs)."""
    img = (FIX / f"{slug(name)}.jpg").read_bytes()
    out, t0 = [], time.time()
    for _ in range(3):
        out.append(call("POST", "/api/scan", img, "image/jpeg")[1])
    ms = (time.time() - t0) * 1000 / 3
    blank = (FIX / "blank.png").read_bytes()
    call("POST", "/api/scan", blank, "image/png")
    time.sleep(clear_secs)
    call("POST", "/api/scan", blank, "image/png")
    added = [r for r in out if r.get("status") == "added"]
    return (added[0]["slot"] if added else None), len(added), ms


def say(key):
    return call("POST", "/api/utterance", (FIX / f"{key}.wav").read_bytes(), "audio/wav")[1]


# ── egress watch ────────────────────────────────────────────────────────────────────────────
class EgressWatch(threading.Thread):
    """Samples every open inet socket of the server processes; records any non-loopback peer."""

    def __init__(self, ports):
        super().__init__(daemon=True)
        self.pids = set()
        for p in ports:
            r = subprocess.run(["lsof", "-ti", f"tcp:{p}", "-sTCP:LISTEN"], capture_output=True, text=True)
            self.pids |= set(r.stdout.split())
        self.remote, self.samples, self.stop_flag = set(), 0, False

    def run(self):
        while not self.stop_flag:
            for pid in self.pids:
                r = subprocess.run(["lsof", "-nP", "-a", "-p", pid, "-i"], capture_output=True, text=True)
                for line in r.stdout.splitlines()[1:]:
                    m = re.search(r"->(\[?[0-9a-fA-F.:]+\]?):(\d+)", line)
                    if m and not re.match(r"^(127\.|\[?::1\]?$|localhost)", m.group(1)):
                        self.remote.add(f"pid {pid} -> {m.group(1)}:{m.group(2)}")
            self.samples += 1
            time.sleep(0.4)


# ── the session ─────────────────────────────────────────────────────────────────────────────
def main():
    print("building fixtures (network, before the egress watch)…", flush=True)
    build_fixtures()
    code, _ = call("GET", "/api/state")
    assert code == 200, "table server is not up on :8800"
    watch = EgressWatch([8800, 8792])
    check(len(watch.pids) >= 1, f"found server processes to watch: {sorted(watch.pids)}")
    watch.start()
    t_start = time.time()

    print("\n1. New game", flush=True)
    code, st = call("POST", "/api/reset", {})
    check(code == 200 and st["hand"] == 0 and st["library"] == 40, "reset: empty hand, 40-card library")

    print("\n2. The AI draws its opening hand on the scan pad", flush=True)
    opening = ["Island", "Island", "Sol Ring", "Mulldrifter", "Cyclonic Rift", "Man-o'-War", "Island"]
    scan_ms = []
    for i, name in enumerate(opening, 1):
        slot, n_added, ms = scan_card(name)
        scan_ms.append(ms)
        check(slot == i and n_added == 1, f"{name}: filled slot {slot} exactly once (want slot {i})")
    code, st = call("GET", "/api/state")
    check(st["hand"] == 7 and st["library"] == 33, f"hand 7, library 33 (got {st['hand']}, {st['library']})")
    public_text = json.dumps(st)
    leaked = [c for c in set(opening) if c in public_text]
    check(not leaked, f"public /api/state names no hidden card (found: {leaked or 'none'})")

    print("\n3. The brain can read the hand; nobody else can", flush=True)
    code, _ = call("GET", "/api/hand")
    check(code == 403, f"/api/hand without the token is refused (HTTP {code})")
    code, hand = call("GET", "/api/hand", headers={"X-Brain-Token": TOKEN_FILE.read_text().strip()})
    got = [h["card"] for h in hand["hand"]]
    check(code == 200 and got == opening, f"/api/hand with the token lists the hand in slot order")

    print("\n4. Cards that must NOT be scanned in", flush=True)
    slot, n, _ = scan_card("Lightning Bolt")
    check(slot is None, "Lightning Bolt (not in the deck) is rejected")
    slot, n, _ = scan_card("Talrand, Sky Summoner")
    check(slot == 8, f"Talrand (1 copy in the deck) scans once, into slot {slot}")
    slot, n, _ = scan_card("Talrand, Sky Summoner")
    check(slot is None, "Talrand a second time is rejected: its only copy is already in hand")

    print("\n5. Table talk", flush=True)
    talk = []

    r = say("sam_play")
    talk.append(r)
    check(r["route"]["kind"] == "play", f"'{r['heard']}' → play ({r['route']['kind']})")
    check("Sheoldred, the Apocalypse" in r["cards"], f"board update names Sheoldred ({r['cards']})")
    check(r["reply"] is None, "no AI answers a play announcement")

    r = say("ask_talrand")
    talk.append(r)
    check(r["route"]["kind"] == "question" and r["speaker"] == "Talrand",
          f"'{r['heard']}' → {r['route']['kind']}, Talrand answers ({r['speaker']}: {r['reply']})")

    r = say("deal_krenko")
    talk.append(r)
    check(r["route"]["kind"] == "deal" and r["speaker"] == "Krenko" and r["reply"] in ("Deal.", "No deal."),
          f"'{r['heard']}' → deal, Krenko answers '{r['reply']}' (accept p={r['route']['accept_deal']:.2f})")

    r = say("pizza")
    talk.append(r)
    check(r["route"]["kind"] == "chatter" and r["reply"] is None, f"'{r['heard']}' → chatter, silence")

    r = say("michael_attack")
    talk.append(r)
    check(r["route"]["kind"] == "play" and "Ghalta, Primal Hunger" in r["cards"],
          f"'{r['heard']}' → play, board: {r['cards']} (nickname 'Ghalta' resolved)")

    r = say("rules")
    talk.append(r)
    check(r["route"]["kind"] in ("rules", "question") and r["speaker"] == "Talrand",
          f"'{r['heard']}' → {r['route']['kind']}, Talrand answers")

    code, r = call("POST", "/api/utterance", (FIX / "silence.wav").read_bytes(), "audio/wav")
    check(code == 200 and r.get("ignored") == "no speech", f"2 s of silence is ignored ({r.get('ignored') or r.get('heard')!r})")

    print("\n6. The AI plays a card, a mis-scan is undone", flush=True)
    code, r = call("POST", "/api/play", {"slot": 3})
    check(code == 200 and r["revealed"] == "Sol Ring" and r["hand"] == 7, f"slot 3 revealed as {r.get('revealed')}, hand 7")
    slot, _, _ = scan_card("Island")
    check(slot == 3, f"next draw reuses the freed slot 3 (got {slot})")
    code, r = call("POST", "/api/undo", {})
    check(code == 200 and r["undone"] == 3 and r["library"] == 32 - 0,
          f"undo empties slot 3 and returns the card to the library (library {r.get('library')})")
    code, r = call("POST", "/api/play", {"slot": 3})
    check(code == 400, "playing an empty slot is refused")

    print("\n7. Hidden-hand guard (unit level: template replies never name a card)", flush=True)
    sys.path.insert(0, str(HERE.parent))
    import voice
    check(voice.leaks_hand("I'm holding Cyclonic Rift for you", ["Cyclonic Rift"]) == "Cyclonic Rift",
          "a reply naming a hand card is caught")
    check(voice.leaks_hand("No deal.", ["Cyclonic Rift", "Island"]) is None, "an ordinary reply passes")

    watch.stop_flag = True
    watch.join()
    print("\n8. Offline", flush=True)
    check(watch.samples > 10, f"egress watch sampled {watch.samples} times over {time.time() - t_start:.0f} s")
    check(not watch.remote, "server processes opened NO non-loopback connection"
          + ("" if not watch.remote else f": {sorted(watch.remote)}"))

    stt = [t["stt_ms"] for t in talk]
    route = [t["route"]["ms"] for t in talk]
    total = [t["total_ms"] for t in talk]
    print(f"\nlatency  scan/frame p50 {sorted(scan_ms)[len(scan_ms)//2]:.0f} ms | speech-to-text p50 "
          f"{sorted(stt)[len(stt)//2]} ms | router p50 {sorted(route)[len(route)//2]} ms "
          f"(max {max(route)}) | utterance total p50 {sorted(total)[len(total)//2]} ms (max {max(total)})")
    print(f"router: {talk[0]['route'].get('router')}")
    bad = [w for ok, w in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
