"""T4 stand-in judge: scores the blind sheet (docs/results/t4/score.html) with a Workers AI model instead of a person.

Run with the Cloudflare account id and a Workers AI token in the environment (never printed; use `infisical run`):

    infisical run ... -- python3 table/tests/t4_judge.py [@cf/zai-org/glm-5.3-flash]

The judge sees only what the human sheet shows: the scene, the player's line and the reply. It never sees the model
behind a reply. Writes docs/results/t4/glm-scores.json (blind ids) and prints the un-blinded averages. This is a model
judging a model: it complements Michael's scores, it does not replace them. Only the table's own test transcripts are
sent; nothing from a real game.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

T4 = Path(__file__).resolve().parents[2] / "docs" / "results" / "t4"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "@cf/zai-org/glm-5.3-flash"
RUBRIC = (
    "You are judging one reply from a Dungeon Master in a spoken, theater-of-the-mind Dungeons & Dragons game. The "
    "reply will be read aloud to the players. Score it strictly and independently.\n"
    "vivid: 1-5. 1 = flat or generic, 3 = competent, 5 = a sensory, specific, memorable image that fits the moment.\n"
    "consistent: \"yes\" only if it stays true to the scene and the player's line, never speaks or decides for a player "
    "character, never invents the outcome of a roll nobody has made, and contains no stray rules text or bookkeeping.\n"
    "keep: \"yes\" if, as a player at this table, you would happily keep playing with this DM.\n"
    "Answer with ONLY a JSON object: {\"vivid\": n, \"consistent\": \"yes|no\", \"keep\": \"yes|no\", \"why\": \"<=20 words\"}."
)


def items() -> list[dict]:
    page = (T4 / "score.html").read_text()
    out = []
    for sec in re.findall(r"<section>(.*?)</section>", page, re.S):
        m = re.search(r"<h3>(R\d+) · (.*?)</h3><p class=line>(.*?)</p><p>(.*?)</p>", sec, re.S)
        if m:
            out.append({"id": m[1], "scene": html.unescape(m[2]), "line": html.unescape(m[3]), "reply": html.unescape(m[4]).strip()})
    return out


def ask(account: str, token: str, prompt: str) -> str:
    body = {"messages": [{"role": "system", "content": RUBRIC}, {"role": "user", "content": prompt}],
            "max_tokens": 1500, "temperature": 0}
    req = urllib.request.Request(f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODEL}",
                                 data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.load(r).get("result", {})
    if isinstance(res.get("response"), str):
        return res["response"]
    choices = res.get("choices") or []
    return (choices[0].get("message", {}).get("content") or "") if choices else ""


def main() -> int:
    account, token = os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_WORKER_AI_KEY") or os.environ.get("CLOUDFLARE_API_KEY")
    if not (account and token):
        print("needs CLOUDFLARE_ACCOUNT_ID and a Workers AI token in the environment", file=sys.stderr)
        return 2
    key = json.loads((T4 / "blind-key.json").read_text())
    rows = []
    for it in items():
        prompt = f"Scene: {it['scene']}\nPlayer: {it['line']}\n\nDM reply to judge:\n{it['reply']}"
        score = None
        for attempt in range(3):
            try:
                m = re.search(r"\{.*\}", ask(account, token, prompt), re.S)
                score = json.loads(m[0]) if m else None
                if score and int(score["vivid"]) in range(1, 6) and score["consistent"] in ("yes", "no") and score["keep"] in ("yes", "no"):
                    break
                score = None
            except Exception as e:  # a bad answer or a transient API error: try again, then record the miss
                print(f"  {it['id']} attempt {attempt + 1}: {type(e).__name__}", file=sys.stderr)
            time.sleep(1)
        rows.append({"id": it["id"], "scene": it["scene"], "judge": score})
        print(f"  {it['id']} {score and (score['vivid'], score['consistent'], score['keep'])}")
    (T4 / "glm-scores.json").write_text(json.dumps({"judge": MODEL, "note": "a model's scores, not Michael's; blind ids", "scores": rows}, indent=1) + "\n")
    by = defaultdict(list)
    for r in rows:
        if r["judge"]:
            by[key[r["id"]]].append(r["judge"])
    print()
    for model, js in sorted(by.items()):
        n = len(js)
        print(f"{model:20} n={n}  vivid {sum(int(j['vivid']) for j in js) / n:.2f}  consistent {sum(j['consistent'] == 'yes' for j in js)}/{n}  keep playing {sum(j['keep'] == 'yes' for j in js)}/{n}")
    return 0 if all(r["judge"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
