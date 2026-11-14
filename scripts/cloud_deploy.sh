#!/usr/bin/env bash
# Deploy table.divinci.ai (the Worker + the room container image) from a commit that is on GitHub.
#
#   scripts/cloud_deploy.sh            # check, stamp, deploy
#   scripts/cloud_deploy.sh --check    # the checks only; deploys nothing
#   ROOM_HOST_VPC_SERVICE_ID=<uuid> scripts/cloud_deploy.sh --host     # or ROOM_HOST_ORIGIN=https://rooms.example.com
#                                      # rooms on a room host (cloud/wrangler.host.jsonc, docs/room-host.md): the Worker
#                                      # only; the room host's image is built and started on its own machine
#
# Refuses a working tree with uncommitted or untracked changes, and a HEAD that isn't on origin/main: the
# image is built from this folder, so either would put unpublished code on the live site of an open-source
# game (152 commits had drifted by 2026-10-05). Stamps the commit into the Worker (GIT_SHA → /version) and
# the image (table/BUILD_SHA → /api/version in every room). Verify afterwards with scripts/cloud_smoke.cjs.
#
# Live table.divinci.ai runs in HOST mode: always pass --host there. The plain form is the old container path and would
# rebind its rooms to Cloudflare Containers. docs/deploy.md has the whole order (room host first, then this) and the
# rollback for both halves.
#
# If you run deploys through a lock or gate of your own, wrap this script in it, e.g.
#   "$DEPLOY_GATE" run --target worker:divinci-table:production -- scripts/cloud_deploy.sh --host
# Nothing here depends on such a gate; DEPLOY_GATE is only the name these docs use for yours.
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
mode=containers
for a in "$@"; do
  case "$a" in
    --check) echo "check only: nothing deployed"; exit 0 ;;
    --host) mode=host ;;
    *) echo "unknown argument: $a" >&2; exit 1 ;;
  esac
done

cd cloud
if [ "$mode" = host ]; then
  if [[ "${ROOM_HOST_VPC_SERVICE_ID:-}" =~ ^[0-9a-f-]{36}$ ]]; then
    # the private path: the committed config plus the binding, written next to it (paths in it are relative) and removed after
    cfg=.wrangler.host.deploy.json
    trap 'rm -f "$cfg"' EXIT
    node -e '
      const fs = require("fs");
      const src = fs.readFileSync("wrangler.host.jsonc", "utf8").replace(/^\s*\/\/.*$/gm, "");
      const c = JSON.parse(src);
      c.vpc_services = [{ binding: "ROOM_HOST", service_id: process.argv[1] }];
      fs.writeFileSync(process.argv[2], JSON.stringify(c, null, 2));
    ' "$ROOM_HOST_VPC_SERVICE_ID" "$cfg"
    env -u FORCE_COLOR -u CLOUDFLARE_API_TOKEN npx wrangler deploy -c "$cfg" --var "GIT_SHA:$sha"
  elif [[ "${ROOM_HOST_ORIGIN:-}" == https://* ]]; then
    env -u FORCE_COLOR -u CLOUDFLARE_API_TOKEN npx wrangler deploy -c wrangler.host.jsonc --var "GIT_SHA:$sha" --var "ROOM_HOST_ORIGIN:$ROOM_HOST_ORIGIN"
  else
    echo "refusing: --host needs ROOM_HOST_VPC_SERVICE_ID=<uuid> or ROOM_HOST_ORIGIN=https://<your room host>" >&2; exit 1
  fi
else
  printf '%s\n' "$sha" > ../table/BUILD_SHA      # gitignored; COPY table/ puts it in the image
  env -u FORCE_COLOR -u CLOUDFLARE_API_TOKEN npx wrangler deploy --var "GIT_SHA:$sha"
fi
echo "CLOUD-DEPLOYED $sha ($mode)"
