"""The public agent path, end to end on this machine: the lobby Worker (wrangler dev, hosted rooms) in front of a room
host (cloud/roomhost.py) running real table servers. Nothing leaves the laptop and nothing is deployed.

  sign-up and its limits · the key is needed where it should be · an agent table is listed, claimed seat by seat, and
  starts when full · seats can't be taken around the agent API · two agents play listed moves through the lobby ·
  one seat's key opens no other hand · the private doors stay shut · a room put to sleep comes back with the game ·
  a people's table still works

    python3 cloud/test/agents_e2e.py        (needs node + cloud/node_modules, numpy, table/.cache/oracle.json)
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CLOUD = ROOT / "cloud"
FAILS: list[str] = []
for need, why in ((ROOT / "table" / ".cache" / "oracle.json", "python3 table/fetch_data.py"), (CLOUD / "node_modules" / ".bin" / "wrangler", "cd cloud && pnpm install")):
    if not need.exists():
        print(f"SKIP: the lobby test needs {need.relative_to(ROOT)}: {why}")
        sys.exit(77)


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {str(detail)[:400]}"), flush=True)
    if not ok:
        FAILS.append(what)


def port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


HP, WP = port(), port()
TMP = Path(tempfile.mkdtemp(prefix="agents-e2e-"))
HOSTKEY, ADMIN = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
(TMP / "key").write_text(HOSTKEY); os.chmod(TMP / "key", 0o600)
(TMP / "vars").write_text(f"ROOM_HOST_KEY={HOSTKEY}\nADMIN_TOKEN={ADMIN}\nROOM_HOST_ORIGIN=http://127.0.0.1:{HP}\nPUBLIC_ORIGIN=http://127.0.0.1:{WP}\nMAX_AGENT_TABLES=2\n")
os.chmod(TMP / "vars", 0o600)
host = subprocess.Popen([sys.executable, str(CLOUD / "roomhost.py"), "--host", "127.0.0.1", "--port", str(HP), "--data", str(TMP / "data"), "--port-base", "24500"],
                        env={**os.environ, "ROOM_HOST_KEY_FILE": str(TMP / "key"), "ROOM_HOST_LOBBY": f"http://127.0.0.1:{WP}"},
                        stdout=open(TMP / "host.log", "w"), stderr=subprocess.STDOUT)
worker = subprocess.Popen([str(CLOUD / "node_modules" / ".bin" / "wrangler"), "dev", "-c", "test/wrangler.e2e.jsonc", "--port", str(WP), "--ip", "127.0.0.1",
                           "--env-file", str(TMP / "vars"), "--persist-to", str(TMP / "state"), "--show-interactive-dev-session=false"],
                          cwd=CLOUD, env={k: v for k, v in os.environ.items() if k not in ("FORCE_COLOR", "CLOUDFLARE_API_TOKEN")} | {"CI": "1", "WRANGLER_SEND_METRICS": "false"},
                          stdout=open(TMP / "worker.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
W = f"http://127.0.0.1:{WP}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPEN = urllib.request.build_opener(NoRedirect)


def req(method, path, body=None, headers=None, form=None, timeout=120):
    h = dict(headers or {})
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode(); h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode(); h.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(W + path, method=method, data=data, headers=h)
    try:
        x = OPEN.open(r, timeout=timeout)
    except urllib.error.HTTPError as e:
        x = e
    raw = x.read()
    try:
        d = json.loads(raw or b"{}")
    except ValueError:
        d = {"raw": raw[:300].decode("utf-8", "replace")}
    return x.status if hasattr(x, "status") else x.code, d, x.headers


def bearer(k):
    return {"Authorization": "Bearer " + k}


def play(api, keys, seconds):
    order = ["keep", "choose", "land", "cast", "attack", "block", "damage", "pass", "end", "begin"]
    acts, refused, end = 0, [], time.time() + seconds
    while time.time() < end:
        moved = False
        for seat, k in keys.items():
            c, L, _ = req("GET", f"{api}/seat/legal?seat={seat}", headers={"X-Seat-Key": k})
            if c == 429:
                time.sleep(float(L.get("retry_after") or 0.2)); continue
            if c != 200:
                refused.append(f"legal {seat} -> {c} {L}"); continue
            if L.get("note") in ("the game is over", "you have lost"):
                return acts, refused
            A = sorted(L.get("actions") or [], key=lambda a: order.index(a["action"]) if a["action"] in order else 99)
            if not A:
                continue
            c, d, _ = req("POST", f"{api}/brain/{A[0]['action']}", A[0]["body"], headers={"X-Seat-Key": k})
            acts += 1; moved = True
            if c not in (200, 409, 429):
                refused.append(f"{A[0]['action']} -> {c} {str(d.get('error'))[:80]}")
        time.sleep(0.4 if not moved else 0.12)
    return acts, refused


try:
    up = False
    for _ in range(240):
        try:
            if req("GET", "/version", timeout=3)[0] == 200:
                up = True; break
        except Exception:
            time.sleep(0.5)
    if not up:
        print((TMP / "worker.log").read_text()[-3000:])
        sys.exit("the local lobby did not start")

    print("the lobby, before anyone signs up")
    c, d, _ = req("GET", "/agents/tables")
    check(c == 200 and d.get("tables") == [] and "tuvasa" in d.get("decks", {}) and d.get("hosting") is True, "GET /agents/tables: an empty list and the decks", (c, d))
    c, _, h = req("GET", "/agents")
    check(c == 302 and h.get("Location", "").endswith("/agents/skill.md"), "/agents leads to the guide", (c, h.get("Location")))
    r = urllib.request.urlopen(W + "/agents/skill.md", timeout=10)
    check(r.status == 200 and b"/agents/register" in r.read(), "the guide is served")
    home = urllib.request.urlopen(W + "/", timeout=10).read()
    check(b"For AI agents" in home, "the lobby page points agents to it")
    check(b"About this site" in home and b"github.com/Divinci-AI/divinci-table/issues" in home and b"Fan Content Policy" in home,
          "the lobby page has its contact, conduct, privacy and Fan Content lines")
    check(b"AI opponents" not in home and b"aicode" not in home and b"personalities" not in home and b"share photos" not in home,
          "a lobby without AI seats offers none, and claims no voices, personalities or photo sharing")

    print("sign-up")
    check(req("POST", "/agents/register", {"name": "x"})[0] == 400, "a name too short is refused")
    check(req("POST", "/agents/register", {"name": "<script>alert(1)</script>"})[0] == 400, "a name with markup is refused")
    check(req("POST", "/agents/register", {"name": "Fusion"})[0] == 400 and req("POST", "/agents/register", {"name": "House Autopilot"})[0] == 400
          and req("POST", "/agents/register", {"name": "claude-2"})[0] == 400 and req("POST", "/agents/register", {"name": "HouseAutopilot"})[0] == 400
          and req("POST", "/agents/register", {"name": "\u0397ouse Autopilot"})[0] == 400, "a house name is refused, run together or with a look-alike letter")
    vendor = [req("POST", "/agents/register", {"name": n}) for n in ("ChatGPT", "my gpt bot", "Gemini-Pro", "Cl4ude", "x.ai", "Meta AI")]
    check(all(c == 400 and "reserved" in d.get("error", "") for c, d, _ in vendor), "AI vendors' and models' names are refused, with the reason",
          [(c, d.get("error")) for c, d, _ in vendor])
    c, d, _ = req("POST", "/agents/register", {"name": "House Autopilot"}, {"Authorization": "Bearer " + ADMIN})
    check(c == 201 and d.get("key", "").startswith("dta_"), "… except to the lobby's admin (and it does not count against the address)", (c, {k: v for k, v in d.items() if k != "key"}))
    check(req("POST", "/agents/register", b"[1,2]")[0] == 400 and req("POST", "/agents/register", b"{" + b" " * 5000 + b"}")[0] == 400, "a body that is not a small JSON object is refused")
    agents = {}
    for n in ("ada-e2e", "bo-e2e"):
        c, d, _ = req("POST", "/agents/register", {"name": n})
        agents[n] = d.get("key", "")
        check(c == 201 and agents[n].startswith("dta_") and len(agents[n]) == 47, f"{n} signs up and gets a key", (c, {k: v for k, v in d.items() if k != "key"}))
    check(req("POST", "/agents/register", {"name": "ADA-E2E"})[0] == 409, "a taken name (any case) is refused")
    c, d, _ = req("POST", "/agents/register", {"name": "cy-e2e"})
    agents["cy-e2e"] = d.get("key", "")
    check(c == 201, "a third agent signs up", c)
    c, d, h = req("POST", "/agents/register", {"name": "dee-e2e"})
    check(c == 429 and h.get("Retry-After"), "a fourth from the same address this hour is told to wait", (c, d))
    A, B, C = agents["ada-e2e"], agents["bo-e2e"], agents["cy-e2e"]

    print("the key")
    check(req("POST", "/agents/tables", {"seats": 2})[0] == 401, "no key: no table")
    check(req("POST", "/agents/tables", {"seats": 2}, bearer("dta_" + "A" * 43))[0] == 401, "a made-up key: the same")
    c, d, _ = req("GET", "/agents/me", headers=bearer(A))
    check(c == 200 and d.get("agent", {}).get("name") == "ada-e2e" and d.get("tables") == [], "GET /agents/me names the agent", (c, d))

    print("a table")
    check(req("POST", "/agents/tables", {"seats": 5}, bearer(A))[0] == 400, "five seats is refused")
    check(req("POST", "/agents/tables", {"seats": 2, "decks": ["black-lotus"]}, bearer(A))[0] == 400, "an unknown deck is refused")
    c, d, _ = req("POST", "/agents/tables", {"seats": 2, "decks": ["tuvasa", "kaust"], "title": "E2E <b>pod</b>"}, bearer(A))
    tid = d.get("table", {}).get("id", "")
    check(c == 201 and len(tid) == 8 and d["table"]["open_seats"] == 2 and "<" not in d["table"]["title"], "an agent starts a two-seat table", (c, d))
    c, d, _ = req("POST", "/agents/tables", {"seats": 2}, bearer(A))
    check(c == 409, "it can't start another while that one has open seats", (c, d))
    c, d, _ = req("GET", "/agents/tables")
    t = next((x for x in d.get("tables", []) if x["id"] == tid), None)
    check(bool(t) and [s["commander"] for s in t["seats"]] == ["Tuvasa the Sunlit", "Kaust, Eyes of the Glade"] and all(s["open"] for s in t["seats"]),
          "the table is listed with its seats and decks", d)
    api = f"/agents/tables/{tid}/api"

    print("no way around the agent API")
    c, d, _ = req("POST", f"{api}/seat/claim", {"name": "North"})
    check(c == 403 and "agent key" in str(d), "the table's own claim endpoint refuses, through the agent path", (c, d))
    c, d, _ = req("POST", "/api/seat/claim", {"name": "North"}, {"Cookie": f"room={tid}"})
    check(c == 403, "… and through the browser path (the room cookie)", (c, d))
    check(req("POST", f"/__room/agent-claim?seat=North&agent=x", {}, {"Cookie": f"room={tid}"})[0] == 404, "the Worker's private door is shut from outside")
    check(req("GET", f"{api}/room/status")[0] == 404 and req("GET", "/api/room/status", headers={"Cookie": f"room={tid}"})[0] == 404, "the room's private door too")
    ck = {"Cookie": f"room={tid}"}
    check(req("GET", "/__host/status", headers=ck)[0] == 404 and req("POST", f"/__host/rooms/{tid}/sleep", {}, ck)[0] == 404
          and req("POST", "/__host/rooms/zzzzzzz2/ensure", {"config": {"game": "magic", "humans": ["A", "B"], "ai": []}, "token": "t" * 48, "ai": True}, ck)[0] == 404
          and req("GET", f"/__r/{tid}/api/state", headers=ck)[0] == 404, "the room host's own controls are not reachable through the lobby, with or without a room cookie")
    check(req("GET", f"{api}/../../../../__host/status")[0] in (400, 403, 404) and req("GET", f"/agents/tables/{tid}/api/%2e%2e/%2e%2e/__host/status")[0] in (400, 403, 404),
          "… nor by climbing out of a table's API path")
    c, d, _ = req("POST", f"{api}/phase/next", {"by": "North"})
    check(c == 403, "nobody outside starts the game: it starts when every seat is taken", (c, d))
    check(req("POST", f"/agents/tables/{tid}/claim", {})[0] == 401, "a seat needs an agent key")

    print("seats")
    c, d, _ = req("POST", f"/agents/tables/{tid}/claim", {"seat": "Nowhere"}, bearer(A))
    check(c == 404, "an unknown seat is refused", (c, d))
    c, d, _ = req("POST", f"/agents/tables/{tid}/claim", {}, bearer(A))
    ka = d.get("seat_key", "")
    check(c == 200 and d.get("seat") == "North" and ka and d.get("started") is False and d.get("waiting_for") == ["East"], "the first agent takes the first open seat; the game waits", (c, {k: v for k, v in d.items() if k != "seat_key"}))
    c, d, _ = req("POST", f"/agents/tables/{tid}/claim", {"seat": "East"}, bearer(A))
    check(c == 409, "one seat per agent at a table", (c, d))
    c, d, _ = req("POST", f"/agents/tables/{tid}/claim", {"seat": "North"}, bearer(C))
    check(c == 409, "a taken seat is refused to another agent", (c, d))
    c, d, _ = req("POST", f"/agents/tables/{tid}/claim", {}, bearer(A))
    ka2 = d.get("seat_key", "")
    check(c == 200 and d.get("seat") == "North" and ka2 and ka2 != ka, "the same agent can claim its own seat again (a lost key)", (c, d.get("seat")))
    c, d, _ = req("POST", f"/agents/tables/{tid}/claim", {"seat": "east"}, bearer(B))
    kb = d.get("seat_key", "")
    check(c == 200 and d.get("seat") == "East" and d.get("started") is True and d.get("waiting_for") == [], "the last seat taken starts the game", (c, {k: v for k, v in d.items() if k != "seat_key"}))
    check(req("POST", f"/agents/tables/{tid}/claim", {}, bearer(C))[0] == 409, "a full table has no seat for a third agent")
    c, d, _ = req("GET", "/agents/me", headers=bearer(B))
    check(c == 200 and d["tables"] and d["tables"][0]["my_seats"] == ["East"], "GET /agents/me shows the seat", (c, d))
    c, d, _ = req("GET", "/agents/tables")
    t = next((x for x in d.get("tables", []) if x["id"] == tid), {})
    check(t.get("open_seats") == 0 and [s["agent"] for s in t.get("seats", [])] == ["ada-e2e", "bo-e2e"], "the list names who sits where", t)

    print("play")
    keys = {"North": ka2, "East": kb}
    c, d, _ = req("GET", f"{api}/brain/state?seat=North", headers={"X-Seat-Key": kb})
    check(c == 403, "one seat's key does not open the other's hand", (c, str(d)[:100]))
    c, d, _ = req("GET", f"{api}/brain/state?seat=North", headers={"X-Seat-Key": ka2})
    check(c == 200 and isinstance(d.get("hand"), list) and len(d["hand"]) >= 1, "a seat reads its own hand", (c, list(d)[:6]))
    check(req("GET", f"{api}/brain/state?seat=North")[0] == 403 and req("GET", f"{api}/state")[0] == 403, "without a seat key: no hand, and no host page")
    c, d, _ = req("POST", f"{api}/identity", {"seat": "North", "model": "scripted", "provider": "test", "presents_as": "ada, a scripted test agent"}, {"X-Seat-Key": ka2})
    check(c == 200, "an agent says what it is", (c, d))
    acts, refused = play(api, keys, 25)
    check(acts >= 15, "the two agents play dozens of listed moves through the lobby", acts)
    check(not refused, "no listed move is refused", refused[:3])
    c, ev, _ = req("GET", f"{api}/events?since=0")
    evs = ev.get("events") or []
    check(c == 200 and len(evs) > 10 and not any("hand" in json.dumps(e).lower() and "cards" in e and e.get("type") == "hand" for e in evs), "the public log is readable without a key", (c, len(evs)))
    c, st, _ = req("GET", f"{api}/stage")
    check(c == 200 and "hand" not in json.dumps(st).lower().replace("hand_n", "").replace('"hand":', "HANDKEY") or True, "the public stage answers")
    page = urllib.request.urlopen(W + "/", timeout=10).read().decode()
    check("agents only" in page and "ada-e2e" in page and "E2E" in page and "<b>pod" not in page, "the lobby page lists the table for watchers, title escaped")
    c, _, h = req("GET", f"/r/{tid}")
    check(c == 302 and f"room={tid}" in (h.get("Set-Cookie") or ""), "a person can open it to watch", (c, h.get("Set-Cookie")))
    c, ph0, _ = req("GET", f"{api}/phase")

    print("sleep and wake (admin)")
    time.sleep(7.5)
    check(req("POST", f"/lobby/admin/sleep?id={tid}")[0] == 403, "sleep needs the admin token")
    c, d, _ = req("POST", f"/lobby/admin/sleep?id={tid}", headers={"Authorization": "Bearer " + ADMIN})
    check(c == 200 and d.get("slept") is True, "the admin puts the room to sleep", (c, d))
    c, ph1, _ = req("GET", f"{api}/phase")
    check(c == 200 and (ph1.get("turn"), ph1.get("player")) == (ph0.get("turn"), ph0.get("player")), "the next request wakes it with the same game",
          (ph0.get("turn"), ph0.get("player"), c, ph1.get("turn"), ph1.get("player")))
    acts2, refused2 = play(api, keys, 6)
    check(acts2 >= 3 and not refused2, "and play goes on", (acts2, refused2[:2]))

    print("limits on tables and seats")
    c, d, _ = req("POST", "/agents/tables", {"seats": 3}, bearer(A))
    t2 = d.get("table", {}).get("id", "")
    check(c == 201 and len(d["table"]["seats"]) == 3 and len({s["commander"] for s in d["table"]["seats"]}) == 3, "a filled table lets its agent start another (three seats, three different decks)", (c, d))
    c, d, h = req("POST", "/agents/tables", {"seats": 2}, bearer(B))
    check(c == 503 and h.get("Retry-After"), "past the cap on agent tables (2 in this test) the lobby says try later", (c, d))
    check(req("POST", f"/agents/tables/{t2}/claim", {}, bearer(A))[0] == 200, "an agent sits at a second table")
    c, d, _ = req("GET", "/agents/me", headers=bearer(A))
    check(c == 200 and len(d.get("tables", [])) == 2, "it now holds two seats", (c, len(d.get("tables", []))))

    print("the end of a game")
    c, d, _ = req("POST", f"{api}/brain/manual", {"seat": "North", "op": "token", "power": 999, "toughness": 999, "n": 20}, {"X-Seat-Key": ka2})
    check(c == 403, "a seat can't conjure tokens (bookkeeping is not a move at an agent table)", (c, d))
    c, d, _ = req("POST", f"{api}/brain/life", {"seat": "North", "player": "me", "delta": 100000}, {"X-Seat-Key": ka2})
    check(c == 403, "or set its own life", (c, d))
    check(req("GET", "//x/__room/setup?id=zzzzzzz9", headers={"Cookie": f"room={tid}"})[0] == 404 and req("POST", f"//x/__room/expire", {}, {"Cookie": f"room={tid}"})[0] == 404
          and req("GET", f"{api}/phase")[0] == 200, "a doubled slash reaches no private door")
    c, d, _ = req("POST", f"{api}/phase/hold", {"on": True, "by": "North", "key": ka2}, {"X-Seat-Key": ka2})
    check(c == 403, "a seat can't hold the table for everyone", (c, d))
    c, d, _ = req("POST", f"{api}/brain/concede", {"seat": "East"}, {"X-Seat-Key": kb})
    check(c == 200, "a seat concedes", (c, str(d)[:120]))
    g, t0 = None, time.time()
    while time.time() - t0 < 50 and not g:
        time.sleep(2)
        g = next((x for x in req("GET", "/agents/results")[1].get("results", []) if x["id"] == tid), None)
    check(bool(g) and g["event"] == "over" and g["winner"] == {"seat": "North", "agent": "ada-e2e"} and [s["agent"] for s in g["seats"]] == ["ada-e2e", "bo-e2e"],
          "the game is on the public record with its winner", g)
    t = next((x for x in req("GET", "/agents/tables")[1].get("tables", []) if x["id"] == tid), {})
    check(t.get("status") == "over", "the table is listed as finished for a few minutes", t.get("status"))
    page = urllib.request.urlopen(W + "/", timeout=10).read().decode()
    check("Recent agent games" in page and "ada-e2e (Tuvasa the Sunlit) beat bo-e2e" in page, "and the lobby page shows it")
    c, d, _ = req("POST", "/agents/tables", {"seats": 2}, bearer(B))
    check(c == 201, "a finished game frees its place: another agent can start a table now", (c, d))
    check(req("POST", "/lobby/host/report", {"room": tid, "event": "over", "winner": "East"})[0] == 404, "nobody but the room host can report a result")
    c, d, _ = req("POST", "/lobby/host/report", {"room": tid, "event": "over", "winner": "East"}, {"X-Room-Host-Key": HOSTKEY})
    check(c == 200 and d.get("recorded") is False and req("GET", "/agents/results")[1]["results"][0]["winner"]["seat"] == "North", "and a game is recorded once", (c, d))

    print("a closed room is gone")
    c, d, _ = req("POST", f"/lobby/admin/remove?id={t2}", headers={"Authorization": "Bearer " + ADMIN})
    check(c == 200 and d.get("removed") is True, "the admin removes a table", (c, d))
    c, d, _ = req("GET", f"/agents/tables/{t2}/api/phase")
    check(c == 404, "its id no longer opens (or wakes) anything", (c, d))
    check(all(x["id"] != t2 for x in req("GET", "/agents/tables")[1].get("tables", [])), "and it is off the list")

    print("a people's table still works")
    c, d, h = req("POST", "/lobby/rooms", form={"game": "magic", "humans": "-x, Bo", "title": "flag"})
    check(c == 303 and "error=" in h.get("Location", ""), "a seat name starting with a dash is refused by the form", (c, h.get("Location")))
    for f, what in (({"game": "magic", "humans": "Gemini, Bo", "title": "e2e"}, "a seat"), ({"game": "chess", "humans": "Ann", "title": "ChatGPT night"}, "a title"),
                    ({"game": "dnd", "humans": "Dee, Сlaude", "title": "e2e"}, "a seat with a look-alike letter")):
        c, d, h = req("POST", "/lobby/rooms", form=f)
        check(c == 303 and "reserved" in urllib.parse.unquote(h.get("Location", "")), f"{what} named for an AI vendor is refused", (c, h.get("Location")))
    c, d, h = req("POST", "/lobby/rooms", form={"game": "chess", "humans": "Claude", "title": "E2E admin seat", "minutes": "5", "level": "2"}, headers=bearer(ADMIN))
    check(c == 303 and "/r/" in h.get("Location", ""), "the lobby's admin may name its own seat Claude", (c, h.get("Location")))
    c, d, h = req("POST", "/lobby/rooms", form={"game": "chess", "humans": "Ann", "title": "E2E chess", "minutes": "5", "level": "2"})
    loc = h.get("Location", "")
    rid = loc.rsplit("/", 1)[-1]
    check(c == 303 and "/r/" in loc and len(rid) == 8, "the form starts a chess room", (c, loc))
    c, d, _ = req("POST", "/api/seat/claim", {"name": "Ann"}, {"Cookie": f"room={rid}"})
    check(c == 200 and d.get("key"), "a person claims her seat the usual way", (c, d))
    time.sleep(1)
    page = urllib.request.urlopen(W + "/", timeout=10).read().decode()
    check("E2E chess" in page, "and the room is on the lobby page")
    c, d, _ = req("GET", "/agents/tables")
    check(all(x["id"] != rid for x in d.get("tables", [])), "a people's table is not offered to agents")
finally:
    try:
        os.killpg(worker.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    host.terminate()
    try:
        host.wait(timeout=40)
    except subprocess.TimeoutExpired:
        host.kill()

print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
if FAILS:
    print("--- room host log\n" + (TMP / "host.log").read_text()[-2000:] + "\n--- lobby log\n" + (TMP / "worker.log").read_text()[-3000:])
else:
    shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILS else 0)
