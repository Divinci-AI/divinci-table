"""The director's configuration: which tables, which seats, which key FILES (never keys), and the integrity rules.

    {
      "tables": [
        {"name": "Table 1", "url": "http://127.0.0.1:8800",
         "seats": [{"name": "Michael", "kind": "human", "key_file": "/path/live/michael-seat.key"},
                   {"name": "Claude",  "kind": "ai",    "key_file": "/path/live/claude-seat.key", "player": "rules autopilot"}]}
      ],
      "delay": {"turns": 1, "secs": 0},          # for tables with a person at them; all-AI tables default to live
      "rotation": {"dwell_secs": 45, "min_hold_secs": 10, "recap_secs": 20},
      "art_dir": "~/…/art",  "names_only": false,
      "commentary": {"backend": "off"}
    }

A table with a person at it can never be configured live (delay 0) from the file alone: that takes the command-line flag
--i-know-live-human-hands as well (validate()). A human's hand is never read unless that human switched it on for the current game."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

LOOPBACK = ("127.0.0.1", "localhost", "[::1]")
FORBIDDEN_PORTS = ()          # (the director may read live tables: that is its job. Tests use scratch ports only.)


class ConfigError(ValueError):
    pass


@dataclass
class Seat:
    name: str
    kind: str                  # "human" | "ai"
    key_file: str | None = None
    player: str = ""           # what drives it, for the lower third: "rules autopilot", "anthropic:claude-opus-5-5", "gemma4 (local)"
    show: bool = True          # an AI seat can be kept off the stream


@dataclass
class Table:
    name: str
    url: str
    seats: list
    delay_turns: int | None = None      # None = the default for its kind
    delay_secs: float | None = None

    @property
    def human_involved(self) -> bool:
        return any(s.kind == "human" for s in self.seats)


@dataclass
class Config:
    tables: list
    delay_turns: int = 1
    delay_secs: float = 0.0
    ai_table_delay_turns: int = 0
    dwell_secs: float = 45.0
    min_hold_secs: float = 10.0
    recap_secs: float = 20.0
    action_hold_secs: float = 18.0
    poll_secs: float = 2.0
    art_dir: str | None = None
    names_only: bool = False
    commentary: dict = field(default_factory=dict)
    i_know_live: bool = False

    def delay_for(self, t: Table) -> tuple[int, float]:
        """(turns behind, extra seconds) for this table's private information."""
        if t.human_involved:
            return (self.delay_turns if t.delay_turns is None else t.delay_turns,
                    self.delay_secs if t.delay_secs is None else t.delay_secs)
        return (self.ai_table_delay_turns if t.delay_turns is None else t.delay_turns, t.delay_secs or 0.0)


def _url_ok(u: str) -> bool:
    from urllib.parse import urlparse
    p = urlparse(u)
    return p.scheme == "http" and (p.hostname in ("127.0.0.1", "localhost", "::1")) and bool(p.port) and not p.username and not p.query and not p.fragment


def parse(d: dict, i_know_live: bool = False) -> Config:
    if not isinstance(d.get("tables"), list) or not d["tables"]:
        raise ConfigError("config needs a list of tables")
    tables = []
    for t in d["tables"]:
        if not _url_ok(str(t.get("url", ""))):
            raise ConfigError(f"table {t.get('name')!r}: url must be http://127.0.0.1:<port> with nothing after it (a table on this laptop; never a key in a URL)")
        seats = []
        for s in t.get("seats") or []:
            if s.get("kind") not in ("human", "ai"):
                raise ConfigError(f"seat {s.get('name')!r}: kind must be \"human\" or \"ai\" (it decides consent and delay; there is no default)")
            if "key" in s:
                raise ConfigError("a seat key does not go in the config: give key_file, the path of the 0600 file the launch script wrote")
            seats.append(Seat(name=str(s["name"]), kind=s["kind"], key_file=s.get("key_file"), player=str(s.get("player") or ""),
                              show=bool(s.get("show", True))))
        if len(seats) < 1:
            raise ConfigError(f"table {t.get('name')!r} has no seats")
        dl = t.get("delay") or {}
        tables.append(Table(name=str(t.get("name") or t["url"]), url=str(t["url"]).rstrip("/"), seats=seats,
                            delay_turns=dl.get("turns"), delay_secs=dl.get("secs")))
    dl, ro = d.get("delay") or {}, d.get("rotation") or {}
    c = Config(tables=tables, delay_turns=int(dl.get("turns", 1)), delay_secs=float(dl.get("secs", 0)),
               ai_table_delay_turns=int((d.get("ai_table_delay") or {}).get("turns", 0)),
               dwell_secs=float(ro.get("dwell_secs", 45)), min_hold_secs=float(ro.get("min_hold_secs", 10)),
               recap_secs=float(ro.get("recap_secs", 20)), action_hold_secs=float(ro.get("action_hold_secs", 18)),
               poll_secs=max(1.0, float(d.get("poll_secs", 2.0))), art_dir=d.get("art_dir"), names_only=bool(d.get("names_only", False)),
               commentary=dict(d.get("commentary") or {}), i_know_live=i_know_live)
    validate(c)
    return c


def validate(c: Config) -> None:
    for t in c.tables:
        turns, secs = c.delay_for(t)
        if turns < 0 or secs < 0:
            raise ConfigError("a delay cannot be negative")
        if t.human_involved and turns == 0 and secs < 60 and not c.i_know_live:
            raise ConfigError(
                f"table {t.name!r} has a person at it ({', '.join(s.name for s in t.seats if s.kind == 'human')}) and is configured to show "
                "private information live (0 turns, under 60 s). That hands the opponent's cards to anyone who can see the stream or the laptop. "
                "If you really mean it, start the director with --i-know-live-human-hands as well; the config file alone cannot do this.")
    if c.dwell_secs < 5:
        raise ConfigError("rotation.dwell_secs under 5 is a strobe, not a broadcast")


def load(path: str, i_know_live: bool = False) -> Config:
    p = Path(path).expanduser()
    return parse(json.loads(p.read_text()), i_know_live=i_know_live)


def read_secret_file(path: str, what: str) -> str:
    """A seat key or an API key from a private file. Refuses a file other users can read. The value is returned to the caller and is
    never logged, printed or put in an exception by this module."""
    p = Path(path).expanduser()
    try:
        st = p.stat()
    except OSError:
        raise ConfigError(f"{what}: no such file (the path is in the config; the launch script writes it)") from None
    if st.st_mode & 0o077:
        raise ConfigError(f"{what}: the file must be private (chmod 600)")
    if st.st_uid != os.getuid():
        raise ConfigError(f"{what}: the file belongs to another user")
    v = p.read_text().strip()
    if not v or "\n" in v or len(v) > 400:
        raise ConfigError(f"{what}: the file does not hold one key")
    return v
