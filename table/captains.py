#!/usr/bin/env python3
"""Captain voices: every commander at the table speaks a short in-character line now and then.

Each commander has its own Divinci release (a persona: the card, a character sketch in our own words,
how to talk) and its own Aura-2 voice. This watches the table's public event stream, and when something
happens to a seat (its turn starts, it casts or attacks, it loses or gains life) that seat's captain
may say one line. The text comes from the captain's release, the audio from Workers AI Aura-2, and the
server shows it on every page and plays it on the table page, the stage (S key) and the AR/VR view.

    infisical run --projectId=… --env=prod --path=/ -- /usr/bin/python3 table/captains.py
    … table/captains.py --try "Captain N'ghathrod" "Michael lost 4 life."   # one line, played here

Reads table/.cache/captains.json (gitignored): {commander: {release_id, voice, ...}}; personas live in
table/captains/personas.json. Needs DIVINCI_FUSION_API_KEY, CLOUDFLARE_WORKER_AI_KEY and
CLOUDFLARE_ACCOUNT_ID in the environment — run it under infisical, never with keys in a file.
Only PUBLIC events reach a captain: it never sees a hand, so it cannot reveal one (and the server
refuses a line that names a card still in an AI seat's hand anyway).
"""
import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://api.divinci.app"
CONF_FILE = HERE / ".cache" / "captains.json"
CONF: dict = {}                                            # loaded in main(): {commander: {release_id, voice, ...}}
AUDIO = HERE / ".cache" / "captains" / "audio"
LOG = HERE / ".cache" / "research" / "captains.jsonl"      # research data: every prompt, line and voice
MAX_WORDS = 22
a = None                                                   # the parsed command line, set in main()


def http(url, body=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")


TRANSCRIPT: dict[str, str] = {}       # commander → transcriptId, so a captain remembers (and avoids) its lines


def ask(commander: str, event: str) -> str:
    rid = CONF[commander]["release_id"]
    body = {"messages": [{"role": "user", "content": f"EVENT: {event}\nYour one line:"}], "releaseId": rid}
    if commander in TRANSCRIPT:
        body["transcriptId"] = TRANSCRIPT[commander]
    status, raw, _ = http(API + "/api/v1/chat/completions", body,
                          {"Authorization": "Bearer " + os.environ["DIVINCI_FUSION_API_KEY"]})
    text = raw.decode(errors="replace")
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"non-JSON reply (HTTP {status}): {text[:200]}")
    if status >= 400:
        raise RuntimeError(f"HTTP {status}: {text[:200]}")
    if d.get("transcriptId"):
        TRANSCRIPT[commander] = d["transcriptId"]
    return clean_line(d["choices"][0]["message"].get("content") if d.get("choices") else "")


def clean_line(raw) -> str:
    """The first line of a reply, without quotes, markdown or stage directions, at most MAX_WORDS words."""
    lines = [x for x in str(raw or "").strip().splitlines() if x.strip()]
    if not lines:
        return ""
    line = re.sub(r"\([^)]*\)|\*[^*]*\*|\[[^\]]*\]", "", lines[0])          # (sighs) *grins* [laughs]
    line = re.sub(r"^[\"'“”‘’`_\s]+|[\"'“”‘’`_\s]+$", "", line)
    return " ".join(line.split()[:MAX_WORDS])


def tts(commander: str, line: str) -> str:
    """Aura-2 on Workers AI → an mp3 named by its content; returns the file name."""
    AUDIO.mkdir(parents=True, exist_ok=True)
    os.chmod(AUDIO.parent, 0o700)
    voice = CONF[commander]["voice"]
    name = hashlib.sha256(f"{voice}|{line}".encode()).hexdigest()[:16] + ".mp3"
    out = AUDIO / name
    if not out.exists():
        status, raw, ctype = http(f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CLOUDFLARE_ACCOUNT_ID']}"
                                  "/ai/run/@cf/deepgram/aura-2-en", {"text": line, "speaker": voice, "encoding": "mp3"},
                                  {"Authorization": "Bearer " + os.environ["CLOUDFLARE_WORKER_AI_KEY"]})
        if status != 200 or "audio" not in ctype:
            raise RuntimeError(f"Aura-2 HTTP {status} ({ctype}): {raw[:160]!r}")
        out.write_bytes(raw)
    return name


def log(rec):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as fh:
        fh.write(json.dumps({"ts": round(time.time(), 2), **rec}) + "\n")


TOKEN = ""
SEAT_CMD: dict = {}                  # seat name → commander (from the server; a renamed seat still works)
BUSY = threading.Lock()


class Gate:
    """When a captain may speak: at least `gap` s since any captain, `cooldown` s since this one."""

    def __init__(self, gap: float, cooldown: float):
        self.gap, self.cooldown, self.last = gap, cooldown, {"any": 0.0}

    def ready(self, commander: str, now: float) -> bool:
        return now - self.last["any"] >= self.gap and now - self.last.get(commander, 0.0) >= self.cooldown

    def spoke(self, commander: str, now: float):
        self.last["any"] = self.last[commander] = now


GATE = Gate(25.0, 75.0)


def seats():
    st, raw, _ = http(a.url + "/api/stage", timeout=5)
    if st == 200:
        for s in json.loads(raw)["seats"]:
            if s.get("commander") in CONF:
                SEAT_CMD[s["name"]] = s["commander"]


def triggers(e: dict, seat_cmd: dict = SEAT_CMD) -> list:
    """[(seat, chance, what happened — in words the captain of that seat hears)]. Public events only."""
    t, out = e.get("type"), []
    if t == "phase" and e.get("step") == "untap" and e.get("player"):
        out.append((e["player"], 0.55, f"{e['player']}'s turn begins — your side is up."))
    elif t == "attention" and e.get("kind") == "turn" and e.get("addressee"):
        out.append((e["addressee"], 0.55, f"{e['addressee']}'s turn begins — your side is up."))
    elif t == "say" and e.get("speaker") in seat_cmd and e.get("action") in ("cast", "attack", "damage", "turn-up"):
        p = {"cast": 0.3, "attack": 0.5, "damage": 0.45, "turn-up": 0.6}[e["action"]]
        out.append((e["speaker"], p, f"{e['speaker']}, your seat, says to the table: \"{e.get('text', '')}\""))
    elif t == "life" and e.get("delta"):
        d, who, by = e["delta"], e.get("player"), e.get("by")
        if d < 0:
            out.append((who, 0.6 if d <= -3 else 0.3, f"{who}, your seat, just lost {-d} life (now {e.get('life')})."))
            if by and by != who:
                out.append((by, 0.3, f"Your seat, {by}, just took {-d} life off {who} (now {e.get('life')})."))
        else:
            out.append((who, 0.25, f"{who}, your seat, gained {d} life (now {e.get('life')})."))
    return [x for x in out if x[0] in seat_cmd]


def speak(seat: str, commander: str, event: str):
    try:
        t0 = time.time()
        line = ask(commander, event)
        if not line:
            return
        f = tts(commander, line)
        st, raw, _ = http(a.url + "/api/captain", {"seat": seat, "captain": commander, "text": line, "audio": f},
                          {"X-Brain-Token": TOKEN}, timeout=10)
        log({"seat": seat, "captain": commander, "event": event, "line": line, "voice": CONF[commander]["voice"],
             "secs": round(time.time() - t0, 1), "posted": st})
        print(f"[{time.strftime('%H:%M:%S')}] {commander}: {line}  ({time.time() - t0:.1f}s, {st})", flush=True)
    except Exception as ex:                       # a captain failing must never disturb the game
        log({"seat": seat, "captain": commander, "event": event, "error": str(ex)[:300]})
        print(f"[{time.strftime('%H:%M:%S')}] {commander}: skipped — {str(ex)[:160]}", flush=True)
    finally:
        BUSY.release()


def main():
    global a, TOKEN, GATE
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8800")
    ap.add_argument("--token-file", default=str(HERE / ".brain-token"))
    ap.add_argument("--gap", type=float, default=25.0, help="seconds between any two captain lines")
    ap.add_argument("--cooldown", type=float, default=75.0, help="seconds before the same captain speaks again")
    ap.add_argument("--chattiness", type=float, default=1.0, help="scales every trigger's chance (0.5 = half as often)")
    ap.add_argument("--try", nargs=2, metavar=("COMMANDER", "EVENT"), dest="try_", help="one line, played on this Mac")
    a = ap.parse_args()
    CONF.update(json.loads(CONF_FILE.read_text()))
    if a.try_:
        cmd, ev = a.try_
        t0 = time.time()
        line = ask(cmd, ev)
        f = tts(cmd, line)
        print(f"{cmd} ({CONF[cmd]['voice']}, {time.time() - t0:.1f}s): {line}")
        subprocess.run(["afplay", str(AUDIO / f)])
        return
    TOKEN = Path(a.token_file).read_text().strip()
    GATE = Gate(a.gap, a.cooldown)
    seats()
    who = ", ".join(f"{s}→{c} ({CONF[c]['voice']})" for s, c in SEAT_CMD.items())
    print(f"captains: {who}", flush=True)
    st, raw, _ = http(a.url + "/api/events?since=latest", timeout=5)
    since = json.loads(raw)["last"]
    while True:
        try:
            st, raw, _ = http(a.url + f"/api/events?since={since}", timeout=10)
            d = json.loads(raw)
        except Exception:
            time.sleep(3)
            continue
        if d.get("restarted"):
            since = d["last"]
            seats()
            continue
        for e in d["events"]:
            since = e["id"]
            if e.get("type") in ("player-added", "new-game"):
                seats()
            if time.time() - e.get("ts", 0) > 20:
                continue
            for seat, chance, what in triggers(e):
                cmd, now = SEAT_CMD.get(seat), time.time()
                if not cmd or not GATE.ready(cmd, now):
                    continue
                if random.random() > chance * a.chattiness or not BUSY.acquire(blocking=False):
                    continue
                GATE.spoke(cmd, now)
                threading.Thread(target=speak, args=(seat, cmd, what), daemon=True).start()
                break
        time.sleep(1)


if __name__ == "__main__":
    main()
