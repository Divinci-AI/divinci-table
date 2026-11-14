"""A port nobody is listening on, for a test's own scratch server.

Until 2026-11-14 a dozen tests each had a port number written into them (8831 shared by six files, 8817 and 8818 by three), so two runs
of the suite on one machine (two people, two agents, or just `run_core.py -j 2`) knocked each other's servers over and the failures
looked like bugs in the table. A test now asks the operating system for a port:

    import freeport
    PORT = freeport.free_port()             one port
    BASE = freeport.free_block(12)          the first of 12 ports in a row that are all free (for a tool that takes a base port)

The port is free at the moment of asking, not reserved: start the server straight away. A port a live game may use (8800, 8801, 8820,
8830, 8883: the table, a second table, the draft, the stage, a fake model) is never returned, whatever the system offers.
Not a test itself (run_core.py runs only *_test.py)."""
from __future__ import annotations

import socket

LIVE_PORTS = frozenset({8800, 8801, 8820, 8830, 8883})


def _bindable(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def free_port() -> int:
    """A free TCP port on 127.0.0.1 from the system's own range, never one a live game uses."""
    while True:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        finally:
            s.close()
        if port not in LIVE_PORTS:
            return port


def free_block(n: int = 2) -> int:
    """The first port of `n` consecutive free ports (none of them a live game's). For a tool that is given a base port and counts up."""
    for _ in range(200):
        base = free_port()
        if base + n < 65536 and all(p not in LIVE_PORTS and _bindable(p) for p in range(base, base + n)):
            return base
    raise OSError(f"no run of {n} free ports found")
