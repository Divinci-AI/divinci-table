"""What the commentators are told. Built ONLY from TableWatch.view() (what the stream may already see), so the integrity rules are
enforced here, in code, and not by asking a model to keep a secret (director_commentary_test.py: a card that is not released is not in
the prompt, whatever the prompt says).

A feed is a list of short facts. Each is public or private; private facts (a hand, a seat's reason) are added only for a table where
integrity.commentary_may_use_private() says so, and only from what the hand view itself is already showing. Rules text is never in
the feed: a commentator cannot read aloud what it was not given."""
from __future__ import annotations

from . import integrity as ig


def card_names(views: list) -> set:
    """Every card name the commentators may say: what is on the shown boards, in shown graveyards, in the shown log's casts, and in
    released hands they were given. A name outside this set is not in this game as far as the stream knows."""
    names = set()
    for v, private_ok in views:
        for s in v["seats"]:
            names.update(p["name"] for p in s.get("permanents") or [] if p.get("name"))
            names.update(s.get("graveyard") or [])
            if s.get("commander"):
                names.add(s["commander"])
            if private_ok and s["private"]["shown"]:
                names.update(c["name"] for c in s["private"]["hand"] or [])
        names.update(v.get("_names") or [])
    return {n for n in names if n}


def build(watches: list, policy: str = "auto", since: dict | None = None) -> dict:
    """{"facts": [...], "names": set, "hand_informed": bool, "newest_ts": float, "cursor": {table: last event id}}."""
    since = since or {}
    facts, views, cursor, newest = [], [], {}, 0.0
    hand_informed = False
    for w in watches:
        v = w.view()
        private_ok = ig.commentary_may_use_private(w.t, policy)
        seen = since.get(w.t.name, 0)
        evs = [e for e in w.shown_events() if e["id"] > seen]
        cursor[w.t.name] = max([seen] + [e["id"] for e in evs])
        names = set()
        for e in evs[-40:]:
            t = e.get("type")
            line = None
            if t == "say" and e.get("speaker") and e.get("action") in ("land", "cast", "attack", "block", "equip", "station", "damage", "choose"):
                line = f"{e['speaker']}: {str(e.get('text') or '')[:160]}"
            elif t == "cast" and e.get("name"):
                names.add(e["name"])
            elif t == "life" and isinstance(e.get("delta"), int):
                line = f"{e.get('player')} {'gains' if e['delta'] > 0 else 'loses'} {abs(e['delta'])} life, now at {e.get('life')}."
            elif t == "died" and e.get("name"):
                line = f"{e.get('owner')}'s {e['name']} died."
                names.add(e["name"])
            elif t == "game-over":
                line = f"GAME OVER at {w.t.name}: {e.get('winner')} wins."
            if line:
                facts.append({"table": w.t.name, "private": False, "text": line})
                newest = max(newest, float(e.get("ts") or 0))
        v["_names"] = names
        state = []
        for s in v["seats"]:
            board = ", ".join((p.get("name") or "a face-down card") + (f" {p['pt']}" if p.get("pt") else "") for p in s.get("permanents") or []
                              if not str(p.get("type") or "").startswith("Basic Land")) or "nothing but lands"
            state.append(f"{s['name']} ({'person' if s['kind'] == 'human' else 'AI'}): life {s.get('life')}, {s.get('hand')} cards in hand; on the battlefield: {board}.")
        facts.append({"table": w.t.name, "private": False, "state": True,
                      "text": f"{w.t.name}, {v.get('turn') or 'nobody'}'s turn ({v.get('step') or 'not started'}). " + " ".join(state)})
        if private_ok:
            for s in v["seats"]:
                pr = s["private"]
                if not pr["shown"]:
                    continue
                hand_informed = True
                hand = ", ".join(f"{c['name']} ({c.get('cost') or 'land'})" for c in pr["hand"]) or "an empty hand"
                facts.append({"table": w.t.name, "private": True, "state": True, "text": f"{s['name']}'s hand (viewers can see it): {hand}."})
                for it in pr["thinking"][-2:]:
                    if it.get("source") in ("thought", "note"):
                        facts.append({"table": w.t.name, "private": True, "text": f"{s['name']}'s own reason: {it['text'][:200]}"})
        views.append((v, private_ok))
    return {"facts": facts, "names": card_names(views), "hand_informed": hand_informed, "newest_ts": newest, "cursor": cursor,
            "fresh": any(not f.get("state") for f in facts)}
