"""Game 4 retro (2026-11-08): real mechanics instead of announcing them. Every check goes through the route a player uses: cast from hand, press the
action, on a table of its own on a free port. The G4 rows are in ~/.divinci/star-trek-mtg/RETRO-LOG.md.

    PYTHONDONTWRITEBYTECODE=1 python3 table/tests/retro_game4_test.py
    RETRO_ONLY="R1|S2" …          only the sections whose title names one of these (each section runs on its own: one that throws is one failure)
    RETRO_SEED=4                  the shuffle every table here starts with (pinned, so a failure repeats)

Set-up (a card into a hand, a land onto the battlefield) uses the host token; everything under test uses the seat's own key."""
from __future__ import annotations

import json
import os
import cards_path  # noqa: E402  (table/tests/cards_path.py: a missing card file or sibling folder SKIPS, never fails)
import traceback
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

cards_path.need_card_data()                            # every table started here needs it, and a test never downloads it
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import make_deck_json_shim as shim  # noqa: E402
import player  # noqa: E402
from ai_turn import resolve_block  # noqa: E402

CARDS = {c["name"]: c for c in shim.load(cards_path.cards_json())}
FAILS: list[str] = []


def check(ok, what, detail=""):
    print(("  ✅ " if ok else "  ❌ ") + what + ("" if ok else f" — {str(detail)[:400]}"), flush=True)
    if not ok:
        FAILS.append(what)


POOL = {"Plains": 40, "Forest": 40, "Mountain": 40, "Swamp": 30, "Island": 30,
        "Class-F Shuttlecraft": 2, "Romulan Bird-of-Prey": 1, "Deep Space 9": 1, "Sarcophagus, Ship of the Dead": 1, "D-7 Battle Cruiser": 1,
        "Tricorder": 2, "Type-2 Phaser": 1, "Combadge": 1, "Bat'leth": 1, "Gwyndala, the Progeny": 1,
        "Horta": 2, "Xindi Surveyors": 2, "Captain Michael Burnham": 1, "David and Carol Marcus, Creators": 1, "Common Goal": 2, "Assault Drone": 2,
        "Beckett Mariner, Impetuous Ensign": 1, "Tal Shiar Operative": 2, "Savvy Profiteer": 1, "Fugitive Listener": 2, "Starfleet Security": 1, "Brad Boimler, Eager Ensign": 1,
        "Moopsy": 1, "Mugato": 1, "Sulu, Space Swashbuckler": 1, "Elim Garak, Simple Tailor": 1, "Storied Veteran": 1, "Starfleet Spanner Jockey": 1,
        "Plasma Cascade": 2, "Energize": 1, "He's Dead, Jim": 1, "Command Decision": 2, "Set Phasers to . . .": 2, "Genesis Effect": 1, "Artificial Anomaly": 1,
        "Blood Crypt": 1, "Cetacean Ops": 1, "Open Communications": 2, "Emergency Medical Hologram": 1, "Vexing Drone": 1,
        "Cold-Blooded Crew": 1, "Syndicate Liquidators": 1, "Humpback Whales": 1, "Quark, Humble Proprietor": 1, "Vash, Freelance Archaeologist": 1, "Troubling Tribbles": 1, "Georgiou, Terran Emperor": 1, "Armus, Skin of Evil": 1, "Targ Wrangler": 1, "Jadzia Dax, Living Legacy": 1, "Seven of Nine, Unassimilated": 1,
        "Data, New Life": 1, "Mr. Spock, Science Officer": 1, "Sickbay": 1, "Deanna Troi, Psychic Counsel": 1, "Zefram Cochrane, Warp Pioneer": 1, "Kira Nerys, Firebrand": 1, "Lieutenant Commander Data": 1, "Hologram Stalker": 1, "Sela, Tal Shiar Paradox": 1, "Test of Loyalty": 1, "Captain Jonathan Archer": 1, "Rok-Tahk, Budding Scientist": 1, "L'Rell, Fierce Visionary": 1, "D'Vana Tendi, Optimistic Ensign": 1, "General Chang, Cold Warrior": 1, "Organic Avulsion Unit": 1, "DOT-7 Repair Squad": 1, "Deep Space Scan": 1, "Melkotian Adjudicator": 1, "Obsidian Order Operative": 1, "Seven of Nine": 1, "Gabriel Lorca, Mirror Menace": 1, "Jett Reno, Problem Solver": 1, "Federation Outpost": 1, "Tal Shiar Analyst": 1, "Boarding Action": 1, "Transporter Malfunction": 1, "Pure Energy": 1, "Federation Resupply": 1, "Final Frontier": 1, "Warp Core": 1, "Genesis Device": 1, "Year of Hell": 1, "Amok Time": 1, "In the Pale Moonlight": 1, "The Measure of a Man": 1, "The Naked Time": 1,
        "The Wrath of Khan": 1, "Those Old Scientists": 1, "The City on the Edge of Forever": 1, "All Good Things...": 1, "The Inner Light": 1, "Neelix, Moral Support": 1, "Pet Sehlat": 1, "Shuttle Ace": 1, "Dr. Beverly Crusher": 1, "Dominion Supervisor": 1}


POOL = {n: (k if n in ("Plains", "Forest", "Mountain", "Swamp", "Island") else 6) for n, k in POOL.items()}   # set-up may ask for any of these several times


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


TMP = Path(tempfile.mkdtemp(prefix="retro4-"))


def deck_file(name):
    f = TMP / f"{name}.json"
    f.write_text(json.dumps({"name": name, "commander": None, "startingLife": 20, "mainBoard": [shim.entry({**next(c for c in json.load(open(cards_path.cards_json())) if c['name'] == n and c.get('set') == 'trk')}, k) for n, k in POOL.items()]}))
    return str(f)


DECK_MICHAEL = deck_file("Michael")
DECK_CLAUDE = deck_file("Claude")


class Table:
    def __init__(self, extra=(), human=False, code=HERE / "server.py"):
        self.port = free_port()
        self.b = f"http://127.0.0.1:{self.port}"
        self.dir = Path(tempfile.mkdtemp(prefix="t-", dir=TMP))
        env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
        env.update(TABLE_HIGHROLL="first", TABLE_CHECKIN="off", STRICT_SEATS="1", HF_HUB_OFFLINE="1", ROUTER="code", REPLIES="template", NO_GEMMA="1", TABLE_NO_VOICE="1",
                   PYTHONDONTWRITEBYTECODE="1", TABLE_RESEARCH_DIR=str(self.dir / "r"), TABLE_SAVES_DIR=str(self.dir / "s"))
        seed = [] if "--seed" in extra else ["--seed", os.environ.get("RETRO_SEED", "4")]   # a pinned shuffle: a failure is the same failure next run
        cmd = [sys.executable, str(code), "--any-card", "--port", str(self.port), "--brain", "external", "--fair-seed", "off", "--token-file", str(self.dir / "token"), *seed,
               "--ai", "Claude|", "--ai-deck", DECK_CLAUDE, "--ai", "Michael|", "--ai-deck", DECK_MICHAEL, "--pilot", "Claude", "--pilot", "Michael", "--seat-rate", "0", *extra]
        if human:
            cmd += ["--human", "Sam|", "--order", "Claude,Michael,Sam"]
        self.proc = subprocess.Popen(cmd, cwd=HERE.parent, env=env, stdout=open(self.dir / "log", "w"), stderr=subprocess.STDOUT)
        self.keys = {}
        for _ in range(120):
            if self.proc.poll() is not None:           # the table stopped before it was up (its log says why): waiting a minute for it changes nothing
                break
            try:
                self.token = (self.dir / "token").read_text().strip()
                urllib.request.urlopen(self.b + "/api/phase", timeout=2)
                break
            except Exception:  # noqa: BLE001 — not up yet
                time.sleep(0.5)

    def call(self, method, path, body=None, key=None, host=True):
        h = {"Content-Type": "application/json"}
        if key:
            h.update({"X-Seat-Key": key, "X-Forwarded-For": "203.0.113.5", "User-Agent": "test-" + key[:4]})
        elif host:
            h["X-Brain-Token"] = self.token
        r = urllib.request.Request(self.b + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
        try:
            with urllib.request.urlopen(r, timeout=30) as x:
                return x.status, json.loads(x.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def start(self):
        self.keys = {n: self.call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in ("Claude", "Michael")}
        self.call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)
        self.call("POST", "/api/phase/next", {"by": "Claude", "key": self.keys["Claude"]}, key=self.keys["Claude"])
        self.wait(lambda p: p.get("player") == "Claude" and p.get("begun") and p.get("step") == "main 1")
        self.turn("Michael", fresh=False)

    def wait(self, pred, secs=45):
        """Until the table is where we want it. A seat holding a castable instant isn't passed for (G2-3): here, as a person or a bot would, it passes, or a shuffle
        that happens to deal an instant sits out the 30 s pass timer at every step and the test goes on from the wrong one."""
        end = time.time() + secs
        while time.time() < end:
            ph = self.call("GET", "/api/phase")[1]
            if pred(ph):
                return ph
            nxt = (ph.get("passes") or {}).get("next")
            if nxt in self.keys:
                self.call("POST", "/api/brain/pass", {"seat": nxt, "player": ph.get("player"), "step": ph.get("step")}, key=self.keys[nxt])
            time.sleep(0.15)
        raise AssertionError(f"the table never got there: {ph.get('player')} {ph.get('step')} passes={ph.get('passes')}")

    def act(self, seat, action, tries=40, **body):
        """An action through the seat's own key, retrying while the others pass."""
        for _ in range(tries):
            c, d = self.call("POST", f"/api/brain/{action}", {"seat": seat, **body}, key=self.keys[seat])
            if c == 409 and "waiting on" in str(d.get("error", "")) and "defenders" not in str(d.get("error")):
                ph = self.call("GET", "/api/phase")[1]     # a seat that holds an instant decides for itself: here, it passes
                for w in str(d.get("waiting") or "").split(", "):
                    if w in self.keys and w != seat:
                        self.call("POST", "/api/brain/pass", {"seat": w, "player": ph.get("player"), "step": ph.get("step")}, key=self.keys[w])
                time.sleep(0.25)
                continue
            return c, d
        return c, d

    def turn(self, who, fresh=True):
        """On to `who`'s main 1. fresh: a NEW turn of theirs, even if they are at main 1 now (lands untap, summoning sickness goes)."""
        for i in range(8):
            ph = self.call("GET", "/api/phase")[1]
            if ph.get("player") == who and ph.get("begun") and ph.get("step") == "main 1" and not (fresh and i == 0):
                return ph
            self.settle_setup()                        # (a question left over from set-up would rightly hold the turn)
            if ph.get("player") and ph.get("begun"):
                self.act(ph["player"], "end", text="over")
            prev = ph.get("player")
            self.wait(lambda p: p.get("player") != prev and p.get("begun") and p.get("step") == "main 1")
        return self.call("GET", "/api/phase")[1]

    def state(self, seat):
        return self.call("GET", f"/api/brain/state?seat={seat}", key=self.keys[seat])[1]

    def perm(self, seat, name):
        return next((p for p in self.state(seat).get("permanents", []) if p["name"] == name), None)

    def perms(self, seat, name):
        return [p for p in self.state(seat).get("permanents", []) if p["name"] == name]

    def hand(self, seat, name):
        c, d = self.call("POST", "/api/brain/search", {"seat": seat, "name": name})
        if c != 200 and any(h["name"] == name for h in self.state(seat).get("hand", [])):
            return                                     # it was drawn already
        assert c == 200, d

    def onto(self, seat, name, tapped=False):
        self.hand(seat, name)
        c, d = self.call("POST", "/api/brain/put", {"seat": seat, "name": name, "tapped": tapped, "quiet": True})
        assert c == 200, d
        pid = [p for p in self.state(seat)["permanents"] if p["name"] == name][-1]["id"]
        self.settle_setup()
        return pid

    def decline(self, seat):
        """Say no to a "you may" the table is asking."""
        for _ in range(4):
            pend = self.state(seat).get("pending_choices") or []
            if not pend:
                return
            self.call("POST", "/api/brain/choose", {"seat": seat, "id": pend[0]["id"], "target": {}}, key=self.keys[seat])

    def settle_setup(self):
        """Putting a card on the table for set-up can raise its enters trigger's question ("each opponent discards"): set-up answers it, with the first option."""
        for n in self.keys:
            for _ in range(4):
                pend = self.state(n).get("pending_choices") or []
                if not pend:
                    break
                opts = pend[0]["slot"].get("options") or [{}]
                self.call("POST", "/api/brain/choose", {"seat": n, "id": pend[0]["id"], "target": {k: v for k, v in opts[0].items() if k != "label"}}, key=self.keys[n])

    def lands(self, seat, n, name="Plains"):
        for _ in range(n):
            self.onto(seat, name)

    def life(self, seat):
        return self.call("GET", "/api/board3d", host=False)[1]["seats"], next(s for s in self.call("GET", "/api/board3d", host=False)[1]["seats"] if s["name"] == seat)["life"]

    def lifeof(self, seat):
        return self.life(seat)[1]

    def board(self, seat):
        return next(s for s in self.call("GET", "/api/board3d", host=False)[1]["seats"] if s["name"] == seat)

    def events(self, since=0):
        return self.call("GET", f"/api/events?since={since}", host=False)[1].get("events", [])

    def legal(self, seat):
        return self.call("GET", f"/api/seat/legal?seat={seat}", key=self.keys[seat])[1]

    def next_to(self, seat, step):
        for _ in range(12):
            ph = self.call("GET", "/api/phase")[1]
            if ph.get("step") == step:
                return ph
            c, d = self.act(seat, "next")
            if c != 200:
                return d
        return self.call("GET", "/api/phase")[1]

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def attack_through(t, attacker_seat, ids, foe):
    """Attack, the defender takes it, deal the damage."""
    return t.act(attacker_seat, "attack", assign={f"#{i}": foe for i in ids})


def main():
    def _section_01():
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            print("Station (G3-9, G4-6, priority 1)")
            ship = t.onto(M, "Class-F Shuttlecraft")
            horta = t.onto(M, "Horta")
            x = t.onto(M, "Xindi Surveyors")
            st = t.perm(M, "Class-F Shuttlecraft")
            check(st["has_station"] and st["station_at"] == 6 and st["charge"] == 0 and not st["pt"], "a Spacecraft starts at 0 charge, no creature yet", st)
            lg = [a for a in t.legal(M)["actions"] if a["action"] == "station"]
            check(any(a["body"]["ship"] == f"#{ship}" and a["body"]["crew"] == f"#{x}" for a in lg), "the legal-moves route offers Station for each untapped creature", lg[:2])
            c, d = t.act(C, "station", ship=f"#{ship}", crew=f"#{x}")
            check(c != 200, "another seat can't station my ship", (c, d))
            c, d = t.act(M, "station", ship=f"#{ship}", crew=f"#{x}")
            s = t.perm(M, "Class-F Shuttlecraft")
            check(c == 200 and s["charge"] == 3 and not s["pt"], "Station through the seat key: 3 charge from a power-3 Xindi; not a creature yet", (c, d, s))
            check(t.perm(M, "Xindi Surveyors")["tapped"], "…and it tapped the crew")
            c, d = t.act(M, "station", ship=f"#{ship}", crew=f"#{x}")
            check(c != 200, "a tapped creature can't station again", (c, d))
            ship_pub = next(p for p in t.board(M)["permanents"] if p["id"] == ship)
            check(ship_pub["charge"] == 3, "the public board (every page) shows the same charge", ship_pub)
            c0 = t.call("POST", "/api/brain/say", {"seat": M, "text": f"Station: I tap X (#{x}, power 3) and put 3 charge counters on Class-F Shuttlecraft #{ship} (now 6).", "force": True}, key=t.keys[M])
            check(t.perm(M, "Class-F Shuttlecraft")["charge"] == 3, "a chat line about charge counters no longer counts: the engine is the only count (charge.js is gone)")
            check(not (HERE / "assets/charge.js").exists() and b"charge.js" not in (HERE / "hand.html").read_bytes(), "assets/charge.js is removed and the hand page doesn't load it")
            c, d = t.act(M, "station", ship=f"#{ship}", crew=f"#{horta}")
            s = t.perm(M, "Class-F Shuttlecraft")
            check(c == 200 and s["charge"] == 10 and s["pt"] == "2/4" and "Flying" in s["keywords"] and "Vigilance" in s["keywords"],
                  "at 6+ it's a 2/4 artifact creature with its printed flying, vigilance", (c, d, s))
            check(any("is an artifact creature now" in x_ for x_ in d.get("said", [])), "…and the table says so", d.get("said"))
            lg = [a for a in t.legal(M)["actions"] if a["action"] == "station" and a["body"]["ship"] == f"#{ship}"]
            check(not lg, "a finished ship is no longer offered", lg[:1])
            t.act(M, "next")
            ph = t.call("GET", "/api/phase")[1]
            c, d = t.act(M, "station", ship=f"#{ship}", crew=f"#{x}")
            check(c != 200 and ("sorcery" in str(d.get("error")) or "tapped" in str(d.get("error"))), "Station is refused once combat has begun (sorcery timing)", (ph.get("step"), c, d))
            # a Spacecraft with no reminder text
            t.turn(M)
            bop = t.onto(M, "Romulan Bird-of-Prey")
            hx = t.perm(M, "Horta")
            c, d = t.act(M, "station", ship=f"#{bop}", crew=f"#{hx['id']}")
            s = t.perm(M, "Romulan Bird-of-Prey")
            check(c == 200 and s["charge"] == 7, "'Station' with no reminder text (Bird-of-Prey) stations", (c, d, s))
            check(s.get("station_at") == 8, "…and reads its threshold off '8+ | Flying'", s)
            t.hand(M, "Open Communications")
            t.lands(M, 3, "Island")
            c, d = t.act(M, "cast", name="Open Communications")
            s = t.perm(M, "Romulan Bird-of-Prey")
            check(c == 200 and s["charge"] == 9 and s["pt"] == "4/4" and "Flying" in s["keywords"], "its 'whenever you cast a noncreature spell' adds two charge, and at 8+ it flies", (c, d, s))

            print("Deep Space 9: a charge line counts only once reached")
            vp = player.VirtualPlayer(DECK_MICHAEL, "U", seed=1)
            c9 = next(c for c in vp.library if c["name"] == "Deep Space 9")
            h9 = next(c for c in vp.library if c["name"] == "Xindi Surveyors")
            p9, ph = player.Perm(c9), player.Perm(h9, sick=False)
            vp.battlefield = [p9, ph]
            check(vp.stats(ph) == (3, 2), "9+ | Creatures you control get +3/+3 does nothing at 0 charge", vp.stats(ph))
            p9.charge = 9
            check(vp.stats(ph) == (6, 5), "…and applies at 9", vp.stats(ph))
            check(not p9.is_("Creature"), "Deep Space 9 prints no power, so it never becomes a creature")

            print("Equip (G4-5, G4-6, priority 2)")
            t.turn(M)
            t2 = t.onto(M, "Tricorder")
            crea = t.perm(M, "Xindi Surveyors")
            base = crea["pt"]
            pw0, tg0 = [int(v) for v in base.split("/")]
            t.lands(M, 2, "Forest")
            lg = [a for a in t.legal(M)["actions"] if a["action"] == "equip"]
            check(any(a["body"]["equipment"] == f"#{t2}" for a in lg), "the legal-moves route offers Equip", (lg[:1], {k: t.call("GET", "/api/phase")[1].get(k) for k in ("player", "step", "begun", "error")}, t.legal(M)))
            src0 = t.state(M)["mana_sources"]
            c, d = t.act(M, "equip", equipment=f"#{t2}", creature=f"#{crea['id']}")
            pm = t.perm(M, "Xindi Surveyors")
            check(c == 200 and pm["pt"] == f"{pw0 + 1}/{tg0 + 1}" and "Tricorder" in pm["attached_names"], "Equip {1} through the seat key: +1/+1 while attached (cost paid)", (c, d, pm))
            check(len(t.state(M)["mana_sources"]) == len(src0) - 1, "…the {1} really tapped a land", (len(src0), len(t.state(M)["mana_sources"])))
            pub = next(p for p in t.board(M)["permanents"] if p["id"] == crea["id"])
            check(pub["pt"] == pm["pt"] and "Tricorder" in pub["attached_names"], "the public board shows it", pub)
            tr = t.perm(M, "Tricorder")
            check(tr["attached_to"] == crea["id"], "the Equipment knows what it's on", tr)
            c, d = t.act(M, "equip", equipment=f"#{t2}", creature=f"#{crea['id']}")
            check(c != 200 and "already" in str(d.get("error")), "re-equipping the same creature is refused, nothing paid", (c, d))
            other = t.perm(M, "Horta")
            c, d = t.act(M, "equip", equipment=f"#{t2}", creature=f"#{other['id']}")
            check(c == 200 and t.perm(M, "Xindi Surveyors")["pt"] == base and "Tricorder" in t.perm(M, "Horta")["attached_names"], "equipping another creature moves it (and pays again)", (c, d))
            land_id = next(p["id"] for p in t.state(M)["permanents"] if "Land" in (p["type"] or ""))
            c, d = t.act(M, "equip", equipment=f"#{t2}", creature=f"#{land_id}")
            check(c != 200, "a land can't be equipped", (c, d))
            # no mana left
            for p_ in t.state(M)["permanents"]:
                if not p_["tapped"] and ("Land" in (p_["type"] or "") or "{T}: Add" in (p_["text"] or "")):
                    t.call("POST", "/api/brain/tap", {"seat": M, "ref": f"#{p_['id']}", "quiet": True})
            c, d = t.act(M, "equip", equipment=f"#{t2}", creature=f"#{crea['id']}")
            check(c != 200 and "can't pay" in str(d.get("error")) and t.perm(M, "Tricorder")["attached_to"] == other["id"], "with no mana it's refused, and stays where it was", (c, d))
            t.act(M, "next")
            t.next_to(M, "beginning of combat")
            c, d = t.act(M, "equip", equipment=f"#{t2}", creature=f"#{crea['id']}")
            check(c != 200 and "sorcery" in str(d.get("error")), "Equip is refused out of the main phase (sorcery timing)", (c, d))

            # the creature dies: the Equipment stays
            t.turn(M)
            t.call("POST", "/api/brain/destroy", {"seat": M, "ref": f"#{other['id']}", "quiet": True})
            tr = t.perm(M, "Tricorder")
            check(tr is not None and tr["attached_to"] is None, "the creature leaves: the Equipment stays on the battlefield, unattached", tr)
            lg = [a for a in t.legal(M)["actions"] if a["action"] == "equip" and a["body"]["equipment"] == f"#{t2}"]
            check(bool(lg), "…and can be equipped again", lg[:1])

            # cast from hand; stats in real combat; an attack trigger
            t.hand(M, "Type-2 Phaser")
            t.lands(M, 6, "Mountain")
            foe = t.onto(C, "Tal Shiar Operative")
            c, d = t.act(M, "cast", name="Type-2 Phaser", targets=[{"at": C, "ref": f"#{foe}"}])
            fo = t.perm(C, "Tal Shiar Operative")
            check(c == 200 and fo["tapped"] and fo["stun"] == 1, "Type-2 Phaser cast from hand, aimed: taps and stuns the target", (c, d, fo))
            ph2 = t.perm(M, "Type-2 Phaser")
            c, d = t.act(M, "equip", equipment=f"#{ph2['id']}", creature=f"#{crea['id']}")
            c1, d1 = t.act(M, "equip", equipment=f"#{t2}", creature=f"#{crea['id']}")
            pm = t.perm(M, "Xindi Surveyors")
            check(c == 200 and c1 == 200 and pm["pt"] == f"{pw0 + 4}/{tg0 + 1}", "two Equipment stack: +3/+0 and +1/+1", (c, d, c1, d1, pm))
            life0 = t.lifeof(C)
            t.act(M, "attack", assign={f"#{crea['id']}": C})
            c, d = t.act(M, "attack", assign={f"#{crea['id']}": C})
            check(c != 200, "it can't attack twice", (c, d))
            st_ = t.state(M)
            check(st_.get("pending_surveil") == 1 and st_.get("surveil_cards"), "Tricorder's 'whenever equipped creature attacks, surveil 1' waits on the player", st_.get("pending_surveil"))
            top = st_["surveil_cards"][0]
            lib0 = st_["library"]
            c, d = t.act(M, "surveil", graveyard=[top])
            st_ = t.state(M)
            check(c == 200 and top in st_["graveyard"] and st_["library"] == lib0 - 1 and not st_.get("pending_surveil"), "…and the surveil puts the card in the graveyard", (c, d))
            c, d = t.act(C, "block", attacker=f"#{crea['id']}")
            check(c == 200 and t.lifeof(C) == life0 - (pw0 + 4), "the equipped power is the damage dealt in real combat", (life0, t.lifeof(C), d))

            print("Stun (Type-2 Phaser)")
            t.turn(C)
            fo = t.perm(C, "Tal Shiar Operative")
            check(fo["tapped"] and fo["stun"] == 0, "a stun counter: it stayed tapped through its untap, and the counter came off", fo)
            t.turn(M)
            t.turn(C)
            check(not t.perm(C, "Tal Shiar Operative")["tapped"], "…and it untaps the turn after")

            print("Combadge (G4-5): copy on cast, the rider")
            t.turn(M)
            t.lands(M, 6, "Plains")
            t.hand(M, "Combadge")
            c, d = t.act(M, "cast", name="Combadge")
            bad = t.perms(M, "Combadge")
            check(c == 200 and len(bad) == 2 and sum(1 for b in bad if b["token"]) == 1, "cast from hand: the copy becomes a token", (c, d, bad))
            check(any("copy" in x_ for x_ in d.get("said", [])), "…and the table says so", d.get("said"))
            cre1, cre2 = t.onto(M, "Fugitive Listener"), t.onto(M, "Fugitive Listener")
            t.turn(M)
            t.lands(M, 8, "Plains")
            bad = t.perms(M, "Combadge")
            c1, d1 = t.act(M, "equip", equipment=f"#{bad[0]['id']}", creature=f"#{cre1}")
            blk = t.onto(C, "Mugato")
            c, d = t.act(M, "attack", assign={f"#{cre1}": C})
            check(c == 200 and not any("can't be blocked" in x_ for x_ in d.get("said", [])), "one Communicator alone: no rider", (c1, d1, c, d))
            c, d = t.act(C, "block", attacker=f"#{cre1}", blocker=f"#{blk}")
            check(c == 200, "…so it can be blocked", (c, d))
            t.turn(M)
            t.lands(M, 2, "Plains")
            c2, d2 = t.act(M, "equip", equipment=f"#{bad[1]['id']}", creature=f"#{cre2}")
            live = [i for i in (cre1, cre2) if any(p["id"] == i for p in t.state(M)["permanents"])]
            c, d = t.act(M, "attack", assign={f"#{cre2}": C})
            rid = any("can't be blocked" in x_ for x_ in d.get("said", []))
            if len(live) == 2:
                check(c == 200 and rid, "two creatures with Communicators: the attacker gets 'can't be blocked this turn'", (c2, d2, c, d))
                c, d = t.act(C, "block", attacker=f"#{cre2}", blocker=f"#{blk}")
                check(c != 200 and "can't be blocked" in str(d.get("error")), "…and the table refuses the block", (c, d))
                lg = [a for a in t.legal(C)["actions"] if a["action"] == "block" and a["body"].get("blocker")]
                check(not lg, "…and the legal list doesn't offer one", lg[:1])
                t.act(C, "block", attacker=f"#{cre2}")
            else:
                # the first creature died in its block: the Equipment fell off it, stays on the battlefield and can go back on another
                check(any(p["id"] == bad[0]["id"] and p["attached_to"] is None for p in t.state(M)["permanents"]), "the blocked creature died: its Combadge stayed on the battlefield", t.state(M)["permanents"][:0])
                t.act(C, "block", attacker=f"#{cre2}")
                t.turn(M)
                cre3 = t.onto(M, "Fugitive Listener")
                t.turn(M)
                t.lands(M, 2, "Forest")
                c3, d3 = t.act(M, "equip", equipment=f"#{bad[0]['id']}", creature=f"#{cre3}")
                c, d = t.act(M, "attack", assign={f"#{cre2}": C})
                check(c3 == 200 and c == 200 and any("can't be blocked" in x_ for x_ in d.get("said", [])), "two creatures with Communicators: the attacker gets 'can't be blocked this turn'", (c3, d3, c, d))
                c, d = t.act(C, "block", attacker=f"#{cre2}", blocker=f"#{blk}")
                check(c != 200 and "can't be blocked" in str(d.get("error")), "…and the table refuses the block", (c, d))
                lg = [a for a in t.legal(C)["actions"] if a["action"] == "block" and a["body"].get("blocker")]
                check(not lg, "…and the legal list doesn't offer one", lg[:1])
                t.act(C, "block", attacker=f"#{cre2}")

            print("Neuroflux Heirloom, an Equipment token (Gwyndala)")
            t.turn(M)
            t.lands(M, 2, "Island")
            t.hand(M, "Gwyndala, the Progeny")
            c, d = t.act(M, "cast", name="Gwyndala, the Progeny")
            g = t.perm(M, "Gwyndala, the Progeny")
            tok = t.perm(M, "Neuroflux Heirloom token")
            check(c == 200 and tok and tok["attached_to"] == g["id"] and g["pt"] == "2/2", "its enters trigger makes the token attached: 2/2", (c, d, g, tok))
            check(tok and tok["equip_cost"] == "{2}", "…and the token has its own equip {2}", tok)

            print("Bat'leth: an aimed enters trigger, cast from hand")
            t.lands(M, 3, "Swamp")
            t.hand(M, "Bat'leth")
            vict = t.onto(C, "Fugitive Listener")
            c, d = t.act(M, "cast", name="Bat'leth", targets=[{"at": C, "ref": f"#{vict}"}])
            v2 = next((p for p in t.state(C)["permanents"] if p["id"] == vict), None)
            check(c == 200 and v2 is None, "the -2/-2 applies: a 3/1 at -1 toughness is gone", (c, d, v2))
        finally:
            t.stop()


    def _section_02():
        print("Next step, the step bar's server side (G4-7, priority 3)")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            c, d = t.act(C, "next")
            check(c != 200, "Next is refused off your own turn", (c, d))
            ph = t.call("GET", "/api/phase")[1]
            check(ph["step"] == "main 1", "it's at main 1", ph["step"])
            lg = [a for a in t.legal(M)["actions"] if a["action"] == "next"]
            check(lg and "beginning of combat" in lg[0]["why"], "the legal route offers it", lg[:1])
            t.lands(M, 2, "Plains")
            t.hand(M, "Set Phasers to . . .")
            foe = t.onto(C, "Fugitive Listener")
            t.hand(M, "Plains")
            c, d = t.act(M, "next")
            ph = t.call("GET", "/api/phase")[1]
            check(c == 200 and ph["step"] == "beginning of combat" and ph["player"] == M, "one press, one step: beginning of combat", (c, d, ph["step"]))
            c, d = t.act(M, "land", name="Plains")
            check(c != 200 and "main 2" in str(d.get("error")), "a land here is refused rather than silently skipping the attack", (c, d))
            c, d = t.act(M, "cast", name="Set Phasers to . . .", modes=[1], targets=[{"at": C, "ref": f"#{foe}"}])
            ph = t.call("GET", "/api/phase")[1]
            check(c == 200 and ph["step"] == "beginning of combat", "an instant at the start of combat stays at the start of combat", (c, d, ph["step"]))
            check(t.perm(C, "Fugitive Listener")["stun"] == 1, "…and did what it says", t.perm(C, "Fugitive Listener"))
            steps = []
            for _ in range(6):
                t.act(M, "next")
                steps.append(t.call("GET", "/api/phase")[1]["step"])
            check(steps[:5] == ["declare attackers", "declare blockers", "combat damage", "main 2", "end step"], "it walks the real steps in order", steps)
            ph = t.wait(lambda p: p.get("player") == C and p.get("begun"), 15)
            check(ph["player"] == C, "the last press hands the turn on", ph)
            # lifelink through Next off combat damage
            t.turn(M)
            brad = t.onto(M, "Brad Boimler, Eager Ensign")
            t.turn(M)
            life0 = t.lifeof(M)
            foelife = t.lifeof(C)
            t.act(M, "attack", assign={f"#{brad}": C})
            t.act(C, "block", attacker=f"#{brad}")
            ph = t.call("GET", "/api/phase")[1]
            c, d = t.act(M, "next")
            check(c == 200 and t.lifeof(M) == life0 + 2 and t.lifeof(C) == foelife - 2, "Next off combat damage still resolves the hit: damage and lifelink, printed on the card", (ph["step"], c, d, t.lifeof(M), t.lifeof(C)))
            c, d = t.act(M, "damage")
            check(t.lifeof(M) == life0 + 2, "…and only once", (c, d, t.lifeof(M)))
        finally:
            t.stop()


    def _section_03():
        print("Public view (G4-8)")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            xs = t.onto(M, "Xindi Surveyors")
            t.hand(M, "Plains")
            t.act(M, "land", name="Plains")
            st = t.perm(M, "Xindi Surveyors")
            check(st["counters"] == 1 and st["pt"] == "4/3", "a seat's own view has its +1/+1 counters", st)
            sar = t.onto(M, "Sarcophagus, Ship of the Dead")
            s = t.perm(M, "Sarcophagus, Ship of the Dead")
            check("10+ | Flying" in s["text"], "and the full text (Sarcophagus's '10+ | Flying' used to be cut at 300 characters)", s["text"][-80:])
            check("10+ | Flying" in next(p for p in t.board(M)["permanents"] if p["id"] == sar)["text"], "the public board too")

            print("Sarcophagus (G4-6): a creature dies, anywhere")
            doomed = t.onto(C, "Tal Shiar Operative")
            t.call("POST", "/api/brain/destroy", {"seat": C, "ref": f"#{doomed}", "quiet": True})
            check(t.perm(M, "Sarcophagus, Ship of the Dead")["charge"] == 1, "an opponent's creature dying puts a charge counter on it", t.perm(M, "Sarcophagus, Ship of the Dead")["charge"])
            horta = t.onto(M, "Horta")
            c, d = t.act(M, "station", ship=f"#{sar}", crew=f"#{horta}")
            check(t.perm(M, "Sarcophagus, Ship of the Dead")["charge"] == 9, "Station adds to it", d)
            doomed = t.onto(M, "Tal Shiar Operative")
            t.call("POST", "/api/brain/destroy", {"seat": M, "ref": f"#{doomed}", "quiet": True})
            s = t.perm(M, "Sarcophagus, Ship of the Dead")
            check(s["charge"] == 10 and s["pt"] == "4/4" and "Flying" in s["keywords"], "my own creature dying counts too: 10, a 4/4 flier", s)
            doomed = t.onto(C, "Tal Shiar Operative")
            t.call("POST", "/api/brain/destroy", {"seat": C, "ref": f"#{doomed}", "quiet": True})
            check(t.perm(M, "Sarcophagus, Ship of the Dead")["charge"] == 10, "'if Sarcophagus isn't a creature': no more once it is", t.perm(M, "Sarcophagus, Ship of the Dead")["charge"])
            check(t.perm(M, "Horta")["pt"] == "8/6", "and it gives my other creatures +1/+0 (with no charge at all)", t.perm(M, "Horta"))
        finally:
            t.stop()


    def _section_04():
        print("Landfall as damage (G4-1), land tokens (G4-3)")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            t.lands(M, 3, "Forest")
            t.hand(M, "Captain Michael Burnham")
            t.lands(M, 3, "Mountain")
            c, d = t.act(M, "cast", name="Captain Michael Burnham")
            check(c == 200, "Burnham cast from hand", (c, d))
            l0, ev0 = t.lifeof(C), t.call("GET", "/api/events?since=latest", host=False)[1].get("last") or 0
            t.hand(M, "Forest")
            c, d = t.act(M, "land", name="Forest")
            b0 = t.perm(M, "Captain Michael Burnham")
            check(c == 200 and t.lifeof(C) == l0 - 1 and b0["counters"] == 0, "a land played from hand: Burnham deals 1 to each opponent, no counter on the first", (c, d, t.lifeof(C), b0))
            evs = t.events(ev0)
            check(any(e.get("type") == "life" and e.get("player") == C and e.get("delta") == -1 for e in evs), "…as a life event every page sees", [e for e in evs if e.get("type") == "life"])
            check(any(e.get("type") == "attention" and e.get("addressee") == C and "no need to change it" in (e.get("text") or "") for e in evs), "…and the seat is told the table took it off", [e.get("text") for e in evs if e.get("type") == "attention"])
            t.hand(M, "David and Carol Marcus, Creators")
            t.lands(M, 3, "Forest") if False else None
            l1 = t.lifeof(C)
            c, d = t.act(M, "cast", name="David and Carol Marcus, Creators")
            b1 = t.perm(M, "Captain Michael Burnham")
            planet = t.perm(M, "New Planet token")
            check(c == 200 and planet and planet["tapped"] and "Land" in (planet["type"] or ""), "David and Carol's New Planet: a real land token, tapped", (c, d, planet))
            check(t.lifeof(C) == l1 - 1 and b1["counters"] == 1, "…and it's a land entering: Burnham's second Landfall this turn: 1 more damage and a +1/+1 counter", (t.lifeof(C), b1))
            # New Planet makes mana of any colour
            t.turn(C)
            t.turn(M)
            for p in t.state(M)["permanents"]:
                if "Land" in (p["type"] or "") and p["name"] != "New Planet token":
                    t.call("POST", "/api/brain/destroy", {"seat": M, "ref": f"#{p['id']}", "quiet": True})
            src = t.state(M)["mana_sources"]
            check(len(src) == 1 and "New Planet" in src[0] and all(k in src[0] for k in "WUBRG"), "next turn New Planet untaps and taps for any colour; David and Carol, who only make it, tap for nothing", src)
            t.hand(M, "Moopsy")
            c, d = t.act(M, "cast", name="Moopsy")
            check(c == 200 and t.perm(M, "Moopsy"), "…and pays for a spell", (c, d))
            t.hand(M, "Plains")
            b2 = t.perm(M, "Captain Michael Burnham")["counters"]
            t.act(M, "land", name="Plains")
            check(t.perm(M, "Captain Michael Burnham")["counters"] == b2, "a new turn starts the resolution count again", t.perm(M, "Captain Michael Burnham"))
        finally:
            t.stop()
        # a person's own real-deck seat: announced, not touched

    def _section_05():
        t = Table(human=True, extra=["--human-pass-secs", "0.2"])
        try:
            t.start()
            M, C = "Michael", "Claude"
            t.lands(M, 3, "Mountain")
            t.hand(M, "Captain Michael Burnham")
            t.act(M, "cast", name="Captain Michael Burnham")
            sam0 = t.lifeof("Sam")
            cl0 = t.lifeof(C)
            last = t.call("GET", "/api/events?since=latest", host=False)[1].get("last") or 0
            t.hand(M, "Forest")
            c, d = t.act(M, "land", name="Forest")
            evs = t.events(last)
            check(c == 200 and t.lifeof("Sam") == sam0, "a person's own (real-deck) seat: the table does NOT touch their life", (c, d, t.lifeof("Sam")))
            check(t.lifeof(C) == cl0 - 1, "…an engine seat at the same table still takes it", (cl0, t.lifeof(C)))
            check(any(e.get("addressee") == "Sam" and "mark it yourself" in (e.get("text") or "") for e in evs), "…and the person is told, in words", [e.get("text") for e in evs if e.get("type") == "attention"])
        finally:
            t.stop()

    def _section_06():
        t = Table(extra=["--ping-damage", "announce"])
        try:
            t.start()
            M, C = "Michael", "Claude"
            t.lands(M, 3, "Mountain")
            t.hand(M, "Captain Michael Burnham")
            t.act(M, "cast", name="Captain Michael Burnham")
            cl0 = t.lifeof(C)
            t.hand(M, "Forest")
            c, d = t.act(M, "land", name="Forest")
            check(c == 200 and t.lifeof(C) == cl0, "--ping-damage announce: the table says it and every seat marks its own", (c, d, t.lifeof(C)))
        finally:
            t.stop()


    def _section_07():
        print("A trigger with nothing aimed asks the seat (Michael's call, 2026-11-09); the table waits")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            synd = t.onto(M, "Syndicate Liquidators")
            big = t.onto(M, "Horta")
            small = t.onto(M, "Xindi Surveyors")
            theirs = t.onto(C, "Moopsy")
            t.hand(M, "Plains")
            c, d = t.act(M, "land", name="Plains")
            st = t.state(M)
            pend = st.get("pending_choices") or []
            check(c == 200 and len(pend) == 1, "Landfall with a target: the seat is asked", (c, d))
            check(t.perm(M, "Horta")["counters"] == 0 and t.perm(M, "Syndicate Liquidators")["counters"] == 0, "…and no counter goes anywhere by itself (no 'biggest creature' default)", (t.perm(M, "Horta"), d.get("said")))
            check(not any("biggest" in str(x) for x in d.get("said", [])), "…and nobody's biggest creature is named", d.get("said"))
            opts = pend[0]["slot"]["options"]
            check({o.get("ref") for o in opts} >= {f"#{synd}", f"#{big}", f"#{small}"} and all(o.get("at") == M for o in opts), "the options are the legal ones: my own creatures", opts)
            lg = t.legal(M)
            check(lg["actions"] and all(a["action"] == "choose" for a in lg["actions"]), "the legal list offers only the choice while it waits", [a["action"] for a in lg["actions"]])
            for act_, body in (("next", {}), ("end", {"text": "x"}), ("attack", {"assign": {f"#{big}": C}})):
                c, d = t.act(M, act_, tries=1, **body)
                check(c == 409 and "choose first" in str(d.get("error")), f"the turn waits: {act_} is refused until the seat answers", (c, d.get("error")))
            c, d = t.act(C, "next", tries=1)
            check(c != 200, "and nobody else moves it either", (c, d.get("error")))
            c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": C, "ref": f"#{theirs}"})
            check(c != 200 and t.state(M).get("pending_choices"), "'target creature you control': an opponent's is refused, and the choice still waits", (c, d.get("error")))
            pend = t.state(M)["pending_choices"]
            c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": M, "ref": f"#{small}"})
            check(c == 200 and t.perm(M, "Xindi Surveyors")["counters"] == 3 and t.perm(M, "Horta")["counters"] == 0, "the person's choice, the smaller creature, gets the two counters (+1 from its own Landfall)", (c, d, t.perm(M, "Xindi Surveyors")))
            check(not t.state(M).get("pending_choices"), "…and the choice is gone")
            c, d = t.act(M, "next")
            check(c == 200, "…and the turn moves again", (c, d.get("error")))
            # an enters trigger cast with nothing aimed
            t.turn(M)
            t.lands(M, 3, "Forest")
            t.lands(M, 2, "Plains")
            t.hand(M, "Emergency Medical Hologram")
            life0 = t.lifeof(M)
            c, d = t.act(M, "cast", name="Emergency Medical Hologram")
            pend = t.state(M).get("pending_choices") or []
            check(c == 200 and len(pend) == 1 and t.lifeof(M) == life0 + 1, "an enters trigger cast with nothing aimed asks too; its other sentence happens", (c, d, pend))
            c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": M, "ref": f"#{big}"})
            check(c == 200 and t.perm(M, "Horta")["counters"] == 1, "…and goes where the seat says", (c, d))
            # exactly one legal target: nothing to decide
        finally:
            t.stop()

    def _section_08():
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            t.onto(M, "Syndicate Liquidators")
            t.hand(M, "Plains")
            c, d = t.act(M, "land", name="Plains")
            check(c == 200 and not t.state(M).get("pending_choices") and t.perm(M, "Syndicate Liquidators")["counters"] == 2, "one legal target: nothing to ask, and the table says so", (c, d))
            # a trigger waiting on the OTHER seat holds the active seat
            t.turn(C)
            t.onto(M, "Horta")
            c, d = t.call("POST", "/api/brain/search", {"seat": M, "name": "Forest", "to": "battlefield", "quiet": True})   # a land entering on someone else's turn
            pend = t.state(M).get("pending_choices") or []
            if pend:
                c, d = t.act(C, "end", tries=1, text="x")
                check(c == 409 and "to choose" in str(d.get("error")), "a choice waiting on the other seat holds the active player too", (c, d.get("error")))
                t.act(M, "choose", id=pend[0]["id"], target={"at": M, "ref": pend[0]["slot"]["options"][0]["ref"]})
            else:
                check(False, "a choice waiting on the other seat holds the active player too", "nothing pending")
        finally:
            t.stop()

    def _section_09():
        print("A question nobody can answer must not hold the game")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            for n in ("Plains", "Plains", "Swamp", "Swamp"):
                t.onto(M, n)
            for h in t.state(C)["hand"]:
                t.call("POST", "/api/brain/discard", {"seat": C, "name": h["name"], "quiet": True})
            check(not t.state(C)["hand"], "(set-up: the opponent's hand is empty)", len(t.state(C)["hand"]))
            t.hand(M, "Fugitive Listener")
            c, d = t.act(M, "cast", name="Fugitive Listener")
            check(c == 200 and not t.state(C).get("pending_choices"), "'each opponent discards' with an empty hand: nothing is left asking", (c, d, t.state(C).get("pending_choices")))
            c, d = t.act(M, "next")
            check(c == 200, "…and the turn moves on", (c, d.get("error")))
        finally:
            t.stop()

    def _section_10():
        print("An engine-run seat, which can't be asked")
        vp = player.VirtualPlayer(DECK_MICHAEL, "AI", seed=4)
        vp.turn = 1
        syn = player.Perm(next(c for c in vp.library if c["name"] == "Syndicate Liquidators"), sick=False)
        hrt = player.Perm(next(c for c in vp.library if c["name"] == "Horta"), sick=False)
        vp.battlefield = [syn, hrt]
        said = vp.land_entered(player.Perm(next(c for c in vp.library if c["name"] == "Forest"), sick=False))
        check(hrt.counters == 2 and any("biggest" in x and "Horta" in x for x in said), "it takes its biggest creature and announces it", said)


    def _section_11():
        print("Sagas (27 lines): lore counters, chapters, the end")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            for n in ("Forest", "Forest", "Island", "Mountain", "Mountain", "Plains", "Swamp", "Swamp"):
                t.onto(M, n)
            t.hand(M, "Year of Hell")
            h0, ch0, l0, cl0 = len(t.state(M)["hand"]), len(t.state(C)["hand"]), t.lifeof(M), t.lifeof(C)
            c, d = t.act(M, "cast", name="Year of Hell")
            y = t.perm(M, "Year of Hell")
            check(c == 200 and y and y["lore"] == 1, "it enters with a lore counter", (c, d, y))
            check(len(t.state(C)["hand"]) == ch0 + 1 and t.lifeof(M) == l0 - 2 and t.lifeof(C) == cl0 - 2, "chapter I happens: each player draws, and 2 damage to each player (the caster too)", (t.lifeof(M), t.lifeof(C)))
            t.turn(M)
            y = t.perm(M, "Year of Hell")
            check(y and y["lore"] == 2 and t.lifeof(M) == l0 - 4 and "lore" in str(next(p for p in t.board(M)["permanents"] if p["name"] == "Year of Hell")), "the next turn of its controller, chapter II", (y, t.lifeof(M)))
            t.turn(C)
            check(t.perm(M, "Year of Hell")["lore"] == 2, "the opponent's turn adds none", t.perm(M, "Year of Hell"))
            t.turn(M)
            check(t.perm(M, "Year of Hell") is None and "Year of Hell" in t.state(M)["graveyard"] and t.lifeof(M) == l0 - 6, "after its last chapter it goes to the graveyard", (t.state(M)["graveyard"], t.lifeof(M)))

            print(" Amok Time: a fight, asked one target at a time; a card from the graveyard")
            mine = t.onto(M, "Horta")
            foe = t.onto(C, "Xindi Surveyors")
            t.call("POST", "/api/brain/search", {"seat": M, "name": "Fugitive Listener"})
            t.call("POST", "/api/brain/discard", {"seat": M, "name": "Fugitive Listener", "quiet": True})
            t.call("POST", "/api/brain/search", {"seat": M, "name": "Moopsy"})
            t.call("POST", "/api/brain/discard", {"seat": M, "name": "Moopsy", "quiet": True})
            t.hand(M, "Amok Time")
            c, d = t.act(M, "cast", name="Amok Time")
            pend = t.state(M).get("pending_choices") or []
            if pend and pend[0]["slot"].get("side") == "own" and len(pend[0]["slot"]["options"]) >= 1:
                c1, d1 = t.act(M, "choose", id=pend[0]["id"], target={"at": M, "ref": f"#{mine}"})
            pend = t.state(M).get("pending_choices") or []
            check(pend and any(o.get("ref") == f"#{foe}" for o in pend[0]["slot"]["options"]) and t.perm(C, "Xindi Surveyors"), "chapter I asks for my creature, then what it fights", pend)
            c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": C, "ref": f"#{foe}"})
            ho = t.perm(M, "Horta")
            check(c == 200 and t.perm(C, "Xindi Surveyors") is None and ho and ho["damage"] == 3, "they fight: each deals its power to the other", (c, d, ho))
            t.turn(M)
            l1 = t.lifeof(M)
            check(t.perm(M, "Amok Time")["lore"] == 2, "chapter II", t.perm(M, "Amok Time"))
            t.turn(M)
            pend = t.state(M).get("pending_choices") or []
            creature_cards = [o for o in (pend[0]["slot"]["options"] if pend else [])]
            check(pend and creature_cards and all(o.get("at") == M for o in creature_cards) and any(o.get("card") == "Fugitive Listener" for o in creature_cards), "chapter III offers the creature cards in my graveyard", pend)
            c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": M, "card": "Fugitive Listener"})
            check(c == 200 and any(h["name"] == "Fugitive Listener" for h in t.state(M)["hand"]) and "Amok Time" in t.state(M)["graveyard"], "…it returns to my hand, and the Saga is done", (c, d))
            c, d = t.act(M, "choose", target={"at": M, "card": "Horta"})
            check(c != 200, "with nothing waiting, choose is refused", (c, d))
        finally:
            t.stop()

    def _section_12():
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            for n in ("Swamp", "Swamp", "Swamp", "Mountain", "Mountain", "Plains", "Plains", "Plains"):
                t.onto(M, n)
            print(" In the Pale Moonlight: the OPPONENT chooses")
            g1, g2 = t.onto(C, "Fugitive Listener"), t.onto(C, "Horta")
            t.onto(C, "Plains")
            t.hand(M, "In the Pale Moonlight")
            c, d = t.act(M, "cast", name="In the Pale Moonlight")
            pend = t.state(C).get("pending_choices") or []
            check(c == 200 and pend and {o["ref"] for o in pend[0]["slot"]["options"]} == {f"#{g1}", f"#{g2}"}, "each opponent is asked, among its own nontoken creatures (the caster doesn't pick)", (c, d, pend))
            c2, d2 = t.act(M, "next", tries=1)
            check(c2 == 409 and "to choose" in str(d2.get("error")), "the turn waits for the opponent's answer", (c2, d2.get("error")))
            c, d = t.act(C, "choose", id=pend[0]["id"], target={"at": C, "ref": f"#{g1}"})
            check(c == 200 and t.perm(C, "Fugitive Listener") is None and t.perm(C, "Horta"), "it sacrifices what it chose", (c, d))
            t.turn(M)
            check(t.perm(M, "Clue token"), "chapter II: Investigate makes a Clue", t.state(M)["permanents"][-1])
            vict = t.onto(M, "Fugitive Listener")
            t.turn(M)
            pend = t.state(M).get("pending_choices") or []
            check(pend and pend[0]["slot"].get("optional"), "chapter III: 'you may sacrifice' can be declined", pend)
            c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": M, "ref": f"#{vict}"})
            check(c == 200 and t.perm(M, "Fugitive Listener") is None and len(t.perms(M, "Romulan token")) == 2, "…and if I do, the 'if you do' happens: two Romulans", (c, d, [p["name"] for p in t.state(M)["permanents"]][-4:]))

            print(" The Measure of a Man: exile, then bring it back")
            ex = t.onto(C, "Horta") if False else t.perm(C, "Horta")["id"]
            t.lands(M, 3, "Plains")
            t.hand(M, "The Measure of a Man")
            c, d = t.act(M, "cast", name="The Measure of a Man")
            pend = t.state(M).get("pending_choices") or []
            c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": C, "ref": f"#{ex}"})
            check(c == 200 and t.perm(C, "Horta") is None, "chapter I exiles its target", (c, d))
            t.turn(M)
            t.turn(M)
            lf = t.perm(C, "Horta")
            check(lf is not None and t.perm(M, "The Measure of a Man") is None, "chapter III returns it to its owner", (lf, t.state(C)["permanents"][-1]))

            print(" The Naked Time; Those Old Scientists; The Wrath of Khan")
            t.lands(M, 2, "Mountain")
            xs = t.onto(M, "Xindi Surveyors")
            c0 = t.perm(M, "Xindi Surveyors")["counters"]
            t.hand(M, "The Naked Time")
            n0 = len(t.perms(M, "Mountain"))
            last = t.call("GET", "/api/events?since=latest", host=False)[1].get("last") or 0
            c, d = t.act(M, "cast", name="The Naked Time")
            check(c == 200 and len(t.perms(M, "Mountain")) == n0 + 1 and t.perms(M, "Mountain")[-1]["tapped"] and t.perm(M, "Xindi Surveyors")["counters"] == c0 + 1, "chapter I fetches a Mountain, tapped, and that's Landfall", (c, d))
            t.turn(M)
            ev = t.events(last)
            check(any(e.get("type") == "table-note" and "chapter 2" in (e.get("text") or "") for e in ev), "chapter II, which the table can't do, says so", [e.get("text") for e in ev if e.get("type") == "table-note"])
            lands0 = sum(1 for p in t.state(M)["permanents"] if "Land" in (p["type"] or ""))
            t.turn(M)
            pend = t.state(M).get("pending_choices") or []
            check(pend and pend[0]["slot"].get("kinds") == ["Land"] and not pend[0]["slot"].get("optional"), "chapter III: sacrifice a land is mandatory, and I choose which", pend)
            c, d = t.act(M, "choose", id=pend[0]["id"], target={k: v for k, v in pend[0]["slot"]["options"][0].items() if k != "label"})
            check(c == 200 and sum(1 for p in t.state(M)["permanents"] if "Land" in (p["type"] or "")) == lands0 - 1, "…and it goes", (c, d))
            t.lands(M, 3, "Island")
            t.hand(M, "Those Old Scientists")
            t.hand(M, "Plains")
            hb = len(t.state(M)["hand"])
            c, d = t.act(M, "cast", name="Those Old Scientists")
            st = t.state(M)
            pend = st.get("pending_choices") or []
            check(c == 200 and len(pend) == 2 and len(st["hand"]) == hb - 1 + 2 and all(o.get("card") for o in pend[0]["slot"]["options"]), "chapter I: draws two, then asks twice which to discard, from my own hand", (c, d, len(pend), len(st["hand"])))
            names = [o["card"] for o in pend[0]["slot"]["options"]]
            b3 = json.dumps(t.call("GET", "/api/board3d", host=False)[1]) + json.dumps(t.state(C).get("opponents", {}).get(M)) + json.dumps(t.call("GET", "/api/events?since=0", host=False)[1])
            check(not any(f'"card": "{n}"' in b3 for n in names), "the hand options are in no public view, nor in what the other seat sees of me", names[:2])
            c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": M, "card": names[0]})
            check(c == 200 and names[0] in t.state(M)["graveyard"], "the first discard is what I named", (c, d))
            pend = t.state(M)["pending_choices"]
            c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": M, "card": pend[0]["slot"]["options"][0]["card"]})
            check(c == 200 and not t.state(M).get("pending_choices") and len(t.state(M)["hand"]) == hb - 1, "two in all, and the hand is two down from the draw", (c, d, len(t.state(M)["hand"])))
        finally:
            t.stop()


    def _section_13():
        print("Beam me up (10 lines), and searching a library")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            for n in ("Mountain", "Mountain", "Mountain", "Mountain", "Mountain", "Mountain", "Forest", "Forest", "Forest", "Plains", "Plains", "Plains"):
                t.onto(M, n)
            me = t.onto(M, "Xindi Surveyors")
            t.hand(M, "Transporter Malfunction")
            t.call("POST", "/api/brain/discard", {"seat": M, "name": "Transporter Malfunction", "quiet": True})
            st = t.state(M)
            b = next((x for x in st.get("beam_cards") or [] if x["name"] == "Transporter Malfunction"), None)
            check(b and b["cost"] == "{3}{R}{R}{R}" and b["payable"], "a card with Beam me up in the graveyard is offered, with its cost", b)
            lg = [a_ for a_ in t.legal(M)["actions"] if a_["action"] == "cast" and a_["body"].get("beam")]
            check(lg and lg[0]["body"]["name"] == "Transporter Malfunction", "the legal list offers it", lg[:1])
            foe = t.onto(C, "Fugitive Listener")
            c, d = t.act(M, "cast", name="Transporter Malfunction", beam=f"#{t.perm(M, 'Mountain')['id']}", targets=[{"at": C, "ref": f"#{foe}"}])
            check(c != 200 and "Transporter Malfunction" in t.state(M)["graveyard"], "it must return a creature: a land is refused, nothing paid", (c, d))
            hand0 = len(t.state(M)["hand"])
            c, d = t.act(M, "cast", name="Transporter Malfunction", beam=f"#{me}", targets=[{"at": C, "ref": f"#{foe}"}])
            st = t.state(M)
            check(c == 200 and t.perm(C, "Fugitive Listener") is None, "cast from the graveyard, it does what it says", (c, d))
            check(t.perm(M, "Xindi Surveyors") is None and any(h["name"] == "Xindi Surveyors" for h in st["hand"]) and len(st["hand"]) == hand0 + 1, "the creature goes back to my hand", st["permanents"][-1])
            check("Transporter Malfunction" not in st["graveyard"] and not any(x["name"] == "Transporter Malfunction" for x in st.get("beam_cards") or []), "the spell is exiled: not in the graveyard, not offered again", st["graveyard"])
            check(len(st["mana_sources"]) <= 6, "the cost was paid", st["mana_sources"])
            c, d = t.act(M, "cast", name="Transporter Malfunction", beam="#1")
            check(c != 200, "there's no casting it twice", (c, d))
            t.turn(M)
            # search, on the page's terms: the seat picks, from its own library, and nobody else can see the list
            t.hand(M, "Federation Resupply")
            c, d = t.act(M, "cast", name="Federation Resupply")
            pend = t.state(M).get("pending_choices") or []
            if c != 200:
                hs = next(h for h in t.state(M)["hand"] if h["name"] == "Federation Resupply")["slots"]
                check(any(o["card"] == "Combadge" for o in hs[0]["options"]), "the search offers what the card allows", hs[0]["options"][:3])
                c, d = t.act(M, "cast", name="Federation Resupply", targets=[{"at": M, "card": "Combadge"}])
            else:
                c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": M, "card": "Combadge"})
            check(c == 200 and any(h["name"] == "Combadge" for h in t.state(M)["hand"]), "search for an Aura, Equipment or Spacecraft: the seat picks", (c, d))
            t.hand(M, "Final Frontier")
            lib0 = t.state(M)["library"]
            h3 = next(h for h in t.state(M)["hand"] if h["name"] == "Final Frontier")["slots"]
            names = {o["card"] for o in (h3[0].get("options") or [])}
            check({"Forest", "Plains", "Island"} <= names and "Horta" not in names, "Final Frontier offers basic lands and Planets, not any card", sorted(names)[:8])
            xs = t.onto(M, "Xindi Surveyors")
            c, d = t.act(M, "cast", name="Final Frontier", targets=[{"at": M, "card": "Island"}])
            isl = t.perms(M, "Island")
            check(c == 200 and isl and isl[-1]["tapped"], "…onto the battlefield tapped", (c, d))
            check(t.perm(M, "Xindi Surveyors")["counters"] == 1, "…and it's a land entering: Landfall", t.perm(M, "Xindi Surveyors"))
            # Pure Energy: both counters on one, or one each
            t.turn(M)
            h1, h2 = t.perm(M, "Xindi Surveyors")["id"], t.onto(M, "Horta")
            t.hand(M, "Pure Energy")
            c, d = t.act(M, "cast", name="Pure Energy", targets=[{"at": M, "ref": f"#{h1}"}, {"at": M, "ref": f"#{h2}"}])
            check(c == 200 and t.perm(M, "Horta")["counters"] == 1, "distribute two counters: one each", (c, d))
            t.hand(M, "Pure Energy")
            c0 = t.perm(M, "Horta")["counters"]
            c, d = t.act(M, "cast", name="Pure Energy", targets=[{"at": M, "ref": f"#{h2}"}, {}])
            check(c == 200 and t.perm(M, "Horta")["counters"] == c0 + 2, "…or both on one", (c, d))
        finally:
            t.stop()


    def _section_14():
        print("Dilemma (9 lines): choosing a mode")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            for n in ("Plains", "Plains", "Plains", "Plains"):
                t.onto(M, n)
            m1 = t.onto(M, "Melkotian Adjudicator")
            t.onto(M, "Elim Garak, Simple Tailor")
            t.onto(M, "Obsidian Order Operative")
            t.onto(M, "Seven of Nine")
            l0, c0, h0 = t.lifeof(M), t.lifeof(C), len(t.state(M)["hand"])
            t.hand(M, "Command Decision")
            c, d = t.act(M, "cast", name="Command Decision", modes=[2])
            st = t.state(M)
            check(c == 200 and t.perm(M, "Melkotian Adjudicator")["counters"] == 1, "casting a modal spell is a dilemma: +1/+1 counter", (c, d))
            check(t.lifeof(C) == c0 - 2 - 1 and t.lifeof(M) == l0 + 2 + 1, "Garak drains 2, the Operative drains 1, through the one writer of each life total", (t.lifeof(M), t.lifeof(C)))
            check(len(st["hand"]) == h0 + 1 - 1 + 1 + 1, "Seven of Nine draws; Command Decision's own mode draws too", len(st["hand"]))
            t.hand(M, "Open Communications")
            cn = t.perm(M, "Melkotian Adjudicator")["counters"]
            for n in ("Island", "Island"):
                t.onto(M, n)
            c, d = t.act(M, "cast", name="Open Communications")
            check(c == 200 and t.perm(M, "Melkotian Adjudicator")["counters"] == cn, "a spell with no modes is no dilemma", (c, d))
            # an enters trigger with modes, a land with modes, an attack with modes
            t.turn(M)
            t.onto(M, "Plains")
            cn = t.perm(M, "Melkotian Adjudicator")["counters"]
            t.hand(M, "Federation Outpost")
            c, d = t.act(M, "land", name="Federation Outpost")
            check(c == 200 and t.perm(M, "Melkotian Adjudicator")["counters"] == cn + 1 and not t.perm(M, "Federation Outpost")["tapped"], "a land that enters untapped with 'choose one' is a dilemma", (c, d))
            ts = t.onto(M, "Tal Shiar Operative")
            t.turn(M)
            cn = t.perm(M, "Melkotian Adjudicator")["counters"]
            c, d = t.act(M, "attack", assign={f"#{ts}": C})
            check(c == 200 and t.perm(M, "Melkotian Adjudicator")["counters"] == cn + 1, "an attack trigger with 'choose one' is a dilemma", (c, d))
            t.settle_setup()                             # (v6c, M5: where the table ASKS the attack's "choose one", it is answered before blocks)
            t.act(C, "block", attacker=f"#{ts}")
            # the card in the graveyard: said, not silently dropped
            t.turn(M)
            t.hand(M, "Gabriel Lorca, Mirror Menace")
            t.call("POST", "/api/brain/discard", {"seat": M, "name": "Gabriel Lorca, Mirror Menace", "quiet": True})
            last = t.call("GET", "/api/events?since=latest", host=False)[1].get("last") or 0
            t.hand(M, "Command Decision")
            c, d = t.act(M, "cast", name="Command Decision", modes=[2])
            notes = [e["text"] for e in t.events(last) if e.get("type") == "table-note"]
            check(c == 200 and any("Gabriel Lorca" in n and "graveyard" in n for n in notes), "Gabriel Lorca's graveyard trigger says so at the table", notes)
        finally:
            t.stop()


    def _section_15():
        print("Step triggers, life/draw/enter triggers, more abilities (batch 4)")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            for n in ("Plains", "Plains", "Swamp", "Swamp", "Mountain", "Mountain", "Island", "Island", "Forest", "Forest"):
                t.onto(M, n)
            print(" upkeep")
            t.onto(M, "Quark, Humble Proprietor")
            t.onto(M, "Troubling Tribbles")
            t.onto(M, "Vash, Freelance Archaeologist")
            tr0 = len(t.perms(M, "Treasure token"))
            t.turn(C)
            check(len(t.perms(M, "Treasure token")) == tr0 + 1, "'each upkeep' fires on the opponent's upkeep too (Quark)", [p["name"] for p in t.state(M)["permanents"]][-3:])
            tb0 = len(t.perms(M, "Troubling Tribbles"))
            check(tb0 == 1, "'your upkeep' doesn't fire on the opponent's (Tribbles)", tb0)
            t.turn(M)
            st = t.state(M)
            check(len(t.perms(M, "Troubling Tribbles")) == 2 and any(p["token"] for p in t.perms(M, "Troubling Tribbles")), "…and does on mine: a token copy", t.perms(M, "Troubling Tribbles"))
            check(len(t.perms(M, "Treasure token")) == tr0 + 2, "Quark again on mine", len(t.perms(M, "Treasure token")))
            check(st.get("pending_surveil") == 1, "Vash: surveil 1 waits on the player", st.get("pending_surveil"))
            t.act(M, "surveil", graveyard=[])
            print(" Treasure pays, and goes")
            for p_ in t.state(M)["permanents"]:
                if "Land" in (p_["type"] or "") and not p_["tapped"]:
                    t.call("POST", "/api/brain/tap", {"seat": M, "ref": f"#{p_['id']}", "quiet": True})
            n0 = len(t.perms(M, "Treasure token"))
            t.hand(M, "Moopsy")
            c, d = t.act(M, "cast", name="Moopsy")
            check(c == 200 and len(t.perms(M, "Treasure token")) == n0 - 1, "a Treasure pays for a spell and is sacrificed", (c, d, n0))
            print(" end step, first main")
            t.turn(M)
            arm = t.onto(M, "Armus, Skin of Evil")
            c0 = len(t.state(C)["hand"])
            c, d = t.act(M, "end", text="over")
            for _ in range(3):
                pc = t.state(C).get("pending_choices") or []
                if pc:
                    t.act(C, "choose", id=pc[0]["id"], target={k: v for k, v in pc[0]["slot"]["options"][0].items() if k != "label"})
            check(len(t.state(C)["hand"]) == c0 - 2, "Armus at my end step: the opponent is asked twice, and discards two", (c0, len(t.state(C)["hand"])))
            t.turn(M)
            tg = t.onto(M, "Targ Wrangler")
            t.turn(M)
            c, d = t.act(M, "attack", assign={f"#{tg}": C})
            t.act(C, "block", attacker=f"#{tg}")
            c, d = t.act(M, "end", text="over")
            pend = t.state(M).get("pending_choices") or []
            check(c == 200 and pend and "Land" not in pend[0]["slot"].get("kinds", []), "Targ Wrangler attacked: at the end step, sacrifice a permanent is asked", (c, d, pend))
            if pend:
                t.act(M, "choose", id=pend[0]["id"], target={k: v for k, v in pend[0]["slot"]["options"][0].items() if k != "label"})
        finally:
            t.stop()

    def _section_16():
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            for n in ("Plains", "Plains", "Plains", "Forest", "Forest", "Forest", "Island", "Island", "Island", "Swamp", "Swamp", "Swamp"):
                t.onto(M, n)
            print(" life and draw triggers")
            cr = t.onto(M, "Dr. Beverly Crusher")
            c, d = t.call("POST", "/api/brain/life", {"seat": M, "delta": 2, "text": "test"}, key=t.keys[M])
            check(t.perm(M, "Dr. Beverly Crusher")["counters"] == 1, "'whenever you gain life': a +1/+1 counter", t.perm(M, "Dr. Beverly Crusher"))
            c, d = t.call("POST", "/api/brain/life", {"seat": M, "delta": -1, "text": "test"}, key=t.keys[M])
            check(t.perm(M, "Dr. Beverly Crusher")["counters"] == 1, "…not for losing life", t.perm(M, "Dr. Beverly Crusher"))
            da = t.onto(M, "Data, New Life")
            c0 = t.perm(M, "Data, New Life")["counters"]
            t.hand(M, "Open Communications")
            c, d = t.act(M, "cast", name="Open Communications")
            check(c == 200 and t.perm(M, "Data, New Life")["counters"] == c0 + 1, "'whenever you draw a card' (Data)", (c, d))
            sp = t.onto(M, "Mr. Spock, Science Officer")
            h0, cn = len(t.state(M)["hand"]), t.perm(M, "Data, New Life")["counters"]
            t.hand(M, "Open Communications")
            h0 = len(t.state(M)["hand"])
            c, d = t.act(M, "cast", name="Open Communications")
            check(c == 200 and len(t.state(M)["hand"]) == h0 - 1 + 2 and t.perm(M, "Data, New Life")["counters"] == cn + 2, "'if you would draw, draw two instead' (Spock), and Data counts both", (c, d, len(t.state(M)["hand"])))
            print(" entering")
            t.turn(M)
            t.onto(M, "Deanna Troi, Psychic Counsel")
            t.onto(M, "Sickbay")
            t.onto(M, "Lieutenant Commander Data")
            life0 = t.lifeof(M)
            t.hand(M, "Moopsy")
            c, d = t.act(M, "cast", name="Moopsy")
            check(c == 200 and t.perm(M, "Moopsy")["counters"] == 1 and t.lifeof(M) == life0 + 1, "a creature cast: Deanna's counter, Sickbay's life", (c, d, t.perm(M, "Moopsy"), t.lifeof(M)))
            cd0 = t.perm(M, "Lieutenant Commander Data")["counters"]
            t.hand(C, "Plasma Cascade")
            for n in ("Mountain", "Mountain", "Mountain"):
                t.onto(C, n)
            c, d = t.act(C, "cast", name="Plasma Cascade", targets=[{"player": M}])
            check(c == 200 and t.perm(M, "Lieutenant Commander Data")["counters"] == cd0 + 1, "'whenever a PLAYER casts a noncreature spell': the opponent's counts", (c, d, t.perm(M, "Lieutenant Commander Data")))
            t.decline(C)
            print(" a named token; an ability with a sacrifice")
            t.hand(M, "Captain Jonathan Archer")
            c, d = t.act(M, "cast", name="Captain Jonathan Archer")
            check(c == 200 and t.perm(M, "Porthos token"), "'create Porthos, a legendary 1/1' makes it", (c, d))
            t.turn(M)
            t.onto(M, "General Chang, Cold Warrior")
            vic = t.perm(M, "Porthos token")["id"]
            ch = t.perm(M, "General Chang, Cold Warrior")["id"]
            c, d = t.act(M, "activate", source=f"#{ch}", index=0)
            check(c != 200 and "--sac" in str(d.get("error")), "a sacrifice cost asks what, nothing paid", (c, d))
            c, d = t.act(M, "activate", source=f"#{ch}", index=0, sac=f"#{vic}")
            check(c == 200 and t.perm(M, "General Chang, Cold Warrior")["counters"] == 1 and t.perm(M, "Porthos token") is None, "General Chang: a counter on each Klingon, for a sacrificed creature", (c, d))
            moo = t.perm(M, "Moopsy")["id"]
            t.turn(M)
            c, d = t.act(M, "activate", source=f"#{moo}", index=0)
            check(c == 200 and "Reach" in t.perm(M, "Moopsy")["keywords"], "'{2}: gains reach until end of turn'", (c, d))
            t.turn(C)
            check("Reach" not in t.perm(M, "Moopsy")["keywords"], "…only until end of turn", t.perm(M, "Moopsy")["keywords"])
            print(" leaving the graveyard")
            t.turn(M)
            t.onto(M, "Sela, Tal Shiar Paradox")
            t.hand(M, "Fugitive Listener")
            t.call("POST", "/api/brain/discard", {"seat": M, "name": "Fugitive Listener", "quiet": True})
            t.hand(M, "Horta")
            t.call("POST", "/api/brain/discard", {"seat": M, "name": "Horta", "quiet": True})
            t.hand(M, "Test of Loyalty")
            n0 = len(t.perms(M, "Romulan token"))
            c, d = t.act(M, "cast", name="Test of Loyalty", modes=[2], targets=[{"at": M, "card": "Horta"}])
            check(c == 200 and any(h["name"] == "Horta" for h in t.state(M)["hand"]) and len(t.perms(M, "Romulan token")) == n0 + 1, "a card leaves my graveyard: Sela makes a tapped Romulan", (c, d))
        finally:
            t.stop()


    def _section_17():
        print("Basic landcycling (5 lines)")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            for n in ("Plains", "Plains"):
                t.onto(M, n)
            t.hand(M, "Humpback Whales")
            lg = [a_ for a_ in t.legal(M)["actions"] if a_["action"] == "cycle" and a_["body"]["name"] == "Humpback Whales"]
            check(bool(lg), "the legal list offers landcycling", lg)
            c, d = t.act(M, "cycle", name="Plains")
            check(c != 200, "a card without it is refused", (c, d))
            h0 = len(t.state(M)["hand"])
            c, d = t.act(M, "cycle", name="Humpback Whales")
            st = t.state(M)
            pend = st.get("pending_choices") or []
            names = {o["card"] for o in pend[0]["slot"]["options"]} if pend else set()
            check(c == 200 and "Humpback Whales" in st["graveyard"] and len(st["mana_sources"]) == 0, "it pays {2} and discards itself", (c, d))
            check(names and names <= {"Plains", "Island", "Swamp", "Mountain", "Forest"}, "the seat picks among basic lands only", names)
            c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": M, "card": "Island"})
            check(c == 200 and any(h["name"] == "Island" for h in t.state(M)["hand"]) and len(t.state(M)["hand"]) == h0, "…the Island goes to my hand", (c, d, len(t.state(M)["hand"])))
        finally:
            t.stop()


    def _section_18():
        print("Common Goal (G4-2)")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            a, b = t.onto(M, "Xindi Surveyors"), t.onto(M, "Fugitive Listener")
            drone = t.onto(C, "Assault Drone")
            t.lands(M, 2, "Forest")
            t.hand(M, "Common Goal")
            c, d = t.act(M, "cast", name="Common Goal")
            check(c != 200 and "Nothing was paid" in str(d.get("error")), "cast with no targets: refused, naming what it can aim at, nothing paid", (c, d))
            check(len(t.state(M)["mana_sources"]) == 2, "…no land was tapped", t.state(M)["mana_sources"])
            c, d = t.act(M, "cast", name="Common Goal", targets=[{"at": C, "ref": f"#{drone}"}, {"at": M, "ref": f"#{a}"}, {"at": C, "ref": f"#{drone}"}])
            check(c != 200, "aiming with a creature that isn't mine is refused", (c, d))
            c, d = t.act(M, "cast", name="Common Goal", targets=[{"at": M, "ref": f"#{a}"}, {"at": M, "ref": f"#{b}"}, {"at": M, "ref": f"#{a}"}])
            check(c != 200, "its target has to be an opponent's", (c, d))
            c, d = t.act(M, "cast", name="Common Goal", targets=[{"at": M, "ref": f"#{a}"}, {"at": M, "ref": f"#{b}"}, {"at": C, "ref": f"#{drone}"}])
            check(c == 200 and t.perm(C, "Assault Drone") is None, "two creatures bite: 3+3 kills the 2/4 Drone", (c, d))
            check(t.perm(M, "Xindi Surveyors") and t.perm(M, "Fugitive Listener"), "…and take nothing back: one-sided")
            check("Common Goal" in t.state(M)["graveyard"], "…it goes to the graveyard", t.state(M)["graveyard"])
            check("Assault Drone" in t.board(C)["graveyard"], "the Drone goes to its owner's graveyard", t.board(C)["graveyard"])
            # one biter, not lethal: the damage stays marked until the turn ends
            hor = t.onto(C, "Horta")
            t.lands(M, 2, "Forest")
            t.hand(M, "Common Goal")
            c, d = t.act(M, "cast", name="Common Goal", targets=[{"at": M, "ref": f"#{a}"}, {}, {"at": C, "ref": f"#{hor}"}])
            check(c == 200 and t.perm(C, "Horta")["damage"] == 3, "one biter is allowed; not lethal, so the damage is marked", (c, d, t.perm(C, "Horta")))
            t.turn(C)
            check(t.perm(C, "Horta")["damage"] == 0, "marked damage clears when the turn turns over", t.perm(C, "Horta"))
        finally:
            t.stop()


    def _section_19():
        print("Keywords a drafted deck never had (game 4: Burnham's trample would have won on turn 7)")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            bur = t.onto(M, "Captain Michael Burnham")
            blk = t.onto(C, "Tal Shiar Operative")
            check("Trample" in t.perm(M, "Captain Michael Burnham")["keywords"], "a deck built by make_deck_json reads its printed keywords", t.perm(M, "Captain Michael Burnham"))
            t.turn(M)
            l0 = t.lifeof(C)
            t.act(M, "attack", assign={f"#{bur}": C})
            c, d = t.act(C, "block", attacker=f"#{bur}", blocker=f"#{blk}")
            check(c == 200 and t.lifeof(C) == l0 - 1 and t.perm(C, "Tal Shiar Operative") is None, "Burnham (3 trample) blocked by a 2/2: 1 tramples over", (c, d, t.lifeof(C)))
            t.turn(M)
            # haste
            t.lands(M, 2, "Mountain")
            t.hand(M, "Starfleet Security")
            t.act(M, "cast", name="Starfleet Security")
            ss = t.perm(M, "Starfleet Security")
            check(not ss["sick"], "Haste: not summoning sick", ss)
            c, d = t.act(M, "attack", assign={f"#{ss['id']}": C})
            check(c == 200, "…and it attacks the turn it's cast", (c, d))
            t.act(C, "block", attacker=f"#{ss['id']}")
            t.turn(M)
            # menace
            ga = t.onto(M, "Elim Garak, Simple Tailor")
            b1, b2 = t.onto(C, "Fugitive Listener"), t.onto(C, "Fugitive Listener")
            t.turn(M)
            c, d = t.act(M, "attack", assign={f"#{ga}": C})
            inc = t.state(C).get("incoming") or []
            check(any(i.get("menace") for i in inc), "the defender is told it has menace", inc)
            c, d = t.act(C, "block", attacker=f"#{ga}", blocker=f"#{b1}")
            check(c != 200 and "menace" in str(d.get("error")), "one blocker is refused", (c, d))
            c, d = t.act(C, "block", attacker=f"#{ga}", blockers=[f"#{b1}", f"#{b2}"])
            check(c == 200, "two together block it", (c, d))
            t.turn(M)
            # can't block, defender
            ve = t.onto(C, "Storied Veteran")
            at = t.onto(M, "Xindi Surveyors")
            j = t.onto(M, "Starfleet Spanner Jockey")
            t.turn(M)
            c, d = t.act(M, "attack", assign={f"#{j}": C})
            check(c != 200 and "defender" in str(d.get("error")), "Defender can't attack", (c, d))
            t.act(M, "attack", assign={f"#{at}": C})
            c, d = t.act(C, "block", attacker=f"#{at}", blocker=f"#{ve}")
            check(c != 200 and "can't block" in str(d.get("error")), "'This creature can't block' is enforced", (c, d))
            t.act(C, "block", attacker=f"#{at}")
            # deathtouch, in a block
            t.turn(M)
            moo = t.onto(C, "Moopsy")
            hor = t.onto(M, "Horta")
            t.turn(M)
            t.act(M, "attack", assign={f"#{hor}": C})
            c, d = t.act(C, "block", attacker=f"#{hor}", blocker=f"#{moo}")
            check(c == 200 and t.perm(M, "Horta") is None, "Deathtouch: a 1/1 blocker kills a 7/6", (c, d))
            # first strike
            t.turn(M)
            sulu = t.onto(C, "Sulu, Space Swashbuckler")
            lis = t.onto(M, "Tal Shiar Operative")
            t.turn(M)
            t.act(M, "attack", assign={f"#{lis}": C})
            t.settle_setup()                             # (v6c, M5: the Operative's "choose one", where the table asks it, comes before blocks)
            c, d = t.act(C, "block", attacker=f"#{lis}", blocker=f"#{sulu}")
            check(c == 200 and t.perm(C, "Sulu, Space Swashbuckler") is not None and t.perm(M, "Tal Shiar Operative") is None, "First strike: a 2/2 blocker kills a 2/2 attacker and survives", (c, d))
        finally:
            t.stop()


    def _section_20():
        print("Aimed spells, cast from hand (priority 5: the highest count of 'read aloud, did nothing')")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            t.lands(M, 10, "Mountain")
            for _ in range(6):                         # (v7a: payment leaves the hand's other cards castable, and taps first the lands the hand doesn't need:
                t.onto(M, "Swamp")                     #  so a section that casts several spells in a row needs each colour more than once)
            for _ in range(6):
                t.onto(M, "Plains")
            for _ in range(4):
                t.onto(M, "Island")
            g = t.onto(C, "Xindi Surveyors")
            t.hand(M, "Plasma Cascade")
            l0 = t.lifeof(C)
            c, d = t.act(M, "cast", name="Plasma Cascade", targets=[{"player": C}])
            check(c == 200 and t.lifeof(C) == l0 - 3, "'any target', at a player: 3 damage", (c, d, t.lifeof(C)))
            pend = t.state(M).get("pending_choices") or []
            check(pend and pend[0]["slot"].get("zone") == "hand" and pend[0]["slot"].get("optional"), "'you may discard a card' asks, and can be declined", pend)
            h0 = len(t.state(M)["hand"])
            t.decline(M)
            check(len(t.state(M)["hand"]) == h0, "…declining keeps the hand and draws nothing", len(t.state(M)["hand"]))
            t.hand(M, "Plasma Cascade")
            c, d = t.act(M, "cast", name="Plasma Cascade", targets=[{"at": C, "ref": f"#{g}"}])
            check(c == 200 and t.perm(C, "Xindi Surveyors") is None, "'any target', at a creature: 3 kills a 3/2", (c, d))
            pend = t.state(M).get("pending_choices") or []
            names = [o["card"] for o in pend[0]["slot"]["options"]] if pend else []
            h0 = len(t.state(M)["hand"])
            c2, d2 = t.act(M, "choose", id=pend[0]["id"], target={"at": M, "card": names[0]})
            check(c2 == 200 and names[0] in t.state(M)["graveyard"] and len(t.state(M)["hand"]) == h0, "…discarding draws a card: 'if you do'", (c2, d2))
            s = t.state(M)
            g2 = t.onto(C, "Horta")
            t.hand(M, "Command Decision")
            lg = [a for a in t.legal(M)["actions"] if a["action"] == "cast" and a["body"].get("name") == "Command Decision"]
            check(lg and all(a["body"].get("modes") for a in lg), "the legal list names the mode (and what it aims at)", lg[:2])
            c, d = t.act(M, "cast", name="Command Decision", modes=[1], targets=[{"at": C, "ref": f"#{g2}"}])
            check(c == 200 and t.perm(C, "Horta") is None, "a mode with a target: destroy target creature with power 4 or greater", (c, d))
            small = t.onto(C, "Moopsy")
            t.hand(M, "Command Decision")
            c, d = t.act(M, "cast", name="Command Decision", modes=[1], targets=[{"at": C, "ref": f"#{small}"}])
            check(c != 200 and "power" in str(d.get("error")), "…power 1 is refused, nothing paid", (c, d))
            c, d = t.act(M, "cast", name="Command Decision", modes=[2])
            check(c == 200, "…and its other mode needs no target", (c, d))
            t.hand(M, "Energize")
            h0 = t.board(C)["hand"]
            c, d = t.act(M, "cast", name="Energize", targets=[{"at": C, "ref": f"#{small}"}])
            check(c == 200 and t.perm(C, "Moopsy") is None and t.board(C)["hand"] == h0 + 1, "return target creature to its owner's hand", (c, d))
            foe = t.onto(C, "Fugitive Listener")
            t.hand(M, "He's Dead, Jim")
            c, d = t.act(M, "cast", name="He's Dead, Jim", targets=[{"at": C, "ref": f"#{foe}"}])
            st = t.state(M)
            check(c == 200 and t.perm(C, "Fugitive Listener") is None and st.get("pending_surveil") == 1, "Destroy target creature. Surveil 1: both sentences happen", (c, d, st.get("pending_surveil")))
            t.act(M, "surveil", graveyard=[])
            check(not t.state(M).get("pending_surveil"), "keeping it on top clears the surveil")
            print("Enters triggers and token shapes")
            t.turn(M)                                      # lands untap
            t.hand(M, "Emergency Medical Hologram")
            tgt = t.onto(M, "Xindi Surveyors")
            life0 = t.lifeof(M)
            c, d = t.act(M, "cast", name="Emergency Medical Hologram", targets=[{"at": M, "ref": f"#{tgt}"}])
            check(c == 200 and t.perm(M, "Xindi Surveyors")["counters"] == 1 and t.lifeof(M) == life0 + 1, "'+1/+1 counter on target creature. You gain 1 life': both, on what was aimed at", (c, d, t.lifeof(M)))
            t.hand(M, "Vexing Drone")
            foe = t.onto(C, "Fugitive Listener")
            c, d = t.act(M, "cast", name="Vexing Drone", targets=[{"at": C, "ref": f"#{foe}"}])
            check(c == 200 and t.perm(C, "Fugitive Listener")["tapped"], "'tap target creature an opponent controls'", (c, d))
            t.lands(M, 2, "Forest")
            t.hand(M, "Neelix, Moral Support")
            c, d = t.act(M, "cast", name="Neelix, Moral Support")
            check(c == 200 and t.perm(M, "Food token"), "'create a Food token', with its own ability", (c, d))
            c, d = t.act(M, "activate", source=f"#{t.perm(M, 'Food token')['id']}", index=0)
            check(c == 200 and t.perm(M, "Food token") is None, "the Food can be eaten", (c, d))
            print("Lands")
            t.turn(C)
            t.turn(M)
            t.hand(M, "Blood Crypt")
            l0 = t.lifeof(M)
            c, d = t.act(M, "land", name="Blood Crypt", pay_life=True)
            check(c == 200 and t.lifeof(M) == l0 - 2 and not t.perm(M, "Blood Crypt")["tapped"], "a shock land: pay 2, it's untapped", (c, d))
            t.turn(C)
            t.turn(M)
            an = t.onto(M, "Artificial Anomaly")
            l0, l1 = t.lifeof(M), t.lifeof(C)
            c, d = t.act(M, "activate", source=f"#{an}", index=0)
            check(c == 200 and t.perm(M, "New Planet token") and t.lifeof(C) == l1 - 2 and t.lifeof(M) == l0 + 2 and t.perm(M, "Artificial Anomaly") is None, "Artificial Anomaly's ability: drain 2, sacrifice, a New Planet", (c, d, t.lifeof(C), t.lifeof(M)))
        finally:
            t.stop()


    def _section_21():
        print("Saying so (priority 5: never silently nothing)")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            t.lands(M, 2, "Mountain")
            t.hand(M, "Beckett Mariner, Impetuous Ensign")
            t.lands(M, 6, "Plains")
            last = t.call("GET", "/api/events?since=latest", host=False)[1].get("last") or 0
            t.hand(M, "Dr. Beverly Crusher")
            c, d = t.act(M, "cast", name="Dr. Beverly Crusher")
            ev = t.events(last)
            notes = [e["text"] for e in ev if e.get("type") == "table-note"]
            check(c == 200 and any("Dr. Beverly Crusher" in n and "gain" in n for n in notes), "a card with an ability the table can't do says so, in the public log, when it lands", (c, d, notes))
            s = t.perm(M, "Dr. Beverly Crusher")
            check(s.get("by_hand"), "…and it's on the card's own view", s)
        finally:
            t.stop()


    def _section_22():
        print("Coverage is true: what it calls handled, the engine does")
        import card_coverage as cv
        cards = shim.load(cards_path.cards_json())
        bad = []
        for card in cards:
            if not ("Instant" in card["types"] or "Sorcery" in card["types"]):
                continue
            if player.modal_options(card) or player.kicker_of(card):
                continue                                   # (v6d: a kicker is chosen by the cast, not by resolve_simple: tests/coverage_v6b_test.py plays it)
            ss = cv.card_lines(card)
            if ss and all(st == "handled" for _, sh, st in ss if sh.startswith("spell")):
                vp = player.VirtualPlayer(DECK_MICHAEL, "U", seed=2)
                foe = player.VirtualPlayer(DECK_CLAUDE, "V", seed=3)
                vp.turn = 1
                vp.battlefield, foe.battlefield = [], []
                c1 = player.Perm(next(c for c in vp.library if c["name"] == "Horta"), sick=False)
                c2 = player.Perm(next(c for c in vp.library if c["name"] == "Fugitive Listener"), sick=False)
                c3 = player.Perm(next(c for c in foe.library if c["name"] == "Horta"), sick=False)
                land = player.Perm(next(c for c in vp.library if c["name"] == "Forest"), sick=False)
                art = player.Perm(next(c for c in foe.library if c["name"] == "Assault Drone"), sick=False)
                vp.battlefield += [c1, c2, land]
                foe.battlefield += [c3, art]
                slots = player.card_slots(card)
                if any(sl.get("zone") for sl in slots):
                    continue                               # a card in a zone: played through the HTTP sections above
                aim = []
                for sl in slots:
                    if sl.get("side") == "own" and "Land" in sl["kinds"]:
                        aim.append({"perm": land, "owner": vp})
                    elif sl.get("side") == "own":
                        aim.append({"perm": c1 if not aim else c2, "owner": vp})
                    elif "Artifact" in sl["kinds"]:
                        aim.append({"perm": art, "owner": foe})
                    else:
                        aim.append({"perm": c3, "owner": foe})
                vp.todo.clear()
                said = vp.resolve_simple(card["text"], aim=aim, card=card)
                if vp.todo:
                    bad.append((card["name"], vp.todo))
        check(not bad, "every instant and sorcery coverage calls handled runs with nothing left over", bad[:5])
        # every trigger coverage calls handled really runs, with nothing aimed and nothing left over
        bad = []
        for card in cards:
            for ln, sh, st in cv.card_lines(card):
                if sh == "Landfall" and st == "handled":
                    vp = player.VirtualPlayer(DECK_MICHAEL, "U", seed=2)
                    vp.turn = 1
                    vp.battlefield = [player.Perm(card, sick=False), player.Perm(next(c for c in vp.library if c["name"] == "Horta"), sick=False)]
                    vp.todo.clear()
                    vp.land_entered(player.Perm(next(c for c in vp.library if c["name"] == "Forest"), sick=False))
                    vp.land_entered(player.Perm(next(c for c in vp.library if c["name"] == "Forest"), sick=False))
                    if vp.todo:
                        bad.append((card["name"], vp.todo))
        check(not bad, "every Landfall coverage calls handled runs through twice with nothing left over", bad[:5])

    def _section_23():
        print("Review fixes (2026-11-09), S1: a face-down creature's real name never reaches the public log")
        v = player.VirtualPlayer(DECK_CLAUDE, "Claude", seed=1)
        secret_card = {"name": "Secret Morph Guy", "types": ["Creature"], "power": "4", "toughness": "4", "text": "Disguise {2}"}
        fd = player.Perm(secret_card, sick=False, face_down=True)
        v.battlefield.append(fd)
        v.add_role(fd, "Sorcerer")
        v.battlefield.append(player.Perm({"name": "Odd Gadget", "types": ["Artifact"], "subtypes": ["Equipment"],
                                          "text": 'Equipped creature has "Whenever this creature attacks, target player mills two cards."\nEquip {1}'}, sick=False, attached_to=fd.id))
        said = v.manual_attack({f"#{fd.id}": "Sam"})
        check(v.todo and not any("Secret Morph Guy" in x for x in v.todo + said) and all(f"#{fd.id}" in x for x in v.todo),
              "S1: the attack's to-do lines (Sorcerer Role, an Equipment's trigger) say the face-down creature as the board does, with its number", v.todo)

    def _section_24():
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            t.hand(M, "Shuttle Ace")
            t.call("POST", "/api/brain/topdeck", {"seat": M, "name": "Shuttle Ace"})
            c, d = t.call("POST", "/api/brain/manifest", {"seat": M, "n": 1})
            fdp = next((p for p in t.state(M)["permanents"] if p.get("face_down")), None)
            check(c == 200 and fdp and fdp["name"] == "Shuttle Ace", "(set-up: a face-down Shuttle Ace)", (c, d, fdp))
            ref = f"#{fdp['id']}"
            t.call("POST", "/api/brain/role", {"seat": M, "ref": ref, "kind": "Sorcerer"})
            ev0 = (t.events() or [{}])[-1].get("id", 0)
            t.turn(M)
            c, d = t.act(M, "attack", assign={ref: C})
            t.act(C, "block", attacker=ref)
            evs = t.events(ev0)
            named = [e.get("text") for e in evs if "Shuttle Ace" in json.dumps(e)]
            notes = [e.get("text") for e in evs if e.get("type") == "table-note" and "Sorcerer" in str(e.get("text"))]
            check(c == 200 and not named, "S1: attacking with it, no public event names the face-down card", (c, named))
            check(notes and all("face-down" in n for n in notes), "S1: the table still says what is left to do, describing it as the board does", notes)
            check(any("Shuttle Ace" in str(x) for x in (d.get("private") or {}).get("todo") or []) or any(ref in str(x) for x in (d.get("private") or {}).get("todo") or []),
                  "S1: the owner's own (private) to-do still finds the card", (d.get("private") or {}).get("todo"))

            print("Review fixes, S2: a refused block describes a face-down attacker as the board does")
            t.turn(M)
            t.lands(M, 4, "Plains")
            for _ in range(2):
                t.hand(M, "Combadge")
                t.call("POST", "/api/brain/put", {"seat": M, "name": "Combadge", "quiet": True})
            t.hand(M, "Horta")
            t.call("POST", "/api/brain/topdeck", {"seat": M, "name": "Horta"})
            t.call("POST", "/api/brain/manifest", {"seat": M, "n": 1})
            fds = [p for p in t.state(M)["permanents"] if p.get("face_down")]
            cbs = [p["id"] for p in t.state(M)["permanents"] if p["name"] == "Combadge"]
            t.settle_setup()
            t.turn(M)
            c1, d1 = t.act(M, "equip", equipment=f"#{cbs[0]}", creature=f"#{fds[0]['id']}")
            c2, d2 = t.act(M, "equip", equipment=f"#{cbs[1]}", creature=f"#{fds[1]['id']}")
            blk = t.onto(C, "Tal Shiar Operative")
            c, d = t.act(M, "attack", assign={f"#{fds[0]['id']}": C})
            c, d = t.act(C, "block", attacker=f"#{fds[0]['id']}", blocker=f"#{blk}")
            err = str(d.get("error"))
            check(c1 == 200 and c2 == 200 and c != 200 and "can't be blocked" in err and "face-down" in err and fds[0]["name"] not in err,
                  "S2: the defender is told 'a face-down 2/2 can't be blocked this turn', never its name", (c1, d1.get("error"), c2, c, err, fds[0]["name"]))
            t.act(C, "block", attacker=f"#{fds[0]['id']}")
        finally:
            t.stop()


    def _section_25():
        print("Review fixes, R1: a two-target question whose first answer leaves play")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            x1 = t.onto(M, "Xindi Surveyors"); t.onto(M, "Fugitive Listener")
            m1 = t.onto(C, "Mugato"); t.onto(C, "Moopsy")
            t.turn(M)
            t.lands(M, 3, "Forest")
            t.hand(M, "Amok Time")
            c, d = t.act(M, "cast", name="Amok Time")
            pend = t.state(M).get("pending_choices") or []
            c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": C, "ref": f"#{m1}"})
            p2 = t.state(M).get("pending_choices") or []
            check(c != 200 and p2 and len(p2[0].get("picked") or []) == 0, "R1: a refused answer records nothing (the question is exactly as it was)", (c, d.get("error"), p2[:1]))
            c, d = t.act(M, "choose", id=pend[0]["id"], target={"at": M, "ref": f"#{x1}"})
            check(c == 200 and len((t.state(M).get("pending_choices") or [{}])[0].get("picked") or []) == 1, "R1: the fighter is named; the table asks what it fights", (c, d))
            ev0 = (t.events() or [{}])[-1].get("id", 0)
            t.call("POST", "/api/brain/destroy", {"seat": M, "ref": f"#{x1}", "quiet": True})
            lg = t.legal(M)
            pend = t.state(M).get("pending_choices") or []
            evs = [e.get("text") for e in t.events(ev0)]
            check(not pend and not any(a["action"] == "choose" for a in lg.get("actions", [])), "R1: the fighter left play: the question ends (no fight) and the legal list offers no answer that would fail", (pend, [a["why"] for a in lg.get("actions", [])][:4]))
            check(any("no fight" in str(x) for x in evs), "R1: …and the table says so", evs[-4:])
            c, d = t.act(M, "next")
            check(c == 200, "R1: …and the turn moves on", (c, d.get("error")))
        finally:
            t.stop()


    def _section_26():
        print("Review fixes, T4: an \"up to one\" target with exactly one legal choice is still ASKED")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            x1 = t.onto(M, "Xindi Surveyors")
            m1 = t.onto(C, "Mugato")
            t.turn(M)
            t.lands(M, 3, "Forest")
            t.hand(M, "Amok Time")
            c, d = t.act(M, "cast", name="Amok Time")
            pend = t.state(M).get("pending_choices") or []
            opts = (pend[0]["slot"].get("options") if pend else None) or []
            check(c == 200 and pend and pend[0]["slot"].get("optional") and len(opts) == 1 and t.perm(C, "Mugato") and t.perm(M, "Xindi Surveyors"),
                  "the fighter (one legal) is settled, but 'up to one target creature an opponent controls' with one legal target still asks: declining is a choice", (c, d, pend))
            c, d = t.act(M, "choose", id=pend[0]["id"], target={})
            check(c == 200 and not t.state(M).get("pending_choices") and t.perm(C, "Mugato") and t.perm(M, "Xindi Surveyors"), "…and declining it means no fight", (c, d))
        finally:
            t.stop()


    def _section_27():
        print("Review fixes, R2: a seat that has lost owes nothing")
        t = Table(extra=["--ai", "Sam|", "--ai-deck", DECK_MICHAEL, "--pilot", "Sam", "--order", "Claude,Michael,Sam"])
        try:
            t.keys = {n: t.call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in ("Claude", "Michael", "Sam")}
            t.call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)
            t.call("POST", "/api/phase/next", {"by": "Claude", "key": t.keys["Claude"]}, key=t.keys["Claude"])
            t.wait(lambda p: p.get("player") == "Claude" and p.get("begun") and p.get("step") == "main 1")
            t.turn("Michael", fresh=False)
            M, C = "Michael", "Claude"
            t.lands(C, 3, "Mountain")
            foe = t.onto(M, "Xindi Surveyors")
            t.hand(C, "Plasma Cascade")
            c, d = t.act(C, "cast", name="Plasma Cascade", targets=[{"at": M, "ref": f"#{foe}"}])
            had = t.state(C).get("pending_choices") or []
            t.call("POST", "/api/brain/life", {"seat": M, "player": C, "delta": -40})
            c1, d1 = t.act(M, "next", tries=1)
            c2, d2 = t.act(M, "end", tries=1, text="x")
            check(had and c1 == 200 and c2 == 200, "R2: Claude lost while owing a choice: the others' turn is not held (next, end)", (bool(had), c1, d1.get("error"), c2, d2.get("error")))
            check(not t.call("GET", f"/api/brain/state?seat={C}")[1].get("pending_choices"), "R2: …and the lost seat's question is gone")
        finally:
            t.stop()


    def _section_28():
        print("Review fixes, R3/R7: lifelink once; a trampler that dies still hits")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            t.lands(M, 3, "Mountain")
            brad = t.onto(M, "Brad Boimler, Eager Ensign")
            t.call("POST", "/api/brain/counter", {"seat": M, "ref": f"#{brad}", "n": 2})
            t.onto(M, "Dominion Supervisor")
            t.settle_setup()
            xs = t.onto(C, "Xindi Surveyors")
            t.turn(M)
            pt = t.perm(M, "Brad Boimler, Eager Ensign")["pt"]
            lm, lc = t.lifeof(M), t.lifeof(C)
            t.act(M, "attack", assign={f"#{brad}": C})
            t.act(C, "block", attacker=f"#{brad}", blocker=f"#{xs}")
            c, d = t.act(M, "damage")
            check(pt == "5/5" and c == 200 and t.lifeof(M) == lm + 5 and t.lifeof(C) == lc - 3,
                  "R3: a blocked 5/5 lifelink trampler (2 to the blocker, 3 over) gains 5, not 8", (pt, c, d, lm, t.lifeof(M), lc, t.lifeof(C)))
            moop = t.onto(C, "Moopsy")
            t.turn(M)
            lm, lc = t.lifeof(M), t.lifeof(C)
            t.act(M, "attack", assign={f"#{brad}": C})
            t.act(C, "block", attacker=f"#{brad}", blocker=f"#{moop}")
            dead = t.perm(M, "Brad Boimler, Eager Ensign") is None
            c, d = t.act(M, "damage")
            check(dead and c == 200, "R7: the trampler died to a deathtouch blocker: `damage` still works (it used to 400 on the dead attacker)", (dead, c, d.get("error")))
            check(t.lifeof(C) == lc - 4 and t.lifeof(M) == lm + 5, "R7: …and its hit still counts as it was: 4 over, lifelink 1 + 4", (lc, t.lifeof(C), lm, t.lifeof(M)))
        finally:
            t.stop()
        A = player.VirtualPlayer(DECK_MICHAEL, "Att", seed=1)
        Dv = player.VirtualPlayer(DECK_CLAUDE, "Def", seed=1)
        blocker = player.Perm(dict(CARDS["Brad Boimler, Eager Ensign"]), sick=False)
        crusher = player.Perm(dict(CARDS["Neelix, Moral Support"]), sick=False)
        Dv.battlefield += [blocker, crusher]
        atk = player.Perm({"name": "Big Ox", "types": ["Creature"], "power": "5", "toughness": "5", "text": ""}, sick=False)
        A.battlefield.append(atk)
        from ai_turn import resolve_block
        l0 = Dv.life
        r = resolve_block(Dv, blocker, 5, False, "Big Ox", atk=atk, atk_owner=A)
        Dv.drain()
        check(Dv.life == l0 + 2 and crusher.counters == 1, "R3: a blocker's lifelink is a life gain: 'whenever you gain life' hears it", (Dv.life, l0, crusher.counters, r["said"]))


    def _section_29():
        print("Review fixes, R6: first strike and double strike deal in their own damage step")
        def duel(ak, bk, ap="2", at="2", bp="2", bt="2", trample=False):
            A_ = player.VirtualPlayer(DECK_MICHAEL, "Att", seed=1)
            D_ = player.VirtualPlayer(DECK_CLAUDE, "Def", seed=1)
            a_ = player.Perm({"name": "Att One", "types": ["Creature"], "power": ap, "toughness": at, "keywords": ak, "text": "\n".join(ak)}, sick=False)
            b_ = player.Perm({"name": "Def One", "types": ["Creature"], "power": bp, "toughness": bt, "keywords": bk, "text": "\n".join(bk)}, sick=False)
            A_.battlefield.append(a_); D_.battlefield.append(b_)
            life_ = D_.life
            r_ = resolve_block(D_, b_, A_.stats(a_)[0], trample, "Att One", atk=a_, atk_owner=A_)
            return bool(r_["attacker_died"]), b_ not in D_.battlefield, life_ - D_.life, a_.damage, r_
        check(duel(["First strike"], ["First strike"])[:2] == (True, True), "R6: first strike vs first strike (2/2 each): both deal in the first-strike step, both die", duel(["First strike"], ["First strike"]))
        check(duel(["First strike"], [])[:2] == (False, True), "R6: a first striker kills a plain 2/2 blocker before it can hit back", duel(["First strike"], []))
        check(duel([], ["First strike"])[:2] == (True, False), "R6: a first-striking blocker kills a plain 2/2 attacker before it can hit", duel([], ["First strike"]))
        r = duel([], ["Double strike"], ap="3", at="4", bp="2", bt="4")
        check(r[0] and not r[1] and r[3] == 4, "R6: a double-strike blocker (2 power) hits twice: a 3/4 attacker takes 4 and dies", r)
        r = duel(["Double strike"], [], ap="2", at="2", bp="1", bt="3")
        check(r[1] and not r[0] and r[2] == 0, "R6: a double-strike attacker (2) kills a 1/3 blocker over its two strikes; no trample, nothing reaches the player", r)
        r = duel(["Double strike", "Trample"], [], ap="3", at="3", bp="0", bt="2", trample=True)
        check(r[1] and r[2] == 4, "R6: double strike + trample: 2 lethal and 1 over in the first step, all 3 over in the second", r)


    def _section_30():
        print("Review fixes, R8: a refused attack is not an attack")
        A = player.VirtualPlayer(DECK_MICHAEL, "Michael", seed=1)
        A.turn = 3
        tw = player.Perm(dict(CARDS["Targ Wrangler"]))
        A.battlefield.append(tw)
        try:
            A.manual_attack({f"#{tw.id}": "Claude"})
            refused = False
        except player.IllegalAction:
            refused = True
        A.__dict__["asks"] = False
        said = A.end_step()
        check(refused and tw.attacked_turn != A.turn and tw in A.battlefield, "R8: a summoning-sick Targ Wrangler's refused attack doesn't count: no 'attacked this turn' sacrifice", (refused, tw.attacked_turn, said))


    def _section_31():
        print("Review fixes, R4: reading a card's text has no side effects (coverage, can_run)")
        import card_coverage
        import random as _random
        calls = []
        hook0, died0, tick0 = player.DEATH_HOOK[0], player.DIED_THIS_TURN[0], player.DEATH_TICK[0]
        real = player.VirtualPlayer(DECK_MICHAEL, "Real", seed=2)
        real.battlefield.append(player.Perm(dict(CARDS["Sarcophagus, Ship of the Dead"]), sick=False))
        ids0 = player._ids
        player._ids = __import__("itertools").count(500000)
        player.DEATH_HOOK[0] = lambda owner, p: calls.append((owner.name, p.name))
        rnd0 = _random.getstate()
        snap = (len(real.battlefield), real.life, list(real.todo), len(real.__dict__.get("outbox") or []))
        probed = 0
        try:
            for c_ in shim.load(cards_path.cards_json()):
                try:
                    card_coverage.by_hand_lines(c_)
                    for sent_ in [x for x in __import__("re").split(r"(?<=\.)\s+|\n", c_.get("text") or "") if x.strip()]:
                        real.can_run(sent_, c_["name"])
                        player.effect_known(sent_, c_["name"])
                        player.effect_known(sent_, c_["name"], aimed=False)
                        probed += 1
                except Exception:  # noqa: BLE001 — a card the reader chokes on is not this check's business
                    pass
            nxt = next(player._ids)
        finally:
            player.DEATH_HOOK[0] = hook0
            rnd1 = _random.getstate()
        check(probed > 500 and not calls and player.DIED_THIS_TURN[0] == died0 and player.DEATH_TICK[0] == tick0,
              f"R4: probing every set card's text ({probed} sentences) fires no death hook and sets no 'a creature died this turn'", (calls[:5], player.DIED_THIS_TURN[0]))
        check(nxt == 500000 and rnd1 == rnd0, "R4: …takes no permanent ids from the table's counter and draws nothing from the shared random generator", (nxt, rnd1 == rnd0))
        check(snap == (len(real.battlefield), real.life, list(real.todo), len(real.__dict__.get("outbox") or [])), "R4: …and the seat that asked is untouched (board, life, to-do, outbox)", snap)
        player._ids = ids0
        player.DIED_THIS_TURN[0] = died0

    def _section_32():
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            t.onto(M, "Sarcophagus, Ship of the Dead")
            x = t.onto(M, "Xindi Surveyors")
            mug = t.onto(C, "Mugato")
            t.turn(M)
            t.lands(M, 2, "Forest")
            t.hand(M, "Common Goal")
            c, d = t.act(M, "cast", name="Common Goal", targets=[{"at": M, "ref": f"#{x}"}, {}, {"at": C, "ref": f"#{mug}"}])
            check(c == 200 and t.perm(C, "Mugato") and t.perm(M, "Sarcophagus, Ship of the Dead")["charge"] == 0,
                  "R4: a cast whose coverage note reads 'destroy' text: nobody died, so Sarcophagus gets no charge", (c, d, t.perm(M, "Sarcophagus, Ship of the Dead")))
        finally:
            t.stop()


    def _section_33():
        print("Review fixes, R5: Two-Headed Giant: 'each upkeep' once per team turn")
        t = Table(extra=["--ai", "Sam|", "--ai-deck", DECK_MICHAEL, "--pilot", "Sam", "--ai", "Fusion|", "--ai-deck", DECK_CLAUDE, "--pilot", "Fusion",
                         "--team", "Feds:Michael,Claude", "--team", "Klingons:Sam,Fusion", "--order", "Michael,Sam,Claude,Fusion"])
        try:
            t.keys = {n: t.call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in ("Michael", "Claude", "Sam", "Fusion")}
            t.call("POST", "/api/phase/windows", {"on": False, "by": "host"}, host=False)
            t.onto("Sam", "Quark, Humble Proprietor")
            t.onto("Claude", "Quark, Humble Proprietor")
            t.call("POST", "/api/phase/next", {"by": "Michael", "key": t.keys["Michael"]}, key=t.keys["Michael"])
            t.wait(lambda p: p.get("player") == "Michael" and p.get("begun") and p.get("step") == "main 1")
            for s_ in ("Michael", "Claude"):
                t.act(s_, "begin", tries=1)
            time.sleep(1)
            tr = {s_: len(t.perms(s_, "Treasure token")) for s_ in ("Sam", "Claude")}
            check(tr == {"Sam": 1, "Claude": 1}, "R5: the Feds' one upkeep: each Quark makes one Treasure (it made one per teammate's half)", tr)
        finally:
            t.stop()
        E = player.VirtualPlayer(DECK_MICHAEL, "Sam", seed=1)
        E.battlefield.append(player.Perm(dict(CARDS["Quark, Humble Proprietor"]), sick=False))
        E.__dict__["skip_each_upkeep"] = True
        n0 = len([p for p in E.battlefield if p.name.startswith("Treasure")])
        E.upkeep()
        check(len([p for p in E.battlefield if p.name.startswith("Treasure")]) == n0, "R5: a seat whose 'each upkeep' the table already ran this turn runs only its 'your upkeep'")


    def _section_34():
        print("Review fixes, T4: a game saved while a question waits restores with the question")
        t = Table()
        try:
            t.start()
            M, C = "Michael", "Claude"
            t.onto(M, "Syndicate Liquidators"); t.onto(M, "Horta"); t.onto(M, "Xindi Surveyors")
            t.hand(M, "Plains")
            c, d = t.act(M, "land", name="Plains")
            pend = t.state(M).get("pending_choices") or []
            ids = [o.get("ref") for o in pend[0]["slot"]["options"]] if pend else []
            bad = []
            for route in ("/api/spectate", f"/api/ai/state?seat={M}", f"/api/checklist?player={M}", "/api/history", "/api/board3d", "/api/events?since=0", "/api/phase", "/api/state", "/api/life"):
                code_, body_ = t.call("GET", route, host=False)
                if "pending_choices" in json.dumps(body_):
                    bad.append(route)
            check(pend and not bad, "while a question waits, no spectator or public route carries it", bad)
            snap = None
            for _ in range(40):
                snap = next(iter(t.dir.rglob("snapshot.pkl")), None)
                if snap:
                    break
                time.sleep(0.5)
            keys = dict(t.keys)
        finally:
            t.stop()
        check(snap is not None, "(the game saved a snapshot while the question waited)")
        if snap is not None:
            t2 = Table(extra=["--restore", str(snap)])
            try:
                t2.keys = keys
                pend2 = t2.state(M).get("pending_choices") or []
                c, d = t2.act(M, "next", tries=1)
                check(len(pend2) == 1 and c == 409 and "choose first" in str(d.get("error")), "restored: the question is still waiting, and the turn still waits on it", (len(pend2), c, d.get("error")))
                lg = t2.legal(M)
                check(lg.get("actions") and all(a["action"] == "choose" for a in lg["actions"]), "restored: the legal list offers only the answers", [a["action"] for a in lg.get("actions", [])])
                if pend2:
                    c, d = t2.act(M, "choose", id=pend2[0]["id"], target={"at": M, "ref": pend2[0]["slot"]["options"][0]["ref"]})
                    check(c == 200 and not t2.state(M).get("pending_choices"), "restored: answering it works, and it's gone", (c, d))
                    c, d = t2.act(M, "next")
                    check(c == 200, "restored: …and the turn moves again", (c, d.get("error")))
            finally:
                t2.stop()


    # Each section runs on its own: one that throws (a table that never got somewhere, a KeyError on a reply) records ONE failure and the
    # rest still run. (One exception used to end the suite, and every check after it went unreported.)
    for fn, title in [(_section_01, 'Station (G3-9, G4-6, priority 1)'), (_section_02, "Next step, the step bar's server side (G4-7, priority 3)"), (_section_03, 'Public view (G4-8)'), (_section_04, 'Landfall as damage (G4-1), land tokens (G4-3)'), (_section_05, '(continued) Landfall as damage (G4-1), land tokens (G4-3)'), (_section_06, '(continued) (continued) Landfall as damage (G4-1), land tokens (G4-3)'), (_section_07, "A trigger with nothing aimed asks the seat (Michael's call, 2026-11-09); the table waits"), (_section_08, "(continued) A trigger with nothing aimed asks the seat (Michael's call, 2026-11-09); the t"), (_section_09, 'A question nobody can answer must not hold the game'), (_section_10, "An engine-run seat, which can't be asked"), (_section_11, 'Sagas (27 lines): lore counters, chapters, the end'), (_section_12, ' In the Pale Moonlight: the OPPONENT chooses'), (_section_13, 'Beam me up (10 lines), and searching a library'), (_section_14, 'Dilemma (9 lines): choosing a mode'), (_section_15, 'Step triggers, life/draw/enter triggers, more abilities (batch 4)'), (_section_16, ' life and draw triggers'), (_section_17, 'Basic landcycling (5 lines)'), (_section_18, 'Common Goal (G4-2)'), (_section_19, "Keywords a drafted deck never had (game 4: Burnham's trample would have won on turn 7)"), (_section_20, "Aimed spells, cast from hand (priority 5: the highest count of 'read aloud, did nothing')"), (_section_21, 'Saying so (priority 5: never silently nothing)'), (_section_22, 'Coverage is true: what it calls handled, the engine does'), (_section_23, "Review fixes (2026-11-09), S1: a face-down creature's real name never reaches the public log"), (_section_24, 'Review fixes, S1/S2 on a table: a face-down attacker in the public log and in a refused block'), (_section_25, 'Review fixes, R1: a two-target question whose first answer leaves play'), (_section_26, "Review fixes, T4: an 'up to one' target with exactly one legal choice is still asked"), (_section_27, 'Review fixes, R2: a seat that has lost owes nothing'), (_section_28, 'Review fixes, R3/R7: lifelink once; a trampler that dies still hits'), (_section_29, 'Review fixes, R6: first strike and double strike deal in their own damage step'), (_section_30, 'Review fixes, R8: a refused attack is not an attack'), (_section_31, "Review fixes, R4: reading a card's text has no side effects (coverage, can_run)"), (_section_32, "(continued) Review fixes, R4: reading a card's text has no side effects (coverage, can_run"), (_section_33, "Review fixes, R5: Two-Headed Giant: 'each upkeep' once per team turn"), (_section_34, 'Review fixes, T4: a game saved while a question waits restores with the question')]:
        only = [x.strip() for x in os.environ.get("RETRO_ONLY", "").split("|") if x.strip()]
        if only and not any(x.lower() in title.lower() for x in only):
            continue                     # RETRO_ONLY="R1|S2": only the sections whose title names one of these
        try:
            fn()
        except Exception as e:  # noqa: BLE001 — recorded, and the next section runs
            traceback.print_exc()
            check(False, f"section finished: {title}", f"{type(e).__name__}: {e}")
    print("\n" + ("❌ " + str(len(FAILS)) + " FAILED: " + "; ".join(FAILS) if FAILS else "all passed"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
