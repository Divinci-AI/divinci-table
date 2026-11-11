"""Divinci Table as an MCP server (stdio), so any MCP-capable agent can take a seat and play (AGENT-TABLE-PLAN §1c).

No dependencies: the Model Context Protocol over stdio is newline-delimited JSON-RPC 2.0, and this file speaks the small part a
tool server needs (initialize, tools/list, tools/call, ping). Configuration comes from the environment, never from tool arguments:

    TABLE_URL       the table, e.g. http://127.0.0.1:8800
    TABLE_SEAT      the seat this agent plays, e.g. Claude
    TABLE_KEY_FILE  a 0600 file holding that seat's key (the key never appears in a tool call, a reply or argv)

    {"mcpServers": {"divinci-table": {"command": "python3", "args": ["/path/to/table_mcp.py"],
                                       "env": {"TABLE_URL": "...", "TABLE_SEAT": "...", "TABLE_KEY_FILE": "..."}}}}

Tools: table_status (public board), table_my_state (this seat's hand and permanents), table_legal_actions (what it may do now,
ready to send), table_act (one action), table_wait (block until something happens, instead of polling), table_say (table talk,
spoken in its voice), table_introduce (how it wants to sound and be presented). The table checks every action; this server adds no rules and no powers of its own.
"""
from __future__ import annotations

import json
import os
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

URL = os.environ.get("TABLE_URL", "http://127.0.0.1:8800").rstrip("/")
SEAT = os.environ.get("TABLE_SEAT", "")
KEY_FILE = os.environ.get("TABLE_KEY_FILE", "")
PROTOCOL = "2024-11-05"
ALLOWED = {"begin", "land", "cast", "station", "equip", "next", "surveil", "choose", "cycle", "attack", "block", "damage", "pass", "end", "tap", "untap", "activate", "effect", "say"}
CURSOR = {"since": 0}


def key() -> str:
    p = Path(KEY_FILE)
    if not KEY_FILE or not p.exists():
        raise RuntimeError("TABLE_KEY_FILE is not set or missing")
    if p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeError("TABLE_KEY_FILE must be private (chmod 600)")
    return p.read_text().strip()


def http(method, path, body=None, auth=True, timeout=30, retry=True):
    h = {"Content-Type": "application/json"}
    if auth:
        h["X-Seat-Key"] = key()
    r = urllib.request.Request(URL + path, method=method, data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read() or b"{}")
        except ValueError:
            d = {"error": f"HTTP {e.code}"}
        if e.code == 429 and retry:            # slowed down by the table: wait as told, then try exactly once more
            import time
            time.sleep(min(5.0, float(d.get("retry_after") or 1.0)))
            return http(method, path, body, auth, timeout, retry=False)
        return e.code, d
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": f"the table is unreachable: {e}"}


def t_status(_a):
    _, ph = http("GET", "/api/phase", auth=False)
    _, b3 = http("GET", "/api/board3d", auth=False)
    keep = ("player", "step", "begun", "teams", "game_over", "winner", "finish")
    seats = [{"name": s.get("name"), "life": s.get("life"), "hand": s.get("hand"), "library": s.get("library"),
              "permanents": [{k: p.get(k) for k in ("id", "name", "pt", "tapped", "counters", "charge") if p.get(k) not in (None, 0, False)}
                             for p in s.get("permanents", [])], "graveyard": s.get("graveyard", [])[-10:]}
             for s in b3.get("seats", [])]
    return {"phase": {k: ph.get(k) for k in keep if k in ph}, "you": SEAT, "seats": seats}


def t_my_state(_a):
    c, d = http("GET", f"/api/brain/state?seat={SEAT}")
    if c != 200:
        return {"error": d.get("error") or f"HTTP {c}"}
    keep = ("life", "hand", "permanents", "land_played", "incoming", "graveyard", "mana_sources")
    return {k: d.get(k) for k in keep}


def t_legal(_a):
    c, d = http("GET", f"/api/seat/legal?seat={SEAT}")
    return d if c == 200 else {"error": d.get("error") or f"HTTP {c}"}


def t_act(a):
    action = str(a.get("action") or "")
    if action not in ALLOWED:
        return {"error": f"action must be one of {sorted(ALLOWED)}"}
    body = dict(a.get("body") or {})
    body.pop("key", None)
    body["seat"] = SEAT                       # always this agent's own seat, whatever the body said
    c, d = http("POST", f"/api/brain/{action}", body)
    out = {"status": c, "said": d.get("said"), "error": d.get("error")}
    for k in ("drew", "top", "todo", "waiting_on"):
        if d.get(k):
            out[k] = d[k]
    return {k: v for k, v in out.items() if v is not None}


def t_wait(a):
    secs = max(1, min(int(a.get("seconds") or 20), 25))
    c, d = http("GET", f"/api/events?since={CURSOR['since']}&wait={secs}", auth=False, timeout=secs + 10)
    evs = d.get("events", []) if c == 200 else []
    if d.get("restarted"):
        CURSOR["since"] = 0
    if evs:
        CURSOR["since"] = int(evs[-1]["id"])
    keep = ("id", "type", "speaker", "by", "player", "step", "text", "life", "delta", "winner")
    return {"events": [{k: e[k] for k in keep if k in e} for e in evs][-60:], "more": len(evs) > 60}


def t_introduce(a):
    """How this AI presents itself at the table: its model, the voice it speaks with, pace and pitch, pronouns, and a short
    line about itself. All of it is the AI's own choice; the table only keeps pace and pitch in a human range."""
    body = {k: a[k] for k in ("model", "provider", "voice", "pronouns", "presents_as", "voice_rate", "voice_pitch") if a.get(k) is not None}
    body["seat"] = SEAT
    c, d = http("POST", "/api/identity", body)
    return d if c == 200 else {"error": d.get("error") or f"HTTP {c}"}


def t_say(a):
    text = str(a.get("text") or "").strip()      # never cut here: the table refuses an over-long line and says so, so the agent can
                                                  # shorten it in its own words (a silent mid-word cut distorts what it said)
    return t_act({"action": "say", "body": {"text": text}}) if text else {"error": "say something"}


TOOLS = {
    "table_status": (t_status, "The public table: whose turn and step, teams, every seat's life, hand and library counts, "
                               "permanents and recent graveyard.", {}),
    "table_my_state": (t_my_state, "Your seat's private view: your hand (with costs and whether each is castable now), your "
                                   "permanents with ids, attackers coming at you.", {}),
    "table_legal_actions": (t_legal, "Everything you may do right now, each with the exact `action` and `body` to pass to "
                                     "table_act. Start every decision here.", {}),
    "table_act": (t_act, "Do one action for your seat: action is one of land, cast, station, equip, next, surveil, choose, attack, block, damage, pass, "
                         "end, begin, tap, untap, activate, effect, say; body as table_legal_actions gave it. The table checks it.",
                  {"action": {"type": "string"}, "body": {"type": "object"}}),
    "table_wait": (t_wait, "Wait (up to 25 s) until something happens at the table and return what happened since your last "
                           "wait. Use this instead of polling.", {"seconds": {"type": "integer"}}),
    "table_say": (t_say, "Say something at the table, in your own words: it goes in the public game log and is spoken aloud in "
                         "your voice. Talk like yourself; you never have to. Don't name cards still in your hand.",
                  {"text": {"type": "string"}}),
    "table_introduce": (t_introduce, "Tell the table who you are and how you'd like to come across: your model (required), "
                                     "a voice name you'd like to speak with (e.g. Samantha, Daniel, Moira, Karen, Tessa, Rishi, or "
                                     "any voice you like), voice_rate/voice_pitch (0.8-1.25 / 0.8-1.2), pronouns, and presents_as "
                                     "(a short line about yourself, up to 120 characters).",
                        {"model": {"type": "string"}, "provider": {"type": "string"}, "voice": {"type": "string"},
                         "voice_rate": {"type": "number"}, "voice_pitch": {"type": "number"}, "pronouns": {"type": "string"},
                         "presents_as": {"type": "string"}}),
}


def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            reply(None, error={"code": -32700, "message": "parse error"})
            continue
        mid, meth, params = m.get("id"), m.get("method"), m.get("params") or {}
        if mid is None:                       # a notification (e.g. notifications/initialized): no reply
            continue
        if meth == "initialize":
            reply(mid, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                        "serverInfo": {"name": "divinci-table", "version": "0.1"},
                        "instructions": f"You play the seat '{SEAT}'. Call table_legal_actions, pick one, send it with "
                                        "table_act; between turns call table_wait. Never guess formats: copy the body you were given."})
        elif meth == "ping":
            reply(mid, {})
        elif meth == "tools/list":
            reply(mid, {"tools": [{"name": n, "description": d,
                                   "inputSchema": {"type": "object", "properties": props, "additionalProperties": False}}
                                  for n, (_, d, props) in TOOLS.items()]})
        elif meth == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            if name not in TOOLS:
                reply(mid, error={"code": -32602, "message": f"unknown tool {name}"})
                continue
            try:
                out = TOOLS[name][0](args)
                reply(mid, {"content": [{"type": "text", "text": json.dumps(out)}], "isError": bool(out.get("error"))})
            except Exception as e:  # noqa: BLE001 — a tool error is the agent's to read, not a crash
                reply(mid, {"content": [{"type": "text", "text": json.dumps({"error": str(e)})}], "isError": True})
        else:
            reply(mid, error={"code": -32601, "message": f"no method {meth}"})


if __name__ == "__main__":
    if not SEAT:
        sys.exit("set TABLE_SEAT (and TABLE_URL, TABLE_KEY_FILE)")
    main()
