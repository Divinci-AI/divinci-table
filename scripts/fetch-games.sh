#!/usr/bin/env bash
# Pull a run's game logs (+ replay viewer) from R2 into harness/ and verify every file.
#   scripts/fetch-games.sh              # default run 2026-09-24
#   scripts/fetch-games.sh 2026-09-24
# Bucket divinci-experiments is private; uses wrangler's OAuth login. CLOUDFLARE_API_TOKEN is
# unset for the call because the account's API tokens lack R2 access and would override OAuth.
set -euo pipefail
RUN="${1:-2026-09-24}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for f in "commander-games-$RUN.tar.gz" MANIFEST.sha256; do
  env -u CLOUDFLARE_API_TOKEN npx -y wrangler@4 r2 object get \
    "divinci-experiments/commander/$RUN/$f" --file "$TMP/$f" --remote >/dev/null
done
tar xzf "$TMP/commander-games-$RUN.tar.gz" -C "$ROOT/harness"
cd "$ROOT/harness"
shasum -a 256 -c "$TMP/MANIFEST.sha256" --quiet
echo "fetched run $RUN: $(ls games | wc -l | tr -d ' ') game logs + viewer.html, all checksums OK"
