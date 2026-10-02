"""Provably fair shuffles for the AI's deck: commit before the first draw, reveal after the game.

The AI is the only player holding hidden cards, so its shuffle is the one that has to be provable.

  seed   = SHA-256 of every input: local entropy, each player's secret word and, optionally, an ANU
           quantum number and a drand round. Each input is sealed in before the shuffle.
  order  = Fisher-Yates driven by SHA-256(seed ‖ i). Not Python's random: that may differ between
           Python versions, and a verifier on another machine must get the identical order.
  commit = SHA-256(seed ‖ the library order, one card per line), published at the start.

After the game the record is revealed (seed inputs + order). Anyone can recompute it with
`fair.py verify FILE`, or tablectl fair verify. The seed mixes in the players' own words, so the AI
(or whoever runs the server) couldn't have chosen the order; the commit came first, so it couldn't be
changed afterwards.

  ~/.venvs/table/bin/python table/fair.py verify table/.cache/fairness/<game>-<seat>.json
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

DIR = Path(__file__).parent / ".cache" / "fairness"
VERSION = "divinci-fair-1"


def make_seed(parts: dict[str, str]) -> str:
    """Hex seed from named inputs. Canonical JSON, so the same inputs always give the same seed."""
    blob = json.dumps({"v": VERSION, "parts": parts}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def fair_order(names: list[str], seed: str) -> list[int]:
    """A permutation of range(len(names)) from the seed: Fisher-Yates with SHA-256 per step."""
    idx = list(range(len(names)))
    for i in range(len(idx) - 1, 0, -1):
        h = hashlib.sha256(f"{seed}:{i}".encode()).digest()
        j = int.from_bytes(h, "big") % (i + 1)          # 256-bit draw: modulo bias is negligible
        idx[i], idx[j] = idx[j], idx[i]
    return idx


def commitment(seed: str, library: list[str]) -> str:
    return hashlib.sha256((seed + "\n" + "\n".join(library)).encode()).hexdigest()


def fingerprint(commit: str) -> str:
    """What the table hears: the first 8 hex digits, read in two groups."""
    return f"{commit[:4].upper()} {commit[4:8].upper()}"


def online_parts(timeout: float = 4.0) -> dict[str, str]:
    """Optional public randomness (only with --fair-seed online): an ANU quantum number and the latest
    drand round, which Cloudflare co-runs in the League of Entropy. Best effort: a source that doesn't
    answer is left out and named in the record."""
    out = {}
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            "https://qrng.anu.edu.au/API/jsonI.php?length=16&type=uint8",
            headers={"User-Agent": "divinci-table/0.1"}), timeout=timeout).read().decode()
        d = json.loads(r)
        if d.get("success"):
            out["anu_qrng"] = bytes(d["data"]).hex()
    except Exception as e:                                       # noqa: BLE001
        out["anu_qrng_error"] = type(e).__name__
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            "https://drand.cloudflare.com/public/latest",
            headers={"User-Agent": "divinci-table/0.1"}), timeout=timeout).read().decode()
        d = json.loads(r)
        out["drand_round"] = str(d["round"])
        out["drand_randomness"] = d["randomness"]
    except Exception as e:                                       # noqa: BLE001
        out["drand_error"] = type(e).__name__
    return out


def seal(seat: str, deck_names: list[str], words: dict[str, str] | None = None,
         online: bool = False) -> dict:
    """Seed, order and commit for one AI seat. Returns the full (secret) record; the public part is
    {"seat", "commit", "fingerprint", "sealed_at", "inputs"} where inputs lists what went in by NAME."""
    parts = {"seat": seat, "local": os.urandom(32).hex()}
    for who, w in sorted((words or {}).items()):
        parts[f"word:{who}"] = w
    if online:
        parts.update(online_parts())
    seed = make_seed(parts)
    canon = sorted(deck_names)                    # the order depends on the seed and the deck, nothing else
    perm = fair_order(canon, seed)
    library = [canon[i] for i in perm]
    commit = commitment(seed, library)
    return {"version": VERSION, "seat": seat, "parts": parts, "seed": seed, "library": library,
            "commit": commit, "fingerprint": fingerprint(commit), "sealed_at": round(time.time(), 1),
            "inputs": sorted(parts)}


def public(record: dict) -> dict:
    return {k: record[k] for k in ("seat", "commit", "fingerprint", "sealed_at", "inputs")}


def save(record: dict, game: str) -> Path:
    DIR.mkdir(parents=True, exist_ok=True)
    p = DIR / f"{game}-{record['seat']}.json"
    p.write_text(json.dumps(record, indent=1))
    os.chmod(p, 0o600)                     # secret until revealed: the players' words and the seed
    return p


def verify(record: dict, deck_names: list[str] | None = None) -> tuple[bool, str]:
    """Recompute everything from the revealed inputs."""
    seed = make_seed(record["parts"])
    if seed != record["seed"]:
        return False, "the seed doesn't match its inputs"
    names = deck_names if deck_names is not None else sorted(record["library"])
    if sorted(names) != sorted(record["library"]):
        return False, "the library isn't the declared deck"
    # the order is a function of the seed and the deck in its canonical (sorted) listing
    perm = fair_order(sorted(names), seed)
    library = [sorted(names)[i] for i in perm]
    if library != record["library"]:
        return False, "the library order isn't the one the seed produces"
    if commitment(seed, library) != record["commit"]:
        return False, "the commit doesn't match"
    return True, f"verified: {record['seat']}'s deck order was fixed by commit {record['fingerprint']}"


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "verify":
        ok, msg = verify(json.loads(Path(sys.argv[2]).read_text()))
        print(("✅ " if ok else "❌ ") + msg)
        sys.exit(0 if ok else 1)
    print(__doc__)
