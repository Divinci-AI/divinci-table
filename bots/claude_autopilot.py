"""Claude's seat on autopilot (retro G2-2). When it is Claude's turn and Claude itself has not acted for GRACE seconds after main 1
opens, this plays a simple turn: a land, then the most expensive spells it can afford (no instants: those stay for Claude), an attack
when it is safe, and End. Claude can still take over at any moment: any Claude action in the log resets the wait.

Uses ONLY Claude's own seat key (live/claude-seat.key), like claude_player.py: it sees Claude's hand, the public board and the log,
never another hand.

    python3 claude_autopilot.py              # runs for the whole game; one line per thing it does
    TABLE_URL=http://127.0.0.1:PORT CLAUDE_KEY_FILE=... AUTOPILOT_GRACE=2 python3 claude_autopilot.py   (tests)
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("TABLE_URL", "http://127.0.0.1:8800")
SEAT = os.environ.get("CLAUDE_SEAT", "Claude")
KEYFILE = Path(os.environ.get("CLAUDE_KEY_FILE") or Path(__file__).resolve().parent / "live" / "claude-seat.key")
GRACE = float(os.environ.get("AUTOPILOT_GRACE", "30"))
PACE = max(1.0, float(os.environ.get("AUTOPILOT_PACE", "1")))     # stretch the tick (2 = half the requests): for a table reached over the internet
KEY = KEYFILE.read_text().strip()


def call(method, path, body=None, retry=True):
    STATS["requests"] += 1
    # (a name of its own: a table behind Cloudflare refuses Python's default User-Agent with "error code: 1010", before the table sees it)
    h = {"Content-Type": "application/json", "X-Seat-Key": KEY, "User-Agent": "divinci-table-autopilot"}
    r = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=20) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read() or b"{}")
        except ValueError:                           # (a proxy's plain-text error page)
            d = {}
        if e.code == 429 and retry:              # the table asked us to slow down: wait as told, then try exactly once more
            time.sleep(min(5.0, float(d.get("retry_after") or 1.0)))
            return call(method, path, body, retry=False)
        return e.code, d
    except Exception as e:  # noqa: BLE001 — the table restarting: try again next tick
        return 0, {"error": str(e)}


REFUSED: set = set()     # (question, answer) pairs the table refused: never sent again (a refused answer used to be resent every tick, forever)


def _key(it: dict, target: dict) -> tuple:
    return (it.get("id"), it.get("source"), it.get("text"), json.dumps(target, sort_keys=True))


def answer_choices() -> int:
    """The table asks a driven seat what a trigger aims at, and waits. The bot ranks the options the table lists by what the slot does (a buff
    or an untap on my best, harm on their best, a sacrifice on my least valuable: slot_role), and the table's own line names the pick. A refused
    answer is never sent again: the next option is tried, then "no target" if the question allows it, and otherwise the bot waits (the
    question may change: a target can leave play). Older tables list nothing: nothing happens."""
    n = 0
    for _ in range(6):
        pend = (call("GET", f"/api/brain/state?seat={SEAT}")[1] or {}).get("pending_choices") or []
        if not pend:
            return n
        it = pend[0]
        used = [json.dumps(p, sort_keys=True) for p in it.get("picked") or [] if p]
        cands = [{k: v for k, v in o.items() if k != "label"} for o in ranked_options(it["slot"], it.get("text") or "", it)]
        cands = [t for t in cands if json.dumps(t, sort_keys=True) not in used]
        if it["slot"].get("optional") and slot_role(it["slot"], it.get("text") or "") == "sacrifice":
            lab = {json.dumps({k: v for k, v in o.items() if k != "label"}, sort_keys=True): (o.get("label") or "").lower() for o in it["slot"].get("options") or []}
            cands = [t for t in cands if "token" in lab.get(json.dumps(t, sort_keys=True), "")]    # v6: a "you may sacrifice": only a token is worth it
        if it["slot"].get("optional"):
            cands.append({})
        todo = [t for t in cands if _key(it, t) not in REFUSED]
        if not todo:
            if _key(it, None) not in REFUSED:          # said once per question, not every tick
                REFUSED.add(_key(it, None))
                print(f"  choose {it['source']}: every answer was refused; waiting for the question to change", flush=True)
            return n
        target = todo[0]
        c, d = call("POST", "/api/brain/choose", {"seat": SEAT, "id": it["id"], "target": target})
        print(f"  choose {it['source']} -> {c} {str(d.get('said') or d.get('error'))[:140]}", flush=True)
        if c != 200:
            REFUSED.add(_key(it, target))
            time.sleep(1.0)                   # never faster than the normal tick, even while working down a list of refusals
            continue
        n += 1
    return n


BASIC_LANDS = {"Plains", "Island", "Swamp", "Mountain", "Forest"}


def answer_looks() -> int:
    """A "surveil N" / "scry N" the table owes this seat (a Tricorder on attack, an enters trigger, He's Dead, Jim): the effect after it, and a
    person's block prompt, wait for the answer. Keep every card on top, except a basic land when I already have five lands out (it goes to the
    graveyard / the bottom). One answer per look; a refused answer is not sent again until the look changes."""
    st = call("GET", f"/api/brain/state?seat={SEAT}")[1] or {}
    done = 0
    for kind, key in (("surveil", "graveyard"), ("scry", "bottom")):
        cards = st.get(f"{kind}_cards") or []
        if not (st.get(f"pending_{kind}") and cards):
            continue
        names = [c.get("name") if isinstance(c, dict) else str(c) for c in cards]
        lands_out = sum(1 for p in st.get("permanents") or [] if "Land" in str(p.get("type") or ""))
        drop = [n for n in names if n in BASIC_LANDS and lands_out >= 5][:1]
        sig = (kind, tuple(names))
        if sig in REFUSED:
            continue
        c, d = call("POST", f"/api/brain/{kind}", {"seat": SEAT, key: drop})
        print(f"  {kind} {len(names)}: {'put ' + drop[0] + ' away' if drop else 'keep it all on top'} -> {c} {str(d.get('said') or d.get('error'))[:100]}", flush=True)
        if c == 200:
            done += 1
        else:
            REFUSED.add(sig)
    return done


def do(action, body=None, tries=40):
    """One action through my seat, retrying while the other player passes (the table answers 409 'waiting on passes')."""
    for _ in range(tries):
        c, d = call("POST", f"/api/brain/{action}", {"seat": SEAT, **(body or {})})
        if c == 409 and d.get("choose"):
            if not answer_choices():          # nothing the table would take: don't hammer it, and don't spin
                time.sleep(1.0)
                return c, d
            continue
        if c == 200 and (d.get("private") or {}).get("choices"):
            answer_choices()
        if c == 200 and answer_looks():
            pass
        if c == 409 and "waiting on passes" in str(d.get("error", "")):
            time.sleep(1.5)
            continue
        said = d.get("said") or d.get("error") or ""
        print(f"  {action} {json.dumps(body or {})[:80]} -> {c} {str(said)[:140]}", flush=True)
        return c, d
    return 599, {"error": "stuck waiting on passes"}


def cmc(cost: str) -> int:
    n = 0
    for sym in re.findall(r"\{([^}]+)\}", cost or ""):
        n += int(sym) if sym.isdigit() else (0 if sym == "X" else 1)
    return n


def power(pt):
    try:
        return int(str(pt).split("/")[0])
    except ValueError:
        return 0


def toughness(pt):
    try:
        return int(str(pt).split("/")[1])
    except (ValueError, IndexError):
        return 0


def station_ships():
    for _ in range(8):
        st = call("GET", f"/api/brain/state?seat={SEAT}")[1]
        perms = st.get("permanents") or []
        ships = sorted([p for p in perms if p.get("station_at") and int(p.get("charge") or 0) < int(p["station_at"]) and p.get("station_can") is not False],
                       key=lambda p: int(p["station_at"]) - int(p.get("charge") or 0))        # closest to done first
        crew = [p for p in perms if p.get("pt") and not p.get("tapped") and power(p["pt"]) > 0]
        if not ships or not crew:
            return
        ship = ships[0]
        need = int(ship["station_at"]) - int(ship.get("charge") or 0)
        # Game 6: stationing with two fresh tokens left no blockers and the seat took 13 in one swing. Station is a sorcery,
        # so the crew stays tapped through the opponent's turn: keep blockers home when their board threatens half our life,
        # unless this tap finishes a ship (which then blocks itself).
        threat = sum(power(c.get("pt") or "0/0") for o in (st.get("opponents") or {}).values() for c in o.get("creatures") or [])
        if threat * 2 >= int(st.get("life") or 0):
            crew = [c for c in crew if power(c["pt"]) >= need]
        sick = sorted([c for c in crew if c.get("sick") and c["id"] != ship["id"]], key=lambda c: -power(c["pt"]))
        finishers = sorted([c for c in crew if not c.get("sick") and c["id"] != ship["id"] and power(c["pt"]) >= need],
                           key=lambda c: power(c["pt"]))
        pick = (sick or finishers or [None])[0]
        if pick is None:
            return
        c, _ = do("station", {"ship": f"#{ship['id']}", "crew": f"#{pick['id']}"})
        if c != 200:
            return


FRIENDLY_LABELS = ("counter on", "give it that", "untap", " role on", "+1/+1 counter")
HOSTILE_LABELS = ("damage to", "destroy", "exile", "return", "tap and stun", "tap", "shrink", "what it fights")


def slot_role(sl: dict, text: str) -> str:
    """What a slot does to what it names, read from the table's own words for it (its label) and then the card's text:
    "sacrifice" (I give something of mine up: sacrifice, discard), "friendly" (a buff, counters, an untap, a Role, my fighter: it should be
    mine), or "hostile" (harm: theirs)."""
    label = (sl.get("label") or "").lower()
    t = (text or "").lower()
    if "sacrifice" in label or label.startswith("discard") or "from your hand" in label:
        return "sacrifice"
    if "fight" in label and sl.get("side") == "own":
        return "friendly"                     # my fighter: my best, so it wins
    if "give it that" in label:
        return "hostile" if " gets -" in t else "friendly"
    if any(w in label for w in FRIENDLY_LABELS):
        return "friendly"
    if any(w in label for w in HOSTILE_LABELS):
        return "hostile"
    if re.search(r"\+1/\+1 counter|\buntap\b|gets \+|\bgains\b", t):
        return "friendly"                     # a slot the label doesn't say (Pure Energy's "and the other on"): the text does
    return "friendly" if sl.get("side") == "own" else "hostile"


def _strength(o: dict) -> int:
    try:
        return int(o.get("label", "").rsplit(" ", 1)[-1].split("/")[0])
    except ValueError:
        return -1


def _value(o: dict) -> tuple:
    """How much I'd mind losing it: a token less than a card, a non-creature (land, Treasure) less than a creature, a small creature less than a big one."""
    lab = (o.get("label") or "").lower()
    pt = lab.rsplit(" ", 1)[-1]
    try:
        p_, t_ = (int(x) for x in pt.split("/"))
        body = p_ + t_
    except ValueError:
        body = 0
    return ("token" not in lab, body > 0, body)


def reveal_ranked(opts: list, reveal: dict) -> list:
    """v7: "look at target opponent's hand, choose a nonland card" (Stray from History, Salt Vampire, Zero): the options are the nonland cards of THEIR hand
    (only this seat's own question holds them). Take the best: the dearest by mana value, then the biggest power, then the name. A look with nothing to
    choose ("I have seen it") is just answered."""
    info = {c["name"]: c for c in (reveal or {}).get("cards") or []}

    def key(o):
        c = info.get((o.get("label") or "").split(" {")[0].split(" (")[0], {})
        try:
            pw = float(c.get("power"))
        except (TypeError, ValueError):
            pw = 0.0
        return (-float(c.get("mv") or 0), -pw, o.get("label") or "")
    return sorted(opts, key=key)


def ranked_options(sl: dict, text: str, it: dict | None = None) -> list:
    """The slot's options, best answer first: a buff or an untap on my strongest, harm on their strongest, a sacrifice on my least valuable.
    Only options on the right side are listed ("any target" lists both: harm is never aimed at my own)."""
    opts = sl.get("options") or []
    if it is not None and it.get("reveal") and sl.get("zone") == "option":
        return reveal_ranked(opts, it["reveal"])
    if sl.get("zone") == "option":            # v6: a choice that isn't a target (a mode, a colour, pay or not): the first the table can do itself
        return sorted(opts, key=lambda o: "(by hand)" in (o.get("label") or ""))
    if sl.get("players_only"):                # v6: "target player/opponent": harm and discard at them, a plain draw at me
        me_first = "draws" in (text or "").lower() and "discards" not in (text or "").lower() and any(o.get("player") == SEAT for o in opts)
        return sorted(opts, key=lambda o: (o.get("player") == SEAT) != me_first)
    role = slot_role(sl, text)
    mine = [o for o in opts if o.get("at") == SEAT]
    theirs = [o for o in opts if o.get("at") != SEAT]
    if role == "sacrifice":
        return sorted(mine or opts, key=_value)
    if role == "friendly":
        return sorted(mine, key=_strength, reverse=True)
    return sorted(theirs, key=_strength, reverse=True)


def aimed_at(card: dict):
    """What to aim this spell at, from the options the table lists (ranked_options: friendly on mine, harm on theirs, sacrifice my least
    valuable). Never the same thing twice. [] when it aims at nothing; None when a required target has no legal option."""
    out, used = [], []
    for sl in card.get("slots") or []:
        if sl.get("optional") and sl.get("side") == "own" and out:
            out.append({})                    # a second biter is a choice; the first will do
            continue
        pool = [o for o in ranked_options(sl, card.get("text") or "")
                if {k: v for k, v in o.items() if k != "label"} not in used]
        if not pool:
            if sl.get("optional"):
                out.append({})                # (Pure Energy's second counter: both go on the first)
                continue
            return None
        pick = {k: v for k, v in pool[0].items() if k != "label"}
        used.append(pick)
        out.append(pick)
    return out


def equip_gear():
    """Equipment on nothing goes on my strongest creature, while the mana lasts (the table checks the cost and the timing)."""
    for _ in range(4):
        st = call("GET", f"/api/brain/state?seat={SEAT}")[1]
        perms = st.get("permanents") or []
        gear = [p for p in perms if p.get("equip_cost") and p.get("attached_to") is None and p.get("equip_can") is not False]   # T18: the table's own check
        crew = sorted([p for p in perms if p.get("pt") and "Creature" in (p.get("type") or "")], key=lambda p: -power(p["pt"]))
        if not gear or not crew:
            return
        c, _ = do("equip", {"equipment": f"#{gear[0]['id']}", "creature": f"#{crew[0]['id']}"})
        if c != 200:
            return


def choose_attackers(ready: list, blockers: list, opp_life: int) -> list:
    """Which ready creatures attack. Game 4 stalled because the old rule (toughness above the biggest blocker's power) kept a 9/6
    home against a 7/6. Now, per attacker:
      - safe: no untapped blocker can kill it and live (a block that kills it costs them the blocker), or
      - lethal: everything swings when, even if each blocker stops our biggest attackers one-for-one, the rest is lethal.
    Chump blocks are the defender's loss, so they don't hold an attacker back."""
    def pw(p): return power(p["pt"])
    def tg(p): return toughness(p["pt"])
    if not ready:
        return []
    unblocked_floor = sum(sorted((pw(p) for p in ready), reverse=True)[len(blockers):])
    if opp_life > 0 and unblocked_floor >= opp_life:
        return ready
    out = []
    for a in ready:
        eaten = any(pw(b) >= tg(a) and tg(b) > pw(a) for b in blockers)   # a blocker kills it and survives
        if not eaten and pw(a) > 0:
            out.append(a)
    return out


EV = {"since": 0, "own": 0}     # event cursor: only NEW events are read (the whole log used to be re-read every tick)
STATS = {"requests": 0, "t0": time.time()}


def pump(wait: float = 0) -> None:
    """Read new events from the cursor; with `wait`, the table holds the request until something happens (up to 25 s), so an
    idle bot makes one request per wait instead of one per second."""
    while True:
        d = call("GET", f"/api/events?since={EV['since']}" + (f"&wait={wait:g}" if wait else ""))[1]
        if d.get("restarted"):
            EV.update(since=0, own=0)
            ATTN.clear(); ATTN_SEEN.clear(); APPLIED_OK.clear()   # a new table numbers its events from 1 again
            continue
        evs = d.get("events", [])
        for e in evs:
            EV["since"] = max(EV["since"], int(e["id"]))
            if e.get("type") == "attention" and str(e.get("addressee") or "").lower() == SEAT.lower() and int(e["id"]) not in ATTN_SEEN:
                ATTN.append(e)
            if e.get("type") == "say" and e.get("speaker") == SEAT and not str(e.get("text", "")).startswith(f"{SEAT}, turn"):
                EV["own"] = int(e["id"])
        if len(evs) < 300:
            return
        wait = 0                                  # a full page: read the rest straight away


ATTN: list = []           # attention events addressed to this seat that the bot has not looked at yet
ATTN_SEEN: set = set()    # their ids: each is handled once (a table restart re-reads the log from 0)
HOLD = {"until": 0.0}     # someone said "hold on, <seat>": don't start anything until then
APPLIES = ("destroy", "exile", "bounce", "wipe", "exile-all", "bounce-all")   # what the table's apply-removal can do
REMOVAL_WINDOW = 900      # the table applies a removal attention for 15 minutes, then refuses it


def handle_attentions() -> None:
    """Answer what the table asks of THIS seat in its log (S4, game 5: a destroy spell aimed at a bot waited for ever).
      removal   -> POST /api/brain/effect kind=apply-removal, when the effect is one the table applies, the target is legal (the
                   table's own `illegal` note is empty) and it is a permanent of mine (or `all`, a wipe). Anything else is said once
                   in the log and left to a person: the table can't do a 'damage'/'counter'/'shuffle'/'aura' removal for a seat.
      attacked  -> defend() at once (an attack declared through the board, at any step), which answers every incoming attacker.
      hold      -> no new turn actions for 20 s.
      effect    -> logged once ("draw yours", "that's yours to do"): a pilot has no action for those; a person does them.
      priority, turn, turn-cancelled -> nothing: the phase loop already passes / plays / ends on its own.
      question, chatter, play, deal  -> nothing: table talk is the banter's (Claude) or nobody's (Fusion)."""
    pending = []
    while ATTN:
        e = ATTN.pop(0)
        if int(e["id"]) in ATTN_SEEN:
            continue
        ATTN_SEEN.add(int(e["id"]))
        pending.append(e)
    removals = [e for e in pending if e.get("kind") == "removal"]
    for e in pending:
        kind = e.get("kind")
        if kind == "attacked":
            defend()
        elif kind == "hold":
            HOLD["until"] = max(HOLD["until"], float(e.get("ts") or time.time()) + 20)   # an old hold (read at start-up) is over
            if HOLD["until"] > time.time():
                print(f"  hold ({e.get('by') or 'someone'}): waiting up to 20 s", flush=True)
        elif kind == "effect":
            print(f"  note for {SEAT} (a person's to do): {str(e.get('text') or '')[:140]}", flush=True)
    for e in reversed(removals):              # newest first: the table's apply-removal takes the newest unapplied one
        apply_removal(e, removals)


def apply_removal(e: dict, batch: list) -> None:
    eff, tgt, spell = e.get("effect"), str(e.get("target") or ""), e.get("spell") or "the removal"
    what = f"removal {spell} ({eff}) on {tgt or '?'}"
    if time.time() - float(e.get("ts") or time.time()) > REMOVAL_WINDOW:
        print(f"  {what}: older than 15 minutes, the table won't take it", flush=True)
        return
    if e.get("illegal"):
        print(f"  {what}: not legal ({str(e['illegal'])[:100]}); not applied", flush=True)
        return
    if eff not in APPLIES:
        print(f"  {what}: the table can't apply '{eff}' for a seat; left for a person to do by hand", flush=True)
        return
    if tgt != "all":
        names = [p.get("name") for p in (call("GET", f"/api/brain/state?seat={SEAT}")[1] or {}).get("permanents") or []]
        if tgt not in names:
            print(f"  {what}: {tgt} isn't on my battlefield; not applied", flush=True)
            return
    # The table applies the NEWEST unapplied removal aimed at this seat, not a named one. If a newer one is pending that the bot did
    # not apply (illegal, or an effect the table can't do), this call would apply THAT instead: leave both to a person.
    newer = [x for x in batch if int(x["id"]) > int(e["id"]) and x not in APPLIED_OK]
    if newer:
        print(f"  {what}: a newer removal ({newer[-1].get('spell')}) is still unapplied; the table would apply that one instead, "
              f"so this is left for a person", flush=True)
        return
    c, d = call("POST", "/api/brain/effect", {"seat": SEAT, "kind": "apply-removal"})
    print(f"  apply-removal {spell} on {tgt} -> {c} {str(d.get('said') or d.get('error'))[:140]}", flush=True)
    if c == 200:
        APPLIED_OK.append(e)


APPLIED_OK: list = []


def opening_hand(ph: dict) -> bool:
    """T15: the table asks every seat to keep or mulligan before the first turn. The bot follows the table's own suggestion (the same rule an
    engine seat uses): mulligan while it says "mulligan" and the kept hand would still be five or more, otherwise keep, putting on the bottom
    the cards the table's rule picks. One answer per tick; an older table has no such step and nothing happens. True when it answered."""
    m = ph.get("mulligan") or {}
    if not m.get("on") or SEAT not in (m.get("waiting") or []):
        return False
    c, la = call("GET", f"/api/seat/legal?seat={SEAT}")
    mine = (la or {}).get("mulligan") or {}
    acts = {a["action"]: a for a in (la or {}).get("actions") or []}
    if c != 200 or mine.get("kept") or "keep" not in acts:
        return False
    again = (mine.get("advice") or {}).get("verdict") == "mulligan" and int(mine.get("keeps") or 0) - 1 >= 5 and "mulligan" in acts
    a = acts["mulligan" if again else "keep"]
    c, d = call("POST", f"/api/brain/{a['action']}", a["body"])
    print(f"  opening hand: {a['action']} -> {c} {str(d.get('said') or d.get('error'))[:120]}", flush=True)
    if c != 200 and again:                     # refused: keep rather than hold the start
        c, d = call("POST", "/api/brain/keep", acts["keep"]["body"])
        print(f"  opening hand: keep -> {c} {str(d.get('said') or d.get('error'))[:120]}", flush=True)
    return c == 200


def respond_to_held(ph: dict) -> bool:
    """v6: an opponent's spell is held for this seat's answer (it holds a counterspell it could cast). Counter a spell of mana value 3 or more
    with the first counter the table lists; otherwise pass at once, so the game never waits out the clock for a bot. True when it answered."""
    h = ph.get("held") or {}
    if SEAT not in (h.get("waiting") or []):
        return False
    c, la = call("GET", f"/api/seat/legal?seat={SEAT}")
    counters = [a for a in (la or {}).get("actions") or [] if a["action"] == "cast"
                and any(isinstance(t, dict) and t.get("card") == h.get("spell") and t.get("at") == h.get("by") for t in a["body"].get("targets") or [])]
    if counters and float(h.get("mv") or 0) >= 3:
        c, d = call("POST", "/api/brain/cast", counters[0]["body"])
        print(f"  respond: counter {h.get('spell')} -> {c} {str(d.get('said') or d.get('error'))[:140]}", flush=True)
        if c == 200:
            answer_choices()
            return True
    c, d = call("POST", "/api/brain/pass", {"seat": SEAT, "player": ph.get("player"), "step": ph.get("step")})
    print(f"  respond: pass on {h.get('spell')} -> {c}", flush=True)
    return c == 200


def last_claude_event_id() -> int:
    """The id of this seat's latest say in the log (cast, land, attack…): acting by hand resets the grace wait."""
    pump(0)
    return EV["own"]


def play_turn():
    st = call("GET", f"/api/brain/state?seat={SEAT}")[1]
    print(f"autopilot: {SEAT} life {st.get('life')}, hand {len(st.get('hand') or [])}", flush=True)
    # 1. a land: the basic whose colour the hand needs most
    if not st.get("land_played"):
        lands = [h for h in st.get("hand") or [] if h.get("land")]
        xlands = [h for h in st.get("exile_playable") or [] if h.get("land")]   # a land it may play from exile: it won't wait, the hand's will
        if xlands:
            do("land", {"name": xlands[0]["name"], "from_exile": True})
        elif lands:
            need = "".join(h.get("cost") or "" for h in st.get("hand") or [] if not h.get("land"))
            want = {"Swamp": need.count("{B}"), "Mountain": need.count("{R}"), "Plains": need.count("{W}"),
                    "Island": need.count("{U}"), "Forest": need.count("{G}")}
            lands.sort(key=lambda h: -want.get(h["name"], 0))
            do("land", {"name": lands[0]["name"]})
    # 2. spells, most expensive first, while any is castable (instants stay in hand: Claude decides those)
    refused = set()
    for _ in range(6):
        st = call("GET", f"/api/brain/state?seat={SEAT}")[1]
        # the cards it may play from exile are cast like the hand's, and first: their window closes (older tables send none: nothing changes)
        cands = [{**h, "_x": True} for h in st.get("exile_playable") or [] if h.get("castable_now") and not h.get("land") and "Instant" not in (h.get("type") or "")
                 and "Aura" not in (h.get("type") or "") and ("x", h["name"]) not in refused] + \
                [h for h in st.get("hand") or [] if h.get("castable_now") and not h.get("land") and "Instant" not in (h.get("type") or "")
                 and "Aura" not in (h.get("type") or "") and h["name"] not in refused]
        if not cands:
            break
        if any(p.get("pt") for p in st.get("permanents") or []):     # v6: the table now lists a wipe as castable (it is): the bot keeps it while it has creatures
            cands = [h for h in cands if not h.get("wipe")]
        if not cands:
            break
        cands.sort(key=lambda h: (not h.get("_x"), -cmc(h.get("cost") or "")))
        body = {"name": cands[0]["name"], **({"from_exile": True} if cands[0].get("_x") else {})}
        aim = aimed_at(cands[0])              # the table now asks what a spell aims at (older tables send no slots: nothing changes)
        key_ = ("x", cands[0]["name"]) if cands[0].get("_x") else cands[0]["name"]
        if aim is None:
            refused.add(key_)                 # nothing legal to aim it at: keep it, try the next
            continue
        if aim:
            body["targets"] = aim
        c, _ = do("cast", body)
        if c != 200:
            refused.add(key_)                 # one refusal no longer stops the rest of the hand
    st = call("GET", f"/api/brain/state?seat={SEAT}")[1]
    if not st.get("land_played"):             # a spell above may have exiled a land it may play
        xlands = [h for h in st.get("exile_playable") or [] if h.get("land")]
        if xlands:
            do("land", {"name": xlands[0]["name"], "from_exile": True})
    # 3. station (G3-9): Spacecraft only fight once they have enough charge counters. Crew with creatures that can't attack this
    #    turn anyway (summoning sick); spend a ready creature only when that finishes a ship.
    station_ships()
    equip_gear()
    # 4. attack: every ready creature that the other side's untapped creatures cannot kill without losing theirs first
    b3 = call("GET", "/api/board3d")[1]
    teams = call("GET", "/api/phase")[1].get("teams") or {}
    mine = {SEAT} | {n for n, t in teams.items() if teams.get(SEAT) and t == teams.get(SEAT)}   # 2HG: never attack a teammate
    opp = next((s for s in b3.get("seats", []) if s.get("name") not in mine and (s.get("life") or 0) > 0), None)
    st = call("GET", f"/api/brain/state?seat={SEAT}")[1]
    if opp:
        blockers = [p for p in opp.get("permanents", []) if p.get("pt") and not p.get("tapped")]
        ready = [p for p in st.get("permanents") or [] if p.get("pt") and not p.get("tapped") and not p.get("sick")]
        if any("can_attack" in p for p in st.get("permanents") or []):   # T16: the table says which creatures may attack (an older table doesn't)
            ready = [p for p in st.get("permanents") or [] if p.get("pt") and p.get("can_attack")]
        go = choose_attackers(ready, blockers, int(opp.get("life") or 0))
        if len(go) == 1 and go[0].get("attack_needs_partner") and not st.get("attacked_ids"):
            go = []                           # it can't attack alone, and nothing else is going with it
        if go:
            do("attack", {"assign": {f"#{p['id']}": opp["name"] for p in go}})
            # The defender gets to block: wait until it has answered every attacker (the table moves to combat damage), or 20s.
            # v6c: the table moves to declare blockers when the blocks are in; `damage` there is this seat's pass, and the table deals
            # the damage step once the defender has passed too (do() retries while it says "waiting on passes").
            end_at = time.time() + 20
            while time.time() < end_at:
                ph_ = call("GET", "/api/phase")[1]
                mine_ = next((a for a in (ph_.get("combat") or {}).get("attacks") or [] if a.get("attacker") == SEAT), None)
                if ph_.get("step") == "combat damage" or ph_.get("game_over") or (mine_ and mine_.get("stage") in ("window", "done")):
                    break
                time.sleep(1.0)
            do("damage", {})
    # 4. end
    do("end", {"text": "That's my turn."})


def my_turn(ph) -> bool:
    """This seat's turn, or (Two-Headed Giant) its team's shared turn."""
    t = ph.get("teams") or {}
    return ph.get("player") == SEAT or bool(t.get(SEAT) and t.get(SEAT) == t.get(ph.get("player")))


def plan_blocks(incoming: list, mine: list, opp_pt: dict, life: int) -> dict:
    """Which of my untapped creatures blocks which attacker: {attacker ref: creature}. A creature blocks one attacker; the biggest attacker is
    answered first. Block when a creature wins the fight or trades evenly, chump only to survive, otherwise take it. With `can_block` on an
    attacker (a v6c table), only the creatures the table lets block it (flying, "can't block this turn") are considered."""
    out, mine = {}, list(mine)
    total = sum(int(e.get("damage") or e.get("power") or 0) for e in incoming)
    for e in sorted(incoming, key=lambda e: -int(e.get("power") or 0)):
        ap = int(e.get("power") or 0)
        at = toughness(opp_pt.get(e["creature"], "0/1"))
        able = [p for p in mine if p["id"] in e["can_block"]] if isinstance(e.get("can_block"), list) else mine
        if e.get("menace"):                              # two or more, or none: the bot takes it (a double block is Claude's call)
            able = []
        win = [p for p in able if power(p["pt"]) >= at and toughness(p["pt"]) > ap]
        trade = [p for p in able if power(p["pt"]) >= at and power(p["pt"]) <= ap]
        chump = sorted(able, key=lambda p: power(p["pt"]) + toughness(p["pt"])) if total >= life else []
        pick = (sorted(win, key=lambda p: power(p["pt"]) + toughness(p["pt"])) or trade or chump or [None])[0]
        if pick:
            out[e.get("ref") or e["creature"]] = pick
            mine.remove(pick)
            total -= ap
    return out


def defend():
    """Someone is attacking this seat: answer every attacker. On a v6c table (its state has `combat`) that is ONE declaration for the whole
    attack: {"blocks": {attacker: [blocker]}}, or {"none": true}; nothing is dealt until the damage step. An older table takes one answer per
    attacker, each resolving at once. Either way no attacker is left hanging: a refused declaration is answered again with no blocks."""
    st = call("GET", f"/api/brain/state?seat={SEAT}")[1]
    incoming = st.get("incoming") or []
    if not incoming:
        return
    opp_pt = {}
    for o in (st.get("opponents") or {}).values():
        for c in o.get("creatures") or []:
            opp_pt.setdefault(c["name"], c["pt"])
    mine = [p for p in st.get("permanents") or [] if p.get("pt") and not p.get("tapped")]
    life = int(st.get("life") or 0)
    plan = plan_blocks(incoming, mine, opp_pt, life)
    if "combat" in st:                                   # v6c: one answer for all of them
        body = {"blocks": {ref: [f"#{p['id']}"] for ref, p in plan.items()}} if plan else {"none": True}
        c, d = call("POST", "/api/brain/block", {"seat": SEAT, **body})
        if c != 200 and plan:                            # refused (the table knows a rule the bot doesn't): take it, never leave it hanging
            print(f"  blocks refused ({str(d.get('error'))[:100]}): no blocks instead", flush=True)
            plan = {}
            c, d = call("POST", "/api/brain/block", {"seat": SEAT, "none": True})
        what = "; ".join(f"{p['name']} on {ref}" for ref, p in plan.items()) or "no blocks"
        print(f"  declare blocks for {len(incoming)} attacker(s): {what} -> {c} {str(d.get('said') or d.get('error'))[:160]}", flush=True)
        return
    for e in sorted(incoming, key=lambda e: -int(e.get("power") or 0)):
        ap = int(e.get("power") or 0)
        pick = plan.get(e.get("ref") or e["creature"])
        body = {"attacker": e.get("ref") or e["creature"]}
        if pick:
            body["blocker"] = f"#{pick['id']}"
        c, d = call("POST", "/api/brain/block", {"seat": SEAT, **body})
        if c != 200 and body.get("blocker"):            # refused (e.g. it flies and ours can't): answer it unblocked, never leave it hanging
            pick = None; body.pop("blocker")
            c, d = call("POST", "/api/brain/block", {"seat": SEAT, **body})
        print(f"  block {e['creature']} ({ap}) with {pick['name'] if pick else 'nothing'} -> {c} {str(d.get('said') or d.get('error'))[:120]}", flush=True)


MATCH_SEEN = {"said": None}


def between_games(ph: dict) -> str:
    """A game has ended. In a match (the table was started with --match bo3: best two out of three on ONE table) that is not the end: the next
    game starts on the same table with the same seat key, so this bot stays. Returns:
        "stay"  another game of the match is coming: the caller waits (and, as the loser, this seat has chosen to play first)
        "over"  the match is over, or this table plays single games (an older table has no /api/match: the same answer): the caller stops
    The table says it in /api/phase too (`match.continues`), so a table that is slow to answer /api/match is not mistaken for a finished one."""
    brief = ph.get("match") or {}
    c, m = call("GET", "/api/match")
    if c != 200 or not isinstance(m, dict) or not m.get("on"):
        return "stay" if brief.get("continues") else "over"
    if m.get("over"):
        print(f"autopilot: the match is over ({m.get('score')})", flush=True)
        return "over"
    nx = m.get("next") or {}
    if nx and MATCH_SEEN["said"] != nx.get("n"):
        MATCH_SEEN["said"] = nx.get("n")
        print(f"autopilot: game over, the match continues ({m.get('score')}); game {nx.get('n')} starts on this table", flush=True)
    if nx and nx.get("chooser") == SEAT and not nx.get("choice"):       # the loser chooses: an AI seat plays first
        c2, d2 = call("POST", "/api/match/next", {"seat": SEAT, "action": "play"})
        print(f"  the choice is mine (I lost the last game): play first -> {c2} {str(d2.get('error') or '')[:100]}", flush=True)
    return "stay"


def main():
    print(f"autopilot for {SEAT} on {BASE}, grace {GRACE:.0f}s", flush=True)
    wait_from, seen, finishing, acted, loops = None, 0, False, False, 0
    acted_turn = None
    stuck = False
    game_id = None
    while True:
        c, ph = call("GET", "/api/phase")
        if c != 200:
            time.sleep(3)
            continue
        if ph.get("game_id") != game_id:             # a new game on this table (a match's next game can begin between two of this loop's
            if game_id is not None:                  # ticks, so the game-over branch below may never have been seen): nothing carries over
                print(f"autopilot: a new game on this table ({ph.get('game_id')})", flush=True)
                wait_from, seen, finishing, acted, acted_turn, stuck = None, 0, False, False, None, False
                REFUSED.clear()
                HOLD["until"] = 0.0
            game_id = ph.get("game_id")
        if opening_hand(ph):                         # T15: keep or mulligan before the first turn; again next tick until kept
            time.sleep(0.5)
            continue
        if respond_to_held(ph):                      # v6: a spell waits on this seat's response: counter it or pass, at once
            continue
        answer_choices()                             # a trigger can be waiting on this seat on anyone's turn (a death in combat)
        answer_looks()                               # v6e: a surveil/scry the table owes this seat (Fusion sat on a Tricorder's surveil 2026-11-10)
        handle_attentions()                          # removal aimed at this seat, an attack declared at it, a hold (S4)
        here = (ph.get("player"), ph.get("step"))
        loops += 1                                   # the phase reply says game_over itself on a bot-only table; the life read
        life = (call("GET", "/api/life")[1] or {}) if loops % 3 == 0 else {}   # is the fallback for tables with people (every 3rd loop)
        if any(isinstance(v, int) and v <= 0 for v in life.values()) or ph.get("game_over") or ph.get("winner"):
            if between_games(ph) == "stay":              # a match: wait here for the next game, and start it with nothing left from this one
                wait_from, seen, finishing, acted, acted_turn, stuck = None, 0, False, False, None, False
                REFUSED.clear()
                HOLD["until"] = 0.0
                time.sleep(1.0 * PACE)
                continue
            print(f"autopilot: game over ({life}), stopping", flush=True)
            return
        if not my_turn(ph):
            finishing = False
            # The bot never casts instants, so when the round waits on this seat, it passes at once. Without this a seat that
            # holds an instant (the server doesn't auto-pass for it) made every step sit out the 30 s timeout (4-bot 2HG game 3).
            if (ph.get("passes") or {}).get("next") == SEAT and ph.get("step") not in ("declare attackers", "declare blockers"):
                call("POST", "/api/brain/pass", {"seat": SEAT, "player": ph.get("player"), "step": ph.get("step")})
            if ph.get("begun") and ph.get("step") in ("declare attackers", "declare blockers"):
                defend()
                # v6c: after blocks there is a window before damage, and the table never passes for a seat that holds something it could
                # cast there. This seat keeps its instants for Claude, so once nothing is left to answer it passes at once.
                if "combat" in ph and (ph.get("passes") or {}).get("next") == SEAT:
                    st_ = call("GET", f"/api/brain/state?seat={SEAT}")[1] or {}
                    if not st_.get("incoming") and not st_.get("pending_choices"):
                        call("POST", "/api/brain/pass", {"seat": SEAT, "player": ph.get("player"), "step": ph.get("step")})
        # v6d: a whole opposing turn can pass between two of this loop's ticks (the turns got faster), so `acted` was never reset and the bot
        # sat in its next main 1 for ever (1 bot game in ~9). The seat's own turn counter says whether this is a new turn of ours.
        if acted and my_turn(ph) and ph.get("begun") and acted_turn is not None:
            tn_ = (call("GET", f"/api/brain/state?seat={SEAT}")[1] or {}).get("turn")
            if tn_ is not None and tn_ != acted_turn:
                print(f"  new turn ({tn_}): playing it (the last one was {acted_turn})", flush=True)
                acted, finishing, stuck, wait_from = False, False, False, None
        # End can advance one step per call: keep pressing it until the turn passes, but never into our NEXT turn
        # (a new turn starts at untap, index 0; game 3's bot ended 150 turns in a row against an empty seat).
        # ...unless the turn's first End was refused while another seat still owed a choice (a discard, a target): then we are
        # still in main 1 of the SAME turn and must keep pressing End, or the table sits there for good (1v1 round 1, 2026-11-09).
        if finishing and (not my_turn(ph) or (int(ph.get("index") or 0) <= 3 and not stuck)):
            finishing = False
        if not my_turn(ph):
            stuck = False
        if finishing and my_turn(ph):
            c2, d2 = call("POST", "/api/brain/end", {"seat": SEAT})
            if c2 == 200:
                print(f"  end (continuing) -> {ph.get('step')}", flush=True)
                stuck = False
            time.sleep(2.0)
            continue
        if not my_turn(ph):
            acted = False
        # main 1 normally; main 2 too, for a 2HG teammate whose partner moved the shared turn on before this seat acted
        if my_turn(ph) and ph.get("begun") and not acted and (ph.get("step") == "main 1" or
                                                              (ph.get("teams") and ph.get("step") == "main 2")):
            if wait_from is None:
                wait_from, seen = time.time(), last_claude_event_id()
            now_seen = last_claude_event_id()
            if now_seen != seen:                         # Claude is playing by hand: keep out of its way
                wait_from, seen = time.time(), now_seen
            elif time.time() - wait_from >= GRACE and time.time() >= HOLD["until"]:
                play_turn()
                wait_from, finishing, acted = None, True, True   # keep ending until the other player's turn starts
                acted_turn = (call("GET", f"/api/brain/state?seat={SEAT}")[1] or {}).get("turn")
                ph2 = call("GET", "/api/phase")[1] or {}
                stuck = my_turn(ph2) and ph2.get("step") == "main 1"
        else:
            wait_from = None
        # Never faster than the old cadence (1 s on our turn, 2 s otherwise): events stream during the other player's turn,
        # and waking on each one tripled the requests. The long wait only stretches the QUIET stretches (up to 6 s).
        floor = (1.0 if my_turn(ph) else 2.0) * PACE
        t_ = time.time()
        pump(floor if my_turn(ph) else 6.0)
        if time.time() - t_ < floor:
            time.sleep(floor - (time.time() - t_))
        if os.environ.get("AUTOPILOT_STATS") and time.time() - STATS["t0"] >= 60:
            print(f"stats: {STATS['requests']} requests in {time.time() - STATS['t0']:.0f}s", flush=True)
            STATS.update(requests=0, t0=time.time())


if __name__ == "__main__":
    main()
