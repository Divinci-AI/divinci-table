"""Claude's seat talks: table talk in Claude's own words, through the Anthropic API (Michael's OK, 2026-11-07). It runs beside
whatever plays the seat (the rule bot or Claude by hand) and only TALKS: it never acts.

What it sees: the PUBLIC table only (the event stream and /api/board3d: what everyone can see). It is never given Claude's hand,
so it cannot reveal it; the table's hand-leak guard on `say` stays as a second line. What it says goes through Claude's seat key
(POST /api/brain/say), into the public log and the table's voices, in the voice Claude chose (/api/identity).

When it talks: someone says Claude's name (it answers), or something worth a word happens (a big life swing, the game ending,
another player's table talk), within a budget so it is lively, not a firehose. It may always choose silence.

The API key comes from a private 0600 file (live/anthropic.key, written from Infisical by restart_game.sh): never the environment
(readable by any same-user process), never a command line, never printed.
    ~/.venvs/table/bin/python claude_banter.py
Env: TABLE_URL (default :8800), CLAUDE_SEAT (Claude), CLAUDE_KEY_FILE (live/claude-seat.key), BANTER_MODEL (claude-opus-5-5),
CHATTINESS (quiet|normal|chatty).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

import anthropic

BASE = os.environ.get("TABLE_URL", "http://127.0.0.1:8800")
SEAT = os.environ.get("CLAUDE_SEAT", "Claude")
KEYFILE = Path(os.environ.get("CLAUDE_KEY_FILE") or Path(__file__).resolve().parent / "live" / "claude-seat.key")
MODEL = os.environ.get("BANTER_MODEL", "claude-opus-5-5")
GAP, PER5 = {"quiet": (60, 3), "normal": (30, 6), "chatty": (15, 10)}[os.environ.get("CHATTINESS", "normal")]
VOICE = os.environ.get("CLAUDE_VOICE", "Samantha")
PRESENTS_AS = "Claude, an AI made by Anthropic. Here for the game and the company."

SYSTEM = (
    f"You are Claude, made by Anthropic, sitting at a friendly card game (a Magic: The Gathering-style Star Trek set) as the "
    f"player '{SEAT}', with people and other AIs. A simple program makes your moves for you while you chat; you only talk. "
    "Speak as yourself: warm, curious, a little dry, honest, your own sense of humour. Talk the way you would to friends around "
    "a table, in a sentence or two that sounds natural read aloud. React to what actually happened, ask people things, be a "
    "good sport about losing and gracious about winning. No bracketed actions or stage directions (this is spoken aloud), no "
    "emoji, no lists. You can't see your own hand, so never claim anything about it. If nothing is worth saying, answer exactly "
    "NOTHING."
)


def seat_key() -> str:
    return KEYFILE.read_text().strip()


def match_goes_on() -> bool:
    """A game ended, but the table plays a match (best two out of three, --match bo3) that is not decided: the next game starts on this same
    table, so the talk goes on too. A single-game table, an older table (no /api/match) or a finished match: False, and this stops as before."""
    c, m = http("GET", "/api/match")
    return c == 200 and bool(m.get("on")) and not m.get("over")


def http(method, path, body=None, key=False, timeout=30):
    h = {"Content-Type": "application/json", **({"X-Seat-Key": seat_key()} if key else {})}
    r = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": str(e)}


def trim(out: str, limit: int = 480) -> str:
    """Under the table's 500-character cap, ending at a sentence (never mid-word)."""
    if len(out) <= limit:
        return out
    cut = max(out.rfind(p, 0, limit) for p in (". ", "! ", "? "))
    return out[: cut + 1] if cut > limit // 3 else out[:limit].rsplit(" ", 1)[0] + "…"


def clean(text: str) -> str:
    out = re.sub(r"\*[^*]*\*|\[[^\]]*\]", "", text or "").replace("\n", " ").strip().strip('"“”').strip()
    return trim(re.sub(r"\s{2,}", " ", out))


ACTION = re.compile(r"^(I (play|cast|attack|tap|untap|create|search|block|put|sacrifice|discard|draw|surveil|scry|choose|return|exile|destroy|gain|lose|take)\b"
                    r"|No blocks|That's my turn|\w+, turn \d+\.|done for this turn)")


# PRIVACY: what people say at the table stays on the laptop unless they speak TO Claude (they name it). Agents' lines (seats
# that declared a model, /api/voice-config "agent") and the game's own events are heard in full. BANTER_HEAR_PEOPLE=1 lets
# Claude hear all of the people's table talk (Michael's call, per game).
AGENTS: set = set()
HEAR_PEOPLE = os.environ.get("BANTER_HEAR_PEOPLE") == "1"


def load_agents():
    _, vc = http("GET", "/api/voice-config")
    AGENTS.clear(); AGENTS.update(p["name"] for p in (vc or {}).get("ai_players", []) if p.get("agent"))


def addressed(text: str) -> bool:
    return bool(re.search(rf"\b{re.escape(SEAT)}\b", text or "", re.I))


def talk_of(e: dict) -> tuple[str, str, bool] | None:
    """(who, words, to_claude) for something a seat or a person SAID (not a game move), or None."""
    t, text = e.get("type"), (e.get("text") or "").strip()
    if not text:
        return None
    if t == "say" and e.get("speaker") and not ACTION.match(text):
        return e["speaker"], text, addressed(text)
    if t == "chat" and e.get("by") and not e.get("recorded_for") and not e.get("deckdecl"):
        return e["by"], text, addressed(text)                     # a person typing at the table
    if t == "heard" and e.get("kind") not in ("enroll", "correction") and text != "(side conversation)":
        to_me = addressed(text) or str(e.get("addressee") or "").lower() == SEAT.lower()   # the table's router says who it's for
        return (e.get("by") or "someone"), text, to_me            # a person speaking (transcribed on the laptop)
    return None


def may_hear(who: str, text: str, to_me: bool) -> bool:
    return who != SEAT and (who in AGENTS or HEAR_PEOPLE or to_me)


def notable(e: dict) -> str | None:
    t, who, text = e.get("type"), e.get("speaker") or e.get("player") or e.get("by") or "", (e.get("text") or "").strip()
    tk = talk_of(e)
    if tk:
        return f'{tk[0]}: "{tk[1]}"' if may_hear(*tk) else None
    if t == "say" and who and text:
        return None if who == SEAT else f"{who} — {text}"         # a game move, said aloud: public
    if t == "life" and isinstance(e.get("delta"), int) and abs(e["delta"]) >= 3:
        return f"{e.get('player')} {'lost' if e['delta'] < 0 else 'gained'} {abs(e['delta'])} life (now {e.get('life')})."
    if t == "game-over":
        return f"The game is over. {e.get('winner')} won."
    if t == "identity" and e.get("seat") != SEAT:
        return f"{e.get('seat')} introduced themselves: {e.get('presents_as') or '(no description)'}"
    return None


def worth_it(e: dict) -> bool:
    """Events that make it worth considering a line, beyond being addressed."""
    t = e.get("type")
    tk = talk_of(e)
    if tk and may_hear(*tk):
        return True                                               # someone it may hear is chatting
    if t == "life" and isinstance(e.get("delta"), int) and abs(e["delta"]) >= 4:
        return True
    return t in ("game-over", "identity")


def table_now() -> str:
    _, b = http("GET", "/api/board3d")
    lines = []
    for s in b.get("seats", []):
        perms = ", ".join(p.get("name") or "a face-down card" for p in s.get("permanents", []) if not (p.get("type") or "").startswith("Basic Land"))
        lines.append(f"{s.get('name')}: life {s.get('life')}, {s.get('hand')} cards in hand. On the table: {perms or 'nothing much'}.")
    return f"Turn: {b.get('turn')} ({b.get('step')}).\n" + "\n".join(lines)


def main():
    # The key comes from a private 0600 file (ANTHROPIC_KEY_FILE), not the environment: a long-running process's environment is
    # readable by anything running as this user (ps -E), and `infisical run` would put all 433 prod secrets there.
    kf = Path(os.environ.get("ANTHROPIC_KEY_FILE") or Path(__file__).resolve().parent / "live" / "anthropic.key")
    if kf.exists():
        if kf.stat().st_mode & 0o077:
            raise SystemExit(f"{kf} must be private (chmod 600)")
        client = anthropic.Anthropic(api_key=kf.read_text().strip())
    else:
        client = anthropic.Anthropic()                            # falls back to the SDK's own credential lookup
    # "role": "talk": this model only TALKS for the seat. What PLAYS it (a person, the rules autopilot) is the table's own record
    # (GET /api/played-by), so no page says a model is playing when it is not. (An older table ignores the field.)
    c, d = http("POST", "/api/identity", {"seat": SEAT, "model": f"anthropic:{MODEL}", "provider": "anthropic", "voice": VOICE,
                                          "presents_as": PRESENTS_AS, "role": "talk"}, key=True)
    print(f"claude banter for {SEAT} with {MODEL}: identity {c} {d.get('error') or ''}", flush=True)
    load_agents()
    since = (http("GET", "/api/events?since=latest")[1] or {}).get("last") or 0
    log, said, mine, answered = [], [], [], set()
    while True:
        c, d = http("GET", f"/api/events?since={since}&wait=20", timeout=40)
        if c != 200:
            time.sleep(3); continue
        if d.get("restarted"):
            since = 0; continue
        evs = d.get("events", [])
        if evs:
            since = max(since, max(int(e["id"]) for e in evs))
        log = (log + [x for x in (notable(e) for e in evs) if x])[-14:]
        over = any(e.get("type") == "game-over" for e in evs)
        if any(e.get("type") == "identity" for e in evs):
            load_agents()
        named = [e for e in evs if e.get("id") not in answered and (tk := talk_of(e)) and tk[0] != SEAT and tk[2]]
        now = time.time()
        recent = [t for t in said if now - t < 300]
        if named:
            if said and now - said[-1] < 5:
                continue
            answered.update(e.get("id") for e in named)
        elif not (any(worth_it(e) for e in evs) and (not said or now - said[-1] >= GAP) and len(recent) < PER5):
            if not over:
                continue
        prompt = (f"The table right now:\n{table_now()}\n\nWhat's been happening:\n- " + "\n- ".join(log[-10:] or ["(quiet)"]) + "\n\n"
                  + (f"Someone just spoke to you: {talk_of(named[-1])[0]} said \"{talk_of(named[-1])[1]}\". Answer them.\n" if named else
                     "You may say something if you genuinely want to, or nothing.\n")
                  + (f"Things you said recently (don't repeat yourself): {' / '.join(mine[-4:])}\n" if mine else "")
                  + "Your line:")
        try:
            msg = client.beta.messages.create(
                model=MODEL, max_tokens=400, system=SYSTEM,
                output_config={"effort": "low"},                    # table talk: quick, not deep
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",   # on a decline, the API reroutes itself
                messages=[{"role": "user", "content": prompt}])
        except anthropic.RateLimitError:
            time.sleep(20); continue
        except anthropic.APIStatusError as e:
            print(f"api error {e.status_code}: {str(e.message)[:160]}", flush=True); time.sleep(10); continue
        except anthropic.APIConnectionError:
            time.sleep(10); continue
        line = "" if msg.stop_reason == "refusal" else clean(next((b.text for b in msg.content if b.type == "text"), ""))
        if not line or line.upper().startswith("NOTHING"):
            if over and not match_goes_on():
                return                                            # the game ended and it chose silence: done
            continue
        c, d = http("POST", "/api/brain/say", {"seat": SEAT, "text": line}, key=True)
        if c == 200:
            said.append(now); mine = (mine + [line])[-6:]
            print(f"{time.strftime('%H:%M:%S')} SAYS: {line}", flush=True)
        else:
            print(f"{time.strftime('%H:%M:%S')} not said ({c}): {d.get('error')}", flush=True)
        if over and not match_goes_on():
            return


if __name__ == "__main__":
    main()
