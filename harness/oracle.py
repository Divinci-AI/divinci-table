#!/usr/bin/env python3
"""The table's oracle: reads a finished game's research folder and reports what the TABLE got wrong.

    python3 harness/oracle.py --game DIR [--decks decks/a.json decks/b.json] [--stall-s 120] [--json]

DIR holds events.jsonl (public stream) and brain.jsonl (private: one record per seat action, refusals included).
Stdlib only, no network, no model. It never prints a hand: a finding names an event id / brain line, a seat and a
SIGNATURE, not a hidden card (except the card a finding is about, which was in the seat's own action).

Finding kinds (see docs/DOGFOOD-LOOP-AND-STREAM-GOAL.md, L1):
  pilot-error           the table refused because a required choice was missing (reported, but not a table bug)
  refused-but-offered   a cast/land the pilot was offered (only castable_now / unplayed-land options are offered) was refused
  accepted-but-illegal  second land in one turn; a card played that was not in the hand; the played card stayed in hand
  no-effect             a spell whose text promises card draw, life gain, tokens or a board wipe produced none
  drift                 hand does not follow from the last hand + draws - plays; a life total that is not the last logged one
  leak                  a public line names a hand card that this action did not just play
  after-elimination     combat damage dealt to a player who is already out (life <= 0)
  stall                 a gap of --stall-s seconds between events with no game-over (only reported if the game never ended)
  unrunnable-text       the table refused a card's text whole
Exit code: 0 when clean, 1 when there is at least one TABLE finding (pilot-error is counted and shown, not failed on) (so a loop can count).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

LAND_WORDS = ("land",)
PILOT_ERROR = re.compile(r"say which with --|leave out --|name a |which one|choose \d+ with --|can.t pay .* to turn", re.I)   # the table asked for a choice the pilot did not make
BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes", "Snow-Covered Plains", "Snow-Covered Island",
          "Snow-Covered Swamp", "Snow-Covered Mountain", "Snow-Covered Forest"}
HAND_TOUCH = {"take", "put", "give", "steal", "discard", "new-game", "reset", "search"}   # change ANOTHER seat's hand (or all of them)
ZONE_ACTIONS = {"bounce", "destroy", "exile", "untap", "tap", "animate", "sacrifice", "die", "turn-up"}   # these name a PERMANENT on the table
KINDS = ("after-elimination", "refused-but-offered", "accepted-but-illegal", "no-effect", "drift", "leak", "stall", "unrunnable-text")


def read_jsonl(p: Path) -> list[dict]:
    out = []
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def card_texts(deck_files) -> dict[str, str]:
    """name -> oracle text, from deck JSONs when they carry it (they may carry only names; then text rules just don't fire)."""
    out = {}
    for f in deck_files or []:
        try:
            d = json.loads(Path(f).read_text())
        except (OSError, ValueError):
            continue
        for c in (d.get("mainBoard") if isinstance(d, dict) else d) or []:
            if isinstance(c, dict) and c.get("name"):
                if c.get("type") and not re.search(r"Instant|Sorcery", str(c["type"])):
                    continue                              # a permanent's "draw a card" is a trigger, not what casting it does
                t = str(c.get("text") or c.get("oracle_text") or "")
                t = re.sub(r"\([^)]*\)", "", t)          # reminder text
                out[c["name"]] = "\n".join(l for l in t.splitlines() if not re.match(r"\s*(\w+cycling|Channel|Cycling|Flashback|Kicker|Escape)\b", l))
    return out


def signature(kind: str, action: str, why: str) -> str:
    """One bug, one signature: ids, numbers, quoted/capitalised card names and seat names are stripped before hashing."""
    w = re.sub(r"#\d+", "#", why or "")
    w = re.sub(r"\d+", "N", w)
    w = re.sub(r"[\"'“”‘’][^\"'“”‘’]*[\"'“”‘’]", "'X'", w)
    w = re.sub(r"\b[A-Z][\w'-]*(\s+[A-Z][\w'-]*)*", "X", w)
    return f"{kind}|{action}|{' '.join(w.lower().split())[:80]}"


def finding(kind, seat, line, action, why="", event=None):
    return {"kind": kind, "seat": seat, "brain_line": line, "event_id": event, "action": action,
            "why": why[:160], "signature": signature(kind, action, why)}


def draw_count(rec) -> int:
    return len(rec.get("drew") or [])


def card_types(deck_files) -> dict[str, str]:
    out = {}
    for f in deck_files or []:
        try:
            d = json.loads(Path(f).read_text())
        except (OSError, ValueError):
            continue
        for c in (d.get("mainBoard") if isinstance(d, dict) else d) or []:
            if isinstance(c, dict) and c.get("name") and c.get("type"):
                out[c["name"]] = str(c["type"])
    return out


PERMANENT = re.compile(r"Creature|Artifact|Enchantment|Planeswalker|Battle|Land")


def check_board(brain, snaps, types, commanders=None, window=4.0, texts=None) -> list[dict]:
    """Battlefield checks from the PUBLIC snapshots (state.jsonl). A snapshot is a sample, so each check is deliberately tolerant."""
    out = []
    stats = check_board.stats = {"plays": 0, "checked": 0, "samples": len(snaps)}
    snaps = sorted(snaps, key=lambda x: x["ts"])
    recs = [(i, r) for i, r in enumerate(brain) if r.get("seat") and r.get("action") and not r.get("refused")]
    # 1. a permanent that was cast / played as a land must show up (or have visibly died / bounced) within `window` seconds
    for i, r in recs:
        body = r.get("body") or {}
        name = body.get("name")
        if r["action"] not in ("cast", "land") or not name or name == "commander" or body.get("face_down"):
            continue
        t = types.get(name, "")
        if r["action"] == "cast" and (not PERMANENT.search(t) or re.search(r"Instant|Sorcery|Aura", t)):   # an Aura sits on what it enchants: the public board does not list it by itself
            continue
        if r["action"] == "land" and not t:
            continue
        stats["plays"] += 1
        after = [x for x in snaps if r["ts"] <= x["ts"] <= r["ts"] + window and r["seat"] in x["seats"]]
        before = [x for x in snaps if x["ts"] < r["ts"] and r["seat"] in x["seats"]]
        if not after or not before:
            continue                                   # unmeasurable: no sample on both sides, so no claim
        stats["checked"] += 1
        seen = any(p[1] == name for x in after for p in x["seats"][r["seat"]]["perms"])
        died = after[-1]["seats"][r["seat"]]["gy"] is not None and before[-1]["seats"][r["seat"]]["gy"] is not None \
            and after[-1]["seats"][r["seat"]]["gy"] > before[-1]["seats"][r["seat"]]["gy"]
        returned = any(name in (x.get("hand_after") or []) for _, x in recs if x["seat"] == r["seat"] and r["ts"] < x["ts"] <= r["ts"] + window and x is not r)
        if not (seen or died or returned or name in (r.get("hand_after") or []) and r["action"] == "land" and body.get("bounce")):
            out.append(finding("no-effect", r["seat"], i, r["action"], "the permanent never appeared on the battlefield"))
    # 1b. a spell whose text creates tokens, or wipes the board ("destroy/exile/return all"), must visibly change the public board
    for i, r in recs:
        name = (r.get("body") or {}).get("name")
        t = (texts or {}).get(name, "") if r["action"] == "cast" else ""
        if not t or "target" in t.lower() or re.search(r"\bmay\b|\bif\b|\bunless\b|\bchoose\b|\bor\b", t, re.I):
            continue                                   # a choice, a condition or a target: the board change depends on state we cannot see
        make = re.search(r"create (a|an|one|two|three|\d+) [^.]*\btokens?\b", t, re.I)
        wipe = re.search(r"\b(destroy|exile|return) all\b", t, re.I)
        if not (make or wipe):
            continue
        after = [x for x in snaps if r["ts"] <= x["ts"] <= r["ts"] + window]
        before = [x for x in snaps if x["ts"] < r["ts"]]
        if not after or not before:
            continue
        stats["spells"] = stats.get("spells", 0) + 1
        if make:
            tok = lambda x: sum(1 for st in x["seats"].values() for p in st["perms"] if p[6])
            if max(tok(x) for x in after) <= tok(before[-1]):
                out.append(finding("no-effect", r["seat"], i, "cast", "the spell creates tokens; none appeared on the battlefield"))
        else:
            named = re.search(r"\b(?:destroy|exile|return) all ([^.]*)", t, re.I).group(1)
            kinds_ = [k for k in ("creature", "artifact", "enchantment", "planeswalker", "land") if k in named.lower()]
            if not kinds_:
                continue                               # "all other permanents" etc.: the set it touches is not stated by type
            want = re.compile("|".join(kinds_), re.I)
            perms = lambda x: sum(1 for st in x["seats"].values() for p in st["perms"] if not p[6] and want.search(types.get(p[1], "")))
            if perms(before[-1]) > 0 and min(perms(x) for x in after) >= perms(before[-1]):
                out.append(finding("no-effect", r["seat"], i, "cast", "the spell removes all permanents of a kind; the battlefield did not shrink"))
    # 2. zones: hand + library + graveyard + non-token permanents (not the commander) must not EXCEED what the seat started with,
    #    and must do so in two samples in a row (a card in flight between zones can be counted twice for one sample)
    base: dict = {}
    over: dict = {}
    for x in snaps:
        for seat, st in x["seats"].items():
            if None in (st["hand"], st["library"], st["gy"]):
                continue
            tot = st["hand"] + st["library"] + st["gy"] + sum(1 for p in st["perms"] if not p[6] and p[1] != st.get("cmdr"))
            if seat not in base:
                base[seat] = tot
            elif tot > base[seat]:
                over[seat] = over.get(seat, 0) + 1
                if over[seat] == 2:
                    out.append(finding("drift", seat, None, "zones", f"zones hold {tot - base[seat]} more card(s) than the deck started with"))
            else:
                over[seat] = 0
    return out


def check_game(events, brain, texts=None, stall_s=120.0, only=None, commanders=None, snaps=None, types=None) -> list[dict]:
    texts = texts or {}
    commanders = set(commanders or [])
    on = set(only or KINDS)
    out = []
    recs = [(i, r) for i, r in enumerate(brain) if r.get("seat") and r.get("action")]
    # last life value announced publicly, by player
    life_events = [e for e in events if e.get("type") == "life" and "life" in e]

    # 1. refusals
    if "refused-but-offered" in on or "unrunnable-text" in on:
        for i, r in recs:
            if not r.get("refused"):
                continue
            why = r.get("why") or ""
            if r["action"] in ("cast", "land", "turn-up") and r["refused"] == "illegal":
                if "unrunnable-text" in on and re.search(r"can.t be played|not supported|don.t know how|unrunnable", why, re.I):
                    out.append(finding("unrunnable-text", r["seat"], i, r["action"], why))
                elif "refused-but-offered" in on:
                    kind = "pilot-error" if PILOT_ERROR.search(why) else "refused-but-offered"
                    out.append(finding(kind, r["seat"], i, r["action"], why))

    # per seat walk: hand continuity, land count per turn
    last_hand: dict[str, list[str]] = {}
    lands_this_turn: Counter = Counter()
    for i, r in recs:
        if r.get("refused"):
            continue
        s, a, hand = r["seat"], r["action"], list(r.get("hand_after") or [])
        body = r.get("body") or {}
        if a in HAND_TOUCH:                           # one seat's action reaches into other hands: continuity is unknown
            last_hand.clear()
        prev = last_hand.get(s)
        if prev and len(prev) >= 3 and not (Counter(prev) & Counter(hand)):
            prev = None                               # nothing in common: the game was reset or reshuffled between the two records
            lands_this_turn[s] = 0
        played = None
        if a == "begin":
            lands_this_turn[s] = 0
        if a == "land":
            played = body.get("name")
            lands_this_turn[s] += 1
            if "accepted-but-illegal" in on and lands_this_turn[s] > 1 and not body.get("bounce"):
                out.append(finding("accepted-but-illegal", s, i, "land", "second land in one turn"))
        if a == "cast":
            played = body.get("name")
        if prev is not None and a in ("land", "cast", "begin", "pass", "say", "end"):
            expect = Counter(prev)
            if a in ("land", "cast") and played and played != "commander" and not body.get("commander"):
                if "accepted-but-illegal" in on and expect[played] == 0 and played not in commanders:
                    out.append(finding("accepted-but-illegal", s, i, a, f"{a} of a card that was not in the hand"))
                expect[played] -= 1 if expect[played] > 0 else 0
            for d in r.get("drew") or []:
                expect[d] += 1
            if a == "cast" and played and "accepted-but-illegal" in on and Counter(hand)[played] > Counter(expect)[played] and played in prev \
                    and not (body.get("face_down") or body.get("bounce")):
                out.append(finding("accepted-but-illegal", s, i, a, "the cast card is still in the hand"))
            if "drift" in on and +Counter(hand) != +Counter(expect) and not r.get("todo"):
                extra = sorted((+(Counter(hand) - Counter(expect))).elements())
                gone = sorted((+(Counter(expect) - Counter(hand))).elements())
                # a bounce land, a returned permanent or a search legitimately adds; flag only unexplained loss
                if gone:
                    out.append(finding("drift", s, i, a, f"hand lost {len(gone)} card(s) that no action accounts for"))
                elif extra and a in ("pass", "say", "end"):
                    out.append(finding("drift", s, i, a, f"hand gained {len(extra)} card(s) during a {a}"))
        if a == "cast" and "no-effect" in on and played:
            # a host-style cast resolves in the seat's NEXT actions (draw, life): look ahead until its next begin/cast/end
            window = [r]
            for j, x in recs:
                if j > i and x.get("seat") == s and not x.get("refused"):
                    if x["action"] in ("cast", "begin", "end", "pass", "mulligan"):
                        break
                    window.append(x)
            t = texts.get(played, "")
            m = re.search(r"draw (a|one|two|three|\d+) cards?", t, re.I)
            if m and prev is not None:
                n = {"a": 1, "one": 1, "two": 2, "three": 3}.get(m.group(1).lower()) or int(m.group(1))
                if "target" not in t.lower() and "may" not in t.lower() and sum(draw_count(x) for x in window) < n:
                    out.append(finding("no-effect", s, i, a, f"text promises drawing {n}; none drawn"))
            m = re.search(r"you gain (\d+) life", t, re.I)
            if m and "may" not in t.lower() and r.get("life") is not None:
                before = next((x.get("life") for _, x in reversed([p2 for p2 in recs if p2[0] < i and p2[1].get("seat") == s])
                               if x.get("life") is not None), None)
                top = max((x.get("life") for x in window if x.get("life") is not None), default=r["life"])
                if before is not None and top - before < int(m.group(1)) and "if " not in t.lower():
                    out.append(finding("no-effect", s, i, a, f"text promises {m.group(1)} life; life did not rise"))
        if a == "mulligan" or a == "new-game":
            lands_this_turn[s] = 0
        last_hand[s] = hand

    # 2. leak: a public say names a card in the speaker's hand that this action did not play
    if "leak" in on:
        by_ts = sorted(((r.get("ts", 0), r) for _, r in recs if not r.get("refused")), key=lambda x: x[0])
        spoken: list[str] = []
        known = set(BASICS)                           # a name the table has already heard (played, bounced, untapped) is no secret
        for e in events:
            if e.get("type") != "say" or not e.get("speaker"):
                continue
            if e.get("action") in ZONE_ACTIONS:           # about a permanent already on the table (a face-down one is revealed as it leaves)
                spoken.append(e.get("text") or "")
                continue
            heard = set(known)
            said_before = " ".join(spoken).lower()
            spoken.append(e.get("text") or "")
            mine = [(ts, r) for ts, r in by_ts if r["seat"] == e["speaker"] and ts <= e.get("ts", 0) + 0.05]
            if not mine:
                continue
            same = [r for ts, r in mine if r["action"] == e.get("action")]   # the record THIS line came from, not just the nearest in time
            r = same[-1] if same else mine[-1][1]
            names = [n for n in (r.get("hand_after") or []) if len(n) > 4]
            played = (r.get("body") or {}).get("name")
            for n in sorted(set(names)):
                if n != played and n not in heard and n.lower() not in said_before and n not in (r.get("drew") or []) and re.search(r"\b" + re.escape(n) + r"\b", e.get("text") or ""):
                    out.append(finding("leak", e["speaker"], None, "say", "a public line names a card still in hand", event=e.get("id")))
                    break

    # 3. life drift: the seat's own record vs the last life event for that player before it
    if "drift" in on:
        for i, r in recs:
            if r.get("refused") or r.get("life") is None:
                continue
            mine_ev = [e for e in life_events if e.get("player") == r["seat"]]
            before = [e for e in mine_ev if e.get("ts", 0) < r.get("ts", 0) - 0.05]
            near = [e for e in mine_ev if abs(e.get("ts", 0) - r.get("ts", 0)) <= 0.05]   # same rounded second: either order is possible
            start = next((x["life"] for _, x in recs if x["seat"] == r["seat"] and x.get("life") is not None), None)
            ok_lives = ({before[-1]["life"]} if before else ({start} if start is not None else set())) | {e["life"] for e in near}   # no life event yet: the starting total
            if ok_lives and r["life"] not in ok_lives and r["action"] not in ("life",):
                out.append(finding("drift", r["seat"], i, r["action"], "life total differs from the last announced one"))
            if r["life"] is not None and r["life"] < -50:
                out.append(finding("drift", r["seat"], i, r["action"], "implausible life total"))

    # 3b. damage to a player who is already out: they have lost, their cards have left the game. Combat damage is simultaneous, so
    # several attackers in the SAME combat step may each take the life total further below 0; only damage after the step has moved on counts.
    if "after-elimination" in on:
        lastlife: dict = {}
        out_at: dict = {}                                # player -> number of phase/turn events seen when they went to 0 or less
        moves = 0
        for e in events:
            if e.get("type") in ("phase", "turn"):
                moves += 1
                continue
            if e.get("type") != "life" or "life" not in e:
                continue
            p_ = e.get("player")
            if lastlife.get(p_) is not None and lastlife[p_] <= 0 and isinstance(e.get("delta"), int) and e["delta"] < 0 \
                    and moves > out_at.get(p_, moves):
                out.append(finding("after-elimination", p_, None, "life", "damage dealt to a player already at 0 life or less, after the step moved on", event=e.get("id")))
                break                                    # one finding per game: the rest are the same bug
            if (lastlife.get(p_) is None or lastlife[p_] > 0) and isinstance(e.get("life"), int) and e["life"] <= 0:
                out_at[p_] = moves
            lastlife[p_] = e.get("life")

    # 4. stall: only if the game never ended
    if "stall" in on and not any(e.get("type") == "game-over" for e in events):
        ts = [e["ts"] for e in events if "ts" in e]
        gaps = [(b - a, a) for a, b in zip(ts, ts[1:])]
        if gaps and max(gaps)[0] > stall_s:
            out.append(finding("stall", None, None, "-", f"{max(gaps)[0]:.0f}s between events"))
    if snaps and ("no-effect" in on or "drift" in on):
        out += [f for f in check_board(brain, snaps, types or {}, commanders, texts=texts) if f["kind"] in on]
    return out


def find_game_dir(p: Path) -> Path:
    if (p / "events.jsonl").exists() or (p / "brain.jsonl").exists():
        return p
    subs = sorted(x for x in p.glob("*/") if (x / "brain.jsonl").exists() or (x / "events.jsonl").exists())
    if not subs:
        raise SystemExit(f"no events.jsonl / brain.jsonl under {p}")
    return subs[-1]


def commander_names(deck_files) -> set[str]:
    out = set()
    for f in deck_files or []:
        try:
            c = json.loads(Path(f).read_text()).get("commander")
        except (OSError, ValueError, AttributeError):
            continue
        for x in (c if isinstance(c, list) else [c]):
            out |= {x} if isinstance(x, str) else ({x.get("name")} if isinstance(x, dict) else set())
    return {n for n in out if n}


def run(game_dir: Path, decks=None, stall_s=120.0, only=None, state=None) -> dict:
    d = find_game_dir(game_dir)
    snaps = read_jsonl(Path(state)) if state else None
    f = check_game(read_jsonl(d / "events.jsonl"), read_jsonl(d / "brain.jsonl"), card_texts(decks), stall_s, only,
                   commander_names(decks), snaps, card_types(decks))
    return {"game": d.name, "findings": f, "by_kind": dict(Counter(x["kind"] for x in f)),
            "signatures": dict(Counter(x["signature"] for x in f)), "board_checked": bool(snaps), "board_coverage": getattr(check_board, "stats", None) if snaps else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True); ap.add_argument("--decks", nargs="*", default=[])
    ap.add_argument("--stall-s", type=float, default=120.0); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rep = run(Path(a.game), a.decks, a.stall_s)
    if a.json:
        print(json.dumps(rep, indent=1))
    else:
        print(f"oracle: game {rep['game']}: {len(rep['findings'])} finding(s) {rep['by_kind']}")
        for s, n in rep["signatures"].items():
            print(f"  {n:>3} x {s}")
    sys.exit(1 if any(f["kind"] != "pilot-error" for f in rep["findings"]) else 0)


if __name__ == "__main__":
    main()
