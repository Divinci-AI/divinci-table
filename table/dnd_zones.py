"""Theater of the mind: positions without a grid (docs/THEATER-GOAL.md T1).

A scene is a handful of named ZONES ("the bar", "the door", "the gallery"), each next to some others. Every
creature is in one zone; two creatures in the same zone can be ENGAGED (in melee). Range follows from that:
engaged, near (the same zone or the next one), far (anything further). In combat a creature's move takes it to
the next zone; a Dash one zone further. Leaving a foe you're engaged with is allowed, and noted: the foe may make
an opportunity attack (the table doesn't roll it for anyone). Out of combat, people go where they like.

Pure functions over a plain dict (JSON-safe, so it survives snapshots). dnd_server.py decides who may do what;
this module only knows the rules.
"""
from __future__ import annotations

from collections import deque

MAX_ZONES = 8                                     # a scene, not a dungeon: also bounds what a DM line can add
COVER = ("none", "half", "three-quarters")


def new_card() -> dict:
    return {"zones": [], "where": {}, "engaged": [], "moved": {}, "dashed": []}


def _clean(s, n: int = 40) -> str:
    return " ".join(str(s or "").split())[:n]


def zone(card: dict, name: str) -> dict | None:
    n = _clean(name).lower()
    return next((z for z in card["zones"] if z["name"].lower() == n), None)


def set_zones(card: dict, spec) -> str | None:
    """Replace the scene's zones. spec: [{"name", "desc"?, "next"?: [names], "cover"?}]. Without any "next",
    the zones form a line in the order given. Creatures in a zone that no longer exists, and every engagement,
    are cleared; the caller puts them back."""
    if not isinstance(spec, list) or not spec:
        return "zones: a list of at least one zone"
    zs, seen = [], set()
    for x in spec[:MAX_ZONES]:
        if not isinstance(x, dict):
            continue
        name = _clean(x.get("name"))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        cover = x.get("cover") if x.get("cover") in COVER else "none"
        zs.append({"name": name, "desc": _clean(x.get("desc"), 200), "cover": cover,
                   "next": [_clean(n) for n in (x.get("next") or []) if isinstance(n, str)][:MAX_ZONES]})
    if not zs:
        return "zones: none had a name"
    names = {z["name"].lower(): z["name"] for z in zs}
    if not any(z["next"] for z in zs):                       # a line, in the order given
        for i, z in enumerate(zs):
            z["next"] = [zs[j]["name"] for j in (i - 1, i + 1) if 0 <= j < len(zs)]
    else:                                                    # known names only, and both ways
        for z in zs:
            z["next"] = [names[n.lower()] for n in z["next"] if n.lower() in names and n.lower() != z["name"].lower()]
        for z in zs:
            for n in z["next"]:
                other = next(o for o in zs if o["name"] == n)
                if z["name"] not in other["next"]:
                    other["next"].append(z["name"])
    card["zones"] = zs
    card["where"] = {c: z for c, z in card["where"].items() if z in {x["name"] for x in zs}}
    card["engaged"], card["moved"], card["dashed"] = [], {}, []
    return None


def hops(card: dict, a: str, b: str) -> int | None:
    """Zones between a and b (0 = the same zone); None if there's no way."""
    za, zb = zone(card, a), zone(card, b)
    if not za or not zb:
        return None
    seen, q = {za["name"]: 0}, deque([za["name"]])
    while q:
        cur = q.popleft()
        if cur == zb["name"]:
            return seen[cur]
        for n in zone(card, cur)["next"]:
            if n not in seen:
                seen[n] = seen[cur] + 1
                q.append(n)
    return None


def engaged_with(card: dict, who: str) -> list[str]:
    return sorted(b if a == who else a for a, b in card["engaged"] if who in (a, b))


def range_between(card: dict, a: str, b: str) -> str | None:
    if a not in card["where"] or b not in card["where"]:
        return None
    if b in engaged_with(card, a):
        return "engaged"
    h = hops(card, card["where"][a], card["where"][b])
    return None if h is None else "near" if h <= 1 else "far"


def place(card: dict, who: str, where: str) -> str | None:
    """Setting up: put a creature in a zone, no movement rules."""
    z = zone(card, where)
    if not z:
        return f"no zone called {where!r}"
    _disengage(card, who)
    card["where"][who] = z["name"]
    return None


def _disengage(card: dict, who: str) -> list[str]:
    foes = engaged_with(card, who)
    card["engaged"] = [p for p in card["engaged"] if who not in p]
    return foes


def remove(card: dict, who: str) -> None:
    _disengage(card, who)
    card["where"].pop(who, None)
    card["moved"].pop(who, None)


def move(card: dict, who: str, to: str, *, dash: bool = False, in_turn: bool = False) -> tuple[str | None, str]:
    """Walk to another zone. In a turn: the next zone with a move, one further with a Dash, and not both again.
    Returns (error, note); the note says who may make an opportunity attack."""
    if who not in card["where"]:
        return f"{who} isn't in the scene", ""
    z = zone(card, to)
    if not z:
        return f"no zone called {to!r}", ""
    h = hops(card, card["where"][who], z["name"])
    if h is None:
        return f"there's no way from {card['where'][who]} to {z['name']}", ""
    if h == 0:
        return None, ""
    if in_turn:
        dashed = dash or who in card["dashed"]
        left = (2 if dashed else 1) - card["moved"].get(who, 0)
        if h > left:
            need = "a Dash" if h == 2 and not dashed else "more than a turn"
            return (f"too far: {z['name']} is {h} zones away; your move this turn reaches "
                    f"{left} more" + (f" (it needs {need})" if left >= 0 else "")), ""
        card["moved"][who] = card["moved"].get(who, 0) + h
        if dash and who not in card["dashed"]:
            card["dashed"].append(who)
    foes = _disengage(card, who)
    card["where"][who] = z["name"]
    note = f"{who} leaves {', '.join(foes)}, who may make an opportunity attack" if foes else ""
    return None, note


def engage(card: dict, a: str, b: str) -> str | None:
    if a == b or a not in card["where"] or b not in card["where"]:
        return "both must be in the scene"
    if card["where"][a] != card["where"][b]:
        return f"{b} isn't in {card['where'][a]}: get to their zone first"
    if b not in engaged_with(card, a):
        card["engaged"].append(sorted([a, b]))
    return None


def new_turn(card: dict, who: str | None) -> None:
    if who:
        card["moved"].pop(who, None)
        card["dashed"] = [d for d in card["dashed"] if d != who]


def public(card: dict) -> dict:
    return {"zones": card["zones"], "where": dict(card["where"]), "engaged": [list(p) for p in card["engaged"]]}


def describe(card: dict, who: str) -> str:
    """'Where am I?' answered from the card, in a sentence or two (spoken in theater mode)."""
    if who not in card["where"]:
        return f"{who} isn't in the scene yet."
    here = zone(card, card["where"][who])
    with_me = [c for c, z in card["where"].items() if z == here["name"] and c != who]
    foes = engaged_with(card, who)
    near = [c for c, z in card["where"].items() if z in here["next"]]
    parts = [f"You're in {here['name']}" + (f": {here['desc']}" if here["desc"] else "") + "."]
    if foes:
        parts.append("You're fighting " + ", ".join(foes) + ".")
    others = [c for c in with_me if c not in foes]
    if others:
        parts.append("Here with you: " + ", ".join(others) + ".")
    if here["cover"] != "none":
        parts.append(f"There's {here['cover']} cover here.")
    if here["next"]:
        parts.append("From here you can reach " + ", ".join(here["next"]) + ".")
    if near:
        parts.append("Nearby: " + ", ".join(f"{c} in {card['where'][c]}" for c in near) + ".")
    far = [c for c in card["where"] if c != who and c not in with_me and c not in near]
    if far:
        parts.append("Further off: " + ", ".join(far) + ".")
    return " ".join(parts)
