"""The D&D battle map: a grid of 5-ft squares, tokens on it, and the rules for moving them.

Pure functions over a plain dict, so the server can save it with the room and the tests need no HTTP.
Who may move what is the server's job (dnd_server.py); this module answers "is that square legal, and
how far is it?":
  • a token covers size×size squares (tiny–medium 1, large 2, huge 3, gargantuan 4), all of which must be
    on the map, not a wall, and not under another token;
  • a move is a walk, not a teleport: the cheapest path around walls, where each step (diagonals too) is
    5 ft, entering difficult terrain costs 10, and you may pass through your own side but not through foes;
  • in combat a walk is limited by the speed left on this turn (`used` is reset when the turn comes round).
"""
from __future__ import annotations

import heapq
import json
import re
from pathlib import Path

SIZES = {"tiny": 1, "small": 1, "medium": 1, "large": 2, "huge": 3, "gargantuan": 4}
WALKABLE = set(".DPM~")
MAX_SIDE = 60                                    # squares; a 300-ft room is plenty, and keeps paths cheap
MAX_TOKENS = 60                                  # a big battle; also bounds what a DM (or a bad TABLE line) can add


def load_manifest(path: Path) -> dict:
    return json.loads(Path(path).read_text())


def location(manifest: dict, loc_id: str) -> dict | None:
    return next((x for x in manifest.get("locations", []) if x.get("id") == loc_id), None)


def parse_layout(rows: list[str]) -> dict:
    """ASCII rows → {w, h, walls, difficult, doors, spawn: {pcs, monsters}}. Ragged rows are padded with walls."""
    rows = [str(r) for r in rows][:MAX_SIDE]
    w = min(MAX_SIDE, max((len(r) for r in rows), default=0))
    out = {"w": w, "h": len(rows), "walls": set(), "difficult": set(), "doors": [], "spawn": {"pcs": [], "monsters": []}}
    for y, row in enumerate(rows):
        for x in range(w):
            c = row[x] if x < len(row) else "#"
            if c not in WALKABLE:
                out["walls"].add((x, y))
            elif c == "~":
                out["difficult"].add((x, y))
            elif c == "D":
                out["doors"].append((x, y))
            elif c == "P":
                out["spawn"]["pcs"].append((x, y))
            elif c == "M":
                out["spawn"]["monsters"].append((x, y))
    return out


def new_map(loc: dict, cell_ft: int = 5) -> dict:
    return {"location": loc["id"], "name": loc.get("name", loc["id"]), "layout": list(loc["layout"]),
            "cell_ft": cell_ft, "tokens": {}, "used": {}}


def _grid(m: dict) -> dict:
    g = m.get("_grid")
    if g is None or g[0] != m["layout"]:
        g = (m["layout"], parse_layout(m["layout"]))
        m["_grid"] = g                           # cached; dropped by public() and by the server's dump()
    return g[1]


def side_of(kind: str) -> str:
    return "foe" if kind == "monster" else "party"


def footprint(size: str, x: int, y: int) -> list[tuple[int, int]]:
    n = SIZES.get(size, 1)
    return [(x + i, y + j) for i in range(n) for j in range(n)]


def _occupied(m: dict, skip: str | None = None) -> dict[tuple[int, int], str]:
    out = {}
    for tid, t in m["tokens"].items():
        if tid != skip:
            for sq in footprint(t["size"], t["x"], t["y"]):
                out[sq] = tid
    return out


def fits(m: dict, size: str, x: int, y: int, skip: str | None = None) -> str | None:
    """None if a token of this size can stand with its corner at (x, y); otherwise why not."""
    g, occ = _grid(m), _occupied(m, skip)
    for sq in footprint(size, x, y):
        if not (0 <= sq[0] < g["w"] and 0 <= sq[1] < g["h"]):
            return "off the map"
        if sq in g["walls"]:
            return "a wall is there"
        if sq in occ:
            return f"{m['tokens'][occ[sq]]['name']} is there"
    return None


def _slug(m: dict, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "token"
    tid, n = base, 2
    while tid in m["tokens"]:
        tid, n = f"{base}-{n}", n + 1
    return tid


def nearest_free(m: dict, size: str, x: int, y: int) -> tuple[int, int] | None:
    """The closest square (by walking distance, ignoring tokens' sides) where a token of this size fits."""
    g = _grid(m)
    seen, todo = {(x, y)}, [(x, y)]
    while todo:
        cx, cy = todo.pop(0)
        if fits(m, size, cx, cy) is None:
            return cx, cy
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nx, ny = cx + dx, cy + dy
                if (nx, ny) not in seen and 0 <= nx < g["w"] and 0 <= ny < g["h"]:
                    seen.add((nx, ny))
                    todo.append((nx, ny))
    return None


def place(m: dict, name: str, kind: str, x: int | None = None, y: int | None = None, size: str = "medium",
          owner: str | None = None, mini: str | None = None, speed: int = 30) -> tuple[str | None, dict | None]:
    """Put a token on the map. Without a square (or on a taken one) it goes to the nearest free spawn of its
    side. Returns (error, token)."""
    if kind not in ("pc", "monster", "npc"):
        return "kind is pc, monster or npc", None
    if len(m["tokens"]) >= MAX_TOKENS:
        return f"the map holds at most {MAX_TOKENS} tokens", None
    size = size if size in SIZES else "medium"
    g = _grid(m)
    if x is None or y is None or fits(m, size, x, y) is not None:
        spawns = g["spawn"]["monsters" if kind == "monster" else "pcs"] or [(g["w"] // 2, g["h"] // 2)]
        spot = next((s for s in spawns if fits(m, size, *s) is None), None) or nearest_free(m, size, *spawns[0])
        if spot is None:
            return "no room on the map", None
        x, y = spot
    tid = _slug(m, name)
    t = {"id": tid, "name": name[:40], "kind": kind, "side": side_of(kind), "size": size, "x": x, "y": y,
         "owner": owner, "mini": mini, "speed": max(0, min(120, int(speed or 30)))}
    m["tokens"][tid] = t
    return None, t


def walk_cost(m: dict, tid: str, x: int, y: int, limit_ft: int | None = None) -> int | None:
    """Feet of the cheapest legal walk for token tid to corner (x, y), or None if it can't get there (within
    limit_ft when given). Squares under your own side can be crossed, never ended on; foes block."""
    t = m["tokens"][tid]
    if fits(m, t["size"], x, y, skip=tid) is not None:
        return None
    g, occ = _grid(m), _occupied(m, tid)
    blocked_by_foe = {sq for sq, o in occ.items() if m["tokens"][o]["side"] != t["side"]}
    step = m.get("cell_ft", 5)
    start, goal = (t["x"], t["y"]), (x, y)
    best = {start: 0}
    heap = [(0, start)]
    while heap:
        cost, (cx, cy) = heapq.heappop(heap)
        if (cx, cy) == goal:
            return cost
        if cost > best.get((cx, cy), 1 << 30) or (limit_ft is not None and cost > limit_ft):
            continue
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if not dx and not dy:
                    continue
                nx, ny = cx + dx, cy + dy
                sqs = footprint(t["size"], nx, ny)
                if any(not (0 <= a < g["w"] and 0 <= b < g["h"]) or (a, b) in g["walls"] or (a, b) in blocked_by_foe
                       for a, b in sqs):
                    continue
                if dx and dy and ((cx + dx, cy) in g["walls"] or (cx, cy + dy) in g["walls"]):
                    continue                     # no slipping diagonally between two walls' corners
                nc = cost + step * (2 if any(sq in g["difficult"] for sq in sqs) else 1)
                if limit_ft is not None and nc > limit_ft:
                    continue
                if nc < best.get((nx, ny), 1 << 30):
                    best[(nx, ny)] = nc
                    heapq.heappush(heap, (nc, (nx, ny)))
    return None


def move(m: dict, tid: str, x: int, y: int, *, walk: bool = True, in_turn: bool = False) -> tuple[str | None, int]:
    """Move a token. walk=False is a DM's hand placing it anywhere it fits (no path, no speed). A walk needs a
    legal path; in_turn also limits it to the speed left this turn. Returns (error, feet walked)."""
    t = m["tokens"].get(tid)
    if not t:
        return "no such token", 0
    if not (isinstance(x, int) and isinstance(y, int)):
        return "squares are whole numbers", 0
    if not walk:
        why = fits(m, t["size"], x, y, skip=tid)
        if why:
            return why, 0
        t["x"], t["y"] = x, y
        return None, 0
    left = max(0, t["speed"] - m["used"].get(tid, 0)) if in_turn else None
    cost = walk_cost(m, tid, x, y, left)
    if cost is None:
        why = fits(m, t["size"], x, y, skip=tid)
        return why or (f"too far: {left} ft of movement left this turn" if in_turn else "no way through"), 0
    t["x"], t["y"] = x, y
    if in_turn:
        m["used"][tid] = m["used"].get(tid, 0) + cost
    return None, cost


def new_turn(m: dict, tid: str | None) -> None:
    if tid:
        m["used"][tid] = 0


def token_by_name(m: dict, name: str) -> dict | None:
    n = (name or "").strip().lower()
    return next((t for t in m["tokens"].values() if t["name"].lower() == n), None)


def public(m: dict) -> dict:
    return {k: v for k, v in m.items() if not k.startswith("_")}
