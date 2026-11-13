#!/usr/bin/env bash
# Make the build context for the room host's image from a commit that is on GitHub (docs/room-host.md).
#
#   scripts/roomhost_bundle.sh [out.tgz]        # default: /tmp/divinci-table-<sha10>.tgz
#
# The image needs two things git does not hold: the card rules text (table/.cache/oracle.json, built once by
# `python3 table/fetch_data.py`) and the commit it was built from (table/BUILD_SHA, shown by /api/version in every
# room). This writes `git archive HEAD` plus those two into one file. On the server:
#
#   mkdir src && tar -xzf divinci-table-<sha10>.tgz -C src && cd src
#   docker build -f cloud/Dockerfile -t divinci-table:<sha10> .
#
# Like scripts/cloud_deploy.sh it refuses uncommitted changes and a HEAD that is not on origin/main, so what runs
# on the server is what anyone can read.
set -euo pipefail
cd "$(dirname "$0")/.."

git fetch -q origin
if [ -n "$(git status --porcelain)" ]; then
  echo "refusing: uncommitted or untracked changes (git status)" >&2; git status --short >&2; exit 1
fi
if ! git merge-base --is-ancestor HEAD origin/main; then
  echo "refusing: HEAD $(git rev-parse --short HEAD) is not on origin/main; push it first" >&2; exit 1
fi
[ -f table/.cache/oracle.json ] || { echo "refusing: table/.cache/oracle.json is missing (python3 table/fetch_data.py)" >&2; exit 1; }
sha=$(git rev-parse HEAD)
out=${1:-/tmp/divinci-table-${sha:0:10}.tgz}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git archive HEAD | tar -x -C "$tmp"
mkdir -p "$tmp/table/.cache"
cp -L table/.cache/oracle.json "$tmp/table/.cache/oracle.json"
printf '%s\n' "$sha" > "$tmp/table/BUILD_SHA"
tar -C "$tmp" -czf "$out" .
echo "ROOMHOST-BUNDLE $sha $out ($(du -h "$out" | cut -f1))"
