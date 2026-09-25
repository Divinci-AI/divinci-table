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
  tablectl cast "Eidolon of Blossoms" --discount 1      (a cost reduction you know applies)
  tablectl attack "Ellivere=Michael" "#14=Sam" [--role-on NAME]
  tablectl block [REF] --amount 12 [--trample] [--attacker Ghalta]   (no REF = no block, take it)
  tablectl end ["text"]
  tablectl destroy|exile|bounce|tap|untap REF      corrections when opponents' cards affect yours
  tablectl counter REF N · token NAME P T [KW..] · draw N · discard NAME · mill N
  tablectl search NAME [--to battlefield] [--tapped]
  tablectl life PLAYER DELTA              e.g. life Michael -5 · life me +3
  tablectl new-game

REF is a permanent's name or '#id' from `state`. Add --quiet to act without announcing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("TABLE_URL", "http://127.0.0.1:8800")
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
    if code >= 400:
        sys.exit(f"refused: {data.get('error', raw[:200])}")
    return data


def act(action, **body):
    d = req("POST", f"/api/brain/{action}", {k: v for k, v in body.items() if v not in (None, [], False)})
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
        return f"[{t}] #{e['id']} ⚑ FOR YOU ({e['kind']}): \"{e['text']}\"{extra}"
    if k == "shown":
        return f"[{t}] #{e['id']} SHOWN {e['card']} (by {e['by']})"
    if k == "say":
        return f"[{t}] #{e['id']} SAID ({e['speaker']}): {e['text']}"
    if k == "life":
        return f"[{t}] #{e['id']} LIFE {e['player']} {e['delta']:+d} → {e['life']} (by {e['by']})"
    return f"[{t}] #{e['id']} {k.upper()}"


def cmd_state(_):
    s = req("GET", "/api/brain/state")
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
        print(f"  #{p['id']} {p['name']}{' ' + p['pt'] if p['pt'] else ''}{att}"
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
        except SystemExit as e:
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
    c.add_argument("--color", help="for 'choose a color' (W/U/B/R/G), e.g. Utopia Sprawl")
    c.add_argument("--quiet", action="store_true")
    bl = sub.add_parser("block"); bl.add_argument("ref", nargs="?"); bl.add_argument("--amount", type=int, required=True)
    bl.add_argument("--trample", action="store_true"); bl.add_argument("--attacker")
    at = sub.add_parser("attack"); at.add_argument("pairs", nargs="+", help='"Creature=Player"'); at.add_argument("--role-on")
    en = sub.add_parser("end"); en.add_argument("text", nargs="?")
    for verb in ("destroy", "exile", "bounce", "tap", "untap"):
        v = sub.add_parser(verb); v.add_argument("ref"); v.add_argument("--quiet", action="store_true")
    co = sub.add_parser("counter"); co.add_argument("ref"); co.add_argument("n", type=int)
    t = sub.add_parser("token"); t.add_argument("name"); t.add_argument("power", type=int); t.add_argument("toughness", type=int)
    t.add_argument("keywords", nargs="*")
    d = sub.add_parser("draw"); d.add_argument("n", type=int, nargs="?", default=1)
    di = sub.add_parser("discard"); di.add_argument("name")
    m = sub.add_parser("mill"); m.add_argument("n", type=int)
    se = sub.add_parser("search"); se.add_argument("name"); se.add_argument("--to", default="hand", choices=["hand", "battlefield"])
    se.add_argument("--tapped", action="store_true")
    l = sub.add_parser("life"); l.add_argument("player"); l.add_argument("delta", type=int)
    sub.add_parser("new-game")
    a = ap.parse_args()

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
        return act("cast", name=a.name, on=a.on, role_on=a.role_on, modes=a.mode, targets=a.targets, x=a.x,
                   discount=a.discount, color=a.color,
                   commander=a.name.lower() == "commander", quiet=q)
    if a.cmd == "attack":
        assign = dict(p.split("=", 1) for p in a.pairs)
        return act("attack", assign=assign, role_on=a.role_on)
    if a.cmd == "block":
        return act("block", blocker=a.ref, amount=a.amount, trample=a.trample, attacker=a.attacker)
    if a.cmd == "end":
        return act("end", text=a.text)
    if a.cmd in ("destroy", "exile", "bounce", "tap", "untap"):
        return act(a.cmd, ref=a.ref, quiet=q, announce=not q)
    if a.cmd == "counter":
        return act("counter", ref=a.ref, n=a.n)
    if a.cmd == "token":
        return act("token", name=a.name, power=a.power, toughness=a.toughness, keywords=a.keywords)
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


if __name__ == "__main__":
    main()
