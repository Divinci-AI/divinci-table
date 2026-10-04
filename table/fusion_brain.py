"""The Fusion player: a Divinci release (RAG over Magic rules + its deck) playing one AI seat.

It watches the table for anything addressed to its seat and turns each real decision into a
numbered list of LEGAL options (what to cast, whom to attack, whether to block). The model can only
pick a number, so it can never make an illegal move. Mechanical steps stay in code so a turn isn't
eight model calls: untap and draw, playing a land, applying removal the table already announced.

Stdlib only. Use /usr/bin/python3: the python.org build fails TLS to *.divinci.app.

  infisical run --projectId="$INFISICAL_WORKSPACE_ID" --env=prod --path=/ -- \\
      /usr/bin/python3 table/fusion_brain.py --seat Fusion

  --dry   ask nothing: pick option 1 every time (rehearsal without the API)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.divinci.app"
# The release ID is account-specific: FUSION_RELEASE_ID, or table/.cache/fusion.json {"release_id": …}
# (gitignored). See docs/fusion-player.md for how to make your own release.
_CFG = Path(__file__).parent / ".cache" / "fusion.json"
_CONF = json.loads(_CFG.read_text()) if _CFG.exists() else {}
RELEASE_ID = os.environ.get("FUSION_RELEASE_ID") or _CONF.get("release_id", "")


def release_for(commander: str | None) -> str:
    """One release per deck (its RAG holds that deck's cards and strategy): fusion.json "releases"
    maps commander → release; FUSION_RELEASE_ID overrides; else the default release_id."""
    return (os.environ.get("FUSION_RELEASE_ID") or (_CONF.get("releases") or {}).get(commander or "")
            or _CONF.get("release_id", ""))
HERE = Path(__file__).parent


class Table:
    def __init__(self, base: str, seat: str, token_file: Path):
        self.base, self.seat, self.token_file = base.rstrip("/"), seat, token_file

    @property
    def token(self):                                     # re-read each time: a server restart writes a new one
        return self.token_file.read_text().strip()

    def _req(self, method, path, body=None, brain=True):
        headers = {"Content-Type": "application/json"}
        if brain:
            headers["X-Brain-Token"] = self.token
        r = urllib.request.Request(self.base + path, method=method, headers=headers,
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                code, raw = resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            code, raw = e.code, e.read().decode()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"error": f"non-JSON (HTTP {code}): {raw[:160]}"}
        return code, data

    def state(self):
        return self._req("GET", f"/api/brain/state?seat={urllib.request.quote(self.seat)}")[1]

    def act(self, action, **body):
        code, d = self._req("POST", f"/api/brain/{action}", {"seat": self.seat, **body})
        told = False
        while code == 409 and d.get("waiting"):          # my turn walks its steps: the others are passing
            if not told:
                print(f"  … {action}: waiting on {d['waiting']}", flush=True); told = True
            time.sleep(2)
            code, d = self._req("POST", f"/api/brain/{action}", {"seat": self.seat, **body})
        line = " | ".join(d.get("said", [])) if code < 400 else f"REFUSED: {d.get('error')}"
        print(f"  → {action} {json.dumps(body)[:120]}  {line}", flush=True)
        return code < 400, d

    def events(self, since):
        return self._req("GET", f"/api/events?since={since}", brain=False)[1]


# Spend cap for public rooms: after FUSION_MAX_CALLS release requests this seat stops asking and plays on as a
# "sleeping" AI (lands, then the passive choice). 0 = no cap (the laptop table). The count lives in the
# container, so it restarts if the room's container is replaced; the lobby's AI-rooms-per-day limit bounds that.
MAX_CALLS = int(os.environ.get("FUSION_MAX_CALLS", "0") or 0)
CALLS = {"n": 0, "announced": False}
PASSIVE = ("stop", "none", "pass", "block", "keep")


def capped() -> bool:
    return bool(MAX_CALLS) and CALLS["n"] >= MAX_CALLS


def ask(prompt: str, dry: bool, release: str | None = None) -> dict:
    if dry:
        return {"choice": 1, "say": ""}
    if capped():
        return {"capped": True}
    CALLS["n"] += 1
    body = json.dumps({"messages": [{"role": "user", "content": prompt}], "releaseId": release or RELEASE_ID}).encode()
    req = urllib.request.Request(API + "/api/v1/chat/completions", data=body, method="POST", headers={
        "Authorization": "Bearer " + os.environ["DIVINCI_FUSION_API_KEY"], "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, text = resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode()
    except Exception as e:                                   # noqa: BLE001 — network: fall back, keep playing
        print(f"  ! Fusion unreachable ({type(e).__name__}); taking option 1", flush=True)
        return {"choice": 1, "say": ""}
    try:
        content = json.loads(text)["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", content, re.S)
        out = json.loads(m.group(0)) if m else {}
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        print(f"  ! unparseable reply (HTTP {status}): {text[:160]}", flush=True)
        out = {}
    print(f"  ⏱ Fusion {time.time() - t0:.1f}s → {out}", flush=True)
    try:                                                     # research log: every decision, in full
        logd = Path(__file__).parent / ".cache" / "research"
        logd.mkdir(parents=True, exist_ok=True)
        with open(logd / "fusion-decisions.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": round(time.time(), 2), "release": release or RELEASE_ID, "prompt": prompt,
                                 "reply": out, "raw": content if 'content' in locals() else text[:2000],
                                 "latency_s": round(time.time() - t0, 2)}) + "\n")
    except OSError:
        pass
    return out if isinstance(out, dict) else {}


# Small, frequent decisions (cast an instant in someone else's turn, or pass) go to a LOCAL decision
# model when one is set, so they cost nothing: Ollama >= 0.35 serves decision models (tev1, nimble) on
# /v1/systemone, which return a probability per option. The turn's real moves stay on the release.
LOCAL_MODEL = os.environ.get("FUSION_LOCAL_MODEL", "")          # e.g. "tev1"; empty = everything on the release
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


def local_choice(state: dict, options: list[tuple[str, dict]], question: str) -> tuple[dict, dict] | None:
    """The option a local decision model prefers, with its probabilities — or None (not set / failed)."""
    if not LOCAL_MODEL:
        return None
    crit = {str(i + 1): lab for i, (lab, _) in enumerate(options)}
    body = {"model": LOCAL_MODEL, "keep_alive": "30m",
            "state": json.dumps(state_for_prompt(state), separators=(",", ":")),
            "questions": {"q": {"type": "choice", "instructions": question, "criteria": crit}}}
    t0 = time.time()
    try:
        req = urllib.request.Request(OLLAMA_URL + "/v1/systemone", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        raw = urllib.request.urlopen(req, timeout=45).read().decode()
        probs = json.loads(raw)["answers"]["q"]["probabilities"]
        best = max(crit, key=lambda k: probs.get(k, 0.0))
    except Exception as e:                                   # noqa: BLE001 — local model down: caller falls back
        print(f"  ! local {LOCAL_MODEL} failed ({type(e).__name__}: {str(e)[:80]}); asking the release", flush=True)
        return None
    print(f"  ⏱ {LOCAL_MODEL} {time.time() - t0:.1f}s → {best} {({k: round(v, 2) for k, v in probs.items()})}", flush=True)
    try:
        logd = Path(__file__).parent / ".cache" / "research"
        with open(logd / "fusion-decisions.jsonl", "a") as fh:
            fh.write(json.dumps({"ts": round(time.time(), 2), "decider": LOCAL_MODEL, "question": question,
                                 "options": crit, "probabilities": probs, "choice": best,
                                 "latency_s": round(time.time() - t0, 2)}) + "\n")
    except OSError:
        pass
    return options[int(best) - 1][1], probs


def state_for_prompt(s: dict) -> dict:
    """What the model needs, compact. The hand is private but this call is server-to-Divinci only."""
    return {"you": s.get("name"), "turn": s.get("turn"), "life": s.get("life_table"),
            "hand": [{k: h.get(k) for k in ("name", "cost", "type", "text", "pt", "castable_now", "land",
                                            "face_down_castable", "face_up_cost")} for h in s.get("hand", [])],
            "commander": s.get("commander_card"), "commander_in_zone": s.get("commander_in_zone"),
            "your_permanents": [{k: p.get(k) for k in ("id", "name", "pt", "tapped", "sick", "role", "attached_to",
                                                       "face_down", "turn_up_cost")}
                                for p in s.get("permanents", []) if "Land" not in (p.get("type") or "")],
            "lands": sum(1 for p in s.get("permanents", []) if "Land" in (p.get("type") or "")),
            "untapped_mana": s.get("untapped_mana"), "graveyard": s.get("graveyard"),
            "opponents_cards_seen": s.get("announced_by_others", [])[-20:]}


def choose(state: dict, options: list[tuple[str, dict]], question: str, dry: bool) -> tuple[dict, str]:
    """options: [(label, {"action":..., **body})]. Returns the chosen option's body and the line to say."""
    prompt = ("STATE:\n" + json.dumps(state_for_prompt(state), separators=(",", ":")) + "\nOPTIONS:\n"
              + "\n".join(f"{i + 1}. {lab}" for i, (lab, _) in enumerate(options)) + f"\n{question}")
    r = ask(prompt, dry, release_for(state.get("commander")))
    if r.get("capped"):                                    # sleeping AI: the most passive option, and no talk
        i = next((k for k, (_, b) in enumerate(options) if b.get("action") in PASSIVE), len(options) - 1)
        return options[i][1], ""
    try:
        i = int(r.get("choice", 1)) - 1
    except (TypeError, ValueError):
        i = 0
    i = i if 0 <= i < len(options) else 0
    return options[i][1], str(r.get("say") or "").strip()


def say(t: Table, line: str):
    if capped() and not CALLS["announced"]:
        CALLS["announced"] = True
        line = "I've reached this table's limit for AI thinking, so I'll just play my lands and pass from here."
    if line:
        t.act("say", text=line[:200])           # the server refuses lines that name a card still in hand


def play_turn(t: Table, dry: bool):
    t.act("begin")
    s = t.state()
    lands = [h["name"] for h in s.get("hand", []) if h["land"]]
    if lands and not s.get("land_played"):
        t.act("land", name=lands[0])
    for _ in range(4):                                     # main phase: up to four spells
        s = t.state()
        opts = []
        for h in s.get("hand", []):
            if h["land"] or not h["castable_now"]:
                continue
            if "{X}" in (h.get("cost") or "") and "Creature" in (h.get("type") or ""):
                continue                                   # X = 0 would be a 0/0 (Hooded Hydra: cast it face down)
            raw = next((x for x in s["hand"] if x["name"] == h["name"]), {})
            targets = (raw.get("aura_targets") or [])
            for tgt in (targets[:3] if targets else [None]):
                if tgt and tgt.startswith("#"):
                    ref = tgt.split()[0]
                    opts.append((f'cast "{h["name"]}" on {tgt}', {"action": "cast", "name": h["name"], "on": ref}))
                elif not targets:
                    opts.append((f'cast "{h["name"]}"', {"action": "cast", "name": h["name"]}))
        for h in s.get("hand", []):
            if h.get("face_down_castable"):
                opts.append((f'cast "{h["name"]}" FACE DOWN for {{3}} (turn up later: {h["face_up_cost"]})',
                             {"action": "cast", "name": h["name"], "face_down": True}))
        for pm in s.get("permanents", []):
            if pm.get("face_down") and pm.get("turn_up_cost"):
                opts.append((f'turn face-down #{pm["id"]} ({pm["name"]}) face up for {pm["turn_up_cost"]}',
                             {"action": "turn-up", "ref": f'#{pm["id"]}'}))
        cc = s.get("commander_card") or {}
        if s.get("commander_in_zone") and cc.get("castable_now"):
            opts.append((f'cast your commander {cc["name"]}', {"action": "cast", "name": "commander", "commander": True}))
        if not opts:
            break
        opts.append(("stop casting this turn", {"action": "stop"}))
        pick, line = choose(s, opts, "Which spell do you cast now?", dry)
        if pick["action"] == "stop":
            break
        body = {k: v for k, v in pick.items() if k != "action"}
        ok, _ = t.act(pick["action"], **body)
        say(t, line)
        if not ok:
            break
    s = t.state()
    ready = [p for p in s.get("permanents", []) if p.get("pt") and not p["tapped"] and not p["sick"]]
    opps = [n for n in (s.get("life_table") or {}) if n != t.seat and s["life_table"][n] > 0]
    attackers = []
    if ready and opps:
        opts = [("don't attack", {"action": "none"})]
        for o in opps:
            opts.append((f"attack {o} with everything ready ({', '.join(p['name'] + ' ' + p['pt'] for p in ready)})",
                         {"action": "attack", "who": o, "ids": [p["id"] for p in ready]}))
        pick, line = choose(s, opts, "Do you attack, and whom?", dry)
        if pick["action"] == "attack":
            ok, _ = t.act("attack", assign={f"#{i}": pick["who"] for i in pick["ids"]})
            say(t, line)
            attackers = [(f"#{i}", pick["who"]) for i in pick["ids"]] if ok else []
            if ok:
                kaust_flip(t, pick["ids"])
    if attackers:
        resolve_combat(t, attackers)
    t.act("end", text="That's my turn.")


def kaust_flip(t: Table, attacking_ids):
    """Kaust, Eyes of the Glade: {T}: turn target face-down ATTACKING creature you control face up.
    Code picks the face-down attacker whose real card is biggest (the model already chose to attack)."""
    s = t.state()
    kaust = next((p for p in s.get("permanents", []) if p["name"].startswith("Kaust") and not p["tapped"]
                  and not p.get("face_down")), None)
    downs = [p for p in s.get("permanents", []) if p.get("face_down") and p["id"] in attacking_ids]
    if not kaust or not downs:
        return
    best = max(downs, key=lambda p: p["id"])            # latest cast: usually the biggest threat
    t.act("tap", ref=f'#{kaust["id"]}', announce=True)
    t.act("turn-up", ref=f'#{best["id"]}', free=True)


def resolve_combat(t: Table, attackers, wait=25):
    """Wait for the defender to answer. On "no blocks" / "take it": resolve the on-damage triggers
    (Ellivere's draw, Pollenbright's Saprolings). The players say their own life (no_life)."""
    since = t.events("latest").get("last", 0)
    end = time.time() + wait
    while time.time() < end:
        d = t.events(since)
        for e in d.get("events", []):
            since = e["id"]
            if e["type"] != "heard":
                continue
            txt = e.get("text", "").lower()
            if re.search(r"\bno blocks?\b|\btakes? (it|that|the|\d+)|\bno block\b|\bunblocked\b", txt):
                t.act("damage", hits={ref: [who, None] for ref, who in attackers}, no_life=True)
                return
            if re.search(r"\bblocks?\b|\bblocking\b", txt):
                return                                   # blocked: the table resolves it; nothing hits
        time.sleep(1)


def on_attacked(t: Table, e: dict, dry: bool):
    s = t.state()
    amount = e.get("amount") or 0
    untapped = [p for p in s.get("permanents", []) if p.get("pt") and not p["tapped"]]
    opts = [(f"take {amount} (no block)", {"action": "block", "amount": amount})]
    for p in untapped:
        opts.append((f"block with #{p['id']} {p['name']} {p['pt']}",
                     {"action": "block", "blocker": f"#{p['id']}", "amount": amount}))
    pick, line = choose(s, opts, f"{e.get('attacker') or 'A creature'} attacks you for {amount}"
                        f"{' with trample' if e.get('trample') else ''}. Block or take it?", dry)
    t.act("block", blocker=pick.get("blocker"), amount=amount, trample=bool(e.get("trample")),
          attacker=e.get("attacker"))
    say(t, line)


def on_removal(t: Table, e: dict):
    if e.get("illegal"):
        say(t, e["illegal"])
        return
    verb = {"destroy": "destroy", "exile": "exile", "bounce": "bounce", "counter": "destroy"}.get(e.get("effect"))
    if verb and e.get("target") and e["target"] != "all":
        t.act(verb, ref=e["target"])
    else:
        say(t, "Noted.")


def on_priority(t: Table, e: dict, dry: bool):
    """NEXT opened a window in someone else's turn: cast an instant now, or pass."""
    s = t.state()
    instants = [h for h in s.get("hand", []) if h.get("castable_now") and "Instant" in (h.get("type") or "")]
    if not instants:                  # every AI seat gets every window (so a window says nothing about a hand);
        t.act("pass", quiet=True)     # with nothing castable there's nothing to ask the release
        return
    opts = [("pass — keep my mana", {"action": "pass"})]
    opts += [(f"cast {h['name']} ({h['cost']}): {(h.get('text') or '')[:120]}", {"action": "cast", "name": h["name"]})
             for h in instants]
    question = f"{e.get('text')} Only respond if it clearly helps you right now."
    local = None if dry else local_choice(s, opts, question)
    pick, line = (local[0], "") if local else choose(s, opts, question, dry)
    if pick.get("action") == "cast":
        t.act("cast", name=pick["name"])
        if line:
            say(t, line)
    else:
        t.act("pass", quiet=True)


def on_talk(t: Table, e: dict, dry: bool):
    s = t.state()
    _, line = choose(s, [("reply", {"action": "reply"})],
                     f'Someone at the table said to you: "{e.get("text")}". Reply in one short sentence '
                     "(never name a card in your hand).", dry)
    say(t, line or ("" if capped() else "Fair enough."))


MULL_MEMO = Path(__file__).parent / ".cache" / "fusion-mulligans.json"


def opening_hand(t: Table, dry: bool):
    """Keep or mulligan, before turn 1: keep 2-5 lands; otherwise mulligan (the first is free in
    Commander; after that one card goes to the bottom per extra mulligan — the priciest nonland). At most
    two mulligans. Each hand we've already decided on is remembered by a hash, so restarting this
    process never mulligans again. Only counts are logged: the hand is private."""
    import hashlib
    try:
        memo = set(json.loads(MULL_MEMO.read_text()))
    except (OSError, ValueError):
        memo = set()
    for n in range(3):
        s = t.state()
        if s.get("turn") or s.get("permanents"):
            return                                       # the game is under way: too late
        hand = s.get("hand") or []
        sig = hashlib.sha256("|".join(sorted(h["name"] for h in hand)).encode()).hexdigest()[:16]
        lands = sum(1 for h in hand if h.get("land"))
        if sig in memo or 2 <= lands <= 5 or n == 2:
            memo.add(sig)
            MULL_MEMO.write_text(json.dumps(sorted(memo)))
            print(f"  ✋ opening hand: keep ({lands} lands, after {n} mulligan(s))", flush=True)
            return
        memo.add(sig)
        MULL_MEMO.write_text(json.dumps(sorted(memo)))
        print(f"  ✋ opening hand: mulligan ({lands} lands)", flush=True)
        if dry:
            return
        t.act("mulligan")
        if n >= 1:                                       # the second mulligan costs a card: bottom the priciest
            s = t.state()
            nonland = [h for h in s.get("hand") or [] if not h.get("land")]
            if nonland:
                mv = lambda h: sum(int(x) if x.isdigit() else 1 for x in re.findall(r"\{([^}]+)\}", h.get("cost") or ""))
                t.act("bottom", name=max(nonland, key=mv)["name"], quiet=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seat", default="Fusion")
    ap.add_argument("--url", default=os.environ.get("TABLE_URL", "http://127.0.0.1:8800"))
    ap.add_argument("--token-file", default=os.environ.get("TABLE_TOKEN_FILE", str(HERE / ".brain-token")))
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if not a.dry and not RELEASE_ID:
        sys.exit("no Fusion release: set FUSION_RELEASE_ID or write table/.cache/fusion.json {\"release_id\": …}")
    if not a.dry and not os.environ.get("DIVINCI_FUSION_API_KEY"):
        sys.exit("DIVINCI_FUSION_API_KEY isn't set: run under infisical run (see the docstring), or --dry")
    t = Table(a.url, a.seat, Path(a.token_file))
    since = t.events("latest").get("last", 0)
    decks = ", ".join(f"{k.split(',')[0]}→{v[-6:]}" for k, v in (_CONF.get("releases") or {}).items()) or RELEASE_ID[-6:]
    print(f"Fusion brain on seat {a.seat} — releases by commander: {decks}{' (DRY)' if a.dry else ''}; "
          f"watching from #{since}", flush=True)
    try:
        opening_hand(t, a.dry)
    except Exception as ex:                                  # noqa: BLE001 — never block the seat on this
        print(f"  ! opening hand check failed: {type(ex).__name__}: {ex}", flush=True)
    held = False
    try:                                                  # started (or restarted) mid-turn: the "your turn" signal
        ph = t._req("GET", "/api/phase", brain=False)[1]   # went by before we were listening — play it now
        if (ph.get("player") or "").lower() == a.seat.lower() and not a.dry:
            print("  ↻ it's already my turn — playing it", flush=True)
            play_turn(t, a.dry)
    except Exception as ex:                               # noqa: BLE001
        print(f"  ! catch-up turn failed: {type(ex).__name__}: {ex}", flush=True)
    while True:
        try:
            d = t.events(since)
        except Exception as ex:                              # noqa: BLE001
            print(f"  ! table unreachable: {ex}", flush=True)
            time.sleep(3)
            continue
        if d.get("restarted"):
            since = 0
        for e in d.get("events", []):
            since = e["id"]
            if e["type"] != "attention" or (e.get("addressee") or "").lower() != a.seat.lower():
                continue
            k = e.get("kind")
            print(f"⚑ {k}: {e.get('text')}", flush=True)
            try:
                if k == "hold":
                    held = True
                elif k == "turn":
                    held = False
                    play_turn(t, a.dry)
                elif held:
                    continue
                elif k == "attacked":
                    on_attacked(t, e, a.dry)
                elif k == "removal":
                    on_removal(t, e)
                elif k == "priority":
                    on_priority(t, e, a.dry)
                elif k in ("question", "chatter", "deal", "play"):
                    on_talk(t, e, a.dry)
            except Exception as ex:                          # noqa: BLE001 — one bad decision never stops the seat
                print(f"  ! {k} failed: {type(ex).__name__}: {ex}", flush=True)
        time.sleep(1)


if __name__ == "__main__":
    main()
