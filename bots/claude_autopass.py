"""Pass for Claude's seat in OTHER players' steps, unless Claude holds an instant it can cast right now (then print and leave it to Claude).
Uses only Claude's own seat key, like claude_player.py. Prints one line whenever the table waits on Claude in a way only Claude can decide."""
import json, os, time, urllib.request
from pathlib import Path
K = (Path(__file__).resolve().parent / "live" / "claude-seat.key").read_text().strip()
B = os.environ.get("TABLE_URL", "http://127.0.0.1:8800")
def call(m, p, body=None):
    r = urllib.request.Request(B + p, method=m, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-Seat-Key": K})
    try:
        with urllib.request.urlopen(r, timeout=10) as x: return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"{}")
    except Exception as e: return 0, {"error": str(e)}
last = ""
while True:
    c, ph = call("GET", "/api/phase")
    if c == 200:
        ps = ph.get("passes") or {}
        key = f"{ph.get('player')}|{ph.get('index')}|{len(ps.get('passed') or [])}"
        if ph.get("player") == "Claude" and key != last:
            if not last.startswith("Claude|") or ph.get("step") == "untap":    # once per turn: when it starts
                if ph.get("step") == "untap":          # begin it myself, so the table never sits on my untap step (retries through others' passes)
                    for _ in range(60):
                        bc, bo = call("POST", "/api/brain/begin", {"seat": "Claude"})
                        if bc == 200 or "waiting on passes" not in str(bo.get("error", "")):
                            break
                        time.sleep(3)
                    print("began", bc, (bo.get("private") or {}).get("drew", bo.get("error", "")), flush=True)
                    ph = call("GET", "/api/phase")[1]
                print("MY TURN", ph.get("step"), flush=True)
            last = key
        elif ps.get("next") == "Claude" and ph.get("player") != "Claude" and key != last:
            last = key
            st = call("GET", "/api/brain/state?seat=Claude")[1]
            inst = [h["name"] for h in st.get("hand") or [] if h.get("castable_now") and "Instant" in (h.get("type") or "")]
            if inst:
                print("RESPONSE POSSIBLE", ph.get("player"), ph.get("step"), "(I hold a castable instant)", flush=True)
            else:
                time.sleep(1.5)
                print("auto-passed", ph.get("player"), ph.get("step"), call("POST", "/api/brain/pass", {"seat": "Claude", "player": ph.get("player"), "step": ph.get("step")})[0], flush=True)
    time.sleep(1.5)
