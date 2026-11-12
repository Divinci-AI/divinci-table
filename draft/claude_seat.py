"""Claude's seat in the live draft (seat 2). `wait` blocks until there is a pick to make and prints my own pack and pool;
`pick N` takes the Nth card of that pack. Only my own token is used: I never see anyone else's pack or picks."""
import json, sys, time, urllib.request
from pathlib import Path

LIVE = Path.home() / ".divinci/star-trek-mtg/live"
BASE = "http://127.0.0.1:8820"
TOKEN = json.loads((LIVE / "draft-agent-tokens.json").read_text())["Claude"]


def call(path, body=None):
    req = urllib.request.Request(f"{BASE}{path}?k={TOKEN}", data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def show(d):
    print(f"pack {d['round']}, pick {d['pick']}; waiting on {', '.join(d['waiting_on']) or 'nobody'}")
    for i, c in enumerate(d["pack"], 1):
        print(f"{i:2}. {c['name']} {c['cost']} [{c['rarity']}] {c['type']} {c['pt']}\n      {c['text'][:200]}".replace("\n      \n", "\n"))
    cnt = {}
    for c in d["pool"]:
        for col in c["colors"] or ["C"]:
            cnt[col] = cnt.get(col, 0) + 1
    print("pool colours:", cnt, "| pool:", "; ".join(c["name"] for c in d["pool"]))


if sys.argv[1] == "wait":
    deadline = time.time() + float(sys.argv[2] if len(sys.argv) > 2 else 540)
    while time.time() < deadline:
        d = call("/api/state")
        if d["done"]:
            print("DRAFT DONE"); print(json.dumps(d.get("deck"))); break
        if not d["picked_this_turn"]:
            show(d); break
        time.sleep(2)
    else:
        print("still waiting for the next pack")
elif sys.argv[1] == "say":
    print(call("/api/banter", {"text": " ".join(sys.argv[2:])}))
elif sys.argv[1] == "pick":
    d = call("/api/state")
    c = d["pack"][int(sys.argv[2]) - 1]
    print("picking", c["name"], call("/api/pick", {"id": c["id"]}))
