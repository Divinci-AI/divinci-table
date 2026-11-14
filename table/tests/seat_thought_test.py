"""A seat's private thoughts and its "show my hand on the stream" flag (table/seat_private.py, routes in server.py), on a real scratch table.

Proves: a thought is posted and read only with that seat's own key (another seat's key and no key are refused, also through a
tunnel); it is capped (600 characters) and rate-limited; it appears in NO public answer (events, spectate, board, phase, summary, the
consent list, another seat's state); consent is the seat's own word (the host's brain token cannot give it), is off by default, is
public as a flag, and is announced in the public log without anything about the hand."""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from director_lib import begin, check, done, free_port, http, start_table

TMP = Path(tempfile.mkdtemp(prefix="thought-"))
srv, B, KF, env = start_table(free_port(), TMP, "Michael", "Claude")
K = {n: Path(f).read_text().strip() for n, f in KF.items()}
TOKEN = (TMP / "token").read_text().strip()
REMOTE = {"X-Forwarded-For": "203.0.113.30"}
SECRET = "ZEBRA-THOUGHT I hold the counterspell for his commander"
try:
    begin(B, "Michael", KF["Michael"])
    st, d, _ = http("POST", B + "/api/seat/thought", {"seat": "Claude", "text": SECRET}, {"X-Seat-Key": K["Claude"]})
    check(st == 200 and d.get("private") is True, "a seat posts a thought with its own key", f"{st} {d}")
    st, d, _ = http("POST", B + "/api/seat/thought", {"seat": "Claude", "text": "remote " + SECRET}, {"X-Seat-Key": K["Claude"], **REMOTE})
    check(st == 200, "…also from outside (through a tunnel), with its key", f"{st} {d}")
    check(http("POST", B + "/api/seat/thought", {"seat": "Claude", "text": "x"}, {"X-Seat-Key": K["Michael"], **REMOTE})[0] == 403, "another seat's key cannot post it")
    check(http("POST", B + "/api/seat/thought", {"seat": "Claude", "text": "x"}, REMOTE)[0] == 403, "no key, no thought")
    st, d, _ = http("GET", B + "/api/seat/thought?seat=Claude", headers={"X-Seat-Key": K["Claude"]})
    check(st == 200 and [t["text"] for t in d["thoughts"]][0] == SECRET and d["thoughts"][0]["ev"] >= 1 and d["thoughts"][0]["kind"] == "thought",
          "the seat reads its thoughts back, each with the public event id it was posted at", str(d)[:200])
    check(http("GET", B + "/api/seat/thought?seat=Claude", headers={"X-Seat-Key": K["Michael"]})[0] == 403, "another seat's key cannot read them")
    check(http("GET", B + "/api/seat/thought?seat=Claude", headers=REMOTE)[0] == 403, "nor can anyone without a key")
    check(http("GET", B + "/api/seat/thought?seat=Claude", headers={"X-Brain-Token": TOKEN})[0] == 200, "the host's own token can (the host can already see every hand)")
    last = d["thoughts"][-1]["id"]
    st, d, _ = http("GET", B + f"/api/seat/thought?seat=Claude&since={last}", headers={"X-Seat-Key": K["Claude"]})
    check(st == 200 and d["thoughts"] == [], "since=N returns only newer thoughts")
    st, d, _ = http("POST", B + "/api/seat/thought", {"seat": "Claude", "text": "y" * 601}, {"X-Seat-Key": K["Claude"]})
    check(st == 413 and d.get("max") == 600, "a thought over 600 characters is refused", f"{st} {d}")
    check(http("POST", B + "/api/seat/thought", {"seat": "Claude", "text": "   "}, {"X-Seat-Key": K["Claude"]})[0] == 400, "an empty thought is refused")
    check(http("POST", B + "/api/seat/thought", {"seat": "Claude", "text": "x", "kind": "public"}, {"X-Seat-Key": K["Claude"]})[0] == 400, "kind is thought or note")
    time.sleep(2.2)
    codes = [http("POST", B + "/api/seat/thought", {"seat": "Michael", "text": f"n{i}", "kind": "note"}, {"X-Seat-Key": K["Michael"]})[0] for i in range(8)]
    check(codes.count(200) <= 4 and 429 in codes, "thoughts are rate-limited (a burst of three, then one every two seconds)", str(codes))

    public = ""
    for path in ("/api/events?since=0", "/api/spectate", "/api/board3d", "/api/phase", "/api/game/summary", "/api/stream-consent", "/api/life", "/api/lobby", "/api/stage"):
        st, d, _ = http("GET", B + path)
        public += json.dumps(d)
    st, other, _ = http("GET", B + "/api/brain/state?seat=Michael", headers={"X-Seat-Key": K["Michael"]})
    check("ZEBRA-THOUGHT" not in public, "the thought is in no public answer (events, spectate, board, phase, summary, lobby, stage, consent)")
    check(st == 200 and "ZEBRA-THOUGHT" not in json.dumps(other), "nor in the other seat's own state")
    check("ZEBRA" not in (TMP / "r").read_text() if (TMP / "r").is_file() else not any("ZEBRA-THOUGHT" in p.read_text(errors="ignore") for p in (TMP / "r").rglob("*") if p.is_file()),
          "nor in the research log on disk")

    st, d, _ = http("GET", B + "/api/stream-consent", headers=REMOTE)
    check(st == 200 and d["seats"] == {"Claude": False, "Michael": False} and d["game_id"], "consent is public as a flag, and off by default", str(d))
    check(http("POST", B + "/api/seat/stream-consent", {"seat": "Michael", "show_hand": True}, {"X-Brain-Token": TOKEN})[0] == 403, "the host's token cannot consent for a player")
    check(http("POST", B + "/api/seat/stream-consent", {"seat": "Michael", "show_hand": True}, {"X-Seat-Key": K["Claude"]})[0] == 403, "another seat cannot consent for a player")
    check(http("POST", B + "/api/seat/stream-consent", {"seat": "Michael", "show_hand": "yes"}, {"X-Seat-Key": K["Michael"]})[0] == 400, "show_hand must be true or false")
    st, d, _ = http("POST", B + "/api/seat/stream-consent", {"seat": "Michael", "show_hand": True}, {"X-Seat-Key": K["Michael"], **REMOTE})
    check(st == 200 and d["show_hand"] is True, "the player switches it on with their own key", f"{st} {d}")
    check(http("GET", B + "/api/stream-consent")[1]["seats"] == {"Claude": False, "Michael": True}, "…and the flag says so")
    evs = http("GET", B + "/api/events?since=0")[1]["events"]
    e = [x for x in evs if x["type"] == "stream-consent"]
    check(len(e) == 1 and e[0]["seat"] == "Michael" and e[0]["show_hand"] is True and set(e[0]) == {"seat", "show_hand", "id", "ts", "type"},
          "the public log records that the seat allowed it, and nothing else", str(e))
    http("POST", B + "/api/seat/stream-consent", {"seat": "Michael", "show_hand": False}, {"X-Seat-Key": K["Michael"]})
    check(http("GET", B + "/api/stream-consent")[1]["seats"]["Michael"] is False, "and it can be withdrawn")
    st, page, _ = http("GET", B + "/note?player=Michael", headers=REMOTE)
    check(st == 200 and "Show my hand on the stream" in page and K["Michael"] not in page, "/note is the page a person uses (no key in the page)")
finally:
    srv.terminate()
# a new game clears both (unit: the store rolls on a new game id)
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import seat_private  # noqa: E402
sp = seat_private.SeatPrivate()
sp.post("g1", "A", "old thought", "thought", 0.0, 1); sp.set_consent("g1", "A", True)
check(sp.read("g2", "A")["thoughts"] == [] and sp.consent_public("g2", ["A"])["seats"] == {"A": False}, "a new game: no thought and no consent is carried over")
done()
