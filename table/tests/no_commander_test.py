"""A deck with no commander (a 40-card 1-vs-1 or sealed deck) plays beside Commander decks.

    ~/.venvs/table/bin/python table/tests/no_commander_test.py

Builds a no-commander copy of an existing Commander deck (the same cards, no commander), then checks the
seat's starting life, that no command-zone path is taken, and that a commander-only request is refused
cleanly rather than crashing. Commander decks keep 40 life and their command zone.
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import player  # noqa: E402

_fails: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ✅ " if ok else "  ❌ ") + what, flush=True)
    if not ok:
        _fails.append(what)


def done() -> int:
    print(f"no_commander_test: {'ok' if not _fails else str(len(_fails)) + ' failed'}")
    return 1 if _fails else 0

DECK = HERE.parent.parent / "decks" / "kaust.json"
tmp = Path(tempfile.mkdtemp(prefix="no-commander-"))


def write_deck(name: str, commander, extra: dict | None = None) -> str:
    d = json.loads(DECK.read_text())
    d["name"] = name
    d["commander"] = commander
    d.update(extra or {})
    p = tmp / f"{name}.json"
    p.write_text(json.dumps(d))
    return str(p)


cmdr_path = write_deck("commander", json.loads(DECK.read_text())["commander"])
bare_path = write_deck("bare", None)
custom_path = write_deck("custom-life", None, {"startingLife": 30})

cmdr = player.VirtualPlayer(cmdr_path, "Cmdr", seed=1)
bare = player.VirtualPlayer(bare_path, "Bare", seed=1)
custom = player.VirtualPlayer(custom_path, "Custom", seed=1)

check(cmdr.life == 40, f"a Commander deck still starts at 40 (got {cmdr.life})")
check(cmdr.commander is not None and cmdr.cmdr_in_zone, "a Commander deck starts with its commander in the command zone")
check(bare.life == 20, f"a no-commander deck starts at 20 (got {bare.life})")
check(bare.commander is None and bare.cmdr_in_zone is False, "a no-commander deck has no command zone")
check(custom.life == 30, f"a deck's startingLife is honoured (got {custom.life})")

state = bare.public()
check(state["commander"] is None and state["commander_in_zone"] is False and state["commander_tax"] == 0,
      "the public state reports no commander and no tax for a no-commander seat")
check(state["commander_card"] is None if "commander_card" in state else True,
      "the private view carries no commander card")

options = bare.castable()
check(not any(is_cmdr for _label, _c, _pay, is_cmdr in options), "no castable option is a commander cast")

try:
    bare.manual_cast("commander")
    check(False, "a commander cast from a no-commander deck is refused")
except player.IllegalAction as e:
    check("no commander" in str(e) or "command zone" in str(e), f"a commander cast is refused cleanly ({e})")
except Exception as e:           # any other exception is a crash, which is a failure
    check(False, f"a commander cast crashed instead of being refused: {type(e).__name__}: {e}")

colour = bare.missing_color()
check(colour in "WUBRG", f"missing_color works without a commander (got {colour!r})")

sys.exit(done())
