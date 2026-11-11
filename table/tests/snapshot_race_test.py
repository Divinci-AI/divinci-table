"""The snapshot is written by many request threads at once (dogfood loop 2: 6-16 'snapshot failed' per game).

    python3 table/tests/snapshot_race_test.py

Every writer must succeed, no temp file may be left behind, and an older state must never land over a newer one.
"""
from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import snapfile as S  # noqa: E402

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    ok = bool(ok)
    PASS, FAIL = PASS + ok, FAIL + (not ok)
    print(("  ✓ " if ok else "  ✗ ") + name + ("" if ok else f" — {str(detail)[:300]}"), flush=True)


d = Path(tempfile.mkdtemp())
p = d / "snapshot.pkl"
errors: list = []
N, T = 60, 12


def worker(t):
    for i in range(N):
        seq = t * N + i + 1
        try:
            S.write_snapshot(p, f"state-{seq}".encode(), seq)
        except Exception as e:                       # the old code failed here: the shared temp file vanished under it
            errors.append(repr(e))


S.SNAP_SEQ[:] = [0, 0]
ts = [threading.Thread(target=worker, args=(t,)) for t in range(T)]
[t.start() for t in ts]
[t.join() for t in ts]
check(f"{T} threads x {N} writes at once: none fails", not errors, errors[:2])
check("no temp file is left behind", [f.name for f in d.iterdir()] == ["snapshot.pkl"], sorted(f.name for f in d.iterdir()))
final = p.read_bytes().decode()
check("the file holds a complete state and never a newer one overwritten by an older one",
      final == f"state-{S.SNAP_SEQ[1]}" and S.SNAP_SEQ[1] == max(int(final.split('-')[1]), 0), (final, S.SNAP_SEQ))
check("the snapshot is private (0600)", (p.stat().st_mode & 0o777) == 0o600, oct(p.stat().st_mode))
S.SNAP_SEQ[:] = [0, 10]
check("a state pickled earlier than the one on disk is skipped", S.write_snapshot(p, b"old", 5) is False and p.read_bytes().decode() == final)
check("…and a later one is written", S.write_snapshot(p, b"new", 11) is True and p.read_bytes() == b"new")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
