#!/usr/bin/env python3
"""Export the divinci.ai/table waitlist (Workers KV) to a CSV for Attio's import (ROADMAP M7).

    python3 scripts/export-waitlist.py              # production namespace → table/.cache/waitlist-YYYY-MM-DD.csv
    python3 scripts/export-waitlist.py --staging

The CSV holds email addresses, so it is written 0600 under table/.cache (gitignored) and never printed;
the script prints only counts. Import it in Attio (People → Import CSV, list "Divinci Table waitlist").
Nobody is emailed by this script: invitations go out in batches, sent by a person.
Needs wrangler logged in (`npx wrangler login`); run from anywhere.
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NS = {e: os.environ.get(f"WAITLIST_KV_{e.upper()}", "") for e in ("production", "staging")}   # ids live with the divinci.ai Worker's config, not this public repo


def wrangler(*args: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != "CLOUDFLARE_API_TOKEN"}   # use the OAuth login
    r = subprocess.run(["npx", "wrangler", *args], cwd=ROOT / "cloud", env=env, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"wrangler failed ({r.returncode}): {r.stderr.strip()[-300:]}")
    return r.stdout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", action="store_true")
    env = "staging" if ap.parse_args().staging else "production"
    if not NS[env]:
        sys.exit(f"set WAITLIST_KV_{env.upper()} to the waitlist KV namespace id (the divinci.ai Worker's wrangler config)")
    keys = [k["name"] for k in json.loads(wrangler("kv", "key", "list", "--namespace-id", NS[env], "--remote", "--prefix", "signup:"))]
    rows = []
    for k in keys:
        rec = json.loads(wrangler("kv", "key", "get", k, "--namespace-id", NS[env], "--remote", "--text"))
        rows.append({"email": rec.get("email", ""), "games": ";".join(rec.get("games") or []), "roles": ";".join(rec.get("roles") or []),
                     "country": rec.get("country") or "", "first_signup": rec.get("first_signup", ""), "last_signup": rec.get("last_signup", "")})
    out = ROOT / "table" / ".cache" / f"waitlist-{env}-{time.strftime('%Y-%m-%d')}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["email", "games", "roles", "country", "first_signup", "last_signup"])
        w.writeheader()
        w.writerows(rows)
    print(f"{env}: {len(rows)} sign-ups → {out.relative_to(ROOT)} (0600, gitignored)")


if __name__ == "__main__":
    main()
