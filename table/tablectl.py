#!/usr/bin/env python3
"""tablectl — drive the virtual AI player from outside the server (the "external brain").

The server (started with --brain external) keeps the deck, board, life totals and table talk, and
checks the arithmetic (mana, one land a turn, legal Aura targets). Whoever runs tablectl makes the
Magic decisions. Everything an action does is announced at the table in the AI's voice.

  tablectl state                         your private view: hand with rules text, board, mana, life, events
  tablectl watch                         follow the table: one line per event (for a live monitor)
  tablectl events [--since N]            recent events once
  tablectl say "text"                    speak at the table
  tablectl begin                         untap, draw (you see the drawn card; the table doesn't)
  tablectl land "Forest"
  tablectl cast "Timely Ward" --on Ellivere
  tablectl cast commander --role-on "Paradise Druid"
  tablectl cast "Austere Command" --mode 3 --mode 1
  tablectl cast "Swords to Plowshares" --targets "Sam's Craw Wurm"
  tablectl cast "Eidolon of Blossoms" --discount 1      (only reductions the engine can't see; Jukai,
                                         Starfield, Danitha and Envoy are applied automatically)
  tablectl attack "Ellivere=Michael" "#14=Sam" [--role-on NAME]
  tablectl block [REF] --amount 12 [--trample] [--attacker Ghalta]   (no REF = no block, take it)
  tablectl damage "Ellivere=Michael" "#14=Sam:3"   your attackers that hit a player (amount defaults to
                                         power): life, lifelink, Ellivere draws, Pollenbright Saprolings
  tablectl role REF Monster              a Role from a choice the engine leaves to you (Gylwain)
  tablectl end ["text"]
  tablectl destroy|exile|bounce|tap|untap REF      corrections when opponents' cards affect yours
  tablectl counter REF N · token NAME P T [KW..] · draw N · discard NAME · mill N
  tablectl search NAME [--to battlefield] [--tapped]
  tablectl life PLAYER DELTA              e.g. life Michael -5 · life me +3
  tablectl new-game                      new game: reshuffles, sealing players' secret words in
  tablectl fair · fair-reveal · fair-verify FILE   the AI decks' fingerprints; publish; check
  tablectl --seat Fusion state           two AI players: act for one seat (or export TABLE_SEAT=Fusion)

REF is a permanent's name or '#id' from `state`. Add --quiet to act without announcing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = os.environ.get("TABLE_URL", "http://127.0.0.1:8800")
SEAT = os.environ.get("TABLE_SEAT")        # with two AI players: which seat this brain plays
TOKEN_FILE = Path(os.environ.get("TABLE_TOKEN_FILE") or Path(__file__).parent / ".brain-token")


def req(method, path, body=None, brain=True):
    headers = {"Content-Type": "application/json"}
    if brain:
        headers["X-Brain-Token"] = TOKEN_FILE.read_text().strip()
    r = urllib.request.Request(BASE + path, method=method, headers=headers,
                               data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            code, raw = resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        code, raw = e.code, e.read().decode()
    except urllib.error.URLError as e:
        sys.exit(f"table server not reachable at {BASE}: {e.reason}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        sys.exit(f"server returned non-JSON (HTTP {code}): {raw[:200]}")
    if code == 409 and data.get("waiting"):              # not refused: the table is waiting on passes
        return data
    if code >= 400:
        sys.exit(f"refused: {data.get('error', raw[:200])}")
    return data


def act(action, **body):
    if SEAT:
        body["seat"] = SEAT
    payload = {k: v for k, v in body.items() if v not in (None, [], False)}
    d = req("POST", f"/api/brain/{action}", payload)
    if d.get("waiting"):                                 # my turn walks its steps: wait for the others' passes
        print(f"…waiting on passes: {d['waiting']}", flush=True)
        while d.get("waiting"):
            time.sleep(2)
            d = req("POST", f"/api/brain/{action}", payload)
    for line in d.get("said", []):
        print(f"🗣  {line}")
    if d.get("private"):
        print(f"🔒 private: {json.dumps(d['private'])}")
    pub = d.get("public", {})
    print(f"   life {pub.get('life')} · hand {pub.get('hand')} · library {pub.get('library')} · "
          f"untapped mana {pub.get('untapped_mana')} · lands {pub.get('lands')}")


def fmt_event(e):
    t = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
    k = e["type"]
    if k == "heard":
        extra = f" → {e['kind']}" + (f" to {e['addressee']}" if e.get("for_ai") else "") + (f" · board {e['cards']}" if e.get("cards") else "")
        return f"[{t}] #{e['id']} HEARD \"{e['text']}\"{extra}"
    if k == "attention":
        extra = ""
        if e.get("kind") == "attacked":
            extra = f" → {e.get('attacker') or '?'} for {e.get('amount')}{' TRAMPLE' if e.get('trample') else ''}: block or take it"
        elif e.get("kind") == "removal":
            extra = f" → {e.get('spell')} ({e.get('effect')}) on {e.get('target')}: " + (
                f"ILLEGAL — {e['illegal']} (say so)" if e.get("illegal") else "apply it")
        mine = not SEAT or not e.get("addressee") or e["addressee"].lower() == SEAT.lower()
        who = "FOR YOU" if mine else f"for {e['addressee']}"
        return f"[{t}] #{e['id']} ⚑ {who} ({e['kind']}): \"{e['text']}\"{extra}"
    if k == "shown":
        return f"[{t}] #{e['id']} SHOWN {e['card']} (by {e['by']})"
    if k == "say":
        return f"[{t}] #{e['id']} SAID ({e['speaker']}): {e['text']}"
    if k == "life":
        return f"[{t}] #{e['id']} LIFE {e['player']} {e['delta']:+d} → {e['life']} (by {e['by']})"
    if k == "captain":
        return f"[{t}] #{e['id']} ⚓ {e.get('captain')} ({e.get('seat')}): {e.get('text')}"
    if k == "highroll":
        rr = e.get("rolls") or {}
        last = rr.get(str(max(map(int, rr))) if rr else "1", {})
        rolls = ", ".join(f"{n} {v}" for n, v in last.items())
        how = "⚛️ quantum" if e.get("mode") == "quantum" else "🎲 real dice"
        if e.get("winner"):
            return f"[{t}] #{e['id']} {how} HIGH ROLL: {rolls} → {e['winner']} goes first ({' → '.join(e.get('order') or [])})"
        return f"[{t}] #{e['id']} {how} HIGH ROLL round {e.get('round')}: {rolls or 'waiting'}" + (f" — waiting on {', '.join(e.get('waiting_on') or [])}" if e.get("waiting_on") else "")
    if k == "autopass":
        return f"[{t}] #{e['id']} 🤖 {e.get('by')} auto-pass: {e.get('mode')}"
    if k == "pass":
        if e.get("kind") == "reset":
            return f"[{t}] #{e['id']} ✋ PRIORITY round again — {e.get('why')}"
        return f"[{t}] #{e['id']} ✋ {e.get('by')} passes ({e.get('player')} · {e.get('step')})" + (f" — waiting on {', '.join(e['left'])}" if e.get("left") else "")
    if k == "chat":
        return f"[{t}] #{e['id']} {'🗨 TALK' if e.get('talk') else '💬 CHAT'} {e.get('by')}: {e.get('text')}" + (f"  📷 {e['photo']}" if e.get("photo") else "")
    if k == "declare":
        return f"[{t}] #{e['id']} ⚔ DECLARED by {e.get('by')}: " + "; ".join(f"{a['attacker']} ({a.get('power')}) → {a['target']}" for a in e.get('attacks', []))
    if k == "todo" and e.get("kind") == "answered":
        return (f"[{t}] #{e['id']} 💬 ANSWER to {e.get('ask')}'s question \"{e.get('question')}\": "
                f"\"{e.get('answer')}\" (by {e.get('by') or '?'}){' — resolved' if e.get('resolved') else ''}")
    if k == "phase" and e.get("kind") == "hold":
        return f"[{t}] #{e['id']} ⏸ HOLD {'ON' if e.get('on') else 'OFF'} (by {e.get('by') or '?'})"
    if k == "phase" and e.get("kind") == "windows":
        return f"[{t}] #{e['id']} STEP TIMEOUTS {'ON' if e.get('on') else 'OFF'} (by {e.get('by') or '?'})"
    if k == "phase":
        return f"[{t}] #{e['id']} PHASE {e.get('player')} · {e.get('step')}" + (" (BACK)" if e.get("back") else "")
    return f"[{t}] #{e['id']} {k.upper()}"


def cmd_state(_):
    s = req("GET", "/api/brain/state" + (f"?seat={urllib.parse.quote(SEAT)}" if SEAT else ""))
    print(f"== {s['name']} ({s['commander']}) — turn {s['turn']}, life {s['life']}, library {s['library']}, "
          f"land played: {s['land_played']}")
    print(f"life: " + ", ".join(f"{k} {v}" for k, v in s["life_table"].items()))
    print(f"mana sources untapped: {', '.join(s['mana_sources']) or 'none'}")
    c = s["commander_card"]
    print(f"command zone: {c['name'] if s['commander_in_zone'] else '(on battlefield)'} {c['cost']} "
          f"tax {s['commander_tax']}{' — CASTABLE' if c['castable_now'] and s['commander_in_zone'] else ''}")
    print("\nHAND (private):")
    for h in s["hand"]:
        flag = "LAND" if h["land"] else ("castable" if h["castable_now"] else "")
        print(f"  • {h['name']} {h['cost']} — {h['type']}{' ' + h['pt'] if h['pt'] else ''}  [{flag}]")
        if h["text"] and not h["land"]:
            print("      " + h["text"].replace("\n", " / ")[:240])
        if h.get("aura_targets") is not None:
            print(f"      can enchant: {', '.join(h['aura_targets']) or 'nothing right now'}")
    print("\nBATTLEFIELD:")
    for p in s["permanents"]:
        if "Land" in (p["type"] or "") and not p["tapped"]:
            continue
        att = f" → on #{p['attached_to']}" if p["attached_to"] else ""
        fd = f" [FACE DOWN — turn up: {p.get('turn_up_cost')}]" if p.get("face_down") else ""
        print(f"  #{p['id']} {p['name']}{fd}{' ' + p['pt'] if p['pt'] else ''}{att}"
              f"{' [tapped]' if p['tapped'] else ''}{' [sick]' if p['sick'] and p['pt'] else ''}")
    lands = [p for p in s["permanents"] if "Land" in (p["type"] or "")]
    print(f"  lands: {len(lands)} ({sum(1 for p in lands if not p['tapped'])} untapped)")
    print(f"\ngraveyard: {', '.join(s['graveyard']) or '—'}")
    print(f"others' announced cards: {', '.join(s['announced_by_others']) or '—'}")
    print("\nrecent table events:")
    for e in s["recent_events"]:
        print("  " + fmt_event(e))


def cmd_events(a):
    d = req("GET", f"/api/events?since={a.since}", brain=False)
    for e in d["events"]:
        print(fmt_event(e))
    print(f"(last event #{d['last']})")


def cmd_watch(a):
    """Follow the event stream forever; one line per event, flushed — built for a live monitor.
    Skips the AI's own speech (you already know what you said)."""
    since = req("GET", "/api/events?since=latest", brain=False)["last"] if a.since is None else a.since
    while True:
        try:
            d = req("GET", f"/api/events?since={since}", brain=False)
        except (SystemExit, OSError) as e:                 # server restarting: wait and carry on
            print(f"[watch] {e}", flush=True)
            time.sleep(5)
            continue
        for e in d["events"]:
            since = e["id"]
            if e["type"] == "say" and not a.all:
                continue
            print(fmt_event(e), flush=True)
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seat", help="which AI seat this brain plays (two-AI tables; or set TABLE_SEAT)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("state")
    e = sub.add_parser("events"); e.add_argument("--since", default="0")
    w = sub.add_parser("watch"); w.add_argument("--since", type=int); w.add_argument("--all", action="store_true")
    s = sub.add_parser("say"); s.add_argument("text"); s.add_argument("--force", action="store_true")
    sub.add_parser("begin").add_argument("--quiet", action="store_true")
    x = sub.add_parser("land"); x.add_argument("name"); x.add_argument("--quiet", action="store_true")
    c = sub.add_parser("cast"); c.add_argument("name"); c.add_argument("--on"); c.add_argument("--role-on")
    c.add_argument("--mode", action="append", type=int); c.add_argument("--targets"); c.add_argument("--x", type=int, default=0)
    c.add_argument("--discount", type=int, default=0, help="generic cost reduction you know applies")
    c.add_argument("--face-down", action="store_true", help="disguise/morph: cast it face down for {3}")
    tu = sub.add_parser("turn-up", help="turn a face-down permanent face up (pays its cost)"); tu.add_argument("ref")
    tu.add_argument("--free", action="store_true", help="no cost (Kaust's {T} ability, Showstopping Surprise)")
    mf = sub.add_parser("manifest", help="top card(s) face down as 2/2s"); mf.add_argument("--n", type=int, default=1)
    mf.add_argument("--cloak", action="store_true", help="cloak: ward 2")
    c.add_argument("--color", help="for 'choose a color' (W/U/B/R/G), e.g. Utopia Sprawl")
    c.add_argument("--quiet", action="store_true")
    bl = sub.add_parser("block"); bl.add_argument("ref", nargs="?"); bl.add_argument("--amount", type=int, required=True)
    bl.add_argument("--trample", action="store_true"); bl.add_argument("--attacker")
    at = sub.add_parser("attack"); at.add_argument("pairs", nargs="+", help='"Creature=Player"'); at.add_argument("--role-on")
    dm = sub.add_parser("damage", help='attackers that hit a player: "Ellivere=Michael" "#14=Sam:5"')
    dm.add_argument("hits", nargs="+")
    dm.add_argument("--no-life", action="store_true", help="triggers only: the players already said their life")
    ro = sub.add_parser("role"); ro.add_argument("ref"); ro.add_argument("kind")
    en = sub.add_parser("end"); en.add_argument("text", nargs="?")
    sub.add_parser("pass", help="answer a priority window: no instant this time")
    td = sub.add_parser("todo", help='ask the physical table to do something: todo "Move Hunted Horror to your graveyard" --for Sam')
    td.add_argument("text", nargs="+"); td.add_argument("--for", dest="for_", default="")
    ak = sub.add_parser("ask", help='a question to the table, shown as coming from this seat: ask "Which card?" --option "Sol Ring"')
    ak.add_argument("text"); ak.add_argument("--option", action="append", default=[]); ak.add_argument("--to", default="")
    go = sub.add_parser("graveyard-out", help="a card leaves your graveyard (stolen, exiled, returned)")
    go.add_argument("name"); go.add_argument("--to", default="", help='e.g. "to Sam\'s battlefield"')
    sub.add_parser("phase", help="whose turn, which step, who still holds priority")
    pb = sub.add_parser("public-board", help="record a HUMAN's board for the 3D view (from photos / what they said)")
    pb.add_argument("seat"); pb.add_argument("cards", nargs="*", help='"Swamp" "Mountain:tapped" "Centaur:token:3/3" "Nihilith:suspended:5"')
    pb.add_argument("--graveyard", default="", help="oldest first; comma-separated, or semicolon-separated when a name has a comma"); pb.add_argument("--commander-out", action="store_true")
    nx = sub.add_parser("next", help="press NEXT for the active human (testing / remote play)"); nx.add_argument("--by")
    for verb in ("destroy", "exile", "bounce", "tap", "untap"):
        v = sub.add_parser(verb); v.add_argument("ref"); v.add_argument("--quiet", action="store_true")
    co = sub.add_parser("counter"); co.add_argument("ref"); co.add_argument("n", type=int)
    t = sub.add_parser("animate"); t.add_argument("ref"); t.add_argument("power"); t.add_argument("toughness"); t.add_argument("keywords", nargs="*")
    t = sub.add_parser("take"); t.add_argument("name"); t.add_argument("--from", dest="from_", required=True)
    t = sub.add_parser("token"); t.add_argument("name"); t.add_argument("power", type=int); t.add_argument("toughness", type=int)
    t.add_argument("--n", type=int, default=1); t.add_argument("--tapped", action="store_true")
    pk = sub.add_parser("peek", help="PRIVATE: the top N cards of your library"); pk.add_argument("n", type=int)
    pk.add_argument("--announce", action="store_true")
    td = sub.add_parser("topdeck", help="a card from hand onto the top of your library"); td.add_argument("name")
    bt = sub.add_parser("bottom", help="a card to the bottom (from hand, or --from-top)"); bt.add_argument("name")
    bt.add_argument("--from-top", action="store_true")
    sub.add_parser("shuffle")
    hr = sub.add_parser("highroll", help="who goes first: highroll quantum | highroll physical | highroll roll NAME VALUE")
    hr.add_argument("what", choices=["quantum", "physical", "roll", "show"]); hr.add_argument("name", nargs="?"); hr.add_argument("value", nargs="?")
    sub.add_parser("mulligan", help="before the game: hand back, shuffle, draw seven (first one free; then 'bottom' one card per extra)")
    pu = sub.add_parser("put", help="hand → battlefield without paying (ninjutsu)"); pu.add_argument("name")
    pu.add_argument("--tapped", action="store_true")
    bl = sub.add_parser("blink", help="exile a permanent and return it (ETBs trigger again)"); bl.add_argument("ref")
    t.add_argument("keywords", nargs="*")
    d = sub.add_parser("draw"); d.add_argument("n", type=int, nargs="?", default=1)
    di = sub.add_parser("discard"); di.add_argument("name")
    m = sub.add_parser("mill"); m.add_argument("n", type=int)
    se = sub.add_parser("search"); se.add_argument("name"); se.add_argument("--to", default="hand", choices=["hand", "battlefield"])
    se.add_argument("--tapped", action="store_true")
    l = sub.add_parser("life"); l.add_argument("player"); l.add_argument("delta", type=int)
    sub.add_parser("new-game")
    sub.add_parser("fair", help="the AI decks' published fingerprints (and the full proof once revealed)")
    sub.add_parser("fair-reveal", help="after the game: publish the seeds and orders")
    fv = sub.add_parser("fair-verify", help="check a revealed record yourself, offline"); fv.add_argument("file")
    a = ap.parse_args()
    global SEAT
    if getattr(a, "seat", None):
        SEAT = a.seat

    if a.cmd == "state":
        return cmd_state(a)
    if a.cmd == "events":
        return cmd_events(a)
    if a.cmd == "watch":
        return cmd_watch(a)
    q = getattr(a, "quiet", False)
    if a.cmd == "say":
        return act("say", text=a.text, force=a.force)
    if a.cmd == "begin":
        return act("begin", quiet=q)
    if a.cmd == "land":
        return act("land", name=a.name, quiet=q)
    if a.cmd == "cast":
        if a.face_down:
            return act("cast", name=a.name, face_down=True)
        return act("cast", name=a.name, on=a.on, role_on=a.role_on, modes=a.mode, targets=a.targets, x=a.x,
                   discount=a.discount, color=a.color,
                   commander=a.name.lower() == "commander", quiet=q)
    if a.cmd == "attack":
        assign = dict(p.split("=", 1) for p in a.pairs)
        return act("attack", assign=assign, role_on=a.role_on)
    if a.cmd == "block":
        return act("block", blocker=a.ref, amount=a.amount, trample=a.trample, attacker=a.attacker)
    if a.cmd == "damage":
        hits = {}
        for h in a.hits:
            ref, rest = h.split("=", 1)
            who, _, n = rest.partition(":")
            hits[ref] = [who, int(n) if n else None]
        return act("damage", hits=hits, no_life=a.no_life)
    if a.cmd == "role":
        return act("role", ref=a.ref, kind=a.kind)
    if a.cmd == "end":
        return act("end", text=a.text)
    if a.cmd == "pass":
        return act("pass", quiet=True)
    if a.cmd == "ask":
        r = req("POST", "/api/todo", {"items": [{"text": a.text, "for": a.to, "ask": a.seat or SEAT or "Claude", "options": a.option}]})
        print(json.dumps(r["added"])); return
    if a.cmd == "todo":                         # one item per argument
        print(json.dumps(req("POST", "/api/todo", {"items": [{"text": t, "for": a.for_} for t in a.text]})["added"])); return
    if a.cmd == "graveyard-out":
        return act("graveyard-out", name=a.name, to=a.to)
    if a.cmd == "phase":
        print(json.dumps(req("GET", "/api/phase", brain=False), indent=1)); return
    if a.cmd == "public-board":
        perms = []
        for spec in a.cards:                     # Name[:tapped][:token][:N/N][:suspended][:<counters>]
            name, *flags = [x.strip() for x in spec.split(":")]
            p = {"name": name}
            for fl in flags:
                if fl in ("tapped", "token", "face_down"): p[fl] = True
                elif re.fullmatch(r"-?\d+/-?\d+", fl): p["pt"] = fl
                elif re.fullmatch(r"[+-]?\d+", fl): p["counters"] = int(fl)
                elif fl: p["zone" if fl in ("suspended", "exiled", "foretold") else "note"] = fl
            perms.append(p)
        gy = [x.strip() for x in a.graveyard.split(";" if ";" in a.graveyard else ",") if x.strip()]   # ";" keeps "Zellix, Sanity Flayer" whole
        print(json.dumps(req("POST", "/api/public-board", {"seat": a.seat, "permanents": perms, "graveyard": gy,
                                                            "commander_out": a.commander_out}))); return
    if a.cmd == "next":
        print(json.dumps(req("POST", "/api/phase/next", {"by": a.by} if a.by else {}, brain=False), indent=1)); return
    if a.cmd in ("destroy", "exile", "bounce", "tap", "untap"):
        return act(a.cmd, ref=a.ref, quiet=q, announce=not q)
    if a.cmd == "counter":
        return act("counter", ref=a.ref, n=a.n)
    if a.cmd == "animate":
        return act("animate", ref=a.ref, power=a.power, toughness=a.toughness, keywords=a.keywords)
    if a.cmd == "take":
        return act("take", name=a.name, **{"from": a.from_})
    if a.cmd == "token":
        return act("token", name=a.name, power=a.power, toughness=a.toughness, keywords=a.keywords, n=a.n,
                   tapped=a.tapped)
    if a.cmd == "turn-up":
        return act("turn-up", ref=a.ref, free=a.free)
    if a.cmd == "manifest":
        return act("manifest", n=a.n, cloak=a.cloak)
    if a.cmd == "peek":
        return act("peek", n=a.n, announce=a.announce)
    if a.cmd == "topdeck":
        return act("topdeck", name=a.name)
    if a.cmd == "bottom":
        return act("bottom", name=a.name, from_top=a.from_top)
    if a.cmd == "shuffle":
        return act("shuffle")
    if a.cmd == "highroll":
        if a.what == "show":
            return print(json.dumps(req("GET", "/api/highroll"), indent=1))
        if a.what == "roll":
            return print(json.dumps(req("POST", "/api/highroll/roll", {"player": a.name, "value": a.value, "by": a.seat}), indent=1))
        return print(json.dumps(req("POST", "/api/highroll/start", {"mode": a.what, "sides": 20, "by": a.seat}), indent=1))
    if a.cmd == "mulligan":
        return act("mulligan")
    if a.cmd == "put":
        return act("put", name=a.name, tapped=a.tapped)
    if a.cmd == "blink":
        return act("blink", ref=a.ref)
    if a.cmd == "draw":
        return act("draw", n=a.n)
    if a.cmd == "discard":
        return act("discard", name=a.name)
    if a.cmd == "mill":
        return act("mill", n=a.n)
    if a.cmd == "search":
        return act("search", name=a.name, to=a.to, tapped=a.tapped)
    if a.cmd == "life":
        return act("life", player=a.player, delta=a.delta)
    if a.cmd == "new-game":
        return act("new-game")
    if a.cmd == "fair":
        print(json.dumps(req("GET", "/api/fair", brain=False), indent=1))
        return
    if a.cmd == "fair-reveal":
        return act("fair-reveal")
    if a.cmd == "fair-verify":
        sys.path.insert(0, str(Path(__file__).parent))
        import fair
        ok, msg = fair.verify(json.loads(Path(a.file).read_text()))
        print(("✅ " if ok else "❌ ") + msg)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
