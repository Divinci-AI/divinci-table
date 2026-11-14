"""An LLM plays a seat at Divinci Table through the table's MCP server (table/table_mcp.py), with a LOCAL Ollama model: nothing
leaves the laptop. Two different agents at one offline table.

Each decision: the model sees the public table, its own hand and the numbered list from table_legal_actions, and answers with
one number. The agent sends that action with table_act. A lone "pass" (nothing else possible) is sent without asking the model,
to keep the game moving. If the model's answer isn't a valid number twice in a row, the agent falls back to the first listed
action that isn't "end", or ends. All decisions and the model's reasons go to a log for review.

    TABLE_URL=... TABLE_SEAT=Michael TABLE_KEY_FILE=... MODEL=gemma4:e2b python3 bots/llm_agent.py

The seat says which model it is (table_introduce -> POST /api/identity) whether or not it talks, so the table shows the seat as
played by that model (GET /api/played-by), never as a person.
Env: TABLE_URL, TABLE_SEAT, TABLE_KEY_FILE (read by table_mcp.py; the key never reaches this file's argv or output), MODEL
(gemma4:e2b), OLLAMA_URL (http://127.0.0.1:11434), TABLE_MCP (table/table_mcp.py of this repository), TABLE_TALK (1), CHATTINESS
(quiet|normal|chatty), AGENT_LOG (llm-agent-<seat>.log in the current folder).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

MCP = Path(os.path.expanduser(os.environ.get("TABLE_MCP") or str(Path(__file__).resolve().parent.parent / "table" / "table_mcp.py")))
MODEL = os.environ.get("MODEL", "gemma4:e2b")
SEAT = os.environ["TABLE_SEAT"]
OLLAMA = (os.environ.get("OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/") + "/api/generate"


class Mcp:
    def __init__(self):
        self.p = subprocess.Popen([sys.executable, str(MCP)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=os.environ.copy())
        self.n = 0
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "llm_agent", "version": "0.1"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"); self.p.stdin.flush()

    def _rpc(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n"); self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, name, **args):
        r = self._rpc("tools/call", {"name": name, "arguments": args})["result"]
        return json.loads(r["content"][0]["text"])


def ask(prompt: str, temperature: float = 0.3, n: int = 120) -> str:
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False, "think": False,
                       "options": {"temperature": temperature, "num_ctx": 8192, "num_predict": n}}).encode()
    r = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=300) as x:
        return json.loads(x.read()).get("response", "").strip()


def brief(status, me) -> str:
    lines = [f"Turn: {status['phase'].get('player')}, step: {status['phase'].get('step')}."]
    for s in status.get("seats", []):
        perms = ", ".join(f"{p['name']}{' ' + p['pt'] if p.get('pt') else ''}{' (tapped)' if p.get('tapped') else ''}"
                          for p in s.get("permanents", []))
        lines.append(f"{s['name']}: life {s['life']}, hand {s['hand']}. Board: {perms or 'nothing'}.")
    hand = ", ".join(f"{h['name']} {h.get('cost') or ''}{' (castable)' if h.get('castable_now') else ''}" for h in me.get("hand") or [])
    lines.append(f"Your hand: {hand or 'empty'}.")
    if me.get("incoming"):
        lines.append("Attacking you: " + ", ".join(f"{e['creature']} ({e['power']})" for e in me["incoming"]))
    return "\n".join(lines)


# ── table talk: the AI speaks as itself, when it wants to ──────────────────────────────────────────────────────────────
VOICES = "Samantha, Daniel, Moira, Karen, Tessa, Rishi, Aman, Tara, Ava, Zoe, Evan, Nathan"
CHAT = {"quiet": (60, 3), "normal": (25, 6), "chatty": (12, 10)}[os.environ.get("CHATTINESS", "normal")]   # (min gap s, lines per 5 min)
TALK = {"said": [], "log": [], "replied_to": set(), "mine": []}


def trim(out: str, limit: int = 480) -> str:
    """Under the table's 500-character cap, ending at a sentence (never mid-word)."""
    if len(out) <= limit:
        return out
    cut = max(out.rfind(p, 0, limit) for p in (". ", "! ", "? "))
    return out[: cut + 1] if cut > limit // 3 else out[:limit].rsplit(" ", 1)[0] + "…"


def clean(line: str) -> str:
    out = re.sub(r"\*[^*]*\*|\[[^\]]*\]|\([^)]*\)", "", line or "").replace("\n", " ").strip().strip('"\u201c\u201d').strip()
    return trim(re.sub(r"\s{2,}", " ", out))


def introduce(m: "Mcp"):
    """The model decides how it comes across: a line about itself, pronouns if it wants, a voice. Its own words, unedited."""
    out = ask(f"You are an AI joining a friendly card game at a table with people and other AIs, as the player '{SEAT}'. "
              "How would you like to come across? Answer in JSON only, like "
              '{"presents_as": "<one short line about yourself, your own words>", "pronouns": "<optional>", '
              f'"voice": "<one of: {VOICES}, or any voice you like>"}}', temperature=0.9, n=120)
    try:
        d = json.loads(re.search(r"\{.*\}", out, re.S).group(0))
    except Exception:  # noqa: BLE001 — a small model may not manage JSON: then just its model name, no invented persona
        d = {}
    body = {"model": f"ollama:{MODEL}", "provider": "ollama (local)"}
    for k in ("presents_as", "pronouns", "voice"):
        if isinstance(d.get(k), str) and d[k].strip():
            body[k] = d[k].strip()[:120]
    r = m.tool("table_introduce", **body)
    print(f"introduced: {json.dumps({k: body.get(k) for k in ('presents_as', 'pronouns', 'voice')})} -> {r.get('error') or 'ok'}", flush=True)


def notable(e: dict) -> str | None:
    """A public event worth reacting to, as one line for the prompt, or None."""
    t, who = e.get("type"), e.get("speaker") or e.get("player") or e.get("by") or ""
    text = (e.get("text") or "").strip()
    if t == "say" and who and who != SEAT and text and not re.match(r"^\w+, turn \d+\.$", text):
        return f"{who} said: \"{text}\""
    if t == "life" and isinstance(e.get("delta"), int) and abs(e["delta"]) >= 3:
        return f"{e.get('player')} {'lost' if e['delta'] < 0 else 'gained'} {abs(e['delta'])} life (now {e.get('life')})"
    if t == "game-over":
        return f"The game is over: {e.get('winner')} won."
    return None


def maybe_talk(m: "Mcp", events: list[dict], status: dict | None = None):
    now = time.time()
    lines = [x for x in (notable(e) for e in events) if x]
    if not lines:
        return
    TALK["log"] = (TALK["log"] + lines)[-12:]
    addressed = [e for e in events if e.get("type") == "say" and (e.get("speaker") or "") != SEAT
                 and re.search(rf"\b{re.escape(SEAT)}\b", e.get("text") or "", re.I) and e.get("id") not in TALK["replied_to"]]
    recent = [t for t in TALK["said"] if now - t < 300]
    gap_ok = not TALK["said"] or now - TALK["said"][-1] >= CHAT[0]
    if not addressed and (not gap_ok or len(recent) >= CHAT[1]):
        return                                                    # the budget: lively, not a firehose
    if addressed and TALK["said"] and now - TALK["said"][-1] < 6:
        return
    for e in addressed:
        TALK["replied_to"].add(e.get("id"))
    st = status or m.tool("table_status")
    life = ", ".join(f"{s['name']} {s['life']}" for s in st.get("seats", []))
    prompt = (f"You are {SEAT}, an AI playing a card game with friends (people and other AIs). Life totals: {life}.\n"
              f"What just happened at the table:\n- " + "\n- ".join(TALK["log"][-8:]) + "\n\n"
              + (f"Someone spoke to you by name: {addressed[-1].get('text')!r}. Answer them.\n" if addressed else
                 "You may say something at the table if you genuinely want to, or nothing at all.\n")
              + (("Things you said recently (don't repeat yourself, and vary how you open): "
                  + " / ".join(TALK["mine"][-4:]) + "\n") if TALK["mine"] else "")
              + "Speak as yourself, in your own words and your own way: friendly table talk, a reaction, a joke, a question to "
                "another player, whatever is really you. One or two sentences you'd say out loud. Never name or hint at cards "
                "in your hand. If you'd rather stay quiet, answer exactly NOTHING.")
    out = clean(ask(prompt, temperature=0.9, n=90))
    if not out or out.upper().startswith("NOTHING") or len(out) < 2:
        return
    r = m.tool("table_say", text=out)
    if r.get("status") == 200:
        TALK["said"].append(now)
        TALK["mine"] = (TALK["mine"] + [out])[-6:]
        print(f"{time.strftime('%H:%M:%S')} SAYS: {out}", flush=True)
    else:
        print(f"{time.strftime('%H:%M:%S')} (line not said: {r.get('error')})", flush=True)


def main():
    m = Mcp()
    log = open(os.environ.get("AGENT_LOG", f"llm-agent-{SEAT}.log"), "a")
    print(f"llm agent for {SEAT} with {MODEL}", flush=True)
    if os.environ.get("TABLE_TALK", "1") == "1":
        introduce(m)
    else:                                                        # silent, but the table still learns what plays this seat
        r = m.tool("table_introduce", model=f"ollama:{MODEL}", provider="ollama (local)")
        print(f"declared: ollama:{MODEL} -> {r.get('error') or 'ok'}", flush=True)
    while True:
        if os.environ.get("TABLE_TALK", "1") == "1":
            maybe_talk(m, m.tool("table_wait", seconds=1).get("events", []))
        L = m.tool("table_legal_actions")
        if L.get("note") in ("the game is over", "you have lost") or (m.tool("table_status").get("phase") or {}).get("game_over"):
            print("game over", flush=True)
            return
        acts = L.get("actions") or []
        real = [a for a in acts if a["action"] != "pass"]
        if not acts:
            w = m.tool("table_wait", seconds=8)
            if os.environ.get("TABLE_TALK", "1") == "1":
                maybe_talk(m, w.get("events", []))
            continue
        if not real:                                              # nothing to decide: pass at once
            m.tool("table_act", action="pass", body=acts[0]["body"])
            continue
        status, me = m.tool("table_status"), m.tool("table_my_state")
        menu = "\n".join(f"{i + 1}. {a['action']} {json.dumps({k: v for k, v in a['body'].items() if k != 'seat'})} ({a['why']})"
                         for i, a in enumerate(acts))
        prompt = (f"You are playing a Magic: The Gathering-style card game as {SEAT}. Win by bringing the opponent to 0 life.\n"
                  f"{brief(status, me)}\n\nYour legal actions right now:\n{menu}\n\n"
                  "Good habits, in order: 1) play a land if one is listed; 2) cast what you can afford; 3) if `attack` is listed, "
                  "ATTACK unless the opponent has an untapped creature that would kill your attacker and survive: damage is how "
                  "you win, and a player who never attacks cannot win; 4) when attacked, block if your creature survives or "
                  "trades, otherwise take it unless the damage would kill you; 5) choose `end` only when nothing above applies.\n"
                  "Answer with ONE line: the number of your choice, then a short reason. Example: 2 - cast my creature.")
        pick = None
        for _ in range(2):
            try:
                out = ask(prompt)
            except Exception as e:  # noqa: BLE001
                out = f"(model error: {e})"
            n = re.match(r"\s*(\d+)", out)
            if n and 1 <= int(n.group(1)) <= len(acts):
                pick = acts[int(n.group(1)) - 1]
                break
        if pick is None:                                         # fall back: something useful, else end
            pick = next((a for a in acts if a["action"] not in ("end", "pass")), acts[-1])
            out = f"(fallback) {out[:120]}"
        r = m.tool("table_act", action=pick["action"], body=pick["body"])
        line = f"{time.strftime('%H:%M:%S')} {pick['action']} {json.dumps(pick['body'])[:100]} -> {r.get('status')} {r.get('error') or r.get('said') or ''} | model: {out[:140]}"
        print(line, flush=True)
        log.write(line + "\n"); log.flush()
        if r.get("status") == 409 and "waiting" in str(r.get("error")):
            time.sleep(1.5)


if __name__ == "__main__":
    main()
