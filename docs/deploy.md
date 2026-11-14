# Deploying and rolling back the public lobby (host mode)

`docs/room-host.md` sets a room host up the first time. This page is what to do every time after that: ship a commit,
check it, and undo it. It is written for the way table.divinci.ai runs (host mode: `cloud/wrangler.host.jsonc`, rooms on
a room host reached through a Workers VPC service), with placeholders where yours differ:

| placeholder | what it is |
|---|---|
| `<sha>` / `<sha10>` | the full commit id / its first ten characters |
| `<room host>` | the ssh name of the machine running `cloud/roomhost.py` |
| `<room host dir>` | its folder: `compose.yaml`, `roomhost.key`, `build/`, `data/`, `assets/` |
| `<vpc service id>` | the Workers VPC service the Worker reaches the room host through |
| `$DEPLOY_GATE` | your own deploy lock or gate, if you use one (nothing here needs it) |

## What there is to deploy

Two halves, deployed separately:

| half | what changes it | how it ships |
|---|---|---|
| **room host image** `divinci-table:<sha10>` | `table/`, `bots/` (the house player), `cloud/roomhost.py`, `cloud/entrypoint.py`, `cloud/Dockerfile` | built on the room host, then `docker compose up -d` |
| **lobby Worker** | `cloud/src/`, `cloud/public/`, `cloud/wrangler.host.jsonc` | `scripts/cloud_deploy.sh --host` (wrangler) |

A commit that touches both needs both. Only commits on `origin/main` ship: both scripts refuse a dirty tree and a HEAD
that is not on `origin/main`, so what runs is what anyone can read. Work from a clean clone at that commit.

⚠️ Always `--host`. Plain `scripts/cloud_deploy.sh` is the container mode (`cloud/wrangler.jsonc`): it rebinds the
rooms to Cloudflare Containers, which a host-mode lobby doesn't have, and every room stops working.

## Order: room host first, then the Worker straight after

The room host must keep working with the Worker that is live while it restarts, and the new Worker may rely on the new
room host. So the box goes first and the Worker follows within minutes. Rolling back goes the other way round.

Before you start, note what is live now, because that is what you roll back to:

    curl -s https://table.divinci.ai/version                       # {"worker": "<sha>"}: the Worker's commit
    ssh <room host> 'docker ps --filter name=divinci-table --format "{{.Names}} {{.Image}} {{.Status}}"'
    ssh <room host> 'docker image ls divinci-table'                  # the images you can go back to
    cd cloud && npx wrangler deployments list -c wrangler.host.jsonc # the Worker versions you can go back to

### 1. The room host

On your machine, from the clean clone at `<sha>`:

    scripts/roomhost_bundle.sh                                       # → /tmp/divinci-table-<sha10>.tgz (git archive + card data + BUILD_SHA)
    scp /tmp/divinci-table-<sha10>.tgz <room host>:<room host dir>/build/ctx-<sha10>.tgz

On the room host, in `<room host dir>`:

    rm -rf build/src && mkdir build/src && tar -xzf build/ctx-<sha10>.tgz -C build/src
    (cd build/src && docker build -f cloud/Dockerfile -t divinci-table:<sha10> .)
    cp compose.yaml compose.yaml.pre-<sha10>                         # the service file you are replacing: the rollback
    sed -i "s#image: divinci-table:.*#image: divinci-table:<sha10>#" compose.yaml   # every service on the table image
    docker compose -f compose.yaml up -d

`SIGTERM` saves every room first (`stop_grace_period: 60s`); players see "the table is restarting" for a few seconds and
the same game comes back on their next request. Keep the previous image: it is the rollback. Delete images two
releases old, not the last one.

### 2. The Worker

From the clean clone at `<sha>`, through your gate if you have one:

    ROOM_HOST_VPC_SERVICE_ID=<vpc service id> "$DEPLOY_GATE" run --target worker:divinci-table:production -- scripts/cloud_deploy.sh --host
    # without a gate:
    ROOM_HOST_VPC_SERVICE_ID=<vpc service id> scripts/cloud_deploy.sh --host
    # or, for a room host at a public https address instead of a VPC service:
    ROOM_HOST_ORIGIN=https://<your room host> scripts/cloud_deploy.sh --host

It prints `CLOUD-DEPLOYED <sha> (host)` at the end; no such line, no deploy. Nothing is built locally in host mode, so
a gate's disk-space floor meant for image builds can be lowered for this run (say that you did).

## Check it

    node scripts/cloud_smoke.cjs <sha> --room <an existing room id> --room-sha <sha10>
    ssh <room host> 'docker ps --filter name=divinci-table --format "{{.Names}} {{.Image}} {{.Status}}"'

The smoke check reads `/version` (the Worker's commit), confirms host mode from `/agents/tables`, fetches two shared
D&D files (in host mode they come from the room host through the tunnel, so this is already a round trip to it) and,
with `--room`, opens that room and reads its `/api/stage`. It never creates a room: rooms start processes and show in
the public lobby. Pick any listed room, or skip `--room`. A room host's table does not show its commit to visitors
(`/api/version` is 403 from outside on purpose), so the image tag in `docker ps` is the room half's version.

Then look at the lobby page itself, and at `/agents/skill.md` if `cloud/public/` changed.

## Roll back

Roll back the half that broke. If both changed and you are unsure, the Worker first, then the room host.

### The Worker

    cd cloud
    npx wrangler deployments list -c wrangler.host.jsonc             # newest first
    npx wrangler versions view <version id> -c wrangler.host.jsonc  # its GIT_SHA var says which commit it was
    npx wrangler rollback <version id> -c wrangler.host.jsonc --message "why"

⚠️ Roll back only to a **host-mode** version: one deployed with `-c wrangler.host.jsonc`, whose bindings show `ROOM` on
`HostedRoom` and a `ROOM_HOST` VPC binding. Versions from before the move to the room host are container mode and
would break the site exactly like a plain `cloud_deploy.sh`. Check `versions view` before you roll back; if no older
host-mode version exists, redeploy a known-good commit with `scripts/cloud_deploy.sh --host` instead.
A rollback keeps the Worker's secrets (they are not part of a version); `GIT_SHA`, and so `/version`, goes back with it.

### The room host

On the room host, in `<room host dir>`, put the previous service file (or just the previous tag) back:

    docker image ls divinci-table                                    # the previous tag must still be there
    cp compose.yaml compose.yaml.broken-<sha10>
    cp compose.yaml.pre-<sha10> compose.yaml                         # or: sed -i "s#image: divinci-table:.*#image: divinci-table:<previous sha10>#" compose.yaml
    docker compose -f compose.yaml up -d

Rooms are saved on `SIGTERM` and come back under the older image. A saved game is a pickle written by the table
version that ran it, and nothing tests that an older version reads a newer one's: if a room does not come back, its
`data/rooms/<id>/room.log` says why. If the previous service file has fewer services (an older release without the house player, say), stop the
extra one too: `docker compose -f compose.yaml.broken-<sha10> stop <service>`.

Then run the smoke check again with the version you went back to.

## Rehearsing a rollback without changing anything

All read-only:

    ssh <room host> 'cd <room host dir> && docker image ls divinci-table && grep -n "image:" compose.yaml compose.yaml.pre-*'
    cd cloud && npx wrangler deployments list -c wrangler.host.jsonc && npx wrangler versions view <version id> -c wrangler.host.jsonc

You should see: the live tag in `compose.yaml`, the previous tag still present as an image, a `compose.yaml.pre-*` for
it, and a host-mode Worker version older than the live one.

## Watching it

An outside uptime check reads `/version` and one shared D&D file (the room host's round trip) every ten minutes, and
notifies by email and chat when either fails. Keep it cheap: no room is created per probe, and neither path counts
against the agent API's daily request budget.
