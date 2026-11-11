"""No route an agent or a spectator can reach shows another seat's hidden hand (PRIMETIME §5, AGENT-TABLE-PLAN §3).

A throwaway two-pilot table plays a few real turns (two autopilot bots), Michael gets face-down permanents (manifest), then every GET route
open to outside callers is read three ways (Claude's seat key, no key, remote with no key), and each reply is searched for the names of
Michael's HIDDEN cards: his hand, his library, and the real names of his face-down permanents. A hidden name is any card of Michael's deck
that is not in Claude's deck and was never legitimately made public: on a battlefield face up, in a graveyard, or cast / played / revealed by
Michael. What Michael made public comes from his own actions in the private research log (brain.jsonl), NEVER from the public event log:
a name in the event log is exactly what this test hunts, so it can't count as public (it once did, and a leak INTO the log hid itself).
Also searched: Claude's /api/seat/legal, and the refusal messages Claude gets for probes aimed at Michael's face-down cards. Also: one
seat's key can't read the other seat's private state, and the brain-only routes refuse a seat key. Decks: TABLE_TEST_DECKS, or the
fixture decks (never live/). The bot: AUTOPILOT (default ~/.divinci/star-trek-mtg/claude_autopilot.py).

Routes that are card catalogs or rules lookups by design (/api/cardnames, /api/card) and static files are skipped; /api/deckdecl
is checked to hold only what a player chose to declare (the table runs with --decklists declared, as an agent table would; the
default "auto" publishes a virtual seat's deck file Commander-style, by design).

    ~/.venvs/table/bin/python table/tests/hand_leak_test.py
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ROOT = Path(os.path.expanduser("~/.divinci/star-trek-mtg"))
sys.path.insert(0, str(HERE / "tests"))
import fixture_decks  # noqa: E402
DECKS = Path(os.environ["TABLE_TEST_DECKS"]) if os.environ.get("TABLE_TEST_DECKS") else fixture_decks.write(Path(tempfile.mkdtemp(prefix="leak-decks-")))
AUTOPILOT = Path(os.environ.get("AUTOPILOT") or ROOT / "claude_autopilot.py").expanduser()
BASICS = ("Plains", "Island", "Swamp", "Mountain", "Forest")
FAILS: list[str] = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {detail}"), flush=True)
    if not ok:
        FAILS.append(what)


s = socket.socket(); s.bind(("127.0.0.1", 0)); PORT = s.getsockname()[1]; s.close()
B = f"http://127.0.0.1:{PORT}"
TMP = Path(tempfile.mkdtemp(prefix="leak-"))
env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
env.update(TABLE_NO_VOICE="1", TABLE_HIGHROLL="first", TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code",
           REPLIES="template", NO_GEMMA="1", TABLE_RESEARCH_DIR=str(TMP / "r"), TABLE_SAVES_DIR=str(TMP / "s"))
srv = subprocess.Popen([sys.executable, str(HERE / "server.py"), "--any-card", "--port", str(PORT), "--brain", "external",
                        "--fair-seed", "off", "--seed", os.environ.get("LEAK_SEED", "11"), "--token-file", str(TMP / "token"), "--decklists", "declared",
                        "--ai", "Michael|", "--ai-deck", str(DECKS / "sam.json"), "--ai", "Claude|", "--ai-deck", str(DECKS / "claude.json"),
                        "--pilot", "Michael", "--pilot", "Claude"],
                       cwd=HERE.parent, env=env, stdout=open(TMP / "log", "w"), stderr=subprocess.STDOUT)
bots = []


def req(method, path, body=None, key=None, host=False, remote=False):
    h = {"Content-Type": "application/json"}
    if key:
        h["X-Seat-Key"] = key
    if host:
        h["X-Brain-Token"] = (TMP / "token").read_text().strip()
    if remote:
        h["X-Forwarded-For"] = "203.0.113.20"
    r = urllib.request.Request(B + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as x:
            return x.status, x.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


try:
    for _ in range(80):
        try:
            urllib.request.urlopen(B + "/api/phase", timeout=2); break
        except Exception:
            time.sleep(0.5)
    K = {}
    for seat in ("Michael", "Claude"):
        K[seat] = json.loads(req("POST", "/api/seat/claim", {"name": seat})[1]).get("key")
        kf = TMP / f"{seat}.key"
        fd = os.open(kf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600); os.write(fd, K[seat].encode()); os.close(fd)
        bots.append(subprocess.Popen([sys.executable, "-u", str(AUTOPILOT)], cwd=AUTOPILOT.parent,
                                     env={**os.environ, "TABLE_URL": B, "CLAUDE_SEAT": seat, "CLAUDE_KEY_FILE": str(kf), "AUTOPILOT_GRACE": "2"},
                                     stdout=open(TMP / f"{seat}.log", "w"), stderr=subprocess.STDOUT))
    req("POST", "/api/phase/windows", {"on": False, "by": "host"})
    req("POST", "/api/phase/next", {"by": "Michael", "key": K["Michael"]}, key=K["Michael"])
    time.sleep(float(os.environ.get("LEAK_SLEEP", "35")))                                     # a few real turns: hands, boards, graveyards and the log all have content
    for b in bots:
        b.terminate()
    time.sleep(1)

    # face-down cards: their real names are hidden too. The bots may be mid-combat (the table refuses a manifest then), and a manifested card may
    # be one that is public anyway (a basic land, a name both decks hold): so manifest in threes until at least three cards are face down
    # AND one of them has a name nothing makes public (this set-up used to fail on those draws, 8 runs in 40).
    c_, b_ = 0, ""
    mich, face_down = {}, set()
    for _try in range(70):
        c1, b1 = req("POST", "/api/brain/manifest", {"seat": "Michael", "n": 3}, host=True)
        if c1 == 200:
            c_ = 200
        else:
            b_ = b1
            # the bots were stopped wherever they were (mid-combat, a pass window open): the table refuses the host's manifest while a seat still owes a
            # pass, so pass for whoever it waits on, then retry.
            for w_ in (json.loads(b1).get("passes", {}).get("need", []) if b1.startswith("{") else []):
                if w_ in K:
                    req("POST", "/api/brain/pass", {"seat": w_}, key=K[w_], remote=True)
            time.sleep(0.5)
            continue
        mich = json.loads(req("GET", "/api/brain/state?seat=Michael", host=True)[1])
        face_down = {p["name"] for p in mich.get("permanents", []) if p.get("face_down")}
        claude_names = {e["name"] for e in json.loads((DECKS / "claude.json").read_text())["mainBoard"]}
        pub_ = set()
        for st_ in json.loads(req("GET", "/api/board3d")[1]).get("seats", []):
            pub_ |= {p.get("name") for p in st_.get("permanents", []) if p.get("name")} | set(st_.get("graveyard", []))
        if len(face_down) >= 3 and (face_down - claude_names - pub_ - set(BASICS)):
            break
    check(c_ == 200, "set-up: Michael manifests face down", (json.loads(b_).get("error") if b_.startswith("{") else b_[-300:]))
    claude_deck = {e["name"] for e in json.loads((DECKS / "claude.json").read_text())["mainBoard"]}
    michael_deck = {e["name"] for e in json.loads((DECKS / "sam.json").read_text())["mainBoard"]}
    public = set()
    for st in json.loads(req("GET", "/api/board3d")[1]).get("seats", []):
        public |= {p.get("name") for p in st.get("permanents", []) if p.get("name")} | set(st.get("graveyard", []))
    shown = set()                                      # what Michael himself made public: his casts, land drops, cycles, and the cards he revealed
    said_lines = []
    for f in (TMP / "r").rglob("brain.jsonl"):
        for line in f.read_text().splitlines():
            r_ = json.loads(line)
            if r_.get("seat") != "Michael" or r_.get("refused"):
                continue
            body = r_.get("body") or {}
            if r_.get("action") in ("cast", "land", "cycle") and body.get("name") and not body.get("face_down"):
                shown.add(str(body["name"]))
            said_lines += [str(x) for x in r_.get("said") or [] if "reveal" in str(x).lower()]
    shown |= {n for n in michael_deck if any(n in x for x in said_lines)}
    # hidden: Michael's hand + library + face-down names, i.e. his deck less anything public (and less Claude's own deck: those names are Claude's to see)
    secret = sorted((michael_deck | face_down | {h["name"] for h in mich.get("hand", [])}) - claude_deck - public - shown)
    secret = [n for n in secret if len(n) > 4 and n not in BASICS]
    hidden_fd = sorted(face_down - claude_deck - public - shown)
    check(len(secret) >= 2, "the test has hidden cards to look for (hand, library, face-down: never public)", str(secret))
    check(hidden_fd, "…including the real name of a face-down permanent", str(sorted(face_down)))

    routes = ["/api/board3d", "/api/events?since=0", "/api/game/summary", "/api/life", "/api/phase", "/api/spectate", "/api/checklist", "/api/fair",
              "/api/highroll", "/api/history", "/api/placed", "/api/seat/claims", "/api/stage", "/api/todos", "/api/voice-config",
              "/api/journal?seat=Michael", "/api/journal/due?seat=Michael", "/api/ai/state?seat=Michael", "/api/deckdecl",
              "/api/brain/state?seat=Claude", "/api/seat/legal?seat=Claude", "/board", "/watch", "/log", "/me", "/hand", "/table"]
    def read_all(r, **kw):
        """A route's whole reply. The event log is paged (300 at a time): every page, or a leak after the first 300 events is never read."""
        if not r.startswith("/api/events"):
            return req("GET", r, **kw)
        since, out, code = 0, [], 200
        for _ in range(200):
            code, body = req("GET", f"/api/events?since={since}", **kw)
            evs_ = json.loads(body).get("events", []) if code == 200 else []
            out.append(body)
            if len(evs_) < 300:
                break
            since = max(int(e["id"]) for e in evs_)
        return code, "\n".join(out)

    leaks = []
    for r in routes:
        for who, kw in (("Claude's key", {"key": K["Claude"]}), ("no key", {}), ("remote, no key", {"remote": True})):
            code, body = read_all(r, **kw)
            hit = [n for n in secret if re.search(re.escape(n), body, re.I)]
            if hit:
                leaks.append(f"{r} as {who} ({code}): {hit}")
                for n in hit:
                    for m in list(re.finditer(re.escape(n), body, re.I))[:2]:
                        a_, b_2 = max(0, m.start() - 120), min(len(body), m.end() + 120)
                        print(f"   LEAKDETAIL route={r} caller={who} card={n!r} matched={m.group(0)!r} in_michael_hand={n in {h['name'] for h in mich.get('hand', [])}} in_face_down={n in face_down} in_michael_deck={n in michael_deck}\n      ctx={body[a_:b_2]!r}", flush=True)
    check(not leaks, f"no route shows a card from another seat's hand, library or face-down permanents ({len(routes)} routes x 3 callers)", "; ".join(leaks))

    if leaks:
        print("   FORENSIC secret=", secret, "shown=", sorted(shown), "public=", sorted(public), "hand=", [h["name"] for h in mich.get("hand", [])], "fd=", sorted(face_down), flush=True)
        print("   FORENSIC board3d=", req("GET", "/api/board3d")[1][:4000], flush=True)
    # What the NAME search cannot see: a face-down BASIC land (the names are skipped above as noise) or a face-down artifact. Claude's view of Michael
    # must count only face-up lands and list every face-down permanent as a creature.
    ov = json.loads(req("GET", "/api/brain/state?seat=Claude", key=K["Claude"])[1]).get("opponents", {}).get("Michael", {})
    up_lands = [p for p in mich.get("permanents", []) if not p.get("face_down") and "Land" in (p.get("type") or "")]
    n_fd = sum(1 for p in mich.get("permanents", []) if p.get("face_down"))
    n_up_cre = sum(1 for p in mich.get("permanents", []) if not p.get("face_down") and "Creature" in (p.get("type") or ""))
    check(ov.get("lands") == len(up_lands) and len(ov.get("lands_list", [])) == len(up_lands) and len(ov.get("creatures", [])) == n_fd + n_up_cre,
          "Claude's view of Michael counts only face-up lands, and lists every face-down card as a creature (no type leaks from under a face-down card)",
          f"lands={ov.get('lands')} lands_list={ov.get('lands_list')} creatures={len(ov.get('creatures', []))} vs face-up lands {len(up_lands)}, face-down {n_fd}")
    # Refusals Claude gets for actions aimed at Michael's face-down cards describe them as the board does
    fd_refs = [f"#{p['id']}" for p in mich.get("permanents", []) if p.get("face_down")]
    probes = [("block", {"attacker": fd_refs[0]}), ("block", {"attacker": fd_refs[0], "blocker": fd_refs[0]}),
              ("cast", {"name": "Boarding Action", "targets": [{"at": "Michael", "ref": r} for r in fd_refs]}),
              ("attack", {"assign": {fd_refs[0]: "Michael"}}), ("choose", {"id": 1, "target": {"at": "Michael", "ref": fd_refs[0]}}),
              ("activate", {"source": fd_refs[0]}), ("equip", {"equipment": fd_refs[0], "creature": fd_refs[0]})] if fd_refs else []
    rleaks, refused_n = [], 0
    for act, body in probes:
        code, text = req("POST", f"/api/brain/{act}", {"seat": "Claude", **body}, key=K["Claude"], remote=True)
        refused_n += code != 200
        hit = [n for n in secret if re.search(re.escape(n), text, re.I)]
        if hit:
            rleaks.append(f"{act} -> {code}: {hit}")
            print(f"   REFUSALLEAK {act} {code} {text[:300]!r}", flush=True)
    check(probes and not rleaks, f"refusal messages to Claude never name Michael's hidden cards ({len(probes)} probes, {refused_n} refused)", "; ".join(rleaks))

    dd = json.loads(req("GET", "/api/deckdecl?seat=Michael", key=K["Claude"])[1])
    check(not dd.get("cards"), "--decklists declared: an undeclared seat's deck list is not public", str(dd)[:160])
    code, body = req("GET", "/api/brain/state?seat=Michael", key=K["Claude"])
    check(code in (401, 403) and not any(n in body for n in secret), "a seat key can't read the other seat's private state", f"{code}")
    for path in ("/api/ai/hand?seat=Michael",):
        code, body = req("GET", path, key=K["Claude"])
        check(code in (401, 403) and not any(n in body for n in secret), f"{path} refuses a seat key (brain token only)", f"{code}")
    code, body = req("GET", "/api/brain/state?seat=Michael", key=K["Michael"], remote=True)
    own_ = [n for n in secret if n in {h["name"] for h in mich.get("hand", [])} | face_down]
    check(code == 200 and own_ and all(n in body for n in own_), "(control) the seat's OWN key does see its hand and its face-down cards' names", f"{code} {own_}")
finally:
    for b in bots:
        b.terminate()
    srv.terminate()

if FAILS:
    print("   FORENSIC TMP=", TMP, flush=True)
    try:
        print("   FORENSIC secret=", secret, "shown=", sorted(shown), "public=", sorted(public), "hand=", [h["name"] for h in mich.get("hand", [])], "fd=", sorted(face_down), flush=True)
        print("   FORENSIC board3d=", req("GET", "/api/board3d")[1][:3000], flush=True)
    except Exception as e:
        print("forensic fail", e)
else:
    import shutil; shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'all passed' if not FAILS else f'{len(FAILS)} FAILED: {FAILS}'}")
sys.exit(1 if FAILS else 0)
