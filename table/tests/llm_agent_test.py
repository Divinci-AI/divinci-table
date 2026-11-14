"""bots/llm_agent.py finds the table's MCP server in this repository, says which model it is, and never needs a model to be reachable
to keep a game moving (2026-11-14, retro M7: the file ran only beside the live games, with a home-directory path built in).

    PYTHONDONTWRITEBYTECODE=1 TABLE_NO_VOICE=1 NO_GEMMA=1 python3 table/tests/llm_agent_test.py

No model is loaded: OLLAMA_URL points at a closed loopback port, so every question to the model fails at once and the agent takes its
documented fall-back (the first listed action that is not "end"). Proved, on a scratch table on a free port: the agent starts
table/table_mcp.py from this checkout with only its own seat's key file; the table shows the seat as played by that model
(GET /api/played-by: "model", never a person); and the agent plays its turn without a traceback."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(HERE), str(HERE / "tests")]
sys.dont_write_bytecode = True
import cards_path  # noqa: E402

BOT = Path(os.path.expanduser(os.environ.get("BOT_DIR", str(HERE.parent / "bots"))))
cards_path.need(BOT / "llm_agent.py", "the LLM agent", "BOT_DIR=<dir with llm_agent.py>")
import match_bo3_test_lib as L  # noqa: E402

R, check, C, M = L.R, L.check, L.C, L.M
s = socket.socket(); s.bind(("127.0.0.1", 0)); closed = s.getsockname()[1]; s.close()     # a loopback port nothing listens on

t = L.Table(life=20)
agent = None
try:
    t.claim()
    tmp = Path(tempfile.mkdtemp(prefix="llm-"))
    kf = tmp / "m.key"
    kf.write_text(t.keys[M]); kf.chmod(0o600)
    log = tmp / "agent.out"
    env = {**os.environ, "TABLE_URL": t.b, "TABLE_SEAT": M, "TABLE_KEY_FILE": str(kf), "MODEL": "test-model:none", "OLLAMA_URL": f"http://127.0.0.1:{closed}",
           "TABLE_TALK": "0", "AGENT_LOG": str(tmp / "agent.log"), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("TABLE_MCP", None)
    agent = subprocess.Popen([sys.executable, "-u", str(BOT / "llm_agent.py")], env=env, cwd=tmp, stdout=open(log, "w"), stderr=subprocess.STDOUT)
    check(t.until(lambda: "declared: ollama:test-model:none -> ok" in log.read_text(), 20), "the agent starts the MCP server of this checkout and declares its model", log.read_text()[-400:])
    who = next(x for x in t.call("GET", "/api/played-by", host=False)[1]["seats"] if x["name"] == M)
    check(who["kind"] == "model" and who["label"] == "ollama:test-model:none", "the table shows the seat as played by that model, not by a person", who)
    t.call("POST", "/api/phase/next", {"by": C, "key": t.keys[C]}, key=t.keys[C])

    def drive():
        p = t.phase()
        if p.get("player") == C and not p.get("begun"):
            t.call("POST", "/api/brain/begin", {"seat": C}, key=t.keys[C])
        elif p.get("player") == C:
            t.call("POST", "/api/brain/end", {"seat": C}, key=t.keys[C])
        elif (p.get("passes") or {}).get("next") == C:
            t.call("POST", "/api/brain/pass", {"seat": C, "player": p.get("player"), "step": p.get("step")}, key=t.keys[C])
        return (tmp / "agent.log").exists() and "(fallback)" in (tmp / "agent.log").read_text()
    check(t.until(drive, 90), "with no model reachable it still plays its turn (the fall-back: the first listed action that is not 'end')", log.read_text()[-500:])
    told = (tmp / "agent.log").read_text() + log.read_text()
    check("Traceback" not in told and agent.poll() is None and t.keys[M] not in told, "no traceback, still running, and the seat key is in none of its output")
    check(next(x for x in t.call("GET", "/api/played-by", host=False)[1]["seats"] if x["name"] == M)["kind"] == "model", "after its moves the seat is still shown as the model's")
finally:
    if agent is not None and agent.poll() is None:
        agent.terminate()
    t.stop()

print("\nFAILED: " + "; ".join(R.FAILS) if R.FAILS else "\nall good")
sys.exit(1 if R.FAILS else 0)
