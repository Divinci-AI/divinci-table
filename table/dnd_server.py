#!/usr/bin/env python3
"""Divinci Table — Dungeons & Dragons (5e, SRD 5.1). Real dice on the table or fair dice on a phone;
an AI Dungeon Master (a Divinci release with the SRD in its RAG) or a person behind the screen.

    python table/dnd_server.py --players "Michael,Sam" --companions "ai:Leonardo:chatty" --port 8810

What the table owns, and what the DM is only told (docs/ROADMAP-GOAL.md M6):
  • DICE are rolled here (secrets.SystemRandom) or read off a physical die by the person who rolled it,
    and every roll is public in the scene log. The DM never rolls and never invents a result: it asks.
  • A CHARACTER'S NUMBERS belong to its player. Only a claimed seat changes its own hit points,
    conditions and sheet — the DM reports damage ("5 slashing to Mara") and the player applies it.
  • MONSTERS and the SCENE TITLE are the DM's: it may set them in a trailing `TABLE: {...}` line,
    which the table parses, applies and strips before anyone sees the narration.
  • INITIATIVE: players roll their own; monsters and AI companions are rolled here.

AI companions are party members with an open-mic personality (openmic.PRESETS). They cost no extra
model call: when a companion should speak, the DM's request asks it to voice them in the same reply.
A companion always speaks when addressed by name; otherwise its chattiness decides (wake word and
budget are code, see openmic.py; the tev1 judge is optional and its outage means silence).

Spend: the AI DM is on locally when DIVINCI_FUSION_API_KEY and a release are set. In a public cloud
room it stays off unless DND_CLOUD_AI=1 (Michael's budget decision, ROADMAP M3), and every room has a
hard cap on DM requests (DND_MAX_DM_CALLS, default 150). With the AI DM off, a seat named in --dm
narrates by hand and everything else works the same.

Rules text: System Reference Document 5.1 by Wizards of the Coast LLC, CC-BY-4.0 (see /api/dnd/credits).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from core import Events, Room, Seats, clean_text, device_of, jdump, save_photo, save_survey  # noqa: E402
import dnd_map as MAP  # noqa: E402
from openmic import PRESETS, wake_word  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--players", default="Player 1,Player 2", help="comma-separated names of the people at the table")
ap.add_argument("--companions", default="", help='comma-separated "ai:<name>[:<quiet|normal|chatty>[:<pregen class>]]"')
ap.add_argument("--dm", default="", help="name of a person who DMs by hand (default: the AI DM)")
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8810)
args = ap.parse_args()

CLOUD = os.environ.get("TABLE_CLOUD") == "1" or sys.platform != "darwin"
RESEARCH = HERE / ".cache" / "research" / "dnd"
API = "https://api.divinci.app"
_CONF = {}
try:
    _CONF = json.loads((HERE / ".cache" / "dnd.json").read_text())       # gitignored: {"dm_release_id": …}
except (OSError, ValueError):
    pass
DM_RELEASE = os.environ.get("DND_DM_RELEASE_ID") or _CONF.get("dm_release_id", "")
MAX_DM_CALLS = int(os.environ.get("DND_MAX_DM_CALLS", "150"))
CREDIT = ("This game uses material from the System Reference Document 5.1 (\"SRD 5.1\") by Wizards of the Coast LLC, "
          "available at https://dnd.wizards.com/resources/systems-reference-document, licensed under the Creative "
          "Commons Attribution 4.0 International License (https://creativecommons.org/licenses/by/4.0/legalcode).")


def ai_dm_enabled() -> bool:
    if args.dm or not DM_RELEASE or not os.environ.get("DIVINCI_FUSION_API_KEY"):
        return False
    return (not CLOUD) or os.environ.get("DND_CLOUD_AI") == "1"


def parse_companion(spec: str) -> dict | None:
    spec = spec.strip()
    if not spec.startswith("ai:"):
        return None
    _, name, *rest = spec.split(":")
    chat = rest[0] if rest and rest[0] in PRESETS else "normal"
    cls = (rest[1] if len(rest) > 1 else "wizard").lower()
    return {"name": name or "Leonardo", "chattiness": chat, "class": cls, "spoke_at": []}


HUMANS = [n.strip() for n in args.players.split(",") if n.strip()]
DM_HUMAN = args.dm.strip()
COMPANIONS = [c for c in (parse_companion(s) for s in args.companions.split(",")) if c]
SEATS = Seats(HUMANS + ([DM_HUMAN] if DM_HUMAN else []))
EVENTS = Events()
LOCK = threading.RLock()
PREGENS = {k: v for k, v in json.loads((HERE / "dnd_pregens.json").read_text()).items() if not k.startswith("_")}
MANIFEST = HERE / "dnd_assets.json"
ASSETS = MAP.load_manifest(MANIFEST)                            # locations and minis (docs/DND-3D-GOAL.md)
ASSET_DIR = HERE / ".cache" / "dnd"                             # built rooms and art on the laptop; R2 in the cloud
START_LOCATION = "clearing"
G: dict = {}


# ── dice ─────────────────────────────────────────────────────────────────────────────────────
RNG = secrets.SystemRandom()
DICE_RE = re.compile(r"^\s*(\d{0,2})d(4|6|8|10|12|20|100)\s*([+-]\s*\d{1,2})?\s*$", re.I)


def parse_dice(expr: str) -> tuple[int, int, int]:
    """'d20+5' → (1, 20, 5); '2d6-1' → (2, 6, -1). Raises ValueError on anything else."""
    m = DICE_RE.match(expr or "")
    if not m:
        raise ValueError("dice look like d20+5 or 2d6-1 (d4 d6 d8 d10 d12 d20 d100, up to 20 dice)")
    n = int(m.group(1) or 1)
    if not 1 <= n <= 20:
        raise ValueError("roll 1 to 20 dice at a time")
    return n, int(m.group(2)), int((m.group(3) or "0").replace(" ", ""))


def roll(expr: str, mode: str = "", physical: list | None = None) -> dict:
    """Fair dice (or a physical roll the player read off the table). mode 'adv'/'dis' rolls a d20 twice.
    Physical results are checked against the dice, never trusted blind."""
    n, sides, mod = parse_dice(expr)
    adv = mode in ("adv", "dis") and n == 1 and sides == 20
    need = 2 if adv else n
    if physical:
        faces = [int(x) for x in physical]
        if len(faces) != need or any(not 1 <= f <= sides for f in faces):
            raise ValueError(f"enter {need} result(s) between 1 and {sides}")
    else:
        faces = [RNG.randint(1, sides) for _ in range(need)]
    kept = (max(faces) if mode == "adv" else min(faces)) if adv else sum(faces)
    return {"expr": f"{n if n > 1 else ''}d{sides}{mod:+d}" if mod else f"{n if n > 1 else ''}d{sides}",
            "faces": faces, "mode": mode if adv else "", "mod": mod, "total": kept + mod, "physical": bool(physical),
            "crit": sides == 20 and n == 1 and kept == 20, "fumble": sides == 20 and n == 1 and kept == 1}


def roll_text(who: str, r: dict, why: str) -> str:
    faces = "/".join(map(str, r["faces"]))
    tag = {"adv": " adv", "dis": " dis"}.get(r["mode"], "")
    return f"ROLL {who}: {r['expr']}{tag} = [{faces}]{r['mod']:+d} = {r['total']}" + (f" ({why})" if why else "") + \
        (" (physical die)" if r["physical"] else "")


# ── characters ───────────────────────────────────────────────────────────────────────────────
SHEET_KEYS = {"class": str, "race": str, "level": int, "ac": int, "max_hp": int, "speed": int, "notes": str}
CONDITIONS = {"blinded", "charmed", "deafened", "exhaustion", "frightened", "grappled", "incapacitated", "invisible",
              "paralyzed", "petrified", "poisoned", "prone", "restrained", "stunned", "unconscious"}


def sheet_from_pregen(name: str, cls: str) -> dict:
    p = json.loads(json.dumps(PREGENS.get(cls) or PREGENS["fighter"]))
    p.update(name=name, hp=p["max_hp"], conditions=[], temp_hp=0)
    return p


def mod_of(score: int) -> int:
    return (int(score) - 10) // 2


def public_sheet(s: dict) -> dict:
    return {k: s.get(k) for k in ("name", "class", "race", "level", "ac", "hp", "max_hp", "temp_hp", "speed",
                                  "conditions", "abilities", "proficiency", "skills", "attacks", "spells", "notes")}


# ── the game ─────────────────────────────────────────────────────────────────────────────────
def new_game() -> None:
    pregen_order = ["fighter", "rogue", "cleric", "wizard"]
    G.clear()
    G.update(game_id=time.strftime("%Y%m%d-%H%M%S"), sheets={}, monsters=[], scene="", begun=False,
             initiative={"active": False, "order": [], "turn": 0, "round": 1, "pending": []},
             requests=[], dm_calls=0, dm_busy=False, dm_dirty=False, ai_spoke=None)
    for i, n in enumerate(HUMANS):
        G["sheets"][n] = sheet_from_pregen(n, pregen_order[i % 4])
    for c in COMPANIONS:
        G["sheets"][c["name"]] = sheet_from_pregen(c["name"], c["class"]) | {"companion": True}
    set_location(START_LOCATION, start=True)


# ── the battle map (dnd_map.py has the rules; this decides who may do what) ────────────────────
def playable(loc: dict | None) -> bool:
    """Players see a location only once a person has approved it (docs/DND-3D-GOAL.md D2). On the laptop,
    drafts play too (marked as drafts) so they can be tried before approval."""
    return bool(loc) and (loc.get("status") == "approved" or not CLOUD)


def playable_locations() -> list[dict]:
    return [x for x in ASSETS["locations"] if playable(x)]


def set_location(loc_id: str, start: bool = False) -> str | None:
    """A fresh map of a library location, with the party at its player spawns and every monster still in
    play at its monster spawns. Returns an error for an unknown (or unapproved) location. A new game may start
    on any location's grid (layouts are plain data), but its room and pictures go out only once approved."""
    loc = MAP.location(ASSETS, loc_id)
    if not loc or not (start or playable(loc)):
        return "no such location"
    G["map"] = MAP.new_map(loc, ASSETS.get("cell_ft", 5))
    G["map"]["assets"] = {k: loc.get(k) for k in ("room", "map_image", "view", "art", "shot", "shot_orig") if loc.get(k)} if playable(loc) else {}
    G["map"]["draft"] = loc.get("status") != "approved"
    for name, s in G["sheets"].items():
        MAP.place(G["map"], name, "pc", owner=None if s.get("companion") else name, speed=s.get("speed", 30))
    sync_monster_tokens()
    return None


def bestiary(name: str) -> dict | None:
    """'Goblin 2' → the SRD goblin from the manifest's bestiary (size, speed, AC, HP and its mini), if listed."""
    base = re.sub(r"\s*#?\d+$", "", (name or "").strip()).lower()
    return next((b for b in ASSETS.get("bestiary", []) if b["srd_name"].lower() == base), None)


def sync_monster_tokens() -> None:
    """The map follows the DM's monster list: a monster gone from the list leaves the map, a new one appears
    at a monster spawn."""
    m = G["map"]
    names = {x["name"] for x in G["monsters"]}
    for tid in [tid for tid, t in m["tokens"].items() if t["kind"] == "monster" and t["name"] not in names]:
        del m["tokens"][tid]
    for x in G["monsters"]:
        if not MAP.token_by_name(m, x["name"]):
            b = bestiary(x["name"]) or {}
            MAP.place(m, x["name"], "monster", size=x.get("size", "medium"), speed=x.get("speed", 30), mini=b.get("mini"))


def turn_name() -> str | None:
    ini = G["initiative"]
    return ini["order"][ini["turn"]]["name"] if ini["active"] and ini["order"] and not ini["pending"] else None


def map_square(v) -> int | None:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if 0 <= n < MAP.MAX_SIDE else None


def apply_map_lines(d: dict) -> None:
    """The DM's map changes from a TABLE line: location, place and move. Everything is checked here; an
    unknown location, name or illegal square is dropped. The DM never moves a person's character, and in
    combat a creature walks at most its speed, along a legal path, on its own turn."""
    if isinstance(d.get("location"), str) and playable(MAP.location(ASSETS, d["location"])):
        set_location(d["location"])
    m = G["map"]
    places = d.get("place") if isinstance(d.get("place"), list) and not turn_name() else []   # setting up, not mid-fight
    for x in places[:20]:
        if isinstance(x, dict) and isinstance(x.get("name"), str):
            t = MAP.token_by_name(m, x["name"])
            if t and t["kind"] != "pc":
                gx, gy = map_square(x.get("x")), map_square(x.get("y"))
                if gx is not None and gy is not None:
                    MAP.move(m, t["id"], gx, gy, walk=False)       # placing what's already in the scene
    for x in (d.get("move") or [])[:20] if isinstance(d.get("move"), list) else []:
        if not isinstance(x, dict) or not isinstance(x.get("name"), str):
            continue
        t = MAP.token_by_name(m, x["name"])
        gx, gy = map_square(x.get("x")), map_square(x.get("y"))
        if not t or gx is None or gy is None or t["owner"]:          # a person's character is theirs to move
            continue
        now = turn_name()
        if now and now != t["name"]:
            continue                                                  # in combat, only on its own turn
        err, ft = MAP.move(m, t["id"], gx, gy, walk=True, in_turn=bool(now))
        if not err:
            EVENTS.emit("dnd", kind="map", text=f"{t['name']} moves {ft} ft" if ft else f"{t['name']} moves")


def map_for_dm() -> dict:
    m = G["map"]
    return {"location": m["location"], "locations": [x["id"] for x in playable_locations()],
            "grid": m["layout"], "legend": "# wall, ~ difficult, D door, P/M spawns; x is the column, y the row, from 0",
            "bestiary": [b["srd_name"] for b in ASSETS.get("bestiary", [])],
            "tokens": [{"name": t["name"], "x": t["x"], "y": t["y"], "size": t["size"], "speed": t["speed"],
                        **({"player": True} if t["owner"] else {})} for t in m["tokens"].values()]}


def companion(name: str) -> dict | None:
    return next((c for c in COMPANIONS if c["name"].lower() == (name or "").lower()), None)


def apply_table_line(text: str) -> str:
    """Strip and apply the DM's optional trailing `TABLE: {...}` line. Accepts monsters and scene only:
    a player's numbers are never the DM's to set."""
    m = re.search(r"^\s*TABLE:(.*)$", text, re.M | re.S)
    if not m:
        return text.strip()
    try:                                                     # a malformed line is still never shown to players
        d = json.loads(m.group(1).strip())
        d = d if isinstance(d, dict) else {}
    except ValueError:
        d = {}
    if isinstance(d.get("monsters"), list):
        mons = []
        for x in d["monsters"][:20]:
            if isinstance(x, dict) and x.get("name"):
                try:
                    b = bestiary(x["name"]) or {}
                    mx = max(1, int(x.get("max_hp") or x.get("hp") or b.get("hp") or 1))
                    mons.append({"name": clean_text(x["name"], 40), "ac": int(x.get("ac") or b.get("ac") or 10),
                                 "hp": max(0, min(mx, int(x.get("hp", mx)))), "max_hp": mx,
                                 "size": x["size"] if x.get("size") in MAP.SIZES else b.get("size", "medium"),
                                 "speed": max(0, min(120, int(x.get("speed") or b.get("speed") or 30)))})
                except (TypeError, ValueError):
                    continue
        G["monsters"] = mons
        sync_monster_tokens()
    if isinstance(d.get("scene"), str):
        G["scene"] = clean_text(d["scene"], 80)
    apply_map_lines(d)
    return text[:m.start()].strip()


def state_for_dm() -> dict:
    ini = G["initiative"]
    return {"scene": G["scene"],
            "party": [{"name": s["name"], "class": s["class"], "level": s["level"], "ac": s["ac"],
                       "hp": f"{s['hp']}/{s['max_hp']}", "conditions": s["conditions"],
                       **({"ai_companion": True} if s.get("companion") else {})} for s in G["sheets"].values()],
            "monsters": G["monsters"],
            "initiative": ({"round": ini["round"], "order": [f"{o['name']} ({o['total']})" for o in ini["order"]],
                            "now": ini["order"][ini["turn"]]["name"] if ini["order"] else None,
                            "waiting_to_roll": ini["pending"]} if ini["active"] else None),
            "map": map_for_dm()}


def recent_lines(n: int = 30) -> list[str]:
    out = []
    for e in EVENTS.items[-200:]:
        if e["type"] == "dm":
            out.append(f"DM: {e['text']}")
        elif e["type"] == "say":
            out.append(f"{e['by']}{' (in character)' if e.get('ic') else ''}: {e['text']}")
        elif e["type"] == "roll":
            out.append(e["text"])
        elif e["type"] == "dnd" and e.get("kind") in ("hp", "condition", "initiative"):
            out.append(f"TABLE: {e.get('text', '')}")
    return out[-n:]


def build_prompt(reason: str, speak: list[str]) -> str:
    comp = "; ".join(f"{c['name']} ({G['sheets'][c['name']]['class']}, {c['chattiness']} personality)" for c in COMPANIONS)
    lines = [f"STATE: {json.dumps(state_for_dm(), ensure_ascii=False)}", "", "RECENT (oldest first):",
             *[f"- {x}" for x in recent_lines()], "", f"NOW: {reason}"]
    if comp:
        lines.append(f"AI COMPANIONS in the party (you voice them only when told below): {comp}.")
    for n in speak:
        lines.append(f"Also give {n} one short in-character line and what they do, on its own line starting \"{n}:\".")
    lines += ["", "Narrate the next beat (2-5 sentences), then hand the scene back or ask for rolls by name.",
              "Plain spoken prose only: no markdown, headings, lists, code blocks, diagrams or citation numbers. "
              "Do not speak for the AI companions or the players unless told to above.",
              'If monsters, the scene title or the map change, end with ONE line: TABLE: {"monsters":[{"name":"Goblin 1","ac":15,'
              '"hp":7,"max_hp":7,"size":"small","speed":30}],"scene":"<short title>","location":"<one of map.locations>",'
              '"place":[{"name":"Goblin 1","x":9,"y":2}],"move":[{"name":"Goblin 1","x":7,"y":3}]} '
              '(list every monster still in play; include only the keys that change; omit the line otherwise). '
              "Only move monsters and companions, never the players' characters, and in combat only the creature whose turn it is."]
    return "\n".join(lines)


def ask_dm(prompt: str) -> str:
    body = json.dumps({"messages": [{"role": "user", "content": prompt}], "releaseId": DM_RELEASE}).encode()
    req = urllib.request.Request(API + "/api/v1/chat/completions", data=body, method="POST", headers={
        "Authorization": "Bearer " + os.environ["DIVINCI_FUSION_API_KEY"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            status, text = resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode(errors="replace")
    try:
        d = json.loads(text)
    except ValueError:
        raise RuntimeError(f"non-JSON reply (HTTP {status}): {text[:200]}")
    if status >= 400 or not d.get("choices"):
        raise RuntimeError(f"HTTP {status}: {text[:200]}")
    return str(d["choices"][0]["message"].get("content") or "")


def plain(text: str) -> str:
    """What the table shows and speaks: no code blocks, citation markers or markdown emphasis."""
    text = re.sub(r"```.*?(```|$)", "", text, flags=re.S)
    text = re.sub(r"\s?\[\d+(?:,\s*\d+)*\]", "", text)
    text = re.sub(r"\*\*|__|^#+\s*", "", text, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_companions(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Pull 'Leonardo: …' lines for AI companions out of the DM's narration."""
    keep, said = [], []
    for line in text.splitlines():
        m = re.match(r"^\s*\**([A-Za-z][\w' -]{0,30}?)\**\s*:\s*(.+)$", line)
        if m and companion(m.group(1)):
            said.append((companion(m.group(1))["name"], m.group(2).strip()))
        else:
            keep.append(line)
    return "\n".join(keep).strip(), said


def dm_turn(reason: str, speak: list[str] | None = None) -> None:
    """One DM request at a time; requests that arrive meanwhile fold into one follow-up."""
    if not ai_dm_enabled():
        return
    with LOCK:
        if G["dm_calls"] >= MAX_DM_CALLS:
            EVENTS.emit("dnd", kind="notice", text=f"The AI DM has reached this table's limit ({MAX_DM_CALLS} requests).")
            return
        if G["dm_busy"]:
            G["dm_dirty"] = True
            G.setdefault("dm_pending_speak", []).extend(speak or [])
            return
        G["dm_busy"] = True
        G["dm_calls"] += 1
        prompt = build_prompt(reason, speak or [])
    threading.Thread(target=_dm_worker, args=(prompt, speak or []), daemon=True).start()


def _dm_worker(prompt: str, speak: list[str]) -> None:
    EVENTS.emit("dnd", kind="thinking")
    t0 = time.time()
    try:
        raw = ask_dm(prompt)
        err = None
    except Exception as e:                                   # noqa: BLE001 — the table keeps going without the DM
        raw, err = "", f"{type(e).__name__}: {str(e)[:160]}"
    with LOCK:
        if err:
            EVENTS.emit("dnd", kind="notice", text="The DM lost the thread for a moment — say what you do again, or press Continue.")
        else:
            text, said = split_companions(plain(apply_table_line(raw)))
            if text:
                EVENTS.emit("dm", text=clean_text_keep_lines(text, 2000))
            for name, line in said:
                c = companion(name)
                c["spoke_at"].append(time.time())
                EVENTS.emit("say", by=name, text=clean_text(line, 300), ic=True, ai=True)
        _log({"prompt": prompt, "reply": raw, "error": err, "seconds": round(time.time() - t0, 1)})
        G["dm_busy"] = False
        again, more = G["dm_dirty"], G.pop("dm_pending_speak", [])
        G["dm_dirty"] = False
    if again:
        dm_turn("Players acted while you were narrating (see RECENT). Respond to them.", more)


def clean_text_keep_lines(s: str, n: int) -> str:
    return "\n".join(" ".join(x.split()) for x in str(s or "").splitlines() if x.strip())[:n]


def _log(rec: dict) -> None:
    try:
        RESEARCH.mkdir(parents=True, exist_ok=True)
        with open(RESEARCH / f"{G['game_id']}-dm.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": round(time.time(), 2), **rec}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def companions_to_speak(text: str, now: float) -> list[str]:
    """Open mic for the party: a companion named at the start of a line always answers; otherwise its
    chattiness sets how often it chimes in (cooldown + budget), never twice in a row."""
    names = [c["name"] for c in COMPANIONS]
    w = wake_word(text, names)
    if w:
        return [w]
    for c in COMPANIONS:
        if c["name"].lower() in text.lower():
            return [c["name"]]
    out = []
    for c in COMPANIONS:
        p = PRESETS[c["chattiness"]]
        recent = [t for t in c["spoke_at"] if now - t < p["window_s"]]
        if c["name"] == G.get("ai_spoke") or len(recent) >= p["budget"] or (recent and now - recent[-1] < p["cooldown_s"]):
            continue
        if RNG.random() < {"quiet": 0.1, "normal": 0.25, "chatty": 0.5}[c["chattiness"]]:
            out.append(c["name"])
            break
    return out


# ── initiative ───────────────────────────────────────────────────────────────────────────────
def initiative_start() -> None:
    ini = {"active": True, "order": [], "turn": 0, "round": 1, "pending": list(HUMANS)}
    for c in COMPANIONS:
        s = G["sheets"][c["name"]]
        r = roll(f"d20{mod_of(s['abilities']['dex']):+d}")
        ini["order"].append({"name": c["name"], "total": r["total"], "dex": s["abilities"]["dex"]})
        EVENTS.emit("roll", by=c["name"], text=roll_text(c["name"], r, "initiative"), roll=r)
    for m in G["monsters"]:
        r = roll("d20")
        ini["order"].append({"name": m["name"], "total": r["total"], "dex": 10, "monster": True})
        EVENTS.emit("roll", by="DM", text=roll_text(m["name"], r, "initiative"), roll=r)
    G["initiative"] = ini
    G["map"]["used"] = {}
    _sort_initiative()
    EVENTS.emit("dnd", kind="initiative", text="Roll initiative! " + (", ".join(HUMANS) + ": roll d20 + Dex." if HUMANS else ""))
    if not ini["pending"]:
        _initiative_set()


def _sort_initiative() -> None:
    G["initiative"]["order"].sort(key=lambda o: (-o["total"], -o["dex"], o["name"]))


def initiative_enter(name: str, total: int) -> None:
    ini = G["initiative"]
    if name in ini["pending"]:
        ini["pending"].remove(name)
        ini["order"].append({"name": name, "total": total, "dex": G["sheets"][name]["abilities"]["dex"]})
        _sort_initiative()
        if not ini["pending"]:
            _initiative_set()


def _initiative_set() -> None:
    ini = G["initiative"]
    EVENTS.emit("dnd", kind="initiative", text="Order: " + ", ".join(f"{o['name']} {o['total']}" for o in ini["order"]))
    if ini["order"]:
        first = ini["order"][0]["name"]
        G["after_lock"] = lambda: dm_turn(f"Initiative is set. It is {first}'s turn.", [first] if companion(first) else [])


# ── state ────────────────────────────────────────────────────────────────────────────────────
def public_state(viewer: str | None = None) -> dict:
    with LOCK:
        return {"game": "dnd", "scene": G["scene"], "begun": G["begun"], "humans": HUMANS, "dm_human": DM_HUMAN or None,
                "ai_dm": ai_dm_enabled(), "dm_busy": G["dm_busy"], "dm_calls": G["dm_calls"], "dm_cap": MAX_DM_CALLS,
                "companions": [{"name": c["name"], "chattiness": c["chattiness"]} for c in COMPANIONS],
                "sheets": {n: public_sheet(s) for n, s in G["sheets"].items()}, "monsters": G["monsters"],
                "initiative": G["initiative"], "pregens": sorted(PREGENS), "credit": CREDIT,
                "map": MAP.public(G["map"]),
                "minis": {x["id"]: x["glb"] for x in ASSETS.get("minis", []) if x.get("glb")},
                "locations": [{"id": x["id"], "name": x["name"] + ("" if x.get("status") == "approved" else " (draft)")}
                              for x in playable_locations()]}


def dump() -> dict:
    with LOCK:
        g = {k: v for k, v in G.items() if k not in ("dm_busy", "dm_dirty")}
        g["map"] = MAP.public(G["map"])                    # the parsed grid is a cache, rebuilt on use
        return {"g": g, "spoke": {c["name"]: c["spoke_at"] for c in COMPANIONS},
                "seats": SEATS.state(), "events": EVENTS.state(), "saved": time.time()}


def load(d: dict) -> None:
    with LOCK:
        new_game()
        G.update(d.get("g") or {})
        G.update(dm_busy=False, dm_dirty=False)
        for c in COMPANIONS:
            c["spoke_at"] = list((d.get("spoke") or {}).get(c["name"], []))
        SEATS.load(d.get("seats") or {})
        EVENTS.load(d.get("events") or {})


new_game()
ROOM = Room(dump, load)


# ── HTTP ─────────────────────────────────────────────────────────────────────────────────────
STATIC = {"/survey": "survey.html", "/": "dnd.html", "/stage": "dnd.html", "/me": "dnd.html", "/dnd": "dnd.html",
          "/xr": "dnd_xr.html"}
ASSET_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, obj=None, body: bytes | None = None, ctype: str = "application/json"):
        body = body if body is not None else jdump(obj if obj is not None else {})
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if 0 < n <= 9_000_000 else b""

    def _json(self) -> dict:
        try:
            d = json.loads(self._body() or b"{}")
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}

    def _room(self, method: str, p: str) -> bool:
        if not p.startswith("/api/room/"):
            return False
        if not ROOM.token_ok(self.headers.get("X-Room-Token", "")):
            if method == "POST":
                self._body()
            self._send(404, {"error": "not found"})
            return True
        if p == "/api/room/status":
            self._send(200, {"adopted": ROOM.adopted, "rev": EVENTS.next_id - 1})
        elif p == "/api/room/adopt" and method == "POST":
            ROOM.adopted = True
            self._send(200, {"ok": True})
        elif p == "/api/room/snapshot":
            blob = ROOM.snapshot()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(blob)))
            self.send_header("X-Snapshot-Rev", str(EVENTS.next_id - 1))
            self.end_headers()
            self.wfile.write(blob)
        elif p == "/api/room/restore" and method == "POST":
            code, out = ROOM.restore(self._body())
            self._send(code, out)
        elif p == "/api/room/release" and method == "POST":
            name = str(self._json().get("seat", ""))
            ok = SEATS.release(name)
            if ok:
                EVENTS.emit("seat", name=name, kind="released")
            self._send(200 if ok else 404, {"ok": ok, "released": name} if ok else {"error": "no such human seat"})
        else:
            self._send(404, {"error": "not found"})
        return True

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if self._room("GET", p):
            return
        if p in STATIC:
            return self._send(200, body=(HERE / STATIC[p]).read_bytes(), ctype="text/html; charset=utf-8")
        if p == "/api/dnd":
            return self._send(200, public_state())
        if p == "/manifest.webmanifest":                 # add to the home screen: the table as an app
            return self._send(200, body=json.dumps({
                "name": "Divinci Table — adventure", "short_name": "Adventure", "start_url": "/", "scope": "/",
                "display": "standalone", "background_color": "#040d12", "theme_color": "#040d12",
                "icons": [{"src": "/assets/dnd-icon-192.png", "sizes": "192x192", "type": "image/png"},
                          {"src": "/assets/dnd-icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}]}).encode(),
                ctype="application/manifest+json")
        if p in ("/dnd/review", "/api/dnd/review"):
            if CLOUD or not self._local():
                return self._send(404, {"error": "not found"})
            if p == "/dnd/review":
                return self._send(200, body=(HERE / "dnd_review.html").read_bytes(), ctype="text/html; charset=utf-8")
            return self._send(200, {"locations": ASSETS["locations"], "cell_ft": ASSETS.get("cell_ft", 5)})
        if p.startswith("/dnd-assets/"):                   # the laptop's built rooms (the cloud Worker serves R2)
            rel = unquote(p[len("/dnd-assets/"):])
            f = (ASSET_DIR / rel).resolve()
            types = {".glb": "model/gltf-binary", ".jpg": "image/jpeg", ".png": "image/png", ".mp4": "video/mp4"}
            if re.fullmatch(r"(locations/[a-z0-9-]{1,40}/[a-z_]{1,20}|minis/[a-z0-9-]{1,40})\.(glb|jpg|png|mp4)", rel) \
                    and f.is_file() and ASSET_DIR.resolve() in f.parents:
                return self._send(200, body=f.read_bytes(), ctype=types[f.suffix])
            return self._send(404, {"error": "not found"})
        if p == "/api/dnd/credits":
            return self._send(200, body=CREDIT.encode(), ctype="text/plain; charset=utf-8")
        if p == "/api/seat/claims":
            return self._send(200, SEATS.public(device_of(self)))
        if p == "/api/events":
            q = parse_qs(u.query).get("since", ["0"])[0]
            return self._send(200, EVENTS.since(EVENTS.next_id - 1 if q == "latest" else int(q or 0)))
        if p == "/api/voice-config":
            return self._send(200, {"humans": [{"name": n} for n in HUMANS], "ai_players": [{"name": c["name"]} for c in COMPANIONS]})
        for pre, root in (("/assets/", HERE / "assets"), ("/vendor/", HERE / "vendor")):   # page code; three.js
            if p.startswith(pre):
                f = (root / unquote(p[len(pre):])).resolve()
                if f.is_file() and root.resolve() in f.parents and f.suffix in ASSET_TYPES:
                    return self._send(200, body=f.read_bytes(), ctype=ASSET_TYPES[f.suffix])
        if p.startswith("/photos/"):
            f = (RESEARCH / G["game_id"] / "photos" / p[8:]).resolve()
            if f.is_file() and RESEARCH in f.parents and f.suffix in (".jpg", ".png"):
                return self._send(200, body=f.read_bytes(), ctype=ASSET_TYPES[f.suffix])
        return self._send(404, {"error": "not found"})

    def _seat_of(self, key: str) -> str | None:
        key = str(key or "")[:200]
        return next((n for n in HUMANS + ([DM_HUMAN] if DM_HUMAN else []) if key and SEATS.ok(n, key)), None)

    STT_MAX = 2_000_000                                    # ~60 s of 16 kHz mono WAV
    STT_PER_MIN = 20

    def _stt(self):
        """Whisper on this Mac (voice.py) for a seated player's held-down talk button: the words come back, the
        audio is never stored. The phone sends 16 kHz mono WAV, the same as it sends the cloud Worker."""
        n = int(self.headers.get("Content-Length") or 0)
        if CLOUD:
            self._body()
            return self._send(404, {"error": "not found"})
        who = self._seat_of(self.headers.get("X-Seat-Key", ""))
        if not who:
            self._body()
            return self._send(403, {"error": "claim your seat first (👤)", "need_seat": True})
        if not 44 < n <= self.STT_MAX:
            self._body() if n <= 9_000_000 else None
            self.close_connection = True
            return self._send(413, {"error": "keep it under a minute"})
        now = time.time()
        recent = [t for t in G.setdefault("stt_at", {}).get(who, []) if now - t < 60]
        if len(recent) >= self.STT_PER_MIN:
            self._body()
            return self._send(429, {"error": "too much talking for a moment — try again shortly"})
        G["stt_at"][who] = recent + [now]
        data = self._body()
        try:
            import voice                                   # numpy + mlx_whisper: loaded only when someone talks
            hint = "Dungeons and Dragons. " + ", ".join(HUMANS + [c["name"] for c in COMPANIONS])
            text, _ = voice.transcribe(voice.wav_to_float32(data), hint)
        except Exception as e:                             # noqa: BLE001
            print(f"stt failed: {type(e).__name__}: {str(e)[:120]}", flush=True)
            return self._send(502, {"error": "couldn't hear that — try again"})
        return self._send(200, {"seat": who, "text": " ".join(str(text).split())[:400]})

    def _local(self) -> bool:
        """The laptop itself (the review page is the host's, like the Magic referee tools)."""
        return self.client_address[0] in ("127.0.0.1", "::1") and not any(
            self.headers.get(h) for h in ("X-Forwarded-For", "Forwarded", "X-Real-IP", "CF-Connecting-IP"))

    def do_POST(self):
        p = urlparse(self.path).path
        if self._room("POST", p):
            return
        if p == "/api/seat/check":                         # the room's Worker asks before it transcribes (cloud)
            self._body()
            who = self._seat_of(self.headers.get("X-Seat-Key", ""))
            return self._send(200, {"seat": who}) if who else self._send(403, {"error": "no such seat key"})
        if p == "/api/xr/stt":                             # push-to-talk on the laptop table (the cloud Worker answers it there)
            return self._stt()
        if p == "/api/dnd/review":                         # the host approves (or sends back) a location
            b = self._json()
            if CLOUD or not self._local():
                return self._send(404, {"error": "not found"})
            loc = MAP.location(ASSETS, str(b.get("id", "")))
            status = b.get("status")
            if not loc or status not in ("draft", "approved"):
                return self._send(400, {"error": "id of a location and status draft|approved"})
            if status == "approved" and not all(loc.get(k) for k in ("room", "map_image", "view")):
                return self._send(409, {"error": "build its room first (scripts/dnd_rooms.py)"})
            with LOCK:
                loc["status"] = status
                if b.get("note") is not None:
                    loc["review_note"] = clean_text(b.get("note"), 300)
                MANIFEST.write_text(json.dumps(ASSETS, indent=2, ensure_ascii=False) + "\n")
            return self._send(200, {"ok": True, "id": loc["id"], "status": status})
        if p == "/api/seat/claim":
            b = self._json()
            code, out = SEATS.claim(str(b.get("name", "")), str(b.get("key") or ""), device_of(self), CLOUD)
            if code == 200:
                EVENTS.emit("seat", name=out["name"], kind="claimed")
            return self._send(code, out)
        if p == "/api/chat/photo":
            by = unquote(self.headers.get("X-By", ""))[:30]
            data = self._body()
            if not SEATS.ok(by, unquote(self.headers.get("X-Seat-Key", ""))):
                return self._send(403, {"error": "claim your seat first (👤) to share photos"})
            code, out = save_photo(data, RESEARCH / G["game_id"] / "photos")
            if code == 200:
                EVENTS.emit("say", by=by, text=clean_text(unquote(self.headers.get("X-Caption", "")), 300) or "📷 a photo of the table", photo=out["photo"])
            return self._send(code, out)
        b = self._json()
        by = SEATS.canonical(str(b.get("by", "")))
        if not by or not SEATS.ok(by, str(b.get("key", ""))):
            return self._send(403, {"error": "claim your seat first (👤)", "need_seat": True})
        is_dm = bool(DM_HUMAN) and by == DM_HUMAN
        with LOCK:
            code, out, then = self._act(p, b, by, is_dm)
            later = G.pop("after_lock", None)
        for f in (then, later):
            if f:
                f()
        return self._send(code, out)

    def _act(self, p: str, b: dict, by: str, is_dm: bool):
        """Returns (status, body, follow-up to run after the lock is released)."""
        now = time.time()
        if p == "/api/survey":
            code, out = save_survey(RESEARCH / G["game_id"], by, b)
            if code == 200:
                EVENTS.emit("survey", by=by)
            return code, out, None
        if p == "/api/chat":                                  # out of character: the DM is not called
            text = clean_text(b.get("text"), 400)
            if not text:
                return 400, {"error": "say something"}, None
            EVENTS.emit("say", by=by, text=text, ic=False)
            return 200, {"ok": True}, None
        if p == "/api/dnd/act":                               # in character: what you say or do
            text = clean_text(b.get("text"), 600)
            if not text:
                return 400, {"error": "say or do something"}, None
            EVENTS.emit("say", by=by, text=text, ic=True)
            G["ai_spoke"] = None
            speak = companions_to_speak(text, now)
            return 200, {"ok": True}, (lambda: dm_turn(f"{by} acts or speaks (latest line in RECENT).", speak))
        if p == "/api/dnd/roll":
            why = clean_text(b.get("why"), 60)
            try:
                r = roll(str(b.get("dice", "d20")), str(b.get("mode", "")), b.get("physical") or None)
            except (ValueError, TypeError) as e:
                return 400, {"error": str(e)}, None
            who = clean_text(b.get("for"), 40) if is_dm else by
            EVENTS.emit("roll", by=by, text=roll_text(who or by, r, why), roll=r)
            then = None
            if why.lower().startswith("init") and not is_dm and G["initiative"]["active"]:
                initiative_enter(by, r["total"])
            elif not is_dm and not G["initiative"]["pending"]:
                then = lambda: dm_turn(f"{by} rolled (latest ROLL in RECENT). Resolve it.")
            return 200, {"roll": r, **public_state()}, then
        if p == "/api/dnd/sheet":                             # your own character only
            if by not in G["sheets"]:
                return 403, {"error": "only a player has a character sheet"}, None
            s = G["sheets"][by]
            if b.get("pregen") in PREGENS:
                G["sheets"][by] = sheet_from_pregen(by, b["pregen"])
                EVENTS.emit("dnd", kind="sheet", text=f"{by} is now a {b['pregen']}")
                return 200, public_state(), None
            for k, typ in SHEET_KEYS.items():
                if k in b:
                    try:
                        s[k] = clean_text(b[k], 500) if typ is str else max(0, min(400, int(b[k])))
                    except (TypeError, ValueError):
                        return 400, {"error": f"{k} must be a {typ.__name__}"}, None
            s["hp"] = min(s["hp"], s["max_hp"])
            return 200, public_state(), None
        if p == "/api/dnd/hp":                                # humans own their numbers
            if by not in G["sheets"]:
                return 403, {"error": "only a player has hit points here"}, None
            s = G["sheets"][by]
            try:
                d = int(b.get("delta", 0))
            except (TypeError, ValueError):
                return 400, {"error": "delta is a whole number"}, None
            if d < 0 and s.get("temp_hp"):
                soak = min(s["temp_hp"], -d)
                s["temp_hp"] -= soak
                d += soak
            s["hp"] = max(0, min(s["max_hp"], s["hp"] + d))
            if "temp" in b:
                s["temp_hp"] = max(0, min(200, int(b["temp"] or 0)))
            EVENTS.emit("dnd", kind="hp", by=by, text=f"{by}: {s['hp']}/{s['max_hp']} HP" + (" — down!" if s["hp"] == 0 else ""))
            return 200, public_state(), None
        if p == "/api/dnd/condition":
            if by not in G["sheets"]:
                return 403, {"error": "only a player has conditions here"}, None
            c = str(b.get("condition", "")).lower()
            if c not in CONDITIONS:
                return 400, {"error": "unknown condition"}, None
            cs = G["sheets"][by]["conditions"]
            on = bool(b.get("on", c not in cs))
            if on and c not in cs:
                cs.append(c)
            elif not on and c in cs:
                cs.remove(c)
            EVENTS.emit("dnd", kind="condition", by=by, text=f"{by} is {'now' if on else 'no longer'} {c}")
            return 200, public_state(), None
        if p == "/api/dnd/begin":
            if G["begun"]:
                return 200, public_state(), None
            G["begun"] = True
            premise = clean_text(b.get("premise"), 400) or "a short heroic one-shot for a level 3 party"
            EVENTS.emit("dnd", kind="begin", by=by, text=f"The adventure begins: {premise}")
            return 200, public_state(), (lambda: dm_turn(
                f"Begin the adventure: {premise}. Set the opening scene and give each character a reason to be here."))
        if p == "/api/dnd/continue":
            return 200, public_state(), (lambda: dm_turn("The table asks you to continue the scene."))
        if p == "/api/dnd/initiative":
            act = str(b.get("action", "start"))
            if act == "start":
                initiative_start()
            elif act == "next" and G["initiative"]["active"] and G["initiative"]["order"]:
                ini = G["initiative"]
                ini["turn"] += 1
                if ini["turn"] >= len(ini["order"]):
                    ini["turn"], ini["round"] = 0, ini["round"] + 1
                now_name = ini["order"][ini["turn"]]["name"]
                t = MAP.token_by_name(G["map"], now_name)
                MAP.new_turn(G["map"], t and t["id"])
                EVENTS.emit("dnd", kind="turn", text=f"Round {ini['round']}: {now_name}'s turn")
                if not any(now_name == h for h in HUMANS):
                    return 200, public_state(), (lambda: dm_turn(f"It is {now_name}'s turn. Take it.",
                                                                 [now_name] if companion(now_name) else []))
            elif act == "end":
                G["initiative"] = {"active": False, "order": [], "turn": 0, "round": 1, "pending": []}
                EVENTS.emit("dnd", kind="initiative", text="Combat is over.")
            return 200, public_state(), None
        if p == "/api/dnd/map/move":                          # people walk their own character; the DM moves anyone
            m, t = G["map"], G["map"]["tokens"].get(str(b.get("token", "")))
            x, y = map_square(b.get("x")), map_square(b.get("y"))
            if not t:
                return 404, {"error": "no such token"}, None
            if x is None or y is None:
                return 400, {"error": "x and y are squares on the map"}, None
            if not is_dm and t["owner"] != by:
                return 403, {"error": "you move only your own character"}, None
            now = turn_name()
            if not is_dm and now and now != t["name"]:
                return 409, {"error": f"it's {now}'s turn"}, None
            err, ft = MAP.move(m, t["id"], x, y, walk=not is_dm, in_turn=bool(now) and not is_dm)
            if err:
                return 409, {"error": err}, None
            EVENTS.emit("dnd", kind="map", by=by, text=f"{t['name']} moves {ft} ft" if ft else f"{t['name']} moves")
            return 200, public_state(), None
        if p in ("/api/dnd/map/place", "/api/dnd/map/remove", "/api/dnd/map/location"):
            if not is_dm:
                return 403, {"error": "only the DM's seat changes the map"}, None
            m = G["map"]
            if p.endswith("/location"):
                err = set_location(str(b.get("id", "")))
                if err:
                    return 404, {"error": err}, None
                EVENTS.emit("dnd", kind="map", text=f"The scene moves to {G['map']['name']}")
            elif p.endswith("/remove"):
                t = m["tokens"].get(str(b.get("token", "")))
                if not t or t["kind"] == "pc":
                    return 404, {"error": "no such token (characters stay on the map)"}, None
                del m["tokens"][t["id"]]
            else:
                kind = b.get("kind") if b.get("kind") in ("monster", "npc") else "npc"
                name = clean_text(b.get("name"), 40)
                if not name:
                    return 400, {"error": "name the creature"}, None
                err, t = MAP.place(m, name, kind, map_square(b.get("x")), map_square(b.get("y")),
                                   size=str(b.get("size", "medium")))
                if err:
                    return 409, {"error": err}, None
            return 200, public_state(), None
        if p == "/api/dnd/narrate":                           # a person behind the screen
            if not is_dm:
                return 403, {"error": "only the DM's seat narrates"}, None
            text = clean_text_keep_lines(b.get("text"), 2000)
            if text:
                EVENTS.emit("dm", text=apply_table_line(text), human=True)
            return 200, public_state(), None
        return 404, {"error": "not found"}, None


if __name__ == "__main__":
    srv = ThreadingHTTPServer((args.host, args.port), H)
    srv.daemon_threads = True
    dm = DM_HUMAN or ("AI DM (release)" if ai_dm_enabled() else "nobody yet — set DND_DM_RELEASE_ID or --dm")
    print(f"dnd: {', '.join(HUMANS)}" + (f" + {', '.join(c['name'] for c in COMPANIONS)}" if COMPANIONS else "") +
          f"; DM: {dm} — http://localhost:{args.port}/", flush=True)
    srv.serve_forever()
