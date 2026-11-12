"""Add one line to the game-night retro log (../RETRO-LOG.md), under "Added during play".
    python3 retro_note.py "Title" --manual "what was done by hand" --cause "why it happened" --fix "what would remove it"
Nothing secret goes in here: no keys, no hands, no library contents."""
import argparse, time
from pathlib import Path

LOG = Path(__file__).resolve().parent.parent / "RETRO-LOG.md"
HEAD = "## Added during play"

ap = argparse.ArgumentParser()
ap.add_argument("title"); ap.add_argument("--manual", default=""); ap.add_argument("--cause", default=""); ap.add_argument("--fix", default="")
a = ap.parse_args()
text = LOG.read_text()
if HEAD not in text:
    text = text.rstrip("\n") + f"\n\n{HEAD}\n"
line = f"- {time.strftime('%Y-%m-%d %H:%M')} **{a.title}**" + "".join(f"; {k}: {v}" for k, v in (("manual", a.manual), ("cause", a.cause), ("fix", a.fix)) if v)
LOG.write_text(text.rstrip("\n") + "\n" + line + "\n")
print("logged:", line[:140])
