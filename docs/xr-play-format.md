# Playing in a headset (Quest 3S) — design

*Written 2026-10-03, the day the Quest 3S arrived. Status: design + a device probe. Builds on [xr.md](xr.md)
(today's `/xr`: AI avatars around a real table in passthrough, LAN only, table placed by a tap, NEXT on a
2D overlay). Commander first; the other games plug in later through the same table layer.*

## The idea: one table, many ways in

There is one table, and everyone sits at it, wherever they are. It has a **Table Frame**: an origin at the centre of
the tabletop, +Y up, distances in metres, and a fixed **seat ring** (an angle and a radius for every seat in turn
order). Every device draws the same game in that frame:

| Who | Wears | Sees | Their cards |
|---|---|---|---|
| **At the table, in a headset** | Quest 3S, passthrough (MR) | the real room and real people; **avatars in the empty chairs** for AI and remote players, each with that seat's board lying life-size on the real table, life and turn floating above | real cards in their hands |
| **Remote, in a headset** | Quest 3S, passthrough at **their own desk** (MR), or VR | their desk becomes the table: every other seat is an avatar with its board | real cards on their own desk |
| **At the table, no headset** | phone (`/me`) and the shared screen (`/stage`) | as today | real cards |
| **AI players** | — | the engine's state | digital, shown as boards to everyone else |

Two rules decide what gets drawn:
1. **Never draw what is already really there.** People at the real table, and their real cards, are seen through
   passthrough, not drawn. Only seats that aren't physically present get an avatar and a drawn board.
2. **Every seat looks the same to whoever is not sitting in it.** A remote person, an AI and an empty chair use the
   same avatar-and-board slot, so a game module never needs to know who is wearing what.

## Lining up the frame (calibration)

The real table and the Table Frame have to match within a couple of centimetres, or drawn cards float or sink.

- **Default: touch two points.** With a hand or a controller, pinch on the **centre of the table**, then on **the
  table edge in front of you**. That gives the origin, the height and your own seat's direction. The table's size
  and shape come from the room config (round or rectangular, in cm), so two touches are enough. This takes about
  ten seconds, works on any headset with no flags, and works the same at home on a remote player's desk.
- **Remembered:** the frame is stored as a **persistent anchor** (the browser keeps 8 per site), so next time the
  table is simply there. Re-touch if the table has moved.
- **Two headsets in one room:** each does the same two touches on the same centre, which lines them up within a few
  centimetres. **Upgrade path:** Meta's experimental *Shared Spaces* (`XRSharedReferenceSpace`) gives co-located
  headsets one coordinate system automatically, but each headset needs a `chrome://flags` switch, so it stays
  optional until that ships by default.
- **Seat ring at the real table:** the room config lists the physical seats clockwise from the host's chair. A
  headset player's own seat comes from the edge they touched; the rest follow the ring.

Image or marker tracking (a printed QR code on the table) is not available on Quest's browser, so it isn't the plan.

## What you do with your hands

Hand tracking first; controllers work the same.

- **NEXT** (pass priority) is a floating plate at the table edge in front of you. **Pinch and hold it for half a
  second.** A long press stops a stray pinch from skipping someone's turn. The same plate shows the current phase
  and whose turn it is.
- **Wrist menu:** turn your left palm toward you for BACK, your life total ±, mute and photo. **Only your own life
  total** has ±, because people own their numbers.
- **Inspect a card:** point at any drawn card (an AI's or a remote player's) and pinch. A card panel opens at
  reading distance with its name, cost, type and rules text, at 2° text height or more. Text uses MSDF rendering,
  and labels tilt toward you because flat card names are hard to read.
- **Your own physical cards** stay physical. There is nothing to do in the headset that the cards already do.

## Seeing remote people: presence

Game state keeps travelling over today's HTTP API and event log; nothing changes there. Presence is new:

- **Pose stream:** each headset sends head plus two wrists (and optionally hand joints) about 15 times a second,
  in Table Frame coordinates, to the room. The room is a **WebSocket on its Durable Object** (hibernation API, so
  an idle room costs nothing), which relays it to the other headsets and the stage. A seat key is needed to send
  a pose, and the connection is dropped when the seat is released.
- **Remote people's avatars** follow that pose: the head and hands move, with simple arm IK. When a pose is more
  than 2 s old, the avatar falls back to idle. **AI avatars** keep the game-driven acting the stage already has.
- People at the table **without** a headset have no pose; remote viewers see their avatar idling at their seat.

## Cards from the headset camera (remote players' boards)

A remote player's real cards on their desk have to reach everyone else's view of their board.

- The 3S browser can give a page the passthrough camera feed (a "headset cameras" permission, 1920×1080, with
  camera pose). That isn't available through WebXR, so the page uses the camera request web pages normally use.
  **📷 Snap my board** captures **one** frame on purpose, never a stream. It goes to the same pipeline phone
  photos use today (`/api/chat/photo` → the board record), to be read by the table's recognizer or confirmed by
  hand.
- **Integrity rule:** a capture is only ever of *your own* board, starts only when you press it, and is never
  automatic. A camera frame can include someone's hand of cards, so the capture shows a framing preview first, and
  any slip goes in `integrity-notes.jsonl` as it does today.
- Only one site can use the cameras at a time, and the feed stops when the browser is minimised, so the page asks
  for the camera only at the moment of the snap.

## Voice

- **At the table:** unchanged. The room hears itself, and the open mic (M5) listens through the table's mic or a phone.
- **AI speech** plays from the AI's avatar as spatial audio (Web Audio panning) in every headset.
- **Remote voice:** phase 1 keeps whatever call the table already uses (a phone on speaker). A built-in voice
  channel (WebRTC through Cloudflare Realtime) is a later phase. It needs its own privacy design and a test that
  the headset's mic works while the immersive session is running.

## Performance budget (Quest 3S)

Same chip and memory as the Quest 3, with a lower-resolution, narrower display.

- **Target 72 fps** (the WebXR default), and go to 90 only if a frame fits in 11 ms. Budget: **≤150 draw calls,
  ≤300k visible triangles.**
- **Avatars:** today's 10–17 MB GLBs are far over budget. Make **Quest versions**: decimated to about 30k triangles
  each, KTX2 textures at 1K, Meshopt compression, shared materials. At most 4 avatars are drawn at full detail.
- **Boards:** instanced card quads with a texture atlas per board, and text as MSDF. No per-card meshes.
- **Shadows:** a single cheap blob shadow per avatar, not real-time shadow maps.

## Where it runs

- **Cloud rooms:** `/xr` inside a room (the room cookie routes it, like `/stage`). It's already HTTPS, so there's
  no certificate to install on the headset.
- **Laptop table:** `TLS=1` as today, for LAN-only game nights.
- **Getting it onto the headset:** an **"Open on Quest"** button on `/me` uses Meta's Web Launch link, so the page
  opens in the headset's browser without typing a URL. A packaged app (a web app installed through Meta's store,
  which starts without a *Start AR* tap) is a later convenience, not a requirement.

## Games after Commander

The table layer (frame, seat ring, avatars, presence, NEXT, inspect, camera snap) is game-independent and lives
beside `core.py`'s contract. A game module only supplies **what lies on the table at a seat**:

- **Commander:** the board3d rows (creatures, permanents, lands; command zone; graveyard).
- **Chess:** one board at the centre (drawn for remote players, real for the table), plus clocks.
- **D&D:** a battle map at the centre with minis (drawn, or real ones read by a camera snap), and sheets on the
  wrist menu.
- **Catan / poker:** the board or felt at the centre, and private hands that only the owner's device ever draws.

## Phases

| Phase | What | Done when |
|---|---|---|
| **P0 — probe** | `/xr-probe` on the headset reports every capability this design depends on (below). | The 3S's answers are recorded in this doc. |
| **P1 — one headset at the real table** | Two-touch calibration with a persistent anchor; avatars and boards in empty chairs; hold-to-NEXT plate; wrist menu; card inspect; Quest-version avatars; `/xr` in cloud rooms; "Open on Quest". | A Commander game with Michael in the headset, Sam at the table and one AI seat, at 72 fps, with no misaligned cards over a full game. |
| **P2 — remote in a headset** | Pose WebSocket on the room's Durable Object; desk calibration; remote avatars that follow head and hands; 📷 snap my board from the headset camera. | Sam plays from home in the headset and Michael sees him at the table. |
| **P3 — two headsets in one room** | Both calibrate to the same centre (Shared Spaces optional); drift check. | Two headsets agree on a drawn card's position within 2 cm. |
| **P4 — voice** | AI speech from its avatar; WebRTC voice for remote players. | A remote player is heard from their avatar's position. |
| **Then** | Chess, D&D, Catan, poker modules on the same table layer. | |

## P0: what the probe checks on the 3S

Open https://table.divinci.ai/xr-probe in the Quest browser, press **Run**, then
**Start AR** (it reports in the headset and shows a JSON summary to copy or screenshot).

1. `immersive-ar` / `immersive-vr` supported, and the frame rates offered.
2. Session features granted: hit-test, anchors, plane-detection, mesh-detection, hand-tracking, depth-sensing,
   layers, dom-overlay.
3. Hand tracking: number of joints seen in the first 3 s.
4. Hit-test: a surface hit within 3 s of looking at the table.
5. Persistent anchor: create → `requestPersistentHandle()` → handle returned.
6. Headset camera: a camera request (`getUserMedia`) succeeds, at what resolution, with the session running.
7. Microphone inside the immersive session.
8. Web Audio: an AudioContext runs and panning is available.
9. Rendering: fps over 5 s with a stress scene of 4 × 30k-triangle meshes and 300 instanced card quads.

## Decisions for Michael

1. **Remote voice:** keep a phone call for now (recommended), or build in-headset voice in P2?
2. **The real table:** shape and size (it fixes the seat ring), and which chair is the host's.
3. **Quest avatars:** remeshing with Meshy costs credits (about 5 per avatar for 12 avatars, or a local
   decimate-and-compress pass for free). Recommend trying the local pass first.
