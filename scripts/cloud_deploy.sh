#!/usr/bin/env bash
# Deploy table.divinci.ai (the Worker + the room container image) from a commit that is on GitHub.
#
#   scripts/cloud_deploy.sh            # check, stamp, deploy
#   scripts/cloud_deploy.sh --check    # the checks only; deploys nothing
#
# Refuses a working tree with uncommitted or untracked changes, and a HEAD that isn't on origin/main: the
# image is built from this folder, so either would put unpublished code on the live site of an open-source
# game (152 commits had drifted by 2026-10-05). Stamps the commit into the Worker (GIT_SHA → /version) and
# the image (table/BUILD_SHA → /api/version in every room). Verify afterwards with scripts/cloud_smoke.cjs.
# Run it through the deploy gate on this laptop:
#   ~/Documents/server/scripts/deploy-gate run --target worker:divinci-table:production -- scripts/cloud_deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

git fetch -q origin
if [ -n "$(git status --porcelain)" ]; then
  echo "refusing: uncommitted or untracked changes (git status)" >&2; git status --short >&2; exit 1
fi
if ! git merge-base --is-ancestor HEAD origin/main; then
  echo "refusing: HEAD $(git rev-parse --short HEAD) is not on origin/main; push it first" >&2; exit 1
fi
sha=$(git rev-parse HEAD)
echo "deploying $sha ($(git log -1 --format=%s))"
[ "${1:-}" = "--check" ] && { echo "check only: nothing deployed"; exit 0; }

printf '%s\n' "$sha" > table/BUILD_SHA          # gitignored; COPY table/ puts it in the image
cd cloud
env -u FORCE_COLOR -u CLOUDFLARE_API_TOKEN npx wrangler deploy --var "GIT_SHA:$sha"
echo "CLOUD-DEPLOYED $sha"
