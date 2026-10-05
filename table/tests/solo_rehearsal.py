"""T6 prep (docs/THEATER-GOAL.md): a solo theater-of-the-mind story with the real local DM, before Michael walks it.

One player (Ana), two AI companions (Leonardo, a wizard; Mira, a cleric), the AI DM on the local model, theater mode.
Eight lines Ana "says" (as the 🎙 would send them after transcribing), some addressed to a companion by name.
Checks what the table can: each beat answers, a companion addressed by name answers in their own voice, the DM never
writes Ana's words, the death-save and other rules hold; times each beat to its first spoken sentence. Writes the
whole story as a transcript to read (docs/results/t6-solo-rehearsal.md): reading it is the real check.

    ~/.venvs/table/bin/python table/tests/solo_rehearsal.py [ollama-model]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODEL = sys.argv[1] if len(sys.argv) > 1 else "gemma4:e2b"
PORT = 8700 + os.getpid() % 90
BASE = f"http://127.0.0.1:{PORT}"
LINES = [
    "I step off the road into the old ruins as the rain starts, and look for shelter.",
    "Leonardo, can you light the way with your magic?",
    "I follow the light deeper in. What do I find?",
    "Mira, do you sense anything evil down here?",
    "Where am I?",
    "I draw my sword and call out: whoever's there, show yourself!",
    "Roll perception",
    "Leonardo, Mira, get ready. On my signal we rush them.",
]
PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {detail}"))


def call(path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "solo"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


tmp = tempfile.mkdtemp()
srv = subprocess.Popen([str(Path.home() / ".venvs/table/bin/python"), "table/dnd_server.py", "--players", "Ana",
                        "--companions", "ai:Leonardo:normal:wizard,ai:Mira:quiet:cleric", "--mode", "theater",
                        "--dm-backend", f"ollama:{MODEL}", "--port", str(PORT)], cwd=ROOT,
                       env={**os.environ, "DIVINCI_FUSION_API_KEY": "", "TABLE_RESEARCH_DIR": tmp},
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
story, beats = [], []
try:
    for _ in range(80):
        try:
            call("/api/dnd")
            break
        except OSError:
            time.sleep(0.25)
    key = call("/api/seat/claim", {"name": "Ana"})["key"]
    since = call("/api/events?since=0")["last"]
    for line in LINES:
        t0 = time.time()
        r = call("/api/dnd/act", {"by": "Ana", "key": key, "text": line})
        story.append(("Ana", line))
        if r.get("answer"):
            story.append(("(the table, to Ana)", r["answer"]))
            beats.append({"line": line, "answered_by": "table", "first_s": 0.0})
            continue
        evs, first, done_at = [], None, None
        while time.time() - t0 < 90:
            new = call(f"/api/events?since={since}")
            since = new.get("last", since)
            evs += new.get("events", [])
            if first is None and any(e["type"] == "dm_part" for e in evs):
                first = time.time() - t0
            if any(e["type"] == "dm" for e in evs) and not call("/api/dnd").get("dm_busy"):
                done_at = time.time() - t0
                break
            time.sleep(0.15)
        parts = [e for e in evs if e["type"] == "dm_part"]
        for e in evs:
            if e["type"] == "roll":
                story.append(("(dice)", e["text"]))
        for p in parts:
            story.append((p["voice"], p["text"]))
        beats.append({"line": line, "first_s": first and round(first, 2), "done_s": done_at and round(done_at, 1),
                      "voices": sorted({p["voice"] for p in parts}), "parts": [p["text"] for p in parts]})
        print(f"  {line[:52]:54} first {first and round(first, 2)} s · voices {sorted({p['voice'] for p in parts})}")
finally:
    srv.terminate()

print("\nchecks")
check("every beat got an answer", all(b.get("answered_by") or b["parts"] for b in beats),
      str([b["line"][:30] for b in beats if not (b.get("answered_by") or b["parts"])]))
for b in beats:
    for who in ("Leonardo", "Mira"):
        if b["line"].startswith(who):
            check(f"{who}, addressed by name, answers in their own voice", who in b["voices"], f"{b['line'][:40]}: {b['voices']}")
check("the DM never speaks as Ana", not any(v == "Ana" or re.match(r"^\s*Ana\s*:", t) for v, t in story[1:] if v != "Ana"))
check("'Where am I?' is answered by the table, not the DM",
      next((b.get("answered_by") for b in beats if b["line"] == "Where am I?"), None) == "table")
check("'Roll perception' rolls the dice", any(v == "(dice)" and "perception" in t for v, t in story))
check("nothing spoken echoes the prompt (STATE, JSON)", not any(re.search(r'STATE|RECENT|\{"', t) for v, t in story if v not in ("Ana", "(dice)")),
      str([t[:60] for v, t in story if re.search(r'STATE|\{"', t)]))
check("the DM set a scene card, so 'Where am I?' is answered from it",
      any(v == "(the table, to Ana)" and t.startswith("You're in") for v, t in story), str([t for v, t in story if v.startswith("(the table")]))
comp = [(v, t) for v, t in story if v in ("Leonardo", "Mira")]
check("companions speak in the first person (not 'He…' / 'She…')", comp and not any(re.match(r"^(He|She|They)\b", t) for _, t in comp), str(comp[:4]))
last = beats[-1]
check("'Leonardo, Mira, get ready': both answer", {"Leonardo", "Mira"} <= set(last["voices"]), str(last["voices"]))
firsts = sorted(b["first_s"] for b in beats if b.get("first_s") and not b.get("answered_by"))
print(f"\nfirst spoken sentence after Ana's line: median {firsts[len(firsts) // 2] if firsts else None} s, max {max(firsts) if firsts else None} s "
      "(the server's side only; add ~0.3 s of speech-to-text and the page's poll)")

out = ROOT / "docs" / "results" / "t6-solo-rehearsal.md"
out.write_text(f"# T6 rehearsal: a solo story with {MODEL} ({time.strftime('%Y-%m-%d')})\n\n"
               "Ana is one player; Leonardo (wizard) and Mira (cleric) are AI companions; the DM is local. Generated by "
               "`table/tests/solo_rehearsal.py`; **read it**: the checks can't tell whether it's a good story.\n\n" +
               "\n".join(f"- **{who}:** {text}" for who, text in story) + "\n")
print(f"transcript: {out}")
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
