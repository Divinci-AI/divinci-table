#!/usr/bin/env python3
"""An AI player at a D&D table: it claims its own seat and plays one character through the same HTTP API a phone uses.

    python table/dnd_agent.py --base http://127.0.0.1:8810 --name Gemma --model gemma4:e2b --pregen fighter \
        --key-file ~/.divinci/dnd/gemma.key

The model is reached through Ollama (local models, or Ollama's `:cloud` ones). The table's rules hold for it as for a
person: it moves only its own token, applies damage to its own sheet, rolls with the table's fair dice, and never
speaks for anyone else. What it sees is what any seat sees: the public state and the scene log.

When it acts (rules in code, so a quiet model can't flood the table and a chatty one can't take over):
  • its own turn in combat; a roll of initiative when the table asks for one (no model call: d20 + Dex);
  • the DM names it; or the DM hands the scene to the whole party and nobody else has answered in a few seconds.
It acts once per DM beat at most, waits for the DM to finish speaking, and stops after --max-acts.
The seat key goes to --key-file (0600) and is never printed or logged.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True, help="the table, e.g. http://127.0.0.1:8810")
ap.add_argument("--name", required=True, help="the seat to claim (a name in the table's --players)")
ap.add_argument("--model", required=True, help="an Ollama model, e.g. gemma4:e2b or glm-5.3:cloud")
ap.add_argument("--pregen", default="", help="fighter, rogue, cleric or wizard")
ap.add_argument("--persona", default="", help="a line about how this character talks and what it wants")
ap.add_argument("--key-file", required=True)
ap.add_argument("--max-acts", type=int, default=40)
ap.add_argument("--patience", type=float, default=6.0, help="seconds to let others answer the party first")
ap.add_argument("--min-gap", type=float, default=12.0, help="seconds between two of this seat's actions (its turn and rolls asked of it excepted)")
ap.add_argument("--turn-acts", type=int, default=3, help="model calls on one combat turn before the seat ends it")
ap.add_argument("--ollama", default=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"))
ap.add_argument("--log", default="", help="a file for what it saw and did (never the key)")
args = ap.parse_args()

UA = f"dnd-agent/{args.name}"


def log(*a) -> None:
    line = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(line, flush=True)
    if args.log:
        with open(args.log, "a") as f:
            f.write(line + "\n")


def call(method: str, path: str, body: dict | None = None, timeout: float = 30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(args.base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return 0, {}


def key() -> str:
    if os.path.exists(args.key_file):
        return open(args.key_file).read().strip()
    code, out = call("POST", "/api/seat/claim", {"name": args.name})
    if code != 200 or not out.get("key"):
        sys.exit(f"{args.name}: couldn't claim the seat ({code} {out.get('error')})")
    fd = os.open(args.key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(out["key"])
    return out["key"]


KEY = ""                     # set in main(): importing this file (the tests) claims nothing


def post(path: str, body: dict):
    return call("POST", path, {"by": args.name, "key": KEY, **body})


def mod(score: int) -> int:
    return (int(score) - 10) // 2


def ask(prompt: str) -> dict:
    schema = {"type": "object", "properties": {
        "say": {"type": "string"}, "roll": {"type": ["object", "null"], "properties": {"dice": {"type": "string"}, "why": {"type": "string"}}},
        "move": {"type": ["object", "null"], "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}},
        "hp": {"type": ["integer", "null"]}, "end_turn": {"type": "boolean"}}, "required": ["say"]}
    body = json.dumps({"model": args.model, "stream": False, "format": schema, "think": False,
                       "messages": [{"role": "user", "content": prompt}], "options": {"temperature": 0.8},
                       "keep_alive": "30m"}).encode()
    req = urllib.request.Request(args.ollama + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read())
    text = (d.get("message") or {}).get("content") or "{}"
    try:
        out = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        out = json.loads(m.group(0)) if m else {"say": ""}
    log(f"model {args.model} {time.time() - t:.1f}s")
    return out if isinstance(out, dict) else {"say": ""}


def lines(events: list) -> list[str]:
    out = []
    for e in events:
        if e["type"] == "dm":
            out.append(f"DM: {e['text']}")
        elif e["type"] == "say":
            out.append(f"{e['by']}{'' if e.get('ic') else ' (out of character)'}: {e['text']}")
        elif e["type"] in ("roll", "answer") or (e["type"] == "dnd" and e.get("text")):
            out.append(f"TABLE: {e.get('text', '')}")
    return out


def hp_said(text: str) -> int | None:
    """Damage or healing the DM just reported for this character, e.g. "5 slashing damage to Gemma", "Gemma takes 7
    damage", "Gemma regains 6 hit points". The player owns the number; this is the player reading it off the narration."""
    n = re.escape(args.name)
    for pat, sign in ((rf"(\d+)\s+(?:points? of\s+)?(?:[a-z]+\s+)?damage\s+(?:to|on)\s+{n}\b", -1),
                      (rf"\b{n}\s+(?:takes|suffers)\s+(\d+)\s+(?:points? of\s+)?(?:[a-z]+\s+)?damage", -1),
                      (rf"\b{n}\s+(?:regains|heals|recovers)\s+(\d+)", 1)):
        m = re.search(pat, text, re.I)
        if m:
            return sign * int(m.group(1))
    return None


SKILLS = {"athletics": "str", "acrobatics": "dex", "sleight of hand": "dex", "stealth": "dex", "arcana": "int", "history": "int",
          "investigation": "int", "nature": "int", "religion": "int", "animal handling": "wis", "insight": "wis",
          "medicine": "wis", "perception": "wis", "survival": "wis", "deception": "cha", "intimidation": "cha",
          "performance": "cha", "persuasion": "cha"}
ABILITY = {"strength": "str", "dexterity": "dex", "constitution": "con", "intelligence": "int", "wisdom": "wis", "charisma": "cha"}


def asked_dice(text: str, me: dict) -> tuple[str, str] | None:
    """The exact roll the DM just asked THIS seat for, when it says which: "Gemma, roll 1d8 plus 3 for your longsword
    damage" → ("1d8+3", "longsword damage"); "Claude, roll a Dexterity saving throw" → ("d20+2", "Dexterity saving
    throw"). Small models answered a damage request with another attack roll (2026-11-15); this needs no model."""
    sents = re.split(r"(?<=[.!?])\s+", text)
    for i, sent in enumerate(sents):
        if not re.search(r"\broll|saving throw|\bsave\b|\bcheck\b", sent, re.I):
            continue
        mine = re.search(rf"\b{re.escape(args.name)}\b", sent, re.I)
        other = re.match(r"^\s*([A-Z][\w']+),", sent)                        # "GLM, roll…" is GLM's, not ours
        if not mine and not (i and re.search(rf"\b{re.escape(args.name)}\b", sents[i - 1], re.I)
                             and not (other and other.group(1).lower() != args.name.lower())):
            continue                                                          # "Gemma, a hit! Please roll 1d8+3…" is ours
        m = re.search(r"\b(\d{0,2})d(4|6|8|10|12|20|100)\b(?:\s*(?:plus|\+)\s*(\d{1,2})\b)?(?:\s*(?:plus|\+)\s*(?:(\d{0,2})d(4|6|8|10|12|20)))?", sent, re.I)
        why = (re.search(r"\bfor (?:your |the |a |an )?([a-z' ]{3,50})", sent, re.I) or [None, ""])[1].strip()
        ab = {k: (int(v) - 10) // 2 for k, v in (me.get("abilities") or {}).items()}
        if m and (m.group(3) or m.group(5) or m.group(2) != "20"):           # spelled out: roll exactly that
            dice = f"{m.group(1)}d{m.group(2)}" + (f"+{m.group(4)}d{m.group(5)}" if m.group(5) else "") + (f"+{m.group(3)}" if m.group(3) else "")
            return dice, (why or "as asked")[:60]
        low = sent.lower()
        for name, key in ABILITY.items():                                     # "a Dexterity saving throw / check"
            if re.search(rf"\b{name}\b", low) and re.search(r"saving throw|\bsave\b|check", low):
                if not re.search(r"\(\s*(" + "|".join(SKILLS) + r")\s*\)", low):
                    return f"d20{ab.get(key, 0):+d}", f"{name.title()} {'saving throw' if 'sav' in low else 'check'}"
        for skill, key in SKILLS.items():                                     # "(Perception) check", "Stealth check"
            if re.search(rf"\b{skill}\b", low):
                bonus = ab.get(key, 0) + (int(me.get("proficiency") or 2) if skill in [x.lower() for x in me.get("skills") or []] else 0)
                return f"d20{bonus:+d}", f"{skill.title()} check"
    return None


def tokens(st: dict) -> list[dict]:
    tk = (st.get("map") or {}).get("tokens") or {}
    return list(tk.values()) if isinstance(tk, dict) else list(tk)


def prompt_for(st: dict, recent: list[str], why: str, refused: str, this_turn: list[str]) -> str:
    me = st["sheets"][args.name]
    a = me.get("abilities") or {}
    mods = ", ".join(f"{k} {mod(v):+d}" for k, v in a.items())
    toks = tokens(st)
    mine = next((t for t in toks if t.get("owner") == args.name), None)
    where = [f"{t['name']} at ({t['x']},{t['y']})" + (" (you)" if t is mine else "") for t in toks]
    ini = st.get("initiative") or {}
    order = [o["name"] for o in ini.get("order", [])] if ini.get("active") else []
    return "\n".join([
        f"You are {args.name}, a player at a Dungeons & Dragons table, playing your character: a level {me.get('level')} "
        f"{me.get('race')} {me.get('class')}. {args.persona}".strip(),
        f"Your sheet: HP {me.get('hp')}/{me.get('max_hp')}, AC {me.get('ac')}, speed {me.get('speed')} ft, proficiency "
        f"+{me.get('proficiency')}, ability modifiers: {mods}. Skills: {', '.join(me.get('skills') or [])}.",
        f"Attacks: {json.dumps(me.get('attacks') or [])}. Spells: {json.dumps(me.get('spells') or [])}. Notes: {me.get('notes', '')}",
        f"Conditions: {', '.join(me.get('conditions') or []) or 'none'}.",
        f"Scene: {st.get('scene') or 'just starting'}. Monsters: {json.dumps(st.get('monsters') or [])}.",
        (f"Combat, round {ini.get('round')}: order {', '.join(order)}; it is {order[ini.get('turn', 0)]}'s turn." if order else "No combat right now."),
        (f"The map (squares of 5 ft; x right, y down): {'; '.join(where)}." if where else ""),
        "", "What happened lately (oldest first):", *[f"- {x}" for x in recent[-24:]], "",
        f"Why you're being asked now: {why}",
        (f"On this combat turn you have already: {'; '.join(this_turn)}. One action per turn (an attack or a spell), "
         "plus your move and at most one bonus action: if your action is done, roll damage only if the DM said you hit, "
         "otherwise end your turn." if this_turn else ""),
        (f"Your last attempt was refused by the table: {refused}" if refused else ""),
        "",
        "Answer as JSON. \"say\": what your character says or does, in the first person, 1-3 short sentences, or \"\" to "
        "stay quiet. \"roll\": only when the DM asked YOU for a roll or you make an attack this turn, e.g. "
        "{\"dice\":\"d20+5\",\"why\":\"Longsword attack on Goblin 1\"} using YOUR modifiers (damage dice only after the DM "
        "says you hit); else null. \"move\": a square {\"x\":..,\"y\":..} only on your own combat turn, within your speed; "
        "else null. \"hp\": a change to YOUR hit points only if the DM just said you took damage (negative) or were healed "
        "(positive); else null. \"end_turn\": true only on your own combat turn once your action is done and resolved.",
        "To attack on your turn, say what you do AND give the attack roll in the same answer. Never speak or decide for "
        "other players, the DM or the monsters. Don't repeat what you already said. Play to have fun and keep the story moving.",
    ])


def main() -> None:
    global KEY
    KEY = key()
    if args.pregen:
        code, out = post("/api/dnd/sheet", {"pregen": args.pregen})
        log("sheet", args.pregen, code, out.get("error", ""))
    st = call("GET", "/api/dnd")[1]
    since = call("GET", "/api/events?since=latest")[1].get("last", 0)
    history: list = []
    acted = 0
    last_beat = None          # the id of the DM line this seat last answered
    handled_turn = None       # (round, turn) of the combat turn this seat last took up
    turn_calls = 0            # model calls on the current combat turn
    this_turn: list = []      # what this seat did on it, for the model
    last_act = 0.0
    hp_beats: set = set()     # DM lines whose damage/healing for this seat has been applied
    refused = ""
    while acted < args.max_acts:
        code, out = call("GET", f"/api/events?since={since}&wait=20", timeout=30)
        if code != 200:
            time.sleep(2)
            continue
        evs = [e for e in out.get("events", []) if e.get("type") != "dm_part"]
        since = out.get("last", since)
        history += evs
        if not evs:
            continue
        st = call("GET", "/api/dnd")[1]
        if st.get("dm_busy"):
            continue
        ini = st.get("initiative") or {}
        me = st["sheets"].get(args.name) or {}
        if ini.get("active") and args.name in ini.get("pending", []):        # the table asked for initiative: d20 + Dex, no model
            r = post("/api/dnd/roll", {"dice": f"d20{mod(me['abilities']['dex']):+d}", "why": "initiative"})
            log("initiative", r[0], (r[1].get("roll") or {}).get("total"))
            continue
        dms_all = [e for e in history if e["type"] == "dm"]
        if (not ini.get("active") and dms_all and dms_all[-1]["id"] != last_beat
                and re.search(r"roll(s|ing)? (for )?initiative", dms_all[-1]["text"], re.I)):
            last_beat = dms_all[-1]["id"]                              # the DM called for initiative: start it (no model call);
            time.sleep(random.uniform(0.5, 2.5))                       # the table refuses a second start, so a race is harmless
            r = post("/api/dnd/initiative", {"action": "start"})
            log("start initiative", r[0], r[1].get("error", ""))
            continue
        order = [o["name"] for o in ini.get("order", [])]
        my_turn = bool(ini.get("active") and order and not ini.get("pending") and order[ini.get("turn", 0)] == args.name)
        dms = [e for e in history if e["type"] == "dm"]
        beat = dms[-1] if dms else None
        fresh = bool(beat and beat["id"] != last_beat)
        tail = re.split(r"(?<=[.!?])\s+", beat["text"].strip())[-2:] if beat else []
        direct = bool(fresh and any(re.search(rf"\b{re.escape(args.name)}\b", t, re.I) for t in tail))   # spoken TO, not just about
        turn_key = (ini.get("round"), ini.get("turn"))
        why = ""
        said = hp_said(beat["text"]) if fresh and beat["id"] not in hp_beats else None
        if said:                                                        # apply what the DM reported, once
            hp_beats.add(beat["id"])
            r = post("/api/dnd/hp", {"delta": said})
            log("hp (from the narration)", said, r[0], r[1].get("error", ""))
        if my_turn and turn_key != handled_turn:
            handled_turn, turn_calls, this_turn = turn_key, 0, []
        if my_turn and turn_calls >= args.turn_acts:                    # a turn can't run forever: pass it on
            r = post("/api/dnd/initiative", {"action": "next"})
            log("end turn (turn limit)", r[0])
            turn_calls = 0
            continue
        asked_roll = bool(direct and re.search(r"\broll|saving throw|\bsave\b|check\b", " ".join(tail), re.I))
        if not my_turn and not asked_roll and time.time() - last_act < args.min_gap:
            continue
        if my_turn and (turn_calls == 0 or fresh):
            why = "It is your turn in combat." + (" The DM just answered your last action." if turn_calls else "")
            turn_calls += 1
        elif direct and (not ini.get("active") or asked_roll):
            why = "The DM just spoke to you by name." + (" It asks for a DAMAGE roll: roll your weapon's damage dice."
                                                         if re.search(r"damage", " ".join(tail), re.I) else "")
        elif fresh and not ini.get("active") and re.search(r"\?\s*$|what do you do|your move", beat["text"], re.I):
            time.sleep(args.patience * random.uniform(0.8, 1.4))      # let the others (and the people) answer first
            newer = call("GET", f"/api/events?since={beat['id']}")[1].get("events", [])
            if any(e.get("type") == "say" and e.get("ic") for e in newer) or call("GET", "/api/dnd")[1].get("dm_busy"):
                last_beat = beat["id"]                                 # someone else took it; wait for the next beat
                continue
            why = "The DM asked the party what they do; nobody has answered yet."
        if not why:
            continue
        exact = asked_dice(beat["text"], me) if (fresh and (direct or my_turn)) else None
        if exact:                                                       # the DM said which dice: roll them, no model call
            last_beat = beat["id"]
            r = post("/api/dnd/roll", {"dice": exact[0], "why": exact[1]})
            log("roll (as asked)", exact, r[0], (r[1].get("roll") or {}).get("total"), r[1].get("error", ""))
            last_act = time.time()
            if my_turn:
                this_turn.append(f"rolled {exact[0]} for {exact[1]} = {(r[1].get('roll') or {}).get('total')}")
            continue
        last_beat = beat["id"] if beat else last_beat
        try:
            act = ask(prompt_for(st, lines(history), why, refused, this_turn if my_turn else []))
        except Exception as e:                                          # the model is down or slow: stay quiet, try next beat
            log("model error", type(e).__name__, str(e)[:120])
            continue
        refused = ""
        acted += 1
        last_act = time.time()
        if isinstance(act.get("hp"), int) and act["hp"] and not said:
            r = post("/api/dnd/hp", {"delta": max(-200, min(200, act["hp"]))})
            log("hp", act["hp"], r[0], r[1].get("error", ""))
        mv = act.get("move")
        if my_turn and isinstance(mv, dict) and isinstance(mv.get("x"), int) and isinstance(mv.get("y"), int):
            mine = next((t for t in tokens(st) if t.get("owner") == args.name), None)
            if mine:
                r = post("/api/dnd/map/move", {"token": mine["id"], "x": mv["x"], "y": mv["y"]})
                log("move", mv, r[0], r[1].get("error", ""))
                if r[0] != 200:
                    refused = f"move to ({mv['x']},{mv['y']}): {r[1].get('error')}"
        rl = act.get("roll")
        say = (act.get("say") or "").strip()
        if asked_roll and isinstance(rl, dict) and rl.get("dice"):      # the DM asked for this roll: the roll is the answer
            say = ""
        if say:                                                         # declare first, then roll: as at a real table
            r = post("/api/dnd/act", {"text": say[:600]})
            log("say", repr(say[:160]), r[0], r[1].get("error", ""))
            if my_turn:
                this_turn.append(f"said: {say[:80]}")
        if isinstance(rl, dict) and rl.get("dice"):
            r = post("/api/dnd/roll", {"dice": str(rl["dice"])[:20], "why": str(rl.get("why", ""))[:60]})
            log("roll", rl, r[0], (r[1].get("roll") or {}).get("total"), r[1].get("error", ""))
            if r[0] != 200:
                refused = f"roll {rl.get('dice')}: {r[1].get('error')}"
            elif my_turn:
                this_turn.append(f"rolled {rl.get('dice')} for {rl.get('why', '')} = {(r[1].get('roll') or {}).get('total')}")
        if my_turn and act.get("end_turn") and not (isinstance(rl, dict) and rl.get("dice")):
            r = post("/api/dnd/initiative", {"action": "next"})
            log("end turn", r[0])
    log(f"{args.name}: done after {acted} actions")


if __name__ == "__main__":
    main()
