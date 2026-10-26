"""The comparison half of harness/replay_diff.py (no game): volatile fields are ignored, a real difference is located, interleaving is
told apart from a state difference.   python3 table/tests/replay_diff_test.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "harness"))
import replay_diff as R  # noqa: E402

A = [{"id": 1, "ts": 1.0, "type": "highroll", "winner": "Kaust", "started": 5.0}, {"id": 2, "ts": 2.0, "type": "say", "speaker": "Kaust", "text": "hi"},
     {"id": 3, "ts": 3.0, "type": "say", "speaker": "Tuvasa", "text": "yo"}]
B = [{"id": 9, "ts": 9.0, "type": "highroll", "winner": "Kaust", "started": 8.0}, {"id": 10, "ts": 9.5, "type": "say", "speaker": "Kaust", "text": "hi"},
     {"id": 11, "ts": 9.9, "type": "say", "speaker": "Tuvasa", "text": "yo"}]
r = R.diff(A, B); assert r["identical"] and r["first_divergence"] is None, r                      # ids, timestamps, clocks do not count
C = [B[0], B[2], B[1]]
r = R.diff(A, C); assert not r["identical"] and r["first_divergence"] == 1 and all(r["per_seat_identical"].values()) and r["same_multiset"], r   # interleaving only
D = [B[0], {**B[1], "text": "different"}, B[2]]
r = R.diff(A, D); assert r["first_divergence"] == 1 and not r["per_seat_identical"]["Kaust"] and not r["same_multiset"], r  # a real difference
r = R.diff(A, A[:2]); assert r["first_divergence"] == 2 and r["events"] == [3, 2], r               # one log is shorter
E_ = [{**A[0], "winner": "Tuvasa"}] + A[1:]
assert not R.diff(A, E_)["identical"], "a different roll winner must differ"
print("replay_diff_test: ok")
