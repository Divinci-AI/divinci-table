"""The game-over research bundle (R3 in docs/RESEARCH-ENGINE-GOAL.md): one folder per game with everything a
researcher needs and nothing that was private.

  python3 table/bundle.py --game 20261002-220213 [--out DIR] [--research-dir DIR]

IN the bundle: the public event log (which carries the fair-randomness proof events), the journals
(only once a `game-over` event exists: they are sealed until then and can name hidden cards), every survey, the
audit report (event ids only, no card names), and a manifest naming every seat and the exact model, provider and
prompt version it DECLARED (`tablectl identity`). A seat that declared nothing is listed as UNDECLARED, never guessed.
Left out unless asked (`--include-notes`): integrity-notes.jsonl, because a fork's notes record the hands.
NEVER in the bundle: brain.jsonl (a seat's private actions and hands), snapshots (hands and libraries), photos (they
can show people), board dumps and hand-off notes. The manifest lists every file with its sha256 and every file left
out with the reason, so a bundle explains itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import audit  # noqa: E402

BUNDLE_VERSION = "1.0"
RESEARCH = Path(os.environ.get("TABLE_RESEARCH_DIR") or Path(__file__).parent / ".cache" / "research")
PRIVATE = {"brain.jsonl": "a seat's private actions and hands",
           "snapshot.pkl": "snapshots hold hands and libraries"}
PRIVATE_PATTERNS = {"snapshot-": "snapshots hold hands and libraries", "board3d-": "board dump taken before a fork",
                    "HANDOFF": "a hand-off note between agents"}


def _lines(path: Path) -> list[dict]:
    out = []
    for l in path.read_text().splitlines():
        try:
            out.append(json.loads(l))
        except json.JSONDecodeError:
            pass
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy(src: Path, dst: Path, files: list, rel: str):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o644)
    files.append({"path": rel, "sha256": _sha(dst), "bytes": dst.stat().st_size})


def build(game: str, out: Path | None = None, research: Path | None = None, include_notes: bool = False) -> Path:
    src = (research or RESEARCH) / game
    if not src.is_dir():
        raise FileNotFoundError(f"no research folder for game {game!r} in {research or RESEARCH}")
    dest = (out or src.parent / "bundles") / f"{game}-bundle"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    files: list[dict] = []
    left_out: list[dict] = []

    events_p = src / "events.jsonl"
    events = _lines(events_p) if events_p.exists() else []
    over = next((e for e in events if e.get("type") == "game-over"), None)
    if events_p.exists():
        _copy(events_p, dest / "events.jsonl", files, "events.jsonl")
    else:
        left_out.append({"path": "events.jsonl", "reason": "not present in the research folder"})

    notes = src / "integrity-notes.jsonl"
    if notes.exists():
        if include_notes:
            _copy(notes, dest / "integrity-notes.jsonl", files, "integrity-notes.jsonl")
        else:        # a fork's notes list the hands as they were: checked on a real game, 3 of 15 names were not public
            left_out.append({"path": "integrity-notes.jsonl",
                             "reason": "can name cards still in a hand (a fork records the hands); --include-notes adds it"})

    for j in sorted(src.glob("journal-*.jsonl")):
        if over:
            _copy(j, dest / "journals" / j.name, files, f"journals/{j.name}")
        else:
            left_out.append({"path": j.name, "reason": "sealed: no game-over event, so journals stay private"})

    for sv in sorted(src.glob("survey-*.json")):
        _copy(sv, dest / "surveys" / sv.name, files, f"surveys/{sv.name}")

    for name, why in PRIVATE.items():
        if (src / name).exists():
            left_out.append({"path": name, "reason": why})
    for p in sorted(src.iterdir()):
        for pat, why in PRIVATE_PATTERNS.items():
            if p.name.startswith(pat) and p.name not in PRIVATE:
                left_out.append({"path": p.name, "reason": why})
        if p.is_dir() and p.name == "photos":
            left_out.append({"path": "photos/", "reason": "photos can show people"})

    rep = audit.report(src)
    (dest / "audit.json").write_text(json.dumps(rep, indent=1, default=str))
    files.append({"path": "audit.json", "sha256": _sha(dest / "audit.json"), "bytes": (dest / "audit.json").stat().st_size})

    ident_p = src / "identity.json"
    ident = json.loads(ident_p.read_text()) if ident_p.exists() else {"harness": None, "seats": {}}
    seat_names = set(ident.get("seats", {}))
    for r in _lines(src / "brain.jsonl") if (src / "brain.jsonl").exists() else []:
        if isinstance(r.get("seat"), str):
            seat_names.add(r["seat"])
    for j in src.glob("journal-*.jsonl"):
        seat_names.update(r["seat"] for r in _lines(j) if isinstance(r.get("seat"), str))
    for sv in src.glob("survey-*.json"):
        try:
            seat_names.add(json.loads(sv.read_text()).get("seat"))
        except (json.JSONDecodeError, OSError):
            pass
    seat_names.discard(None)
    seats = {}
    for n in sorted(seat_names):
        d = ident.get("seats", {}).get(n)
        seats[n] = ({"identity": "declared", **{k: d.get(k) for k in ("model", "provider", "prompt_version", "notes") if d.get(k)}}
                    if d else {"identity": "UNDECLARED"})

    manifest = {"bundle_version": BUNDLE_VERSION, "game": game, "built_ts": round(time.time(), 2),
                "game_over": ({"winner": over.get("winner"), "order": over.get("order")} if over else None),
                "journals_sealed": not over, "harness": ident.get("harness"), "seats": seats,
                "undeclared_seats": [n for n, v in seats.items() if v["identity"] == "UNDECLARED"],
                "files": files, "left_out": left_out,
                "note": "Humans-at-the-table and all-AI games are separate conditions; do not pool them. "
                        "An UNDECLARED seat cannot be pinned to a model and is excluded from any rating."}
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (dest / "README.txt").write_text(
        f"Research bundle for game {game} (bundle version {BUNDLE_VERSION}).\n"
        "manifest.json lists every file with its sha256, every seat with the identity it declared, and every file\n"
        "that was left out and why. Nothing private (hands, libraries, photos, brain logs) is included.\n")
    return dest


def load(path: Path) -> dict:
    """A bundle as a dict, verifying every file's sha256 against the manifest (a changed file raises)."""
    m = json.loads((path / "manifest.json").read_text())
    for f in m["files"]:
        if _sha(path / f["path"]) != f["sha256"]:
            raise ValueError(f"{f['path']} does not match the manifest")
    return {"manifest": m, "events": _lines(path / "events.jsonl") if (path / "events.jsonl").exists() else [],
            "audit": json.loads((path / "audit.json").read_text()),
            "journals": {p.stem: _lines(p) for p in sorted((path / "journals").glob("*.jsonl"))} if (path / "journals").exists() else {},
            "surveys": {p.name: json.loads(p.read_text()) for p in sorted((path / "surveys").glob("*.json"))} if (path / "surveys").exists() else {}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True)
    ap.add_argument("--out")
    ap.add_argument("--research-dir")
    ap.add_argument("--include-notes", action="store_true", help="add integrity-notes.jsonl (may name hidden cards)")
    a = ap.parse_args()
    d = build(a.game, Path(a.out) if a.out else None, Path(a.research_dir) if a.research_dir else None, a.include_notes)
    m = json.loads((d / "manifest.json").read_text())
    print(f"bundle: {d}\n  files: {len(m['files'])}  left out: {len(m['left_out'])}  seats: {len(m['seats'])}"
          f"  undeclared: {m['undeclared_seats'] or 'none'}  journals sealed: {m['journals_sealed']}")
