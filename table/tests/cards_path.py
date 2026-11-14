"""Where the tests find card data, test decks and the author's optional comparison folders.

A fresh clone has none of the author's local state (~/.divinci, frozen copies of older tables, the Claude bot).
A test that needs one of them SKIPS with the one command that fixes it (exit code 77, which run_all.sh counts as
skipped, not failed). Nothing here ever reads a real game's decks: test decks are built from card data.

    python3 table/fetch_data.py                one-time download of every card's name and rules text into table/.cache/ (about 40 MB)
    python3 table/fetch_data.py --set trk      …and the Star Trek set's cards into table/.cache/cards-trk.json
    TABLE_CARDS_JSON=<file>                    or point at your own copy of the set
    TABLE_TEST_DECKS=<dir>                     or a folder of deck JSON files (claude.json, sam.json, michael.json)

A test NEVER downloads: run_core.py sets TABLE_OFFLINE=1, under which the table, table/oracle.py and fetch_data.py all refuse to fetch
(until 2026-11-14 a fresh checkout's first test run quietly pulled those 40 MB). A test that starts the table or reads rules text calls
need_card_data() first and skips when the files are not there.
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


CARD_DATA = TABLE / ".cache" / "oracle.json"            # every card's rules text (table/oracle.py); the table builds its name list from it


def need_card_data():
    """Skip this test file unless the card data is on disk. The table does not start without a card-name list, and every lookup of a
    card's text reads the Oracle file; both come from one download that a test must never make."""
    if not CARD_DATA.is_file():
        skip("needs the card data (table/.cache/oracle.json), and a test never downloads it: run `python3 table/fetch_data.py` once "
             "with network access (about 40 MB from Scryfall; after that everything is offline)")


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
