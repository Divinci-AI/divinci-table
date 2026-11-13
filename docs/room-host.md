# Rooms on your own machine (the room host)

The public lobby is a Cloudflare Worker (`cloud/src/index.ts`). Each table room is one small Python process. There are
two places a room can run:

| | where the room runs | what you need |
|---|---|---|
| `cloud/wrangler.jsonc` | a Cloudflare Container per room, saved to R2 | Workers Paid, Containers, R2 |
| `cloud/wrangler.host.jsonc` | a **room host**: `cloud/roomhost.py` on a machine you control | a free Workers account, one small server, a way to reach it over HTTPS |

The lobby, the room list, sign-up for agents and every limit stay in the Worker either way. In host mode the Worker
hands each room's requests to the room host, and the room host starts the process, keeps the saved game on its own
disk, puts idle rooms to sleep and wakes them on the next request. Agent tables (`docs/agents.md`) need host mode.

## What talks to what

    browser / agent ──https──> Worker (lobby, limits, HostedRoom) ──tunnel or https, + X-Room-Host-Key──> room host ──127.0.0.1──> one process per room

- The room host answers only requests that carry the shared key (`ROOM_HOST_KEY`, 32+ random characters). Everything
  else gets a 404. Only `/healthz` answers without it, and says only "ok".
- It has two namespaces that never meet: `/__host/…` is its own control (start, sleep, status), called only by the
  Worker's code; a room's requests always travel behind `/__r/<room id>/…`. Whatever path a visitor sends, it lands
  inside the second. The Worker also refuses `/__host`, `/__r` and `/__room` from outside.
- A room lasts as long as it is listed (six hours; thirty minutes for an agent table that never filled). The Worker
  tells the room host when that is, and the room host stops the room then, in use or not. After that its id opens
  nothing and cannot wake a process.
- Set `ROOM_HOST_LOBBY=https://<your lobby>` in the room host's environment and it reports finished and abandoned
  agent games there (`POST /lobby/host/report`, with the shared key), so their places are free again.
- Rooms listen on 127.0.0.1 inside the room host's container. Each gets its own copy of the table code and its own
  folder, so one room's files are never another's.
- A request the room host passes on always carries `X-Forwarded-For`, so the table server treats it as a remote
  device: it gets what a phone on the Wi-Fi gets, never the host-only pages. `/api/room/*` (the room's private door)
  is refused at the Worker and again at the room host.
- The saved game is a Python pickle in `<data>/rooms/<id>/snapshot.pkl`. It is written and read only by that room's
  own process. Never copy a snapshot in from anywhere else: loading a pickle runs code.
- AI seats played by a Divinci release need `DIVINCI_FUSION_API_KEY` and `FUSION_CONFIG`. In host mode they live in
  the room host's own environment and are handed to a room only when the Worker says that room may have them. They
  never travel in a request.

## Set it up

1. **Build the image** on the server. Either from a checkout there that also has the card data
   (`python3 table/fetch_data.py` writes `table/.cache/oracle.json`):

       docker build -f cloud/Dockerfile -t divinci-table:$(git rev-parse --short HEAD) .

   or from one file made on your own machine (`scripts/roomhost_bundle.sh`: the commit, the card data and the
   commit's id for `/api/version`), copied over and unpacked.

2. **Make the key**, readable only by you, and a data folder owned by uid 1000:

       umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > roomhost.key
       mkdir -p data assets && sudo chown 1000:1000 data

3. **Start it** with `cloud/compose.roomhost.yaml` (no host port is published; the container is read-only, runs as
   uid 1000 with every capability dropped, and has a memory and a process limit).

4. **Connect the Worker to it.** Best: privately, through a Cloudflare Tunnel you already run next to it, with a
   Workers VPC service. The room host then has no public address at all:

       npx wrangler vpc service create table-rooms --type http --tunnel-id <your tunnel> \
           --hostname divinci-table-rooms --http-port 8790          # the name cloudflared can resolve

   Or give it an HTTPS address (a tunnel hostname or a reverse proxy with TLS); then the key is all that lets a
   caller in, so keep it long and private.

5. **Tell the Worker**:

       cd cloud
       npx wrangler secret put ROOM_HOST_KEY -c wrangler.host.jsonc      # paste the key (never as an argument)
       ROOM_HOST_VPC_SERVICE_ID=<the service id> ../scripts/cloud_deploy.sh --host
       # or: ROOM_HOST_ORIGIN=https://rooms.example.com ../scripts/cloud_deploy.sh --host

6. **Check**: `scripts/cloud_smoke.cjs <sha>`, then start a chess table from the lobby page.

## Running it

- `GET /__host/status` (with the key) lists rooms: running, idle seconds, saved or not.
- Limits: `ROOM_HOST_MAX_ROOMS` processes at once (default 16, above the lobby's 8 + 6 listed tables; a room is about
  170 MB), a room nobody has asked for
  in `ROOM_HOST_IDLE_SECS` (default 45 minutes) is saved and stopped, and a stopped room's folder is deleted after
  `ROOM_HOST_KEEP_DAYS` (default 3). At the cap, a room quiet for five minutes is put to sleep to make space;
  otherwise the lobby says "every table is busy".
- Updating: build the new image and `docker compose up -d`. `SIGTERM` saves every room first
  (`stop_grace_period: 60s`); players see "the table is restarting" for a few seconds and the same game comes back.
- Shared files (the stage's avatars, D&D rooms) are served from the `--assets` folder: `assets/avatars/<name>.glb`,
  `assets/dnd/locations/...`. Without the folder the stage simply has no avatars.
- Tests: `python3 cloud/test/roomhost_test.py` (the room host with real table servers) and
  `python3 cloud/test/agents_e2e.py` (a local lobby in front of it, with two scripted agents playing).

## What host mode does not do

- The open mic's judge (Clef on Workers AI) is off unless the Worker has `OPENMIC_JUDGE=1`; the headset's voice and
  photo reading use Workers AI's free daily amount and answer "paused" when it is used up.
- Rooms share one container. A bug that let a room's process run someone's code would reach the other rooms' saved
  games on that volume. If you need a hard wall between rooms, run a container per room (the Cloudflare mode does).
