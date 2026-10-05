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
- [ ] Map state in `dnd_server.py`, saved and restored with the room; placeholder grid and tokens.
- [ ] `POST /api/dnd/map/move` (own token only; in bounds, not blocked, not occupied; movement counted
      against speed on your initiative turn), `…/place` and `…/location` (DM only).
- [ ] `TABLE:` lines from the AI DM can set `location`, `place` and `move`, validated like every other
      TABLE field (unknown names and illegal squares are dropped, never trusted).
- [ ] `dnd.html` gets a 2D map (canvas): tokens, tap your token then a square to move, the DM drags any.
- [ ] **Check:** `table/tests/dnd_test.py` grows map checks: another seat's key can't move your token (403),
      no key can't move anything, blocked and occupied squares are refused, the map survives a snapshot and
      restore, a malformed TABLE line changes nothing. Mutation-check the owner rule.

## D2 — Location library v1 (8 places, free credits)
- [ ] `scripts/dnd_location.py`: prompt → Cosmos 3 key art → 5 s establishing shot; top-down map image;
      grid, blocked squares and spawn points marked in a small review page (`/dnd/review`, laptop only).
- [ ] Rooms in Blender from a JSON layout (port Zombay's content-neutral `build_scene.py` / `render_views.py`
      idea), exported to glTF within the room budget.
- [ ] Eight starter locations: tavern, road, forest clearing, cave, crypt, keep hall, bridge, ruins.
- [ ] Manifest validator (`table/tests/dnd_assets_test.py`): every location has art, shot, room, map,
      licence and credit; every file exists in R2; every room is under budget.
- [ ] **Check:** a person (Michael) reviews all eight in the review page before any player sees them.

## D3 — Minis v1
- [ ] Import the Zombay meshes (D0) through one importer: decimate to budget, convert to GLB, record the licence.
- [ ] About 20 SRD monsters for the starter adventure (goblin, wolf, skeleton, zombie, bandit, ogre, …) from
      Meshy / Tripo within free credits; rig and animate (idle, walk, attack) only where credits allow.
- [ ] Standees for everything else, from the creature's art.
- [ ] **Check:** the validator rejects a mini over 20k triangles or without a licence (mutation-checked);
      every SRD monster in the starter adventure resolves to a mini or a standee.

## D4 — The 3D table on the web
- [ ] Stage view (three.js): the location's room, the grid, minis on their squares, animated moves, HP state
      as a ring, the active turn highlighted. A location change plays its establishing shot first.
- [ ] Falls back to the 2D map when WebGL is weak or the device asks for reduced motion.
- [ ] **Check:** Playwright (`table/tests/dnd_page_e2e.cjs`): load, move a token from the phone view, see it
      move on the stage; frame time logged on the laptop.

## D5 — Phones
- [ ] The phone page: your sheet, dice, scene art, the 2D map with tap-to-move, initiative, push-to-talk to
      the DM (reusing `/api/xr/stt` in cloud rooms; Whisper on the laptop table). Installable as a web app.
- [ ] **Check:** a full combat round played from two phones on the laptop table and in a cloud room.

## D6 — The headset (Quest 3S)
- [ ] **AR:** the battle map as a diorama on the real table (place / re-place / scale from xr-controls);
      grab your own mini to move it (the server decides); dice on a button and by voice.
- [ ] **VR:** the location's room around the table at life size, or the diorama in front of you; remote
      players as head-and-hands avatars (presence, as in Magic).
- [ ] Controller map consistent with Magic (A = end turn, X = talk, panel on left-stick click).
- [ ] **Check:** a session on the actual headset: 72 fps with 4 players and 12 minis, mic and cameras
      work during the immersive session, presence holds for 30 minutes. Record the numbers.

## D7 — The AI DM runs the map
- [ ] The DM picks a location from the library (never generates one live), places monsters at spawn
      points, and moves them on their turns; every move is validated server-side.
- [ ] **Check:** three simulated encounters (`dnd_test` with a fake DM): no illegal move accepted, no
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

## Done log
