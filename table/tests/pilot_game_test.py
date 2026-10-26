"""Pieces of harness/pilot_game.py that need no game: reading life totals, and the /health keepalive.   python3 table/tests/pilot_game_test.py"""
import sys, threading, time
from pathlib import Path

H = Path(__file__).resolve().parents[2] / "harness"
sys.path.insert(0, str(H))
import fake_router, pilot_game as P  # noqa: E402

assert P.lives([{"type": "life", "player": "A", "life": 30}, {"type": "say"}, {"type": "life", "player": "A", "life": -2}, {"type": "life", "player": "B", "life": 5}]) == {"A": -2, "B": 5}
assert P.lives([]) == {}

srv = fake_router.start("first", 1)
stats, stop = {"ok": 0, "failed": 0, "max_s": 0.0}, threading.Event()
t = threading.Thread(target=P.keepalive, args=(srv.base.removesuffix("/v1"), stats, stop), daemon=True); t.start()
time.sleep(0.5); stop.set(); t.join(3)
assert stats["ok"] >= 1 and stats["failed"] == 0, stats
dead = {"ok": 0, "failed": 0, "max_s": 0.0}; stop2 = threading.Event()
t = threading.Thread(target=P.keepalive, args=("http://127.0.0.1:9", dead, stop2), daemon=True); t.start()
time.sleep(0.5); stop2.set(); t.join(3)
assert dead["failed"] >= 1 and dead["ok"] == 0, dead
assert len(srv.seen) == 0, "the keepalive must never make a model request"
print("pilot_game_test: ok")
