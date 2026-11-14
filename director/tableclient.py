"""A READ-ONLY client for one table. It can GET a short allow-list of paths and nothing else: there is no way to POST through it, so a
director holding a person's seat key cannot play, speak or concede for them (director_integrity_test checks this file has no POST).

Keys: read from their 0600 file at the moment of use, sent only in the X-Seat-Key header to a loopback table, never in a URL, never
stored on an object that is serialised, never in an exception or a log line (errors are reduced to a status code and a fixed phrase)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config as cfg

PUBLIC = ("/api/phase", "/api/events", "/api/spectate", "/api/game/summary", "/api/stream-consent", "/api/board3d", "/api/match", "/api/life",
          "/api/voice-config")
PRIVATE = ("/api/brain/state", "/api/seat/legal", "/api/seat/thought")     # each needs ?seat=X and X's own key
UA = "divinci-table-director"


class TableError(Exception):
    """What went wrong, with nothing secret in it: a status code and a fixed phrase."""
    def __init__(self, code: int, what: str):
        super().__init__(f"{what} ({code})")
        self.code = code


class TableClient:
    def __init__(self, base: str, timeout: float = 6.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.requests = 0

    def _get(self, path: str, query: str, headers: dict):
        self.requests += 1
        r = urllib.request.Request(self.base + path + (("?" + query) if query else ""), method="GET", headers={"User-Agent": UA, **headers})
        try:
            with urllib.request.urlopen(r, timeout=self.timeout) as x:
                return json.loads(x.read() or b"{}")
        except urllib.error.HTTPError as e:
            raise TableError(e.code, "the table refused " + path) from None      # (from None: the request, with its header, is not chained)
        except (urllib.error.URLError, OSError, ValueError):
            raise TableError(0, "the table did not answer " + path) from None

    def public(self, path: str, query: str = ""):
        if path not in PUBLIC:
            raise TableError(-1, "not a public path the director reads")
        return self._get(path, query, {})

    def private(self, path: str, seat: str, key_file: str, extra: str = ""):
        """A seat's own state, with that seat's key. The caller must already hold integrity's permission to read this seat."""
        if path not in PRIVATE:
            raise TableError(-1, "not a private path the director reads")
        from urllib.parse import quote
        key = cfg.read_secret_file(key_file, f"seat key for {seat}")
        try:
            return self._get(path, "seat=" + quote(seat) + (("&" + extra) if extra else ""), {"X-Seat-Key": key})
        finally:
            del key
