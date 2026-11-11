"""Where the tests find card data, test decks and the author's optional comparison folders.

A fresh clone has none of the author's local state (~/.divinci, frozen copies of older tables, the Claude bot).
A test that needs one of them SKIPS with the one command that fixes it (exit code 77, which run_all.sh counts as
skipped, not failed). Nothing here ever reads a real game's decks: test decks are built from card data.

    python3 table/fetch_data.py --set trk      one-time download of the Star Trek set's cards into table/.cache/cards-trk.json
    TABLE_CARDS_JSON=<file>                    or point at your own copy
    TABLE_TEST_DECKS=<dir>                     or a folder of deck JSON files (claude.json, sam.json, michael.json)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
TABLE = TESTS.parent
SKIP_CODE = 77


def skip(why: str):
    """Stop this test file as skipped. The message goes to stderr so `$(python fixture_decks.py)` stays clean."""
    print(f"SKIP: {why}", file=sys.stderr, flush=True)
    sys.exit(SKIP_CODE)


def cards_json() -> str:
    """The Star Trek set's cards (Scryfall-shaped list). First of: $TABLE_CARDS_JSON, table/.cache/cards-trk.json, the author's copy."""
    for c in (os.environ.get("TABLE_CARDS_JSON"), TABLE / ".cache" / "cards-trk.json", Path.home() / ".divinci" / "star-trek-mtg" / "cards.json"):
        if c and Path(c).expanduser().is_file():
            return str(Path(c).expanduser())
    skip("needs the Star Trek set's card data: run `python3 table/fetch_data.py --set trk` once (or set TABLE_CARDS_JSON=<file>)")


def decks_dir() -> Path:
    """A folder of 40-card test decks: $TABLE_TEST_DECKS, else built fresh from the card data (never a real game's decks)."""
    d = os.environ.get("TABLE_TEST_DECKS")
    if d and Path(d).is_dir():
        return Path(d)
    sys.path.insert(0, str(TESTS))
    import fixture_decks
    return fixture_decks.write(Path(tempfile.mkdtemp(prefix="testdecks-")))


def need(path, what: str, how: str = ""):
    """Skip this test file unless an optional file or folder exists (a frozen older table, the bot, ...)."""
    p = Path(path).expanduser()
    if not p.exists():
        skip(f"needs {what} at {p}" + (f" ({how})" if how else ""))
    return p
