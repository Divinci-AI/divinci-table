"""T15: the autopilot bot (review-20261109/bot-v6) answers the opening-hand step by the table's rule and never holds the start.
    PYTHONDONTWRITEBYTECODE=1 ~/.venvs/table/bin/python table/tests/mulligan_bot_test.py   (BOT_DIR: where claude_autopilot.py is)"""
import importlib, os, re, sys, tempfile, time
from pathlib import Path
HERE = Path(__file__).resolve().parent.parent
BOT = Path(os.path.expanduser(os.environ.get("BOT_DIR", str(HERE.parent / "bot-v6"))))
sys.path[:0] = [str(HERE), str(HERE / "tests"), str(BOT)]
os.environ["TABLE_MULLIGAN"] = "on"
os.environ["TABLE_MULLIGAN_SECS"] = "0"
import retro_game4_test as R
t = R.Table()
try:
    t.keys = {n: t.call("POST", "/api/seat/claim", {"name": n}, key="dev-" + n)[1].get("key", "") for n in ("Claude", "Michael")}
    keyf = Path(tempfile.mkdtemp()) / "k"
    keyf.write_text(t.keys["Claude"]); keyf.chmod(0o600)
    os.environ.update(CLAUDE_SEAT="Claude", CLAUDE_KEY_FILE=str(keyf), TABLE_URL=t.b)
    ap = importlib.import_module("claude_autopilot")
    ap.BASE, ap.SEAT, ap.KEY = t.b, "Claude", t.keys["Claude"]
    ph = t.call("GET", "/api/phase")[1]
    R.check(ap.opening_hand(ph) is False, "before the step opens the bot does nothing", ph.get("mulligan"))
    t.call("POST", "/api/phase/next", {"by": "Michael", "key": t.keys["Michael"]}, key=t.keys["Michael"])
    for _ in range(6):
        ph = t.call("GET", "/api/phase")[1]
        if not ap.opening_hand(ph):
            break
    ph = t.call("GET", "/api/phase")[1]
    st = t.call("GET", "/api/brain/state?seat=Claude", key=t.keys["Claude"])[1]
    ev = t.call("GET", "/api/events?since=0")[1]
    ev = ev.get("events") if isinstance(ev, dict) else ev
    says = [e["text"] for e in ev if e.get("type") == "say" and e.get("speaker") == "Claude"]
    R.check(ph["mulligan"]["waiting"] == ["Michael"] and st["mulligan"]["kept"], "the bot answers the step: it has kept, only the person is waited on", ph.get("mulligan"))
    R.check(says and all(re.fullmatch(r"Claude (keeps|mulligans to) (seven|six|five)\.", s) for s in says) and says[-1].startswith("Claude keeps"), "by the rule, never below five, and the log has sizes only", says)
    R.check(len(st["hand"]) == 7 - st["mulligan"]["taken"], "its hand is seven less its mulligans (the bottoms were sent)", (len(st["hand"]), st["mulligan"]))
    R.check(ap.opening_hand(t.call("GET", "/api/phase")[1]) is False, "asked again after keeping, it does nothing", "")
    t.call("POST", "/api/brain/keep", {"seat": "Michael"}, key=t.keys["Michael"])
    end = time.time() + 10
    while time.time() < end and t.call("GET", "/api/phase")[1].get("player") is None:
        time.sleep(0.2)
    R.check(t.call("GET", "/api/phase")[1].get("player") == "Claude", "the game starts once the person keeps", "")
finally:
    t.stop()
print("FAILED: " + "; ".join(R.FAILS) if R.FAILS else "all good")
sys.exit(1 if R.FAILS else 0)
