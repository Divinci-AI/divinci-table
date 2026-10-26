"""Writing the game snapshot safely. Many request threads call snapshot() at once (dogfood loop 2: 6-16 'snapshot failed' per game,
because they all renamed one shared temp file); this is the one place that writes it."""
from __future__ import annotations

import os
import threading
from pathlib import Path

SNAP_IO = threading.Lock()   # one writer at a time
SNAP_SEQ = [0, 0]            # [taken, written]: a state pickled earlier never overwrites one pickled later


def write_snapshot(p: Path, blob: bytes, seq: int) -> bool:
    """Write blob to p atomically (own temp file, then rename). Skips when a later state is already on disk. True when written."""
    with SNAP_IO:
        if seq < SNAP_SEQ[1]:
            return False
        tmp = p.with_name(f"{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_bytes(blob)
            os.chmod(tmp, 0o600)
            tmp.replace(p)
        finally:
            tmp.unlink(missing_ok=True)
        SNAP_SEQ[1] = seq
        return True
