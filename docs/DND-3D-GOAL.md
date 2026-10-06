# D&D in 3D — the adventure table on web, phone and headset, as a goal

*Run with `/goal docs/DND-3D-GOAL.md` (from the divinci-table repo). Written 2026-10-04, after the Quest 3S
headset work for Magic (controls, voice, board photos, open mic, presence) and a review of NVIDIA's world
models and the Zombay pipeline. Tick a box only when its check has been run and its output read.*

## Where we start (2026-10-04)

- **D&D today** (`table/dnd_server.py`, `table/dnd.html`, on the shared `core.py`): SRD 5.1 pregens, sheets,
  fair dice, initiative, conditions, a monster list, a one-line scene, surveys, and an AI Dungeon Master
  (off in public rooms, capped at 150 requests per room). It is a 2D web page: **no map, no minis, no 3D.**
- **Reusable from Magic's headset work:** AR table placement with mini / life-size scaling, Touch-controller
  buttons and the in-headset panel (`assets/xr-controls.js`), push-to-talk speech (`/api/xr/stt`), head-and-hands
  presence (`assets/xr-presence.js`), seat keys and the seat guard, cloud rooms that survive restarts, R2 assets
  (`divinci-table-assets`).
- **World models, as researched 2026-10-04:**
  - **NVIDIA Cosmos 3** (May 2026; OpenMDW-1.1, commercial use allowed; hosted on build.nvidia.com):
    text-to-image and image-to-video. **This is what players see.**
  - **NVIDIA Lyra 2.0** (Apr 2026): one image to an explorable 3D world (video, then 3D Gaussian splats).
    Code Apache-2.0, **weights research-only** (no commercial use without an NVIDIA Research licence);
    1× H100 80 GB, ~10 min per scene. **Internal prototype only, gated.**
  - **Zombay's finding** (`ZOMBAY-AI/zombay`, `services/blender-mcp-runpod/README.md`): AI video → splat
    failed every time they tried it (Wan, Veo, Lyra, InstantSplat, MoDGS) because generated frames have
    inconsistent geometry. What worked: build the scene in **Blender**, render ~100 views at known camera
    poses (25 s on a Mac), train splatfacto on a rented L40S (~4 min), export `.ply`, view with Spark.js.
    **So rooms are built in Blender and exported to glTF; splats are optional polish.**

## Rules for every milestone

1. **Measure, don't assume.** Every box names its check. Run it, read the output, record what you saw.
2. **Live play comes first.** No restarts or heavy tests while a game is on (Magic or D&D, laptop or cloud).
3. **Deploys:** the cloud Worker and room image may be deployed as each milestone finishes. Production
   divinci.ai changes go through `deploy-gate announce` and need Michael's OK.
4. **Nothing public without a human:** no posts, emails or announcements; Buffer posts stay drafts.
5. **Spend: free tiers only** (Michael, 2026-10-04). Use build.nvidia.com credits, the Meshy/Tripo credits we
   already have, Workers AI's free allowance, Blender on the Mac and free Colab. Anything that costs money
   (RunPod / H100 hours, Rodin, paid credits) comes back to Michael first, with the amount.
6. **Licences are part of done.** Every asset has a licence and a credit line in its manifest entry, and the
   credits page lists them. No Lyra 2 output in front of a player without a commercial licence.
7. **SFW only.** Nothing from xr-sandbox's packs or Zombay's production folder, prompts, characters or
   face-swap service. Copy only Zombay's content-neutral code (Blender render, splat training, Rodin client).
8. **Security pass** on anything reachable from the internet: who may move which token, what a stranger can
   read or write, size limits on anything uploaded, and whether a secret can leak (the repo is public).
9. **Performance budgets** (Quest 3S is the floor): a mini ≤ 20k triangles and ≤ 1k textures (KTX2 where
   supported); ≤ 12 minis animated at once; a room ≤ 150k triangles; 72 fps in the headset with 4 players.
   Phones get the same assets at a lower level of detail.
10. Commits are local unless a milestone says to push; `divinci.ai` changes go to `origin/table-landing`.

## How the pieces fit

```
                 offline (Mac + free tiers)                         in the game
  Cosmos 3 ──► key art · establishing shot (5 s) ─┐
  Blender  ──► room as glTF (+ optional splat)    ├─► R2 dnd/… ──► manifest ──► web stage (three.js)
  Meshy / Tripo / Zombay meshes ──► minis (GLB)   ─┘                        ├─► phone (2D map, sheet)
  SRD 5.1 ──► monsters, rules                                               └─► headset (AR diorama / VR room)
                                       shared map state on the room's server (seat-guarded)
                                       AI DM picks a LOCATION from the library; never generates live
```

- **Location library:** each location = `{id, name, tags, art.jpg, shot.mp4, room.glb, map: {w, h, cell_ft: 5,
  blocked[], doors[], spawn{pcs[], monsters[]}}, licence, credit}`. Files in R2 under `dnd/locations/<id>/`;
  the manifest (`table/dnd_assets.json`) is in the repo.
- **Minis:** `{id, name, srd_name?, glb, scale, size: tiny…gargantuan, animations[], tris, licence, credit}`
  under `dnd/minis/`. A creature with no mesh gets a **standee** (its art on a card), so the map never waits.
- **Map state** (on the room's D&D server, saved with the room): location, tokens `{id, name, kind: pc|monster|npc,
  mini, x, y, hp_state}`, fog later. People move only their own character; only the DM (person or AI) moves
  monsters, places tokens or changes the location.

---

## D0 — Settle the inputs
- [ ] Find the **Zombay meshes** Michael means (the repo has none; `~/Documents/Zombay-3D` holds one CC-BY
      Sketchfab model and a MakeHuman shoe pack). Record where they are and their licences.
- [ ] Check free balances: build.nvidia.com credits (Cosmos 3), Meshy (`meshy_check_balance`), Tripo,
      Workers AI's daily allowance. Write the numbers here.
      *2026-10-04:* Meshy **1,062 credits** (≈30 per textured mini, +5 rig, +3 per animation → ~20 rigged
      minis with idle + walk ≈ 800). **No NVIDIA key** in Infisical (and none for Tripo or Rodin):
      build.nvidia.com needs Michael to sign in and create an API key, stored as `NVIDIA_API_KEY`.
- [ ] **Naming:** "Dungeons & Dragons" / "D&D" is a Wizards of the Coast trademark; the SRD's CC-BY covers
      rules content, not the name. Michael decides the product wording (e.g. "fantasy adventures, compatible
      with fifth edition") for the lobby, the page title and divinci.ai/table.
- [ ] Credits page drafted: SRD 5.1 (CC-BY-4.0), each CC-BY model, "Built on NVIDIA Cosmos".

## D1 — The battle map (no generation needed)
- [x] Map state in `dnd_server.py`, saved and restored with the room; placeholder grid and tokens.
- [x] `POST /api/dnd/map/move` (own token only; in bounds, not blocked, not occupied; movement counted
      against speed on your initiative turn), `…/place` and `…/location` (DM only).
- [x] `TABLE:` lines from the AI DM can set `location`, `place` and `move`, validated like every other
      TABLE field (unknown names and illegal squares are dropped, never trusted).
- [x] `dnd.html` gets a 2D map (canvas): tokens, tap your token then a square to move, the DM drags any.
- [x] **Check:** `table/tests/dnd_test.py` grows map checks: another seat's key can't move your token (403),
      no key can't move anything, blocked and occupied squares are refused, the map survives a snapshot and
      restore, a malformed TABLE line changes nothing. Mutation-check the owner rule.

## D2 — Location library v1 (8 places, free credits)
- [ ] `scripts/dnd_location.py`: prompt → Cosmos 3 key art → 5 s establishing shot; top-down map image;
      grid, blocked squares and spawn points marked in a small review page (`/dnd/review`, laptop only).
      *Written; review page and top-down maps done. The Cosmos calls are NOT yet run (no NVIDIA key):
      `--dry-run` prints the requests; the shot is image2video from the room's Blender view so it shows the same room.*
- [x] Rooms in Blender from a JSON layout (port Zombay's content-neutral `build_scene.py` / `render_views.py`
      idea), exported to glTF within the room budget.
- [x] Eight starter locations: tavern, road, forest clearing, cave, crypt, keep hall, bridge, ruins.
      *Layouts and rooms built (1,424–2,206 triangles each, 1–2 s each in Blender); Cosmos art and shots pending the key.*
- [ ] Manifest validator (`table/tests/dnd_assets_test.py`): every location has art, shot, room, map,
      licence and credit; every file exists in R2; every room is under budget.
      *Built and passing (97 checks, `--built`): layouts, reachability, licences, local files, triangle budget.
      Not yet: checking R2 — the rooms aren't uploaded (that and the Worker's `/dnd-assets/` route ship with
      the next cloud deploy, which waits for Michael).*
- [ ] **Check:** a person (Michael) reviews all eight in the review page before any player sees them.

## D3 — Minis v1
- [ ] Import the Zombay meshes (D0) through one importer: decimate to budget, convert to GLB, record the licence.
      *The importer is built (`scripts/dnd_minis.py add`, Blender: decimate, ≤1k textures, scale to creature size,
      keeps the rig; refuses a model without a licence): Fusion's 62,143-triangle avatar → 19,375 triangles,
      16.9 MB → 2.1 MB in ~3 s. The Zombay meshes themselves wait on where they are (D0).*
- [ ] About 20 SRD monsters for the starter adventure (goblin, wolf, skeleton, zombie, bandit, ogre, …) from
      Meshy / Tripo within free credits; rig and animate (idle, walk, attack) only where credits allow.
      *The bestiary is in the manifest (20 SRD 5.1 monsters with size, speed, AC, HP; the server gives a DM's
      "Goblin 2" the goblin's numbers and footprint). Generating them with Meshy waits for Michael's yes on credits.*
- [x] Standees for everything else, from the creature's art.
      *Every bestiary monster is a standee until it has a mini; D4 draws them.*
- [x] **Check:** the validator rejects a mini over 20k triangles or without a licence (mutation-checked);
      every SRD monster in the starter adventure resolves to a mini or a standee.
      *`dnd_assets_test --built` 122 passed, incl. a self-test: an unlicensed mini and a 30k-triangle model are
      both reported, a good one isn't.*

## D4 — The 3D table on the web
- [x] Stage view (three.js): the location's room, the grid, minis on their squares, animated moves, HP state
      as a ring, the active turn highlighted. A location change plays its establishing shot first.
- [x] Falls back to the 2D map when WebGL is weak or the device asks for reduced motion.
- [x] **Check:** Playwright (`table/tests/dnd_page_e2e.cjs`): load, move a token from the phone view, see it
      move on the stage; frame time logged on the laptop.

## D5 — Phones
- [x] The phone page: your sheet, dice, scene art, the 2D map with tap-to-move, initiative, push-to-talk to
      the DM (reusing `/api/xr/stt` in cloud rooms; Whisper on the laptop table). Installable as a web app.
- [ ] **Check:** a full combat round played from two phones on the laptop table and in a cloud room.
      *Laptop: done (`dnd_round_e2e` 9/9). Cloud room: waits for the next deploy (Michael's OK).*

## D6 — The headset (Quest 3S)
- [x] **AR:** the battle map as a diorama on the real table (place / re-place / scale from xr-controls);
      grab your own mini to move it (the server decides); dice on a button and by voice.
- [x] **VR:** the location's room around the table at life size, or the diorama in front of you; remote
      players as head-and-hands avatars (presence, as in Magic).
- [x] Controller map consistent with Magic (A = end turn, X = talk, panel on left-stick click).
- [ ] **Check:** a session on the actual headset: 72 fps with 4 players and 12 minis, mic and cameras
      work during the immersive session, presence holds for 30 minutes. Record the numbers.
      *Built and checked outside a headset (`dnd_xr_e2e` 8/8: renders, every token, 0.80 m diorama, spoken dice
      parsed, no errors). The immersive check itself needs Michael and the Quest.*

## D7 — The AI DM runs the map
- [x] The DM picks a location from the library (never generates one live), places monsters at spawn
      points, and moves them on their turns; every move is validated server-side.
- [x] **Check:** three simulated encounters (`dnd_test` with a fake DM): no illegal move accepted, no
      token teleports, the DM never moves a player's character.

## D8 — Walk-in worlds (research, gated)
- [ ] Blender rooms → known-pose renders → splat (Zombay's pipeline) for one location, viewed with Spark.js
      on the web and the Quest. GPU time costs money: Michael approves the amount first.
- [ ] Lyra 2.0 prototype for one location, internal only, if a GPU is available; ask NVIDIA Research about
      a commercial licence before any player sees its output.
- [ ] **Check:** side-by-side of glTF room vs splat on the Quest: fps, load time, and how it looks.

## D9 — A real session
- [ ] A two-hour one-shot with people on web, phone and headset together; surveys afterwards.
- [ ] **Check:** results in the ledger; what broke goes into the next goal.

## D10 — Playtest 1: the rehearsal's bug list, then the real one

*2026-10-05.* `table/tests/dnd_playtest_rehearsal.cjs` plays all of `docs/PLAYTEST-1.md` on a local table: Michael
as DM on a desktop, Ana and Ben on phones, Cy on a desktop in 3D with the headset page open too; three scenes
(tavern brawl, road ambush, the goblin cave), with the plan's measurements. 23/23 checks pass; the measurements
are in `docs/results/playtest-1-rehearsal-2026-10-05.json`. What it can't measure is everything the plan is really
for: confusion, comfort, the Quest itself, the cloud's latency. Those need the real playtest.

| # | Finding | Status |
|---|---|---|
| 1 | **Moves reach the other devices too slowly:** median 1.08 s, worst 1.50 s; 16 of 24 over the 1 s target, on phones, 3D desktop and headset alike. Cause: the pages poll (`dnd.html` every 1.5 s, `/xr` every 1 s). | **Fixed 2026-10-05 (Michael chose long-polling).** `/api/events?since=N&wait=20` is held until something newer exists (`core.Events.wait_since`, woken by `emit`); `dnd.html` and the headset page loop on it, with a 4 s / 3 s state refresh as a safety net, and hidden tabs don't hold a connection. Same rehearsal, same machine, back to back: median **1170–1450 ms → 126–184 ms**, worst 1490 → 191–641 ms, 16–26 of 45 measurements over target → 2. `events_longpoll_test.py` (12 checks; the wake-up is mutation-checked). Also: the server's accept backlog went from 5 to 128, and the rehearsal now gives each device its own browser, as real phones are (one browser's six-connections-per-host limit starves held requests). |
| 2 | **`dnd_server.py` ignored `TABLE_RESEARCH_DIR`**, so every D&D browser test wrote its games and surveys among real games' research data (`table/.cache/research/dnd/`, 52 dated entries from test runs). | **Fixed.** Tests now leave that folder untouched (54 entries before and after a full D&D test pass). The 52 older entries are left for Michael to clear: some could be real local games. |
| 3 | Location switches: median 1.15 s, worst 1.65 s to every device, headset included (target 5 s). Rolls (phone and real), damage and conditions owned by their players, a 45-ft move on a 30-ft turn refused, someone going down, surveys private: all as designed. | Fine. |
| 4 | The first desktop's 3D room took 7.1 s with five browsers starting at once on one laptop; a desktop alone takes 2.7–3.6 s. | Not a bug: an artefact of simulating everyone on one machine with software graphics. Re-measure on real devices. |
| 5 | Headset page frame time 59 ms under headless software GL. | Says nothing about the Quest (72 fps there in D6's check). Measure on the headset. |

- [x] Decide #1 (long-poll) and fix it; the rehearsal's move metric is the check (< 1 s on every device): done, see the table above.
- [ ] The real Playtest 1 with people: confusion, comfort, the Quest, cloud load and switch times, the cost.

## Done log

- **2026-10-05 · Hardening after the first cloud check.** (1) A failed room/mini download is retried
  (1/3/9/20 s); `dnd_room_load_e2e.cjs` reproduced the stuck placeholder first. (2) A map's files and draft mark
  come from the location's status at publish time (`map_view()`), so approval reaches running games and a
  location sent back to draft stops going out. (3) The public repo had drifted 152 commits from the live site:
  history scrubbed of a personal detail and two internal KV namespace IDs, pushed; `scripts/cloud_deploy.sh`
  now refuses a dirty tree or an unpushed HEAD and stamps the commit (`/version`, `/api/version`);
  `scripts/cloud_smoke.cjs` checks the live commit, the room files and a fresh browser's 3D room. (4) Rooms
  meshopt-compressed: 29 MB → 7.3 MB, 3 MB budget per room in `dnd_assets_test`; decoder deployed before the
  compressed files were uploaded. Live: `66711e2` SMOKE-OK with the compressed rooms. Next: `docs/PLAYTEST-1.md`.

- **2026-10-05 · Cloud deploy.** Michael approved all 8 locations; `scripts/dnd_upload.py --yes` put their 24 files
  (29.3 MB, approved only) in R2 `divinci-table-assets/dnd/`; Worker `d2de80de` serves `/dnd-assets/…`
  (`dndAssetKey`, traversal-tested, both regex anchors mutation-checked) and the room image rolled to
  `sha256:0927059b…` (container version 32). Live: room files 200 with the right types, bad paths 404; a fresh
  D&D room at table.divinci.ai lists all 8 locations as approved and renders the kit-built clearing in 3D.
  Seen once: on a room's very first load the 3D panel showed the placeholder blocks until a refresh. Not yet
  reproduced or explained; watch for it. (A room created mid-rollout kept the old image's draft flag.)

- **2026-10-05 · Rooms rebuilt from CC0 kits.** Michael found the primitive rooms too basic. Each layout character
  now becomes a model from KayKit Dungeon Remastered (Kay Lousberg) or Kenney's Nature, Graveyard and Fantasy Town
  kits, all CC0, fetched and SHA-256-pinned by `scripts/dnd_kits.py` (the pin refused the first, branch-zip
  download), fitted to its square and merged into one mesh. Kenney's mint/teal nature colours are recoloured to a
  woodland palette; maps render with Eevee (Workbench showed the kits' factor-only colours as white). All 8 rooms
  6.5k–97k triangles (budget 150k); kit credits at `/api/dnd/credits`. `dnd_assets_test` 98/98, `dnd_test`
  128/128, `dnd_page_e2e` 11/11, `dnd_round_e2e` 9/9, `dnd_xr_e2e` 8/8. Contact sheet checked by eye; fixed on the
  way: wood walls that vanished on the map, water rendering white, a bridge turned along the river.

- **2026-10-04 · D7 done.** The DM's STATE carries the grid, the locations, the bestiary and which tokens are
  people's; its TABLE line sets location / place / move; the server checks every move. `dnd_dm_sim_test.py`:
  three encounters (cave, crypt, bridge) with an adversarial fake DM — teleports, walls, off the map, onto
  others, players' characters, out of turn, bad locations, mid-fight 'place' — **16 passed**, 12 runs out of 12;
  5–12 legal walks accepted and 113–142 bad moves refused per encounter. Mutation-checked: without the owner rule
  the DM moves Ana and Sam; without the turn rule the spider moves on the goblin's turn; without the path/speed
  rule moves are charged 0 — each fails 3 checks. The first version of the invariant was wrong (a creature may
  split its speed into legs, so its charge can exceed the straight walk); it failed 4 runs in 6 on the wolf.

- **2026-10-04 · D6 built (the headset check waits for the Quest).** `dnd_xr.html` at `/xr` on the D&D server:
  Start AR / Enter VR; AR hit-test placement; the map as a 0.8 m diorama or life size (1 square = 1.524 m, sticks
  walk you and snap-turn you through the reference space); trigger or squeeze on your own mini, then a square
  (the server decides); a wrist panel (turn, your HP and movement left, Roll d20, Next turn, Diorama/Life size,
  Re-place); A = next turn on yours, hold X = talk, Y = d20, left-stick click = panel; "roll a d20 for stealth"
  rolls, anything else said is your action; presence (heads and hands) in cloud rooms, unmodified, because the
  table group stays in metres. `dnd_xr_e2e` **8/8**; a preview screenshot of the cave diorama checked by eye.

- **2026-10-04 · D5 (laptop done; cloud check waits for the deploy).** Phone page: scene art banner (Cosmos art,
  else the room's view), 🎙 hold-to-talk (16 kHz WAV → `/api/xr/stt`: the room's Worker in the cloud, Whisper on
  the laptop via `voice.py`; the words become your character's action), `/api/seat/check` for the Worker,
  installable (web app manifest + icons), shorter log and room for the seat chip on phones. Checked: `say` →
  Whisper through the D&D server word-perfect in 2.5 s; `dnd_round_e2e` **9/9** — initiative from a phone, both
  roll d20 from the dice panel, each moves on their own turn by tapping, the DM moves the goblin, round 2; a fake
  microphone's spoken action reaches the log as Michael's. `dnd_test` **126 passed** (talk needs a key, >1 min
  refused, the container never transcribes in the cloud, manifest). Found by the test, not the app: after
  tapping the dice the phone is scrolled below the map, so the test scrolls back first.

- **2026-10-04 · D4 done.** `assets/dnd-scene.js` (one module for the web stage and, next, the headset): the room
  (or boxes from the layout when there's no approved room), grid, minis (SkeletonUtils clones, idle animation) or
  standees that face the viewer, HP rings, the active turn's pulsing ring, walks animated over 0.45 s, raycast
  picking. `dnd.html`: 2D/3D toggle (3D by default on desktops with WebGL2; 2D on phones and with reduced
  motion), orbit by drag, wheel/pinch zoom, tap-to-move in 3D; a location change plays its establishing shot
  (VP9 original first, H.264 copy for Safari). Checked: `dnd_page_e2e` **11/11** (3D raycast move, the phone's
  move walks on the 3D table, reduced motion → 2D); 3D frame time 17 ms in headless software GL; a screenshot
  with the clearing's room, standees, a 2×2 ogre and a real mini; the establishing shot shown, played and
  closed (Playwright's Chromium can't decode H.264 — the reason both encodings ship).

- **2026-10-04 · D2 (all but Cosmos and the review).** Eight layouts with theme characters; `scripts/dnd_room_blender.py`
  + `scripts/dnd_rooms.py` build room.glb / map.jpg (flat Workbench, aligned to the grid) / view.jpg for all eight in
  ~11 s; fixes found by looking: shadows hid squares on the map (now lit from above, no shadows), hex colours
  were treated as linear (washed out; now sRGB→linear), wall tops read as floor (darker walls). Laptop serves
  `/dnd-assets/…`; the 2D map draws the room's render under the grid; `/dnd/review` + `/api/dnd/review`
  (laptop only, not through a proxy) approve a location once its room is built; public rooms offer only approved
  locations and never send a draft's files. `dnd_test` **116 passed**, `dnd_assets_test --built` **97 passed**,
  `dnd_page_e2e` **7/7**. `scripts/dnd_location.py` (Cosmos 3) written from NVIDIA's model card; not run (no key).

- **2026-10-04 · D1 done.** `table/dnd_map.py` (grid, footprints by size, cheapest legal walk with difficult
  terrain, allies passable and foes not, no squeezing between wall corners, speed per turn, 60-token cap),
  `table/dnd_assets.json` (manifest; ASCII layouts; the starter clearing), map state in `dnd_server.py`
  (saved with the room; monsters follow the DM's list; `/api/dnd/map/move|place|remove|location`; TABLE
  `location`/`place`/`move` validated), and a 2D canvas map on `dnd.html` (tap or drag; DM location picker;
  movement left on your turn). Checks: `dnd_test.py` **101 passed, 0 failed**; mutation-checked: removing
  the owner rule fails "Sam's key can't move Michael's character", removing the TABLE owner rule fails
  "…not even on that person's own turn" (a gap the first version of the test missed: the turn rule masked
  it), allowing mid-fight `place` fails "…can't 'place' a monster across the map mid-fight".
  `dnd_page_e2e.cjs` **7/7** (tap-to-move, the other phone sees it, can't pick up another's token, no
  horizontal scroll at 390 px, map redraw 0.05 ms). Screenshot checked by eye.
