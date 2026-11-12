"""Fusion's seat in the live draft: picks for seat 3 with local Gemma 4 (Ollama, offline). Only Fusion's own pack and pool
are read (its own token). If the model gives no usable answer, it takes the highest-rarity card and logs that it did."""
import json, re, sys, time, urllib.request
from pathlib import Path

LIVE = Path.home() / ".divinci/star-trek-mtg/live"
BASE, MODEL = "http://127.0.0.1:8820", "gemma4:e2b"
TOKEN = json.loads((LIVE / "draft-agent-tokens.json").read_text())["Fusion"]
RANK = {"mythic": 3, "rare": 2, "uncommon": 1, "common": 0}
log = open(LIVE / "fusion-draft.log", "a", buffering=1)


def call(path, body=None):
    req = urllib.request.Request(f"{BASE}{path}?k={TOKEN}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def ask(d):
    pack = "\n".join(f"{i}. {c['name']} {c['cost']} [{c['rarity']}] {c['type']} {c['pt']} :: {c['text'][:140]}".replace("\n", " ")
                     for i, c in enumerate(d["pack"], 1))
    pool = ", ".join(c["name"] for c in d["pool"]) or "nothing yet"
    prompt = (f"You are drafting a 40-card Star Trek Magic: The Gathering deck. Pack {d['round']}, pick {d['pick']}.\n"
              f"Your picks so far: {pool}\n\nThis pack:\n{pack}\n\n"
              'Choose the single best card for your deck. Reply with JSON only: {"pick": <number>}')
    body = {"model": MODEL, "prompt": prompt, "stream": False, "format": "json", "keep_alive": "10m", "think": False,
            "options": {"temperature": 0.3, "num_predict": 120}}
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        txt = json.loads(r.read())["response"]
    try:
        n = int(json.loads(txt)["pick"])
    except Exception:
        log.write("   raw reply: " + repr(txt[:200]) + "\n")
        raise
    return n - 1 if 1 <= n <= len(d["pack"]) else None


last = None
while True:
    try:
        d = call("/api/state")
        if d["done"]:
            break
        key = (d["round"], d["pick"])
        if d["picked_this_turn"] or key == last:
            time.sleep(2)
            continue
        how = "gemma"
        try:
            idx = ask(d)
        except Exception as e:
            idx, how = None, f"gemma failed ({type(e).__name__})"
        if idx is None:
            idx = max(range(len(d["pack"])), key=lambda i: RANK.get(d["pack"][i]["rarity"], 0))
            how = how if how != "gemma" else "gemma gave no usable number"
            how += "; took highest rarity"
        r = call("/api/pick", {"id": d["pack"][idx]["id"]})
        log.write(f"pack {d['round']} pick {d['pick']}: {d['pack'][idx]['name']} ({how}) {r}\n")
        last = key
    except Exception as e:
        log.write(f"error {type(e).__name__}: {e}\n")
        time.sleep(3)
try:                                              # free the memory when the draft ends
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:11434/api/generate", method="POST",
                           data=json.dumps({"model": MODEL, "keep_alive": 0}).encode()), timeout=20).read()
except Exception:
    pass
log.write("draft done; model unloaded\n")
