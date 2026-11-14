"""For a seat's player (an LLM agent): say why you did what you did, privately, so a broadcast director can show it.

    from seat_thoughts import think
    think(BASE, SEAT, KEY, "Holding the removal for his commander; the 2/2 is enough pressure for now.")

It is one POST to /api/seat/thought with the seat's own key. The table keeps it private to that seat (docs/broadcast-director.md):
it is never in the public log. At most 600 characters, about one every two seconds; a failure is ignored (thinking aloud must never
stop a move). Post the reason the model actually gave for the move it actually made: nothing is added or tidied here."""
from __future__ import annotations

import json
import urllib.request


def think(base: str, seat: str, key: str, text: str, timeout: float = 4.0) -> bool:
    text = " ".join(str(text or "").split())[:600]
    if not text:
        return False
    r = urllib.request.Request(base.rstrip("/") + "/api/seat/thought", method="POST", data=json.dumps({"seat": seat, "text": text}).encode(),
                               headers={"Content-Type": "application/json", "X-Seat-Key": key, "User-Agent": "divinci-table-agent"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as x:
            return x.status == 200
    except Exception:  # noqa: BLE001
        return False
