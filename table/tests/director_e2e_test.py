"""The director end to end: two real scratch tables (repo fixture decks, rule bots in every seat), the director's own HTTP server, and
plain HTTP requests as OBS's browser, an operator, a stray bot and a mis-pointed tunnel would make them.

Table 1 stands for a table with a person ("Michael" is configured as kind human; a rule bot moves his cards so the game goes on).
Table 2 is all AI.

Proves:
  1. no seat key (and no control token) is in ANY response the director gives: pages, scripts, data, errors, headers.
  2. the data needs the page's cookie or the token; a request without them is refused AND counted; a request that came through a proxy
     or under another Host name gets nothing; control needs the token.
  3. the person's private state is never fetched before they consent, and after they consent (their own key, at the table) their hand
     appears only later (the turn delay), with the lower third saying so.
  4. the delay, for real: a thought posted mid-turn at table 1 is not in the director's output until the table's own spectator
     boundary has passed it; then it is.
  5. the all-AI table shows hands live, and the thinking panel carries the rule bot's moves with the table's reasons.
  6. the panic key takes every hand off at once.
  7. names-only is what a page gets when no art folder is configured; with one, art is served from it and nothing outside it.
  8. when a game ends the director cuts to its recap.
  9. nothing in bots/ or table/ reads the director."""
from __future__ import annotations

import json
import re
import secrets
import tempfile
import time
from pathlib import Path

from director_lib import REPO, begin, check, done, free_port, http, start_autopilot, start_table

from director import config as cfg
from director.server import Director, serve

TMP = Path(tempfile.mkdtemp(prefix="dir-e2e-"))
(TMP / "t1").mkdir(); (TMP / "t2").mkdir()
procs = []
s1, B1, KF1, env1 = start_table(free_port(), TMP / "t1", "Michael", "Claude", seed=5)
s2, B2, KF2, env2 = start_table(free_port(), TMP / "t2", "Fusion", "Botty", seed=7)
procs += [s1, s2]
KEYS = [Path(f).read_text().strip() for f in list(KF1.values()) + list(KF2.values())]
art = TMP / "art"; art.mkdir()
(art / "index.js").write_text('window.CARD_ART={"Tricorder":"abc123"};')
(art / "abc123.jpg").write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
(TMP / "outside.jpg").write_bytes(b"secret")
CONF = {"tables": [
    {"name": "Table 1", "url": B1, "seats": [{"name": "Michael", "kind": "human", "key_file": KF1["Michael"]},
                                             {"name": "Claude", "kind": "ai", "key_file": KF1["Claude"], "player": "rules autopilot"}]},
    {"name": "Table 2", "url": B2, "seats": [{"name": "Fusion", "kind": "ai", "key_file": KF2["Fusion"], "player": "rules autopilot"},
                                             {"name": "Botty", "kind": "ai", "key_file": KF2["Botty"], "player": "rules autopilot"}]}],
    "poll_secs": 1.0, "rotation": {"dwell_secs": 8, "recap_secs": 8, "min_hold_secs": 3, "action_hold_secs": 5}}
TOKEN = secrets.token_urlsafe(24)
PORT = free_port()
D = f"http://127.0.0.1:{PORT}"
d = Director(cfg.parse(CONF))
srv, stop = serve(d, PORT, TOKEN)
bodies: list = []


def get(path, headers=None, method="GET", body=None):
    st, b, h = http(method, D + path, body, headers)
    bodies.append(json.dumps(b) + json.dumps(h))
    return st, b, h


try:
    begin(B1, "Michael", KF1["Michael"]); begin(B2, "Fusion", KF2["Fusion"])
    for base, kf, env, tmp in ((B1, KF1, env1, TMP / "t1"), (B2, KF2, env2, TMP / "t2")):
        for seat, f in kf.items():
            procs.append(start_autopilot(base, seat, f, env, tmp))

    print("2. who may read")
    st, page, h = get("/director")
    ck = h.get("Set-Cookie", "")
    check(st == 200 and "stage.js" in page and "HttpOnly" in ck and "SameSite=Strict" in ck, "/director is a page, and sets an HttpOnly session cookie", ck[:60])
    COOKIE = {"Cookie": ck.split(";")[0]}
    check("?" not in "/director" and "key" not in page.lower().replace("keydown", ""), "the page's URL has no key or token, and the page holds none")
    st, b, _ = get("/api/program")
    check(st == 403, "the data without the page's cookie: refused", str(st))
    st, b, _ = get("/api/pov?seat=Fusion", {"User-Agent": "divinci-table-autopilot"})
    check(st == 403, "a bot asking for a point of view: refused")
    st, cs, _ = get("/api/control/state", {"X-Director-Token": TOKEN})
    check(st == 200 and cs["unknown_readers"]["count"] == 2 and cs["unknown_readers"]["last_ua"] == "divinci-table-autopilot",
          "…and counted, with what asked, for the operator to see", str(cs.get("unknown_readers")))
    check(get("/api/program", COOKIE)[0] == 200, "with the cookie: served")
    check(get("/api/program", {**COOKIE, "X-Forwarded-For": "203.0.113.9"})[0] == 403, "through a proxy or tunnel (a forwarding header): nothing, even with the cookie")
    check(get("/director", {"CF-Connecting-IP": "203.0.113.9"})[0] == 403, "…not even the page")
    check(get("/api/program", {**COOKIE, "Host": "evil.example"})[0] == 403, "under another Host name: nothing")
    check(get("/api/control/state", COOKIE)[0] == 403 and get("/api/control", COOKIE, "POST", {"cmd": "hide"})[0] == 403, "control needs the token: the page's cookie is not enough")
    check(get("/api/control", {"X-Director-Token": TOKEN, "Origin": "https://evil.example"}, "POST", {"cmd": "next"})[0] == 403, "control from another site's page is refused")
    check(get("/control")[0] == 200 and TOKEN not in bodies[-1], "the control page itself carries no token (it comes in the URL fragment)")
    check(srv.server_address[0] == "127.0.0.1", "the server is bound to 127.0.0.1")

    print("3+5. consent, and the all-AI table")
    t0 = time.time()
    fusion = None
    while time.time() - t0 < 40:
        st, pv, _ = get("/api/pov?seat=Fusion", COOKIE)
        me = pv["stage"]["me"]
        if me["private"]["shown"] and any(i["source"] == "move" and i.get("why") for i in me["private"]["thinking"]):
            fusion = pv
            break
        time.sleep(1)
    check(fusion is not None, "the all-AI table: the featured seat's hand is on the page, and the thinking panel has a move with the table's reason", json.dumps(pv["stage"]["me"]["private"])[:300])
    if fusion:
        hand = fusion["stage"]["me"]["private"]["hand"]
        check(all(set(c) == {"name", "cost", "type", "text", "pt"} for c in hand), "hand cards carry name, cost, type, text, P/T and nothing else")
        check(fusion["stage"]["delay"].startswith("live") and fusion["stage"]["lower_third"]["player"] == "rules autopilot" and fusion["stage"]["lower_third"]["table"] == "Table 2",
              "the lower third names the player, what drives it and the table; the strip says live", json.dumps(fusion["stage"]["lower_third"]))
        check(all(not o["private"]["shown"] and o["private"]["hand"] is None for o in fusion["stage"]["opponents"]), "the opponent's hand is not on a point-of-view page")
        check(fusion["pip"][0]["name"] == "Table 1" and "hand" in fusion["pip"][0]["seats"][0] and "private" not in json.dumps(fusion["pip"]),
              "the picture-in-picture of the other table is public information only")
        mv = [i for i in fusion["stage"]["me"]["private"]["thinking"] if i["source"] == "move" and i.get("why")][-1]
        st, legal_like, _ = http("GET", B2 + "/api/events?since=0")
        check(any(e.get("type") == "say" and e.get("text") == mv["text"] for e in legal_like["events"]), "the move shown is a real line from the table's public log", mv["text"])
    st, pv, _ = get("/api/pov?seat=Michael", COOKIE)
    mp = pv["stage"]["me"]["private"]
    check(mp["hand"] is None and "not switched it on" in mp["reason"] and d.watches[0].private_reads["Michael"] == 0,
          "the person: no consent, so the hand is not shown and their private state was never fetched", json.dumps(mp) + str(d.watches[0].private_reads))
    check(pv["stage"]["me"]["hand"] is not None and pv["stage"]["delay"] == "1 turn behind", "their public side (hand SIZE, board) is still shown, one turn behind")
    KM = Path(KF1["Michael"]).read_text().strip()
    st, _, _ = http("POST", B1 + "/api/seat/stream-consent", {"seat": "Michael", "show_hand": True}, {"X-Seat-Key": KM})
    check(st == 200, "the person switches it on at the table with their own key")
    shown_at = None
    t0 = time.time()
    while time.time() - t0 < 60:
        st, pv, _ = get("/api/pov?seat=Michael", COOKIE)
        if pv["stage"]["me"]["private"]["shown"]:
            shown_at = time.time() - t0
            break
        time.sleep(0.5)
    check(shown_at is not None and d.watches[0].private_reads["Michael"] > 0, "…after which the hand appears", str(shown_at))
    if shown_at is not None:
        check("shown" == pv["stage"]["lower_third"]["hand"] and pv["stage"]["lower_third"]["delay"] in ("1 turn behind", "game over"), "and the lower third says it is shown, and how late")

    print("4. the delay, on a real table")
    KC = Path(KF1["Claude"]).read_text().strip()
    over1 = http("GET", B1 + "/api/phase")[1].get("game_over")
    if over1:
        check(False, "table 1 was still being played when the delay was measured", "the game ended too soon: rerun")
    else:
        mark = "MARK-" + secrets.token_hex(4)
        st, r, _ = http("POST", B1 + "/api/seat/thought", {"seat": "Claude", "text": f"{mark} thinking about my hand"}, {"X-Seat-Key": KC})
        st2, th, _ = http("GET", B1 + "/api/seat/thought?seat=Claude", headers={"X-Seat-Key": KC})
        ev = next(t["ev"] for t in th["thoughts"] if mark in t["text"])
        seen_early, seen_late, t0 = False, False, time.time()
        while time.time() - t0 < 60:
            sp = http("GET", B1 + "/api/spectate")[1]            # read the boundary BEFORE the director's answer: it only ever moves forward
            ph = http("GET", B1 + "/api/phase")[1]
            body = json.dumps(get("/api/pov?seat=Claude", COOKIE)[1]) + json.dumps(get("/api/program", COOKIE)[1])
            passed = int(sp.get("as_of_event") or 0) >= ev or ph.get("game_over")
            if mark in body and not passed:
                seen_early = True
            if mark in body:
                seen_late = True
                break
            time.sleep(0.3)
        check(st == 200 and not seen_early, "a thought posted mid-turn is NOT in the director's output before the table's spectator boundary passes it")
        check(seen_late, "…and is, once the boundary has passed", f"ev={ev}")

    print("6. panic")
    st, pv, _ = get("/api/pov?seat=Fusion", COOKIE)
    was = pv["stage"]["me"]["private"]["shown"]
    st, _, _ = get("/api/control", {"X-Director-Token": TOKEN}, "POST", {"cmd": "hide"})
    after = [get("/api/pov?seat=" + s, COOKIE)[1] for s in ("Fusion", "Botty", "Claude", "Michael")] + [get("/api/program", COOKIE)[1]]
    check(st == 200 and was and all(a["stage"] is None or (a["stage"]["me"]["private"]["hand"] is None and a["stage"]["me"]["private"]["thinking"] == []) for a in after),
          "hide: no hand and no thought is in any answer, from the very next request")
    check(all(a["stage"]["me"]["private"]["reason"] == "hidden by the operator" for a in after[:4]) and len(after[0]["hidden"]) == 4, "and the page is told why")
    get("/api/control", {"X-Director-Token": TOKEN}, "POST", {"cmd": "unhide"})

    print("7. art")
    st, js, _ = get("/art/index.js")
    check(st == 200 and js.strip() == "window.CARD_ART={};" and get("/api/program", COOKIE)[1]["names_only"] is True, "no art folder configured: the page is told there is no art (names only)")
    check(get("/art/abc123.jpg")[0] == 404, "…and no image is served")
    d2 = Director(cfg.parse({**CONF, "art_dir": str(art)}), clients={"Table 1": d.watches[0].c, "Table 2": d.watches[1].c})
    P2 = free_port()
    srv2, stop2 = serve(d2, P2, TOKEN)
    st, js, _ = http("GET", f"http://127.0.0.1:{P2}/art/index.js")
    check(st == 200 and "Tricorder" in js and http("GET", f"http://127.0.0.1:{P2}/art/abc123.jpg")[0] == 200, "with an art folder: its index and images are served from it")
    check(http("GET", f"http://127.0.0.1:{P2}/art/../outside.jpg")[0] == 404 and http("GET", f"http://127.0.0.1:{P2}/art/%2e%2e/outside.jpg")[0] == 404, "nothing outside the art folder is served")
    d3 = Director(cfg.parse({**CONF, "art_dir": str(art), "names_only": True}), clients={"Table 1": d.watches[0].c, "Table 2": d.watches[1].c})
    P3 = free_port()
    srv3, stop3 = serve(d3, P3, TOKEN)
    check(http("GET", f"http://127.0.0.1:{P3}/art/index.js")[1].strip() == "window.CARD_ART={};" and http("GET", f"http://127.0.0.1:{P3}/art/abc123.jpg")[0] == 404,
          "names_only: true switches art off even with a folder configured")
    for s_, st_ in ((srv2, stop2), (srv3, stop3)):
        st_.set(); s_.shutdown(); s_.server_close()

    print("8. recap")
    seen_recap, t0 = None, time.time()
    while time.time() - t0 < 200:
        st, pg, _ = get("/api/program", COOKIE)
        if pg["mode"] == "recap" and pg["stage"]["recap"] and pg["stage"]["recap"].get("winner"):
            seen_recap = pg
            break
        time.sleep(0.5)
    check(seen_recap is not None, "a game ends: the director cuts to that table's recap (winner, totals)", "no game ended in 200 s")
    if seen_recap:
        check(seen_recap["stage"]["me"] is None and set(seen_recap["stage"]["recap"]["totals"]) == {s["name"] for s in seen_recap["stage"]["opponents"]},
              "the recap is the table's public summary", json.dumps(seen_recap["stage"]["recap"])[:200])

    print("1. no key anywhere")
    for path, hd in (("/pov?seat=Claude", {}), ("/assets/stage.js", {}), ("/assets/stage.css", {}), ("/nope", {}), ("/api/pov?seat=Nobody", COOKIE),
                     ("/api/control/state", {"X-Director-Token": TOKEN}), ("/api/program", COOKIE), ("/speech/1.wav", COOKIE), ("/api/pov", COOKIE)):
        get(path, hd)
    st, _, _ = get("/api/control", {"X-Director-Token": TOKEN, "Content-Type": "application/json"}, "POST", None)
    allb = "\n".join(bodies)
    check(len(bodies) > 60 and not any(k in allb for k in KEYS), f"none of the four seat keys is in any of {len(bodies)} responses (bodies and headers, errors included)")
    check(TOKEN not in allb and "key_file" not in allb and str(TMP) not in allb.replace(str(art), ""), "nor the control token, nor a key file's path")
    logs = "".join(p.read_text(errors="ignore") for p in TMP.rglob("*.log"))
    check(not any(k in logs for k in KEYS), "nor in the tables' and bots' logs")
finally:
    stop.set(); srv.shutdown(); srv.server_close()
    for p in procs:
        p.terminate()

print("9. nothing plays against the director")
hits = []
for f in list((REPO / "bots").glob("*.py")) + [p for p in (REPO / "table").glob("*.py")] + list((REPO / "table" / "assets").glob("*.js")):
    src = f.read_text(errors="ignore")
    if re.search(r"/api/program|/api/pov|director\.server|8930|dsession|X-Director-Token", src):
        hits.append(f.name)
check(hits == [], "no bot and no table code reads the director's port, its data or its cookie", str(hits))
done()
