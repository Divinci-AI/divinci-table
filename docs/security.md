# Security model

*Written 2026-10-04 after a review of the cloud rooms (table.divinci.ai). The table began as a laptop on a home
network among friends; cloud rooms put the same servers on the public internet. This page says who may do
what, and which test holds each rule.*

## Who is who

| Who | How they prove it | What they may do |
|---|---|---|
| **The laptop host** | the request comes from the laptop itself (`_is_local`) | everything (scan pad, mic, resets, referee tools) |
| **A seated player** | the seat key their device got when it claimed the seat (`X-Seat-Key`, added by `assets/seat.js` to every POST the page makes to its own table; never sent elsewhere) | act for **their own seat**; table-wide actions under their own name |
| **A pilot** (a person playing a virtual deck) | the same seat key, for a `--pilot` seat | drive **their own** engine seat from `/hand`; see only their own hand |
| **An AI player** | the brain token (a file inside the room's container) | the brain API for any engine seat |
| **The room's Durable Object** | the per-room `ROOM_TOKEN` | snapshot, restore, release (`/api/room/*`; the Worker refuses these from the internet) |
| **An admin** | `Authorization: Bearer <ADMIN_TOKEN>` (compared in constant time) | sleep/remove rooms, release seats, the AI kill switch |
| **Anyone with the link** | nothing | watch: public table state, events, boards, the stage, `/xr` |

## The rules, and what holds them

1. **Another device changes the table only with a seat key** — in cloud rooms always, on a home network with
   `STRICT_SEATS=1`. OWN actions (your life, hand count, fair word, attacks, chat, card actions) must name the
   key's own seat; table actions (BACK, hold, step timeouts, todos, placed marks, high roll) are recorded under
   the key's seat, whatever name the page sent. *`table/tests/remote_guard_test.py`* (mutation-checked: with the
   guard off, 14 checks fail and an anonymous request changes life totals).
2. **People own their numbers.** Only your seat's key changes your life total; a pilot can't change another
   player's life, and a pilot's combat damage counts only their own lifelink. *remote_guard_test.* **A pilot's typed text
   and photo captions from another device are table talk, never moves.** Typed or spoken text is read as said aloud at
   the table ("Sam is at 9" moves a life total, "I cast <removal> on Claude's Horta" asks Claude to apply it). For a plain
   human seat that is the game (their cards are physical, and the table believes what players say, as at a real one). A
   **pilot's** moves are the engine's, checked there; from another device under `STRICT_SEATS` a pilot's text is only
   said. Until 2026-11-11 it was parsed too, so a pilot's key (or, on the photo route, no key at all) could set another
   seat's life or destroy their permanent by typing it. *`table/tests/remote_authority_test.py` (fails on the earlier
   code); `chat_board_test.py` and `retro5_test.py` hold the human announcements.*
3. **Hidden information stays hidden.** A pilot sees only their own hand; another seat's key, no key or a made-up
   key gets 403; `take` (reaching into another hand) is the host's alone, so it can't be used to probe a hand by
   guessing names. *remote_guard_test (mutation-checked).*
4. **AI spend is bounded and switchable.** AI seats need the invitation code (5 words from 64, constant-time
   compare; 8 wrong codes an hour per address and 60 overall lock code checking for an hour), at most 4 AI rooms
   a day (refunded if the room then fails to start), 300 release requests per AI seat (then it plays on passively),
   and `/lobby/admin/ai-off`, which also holds for a room that wakes up again (no AI key in its container).
   *`cloud/test/policy.test.ts`, `table/tests/fusion_cap_test.py`.*
5. **The D&D AI Dungeon Master** is off in public rooms (`DND_CLOUD_AI`) and capped at 150 requests per room;
   players' words reach it as data, and it can set only monsters and the scene. *`table/tests/dnd_test.py`.*
6. **Pages escape what they show.** Card text, names, chat, DM narration and survey answers are escaped before
   they reach the page; nothing from a player is rendered as HTML.
7. **Voice and board photos from the headset** (`/api/xr/stt`, `/api/xr/vision`, run by the room's Worker with Workers
   AI): seated players only (the Worker asks the room's server, `/api/seat/check`), off with the AI kill switch,
   40 transcriptions per 10 minutes and 20 photos per hour per room, size-capped. The audio and photo are never
   stored; the words become the player's own chat line, and a photo's card list is only a proposal until the
   player adds it — and it can only ADD to their own board. Model output is cleaned (`parseBoardReply`) before
   anyone sees it. *`cloud/test/policy.test.ts`, remote_guard_test.*
8. **Presence** (`/api/xr/presence`, a WebSocket on the room): the first message must carry a seat key, checked by
   the room's server (never in the URL); poses are validated (`parsePose`: 7 finite numbers, within 20 m) before
   they are relayed, 600-byte messages, 30 per second; only seated sockets send or receive.
9. **The open mic's judge** (`/api/xr/judge`): only the room's own container may call it (its private room token,
   constant-time compared; anyone else gets 404); Clef-Flash on Workers AI, 200 lines per 10 minutes, off with the
   kill switch. Lines from an open mic are marked `spoken` and reach the public log only as the table heard them,
   with side conversation replaced by "(side conversation)". *`table/tests/openmic_room_test.py` (mutation-checked).*
10. **Watching my board** (passive vision): only while the player looks down at the table, only after the view changed
   and settled, at most once per 30 s, and only with the seat's deck known (it names only cards from that deck);
   results are suggestions until the player adds them, and adding never removes a card.
11. **Surveys and photos.** Seat-gated (any device but the laptop: since 2026-11-11 not only in cloud rooms), recorded
   under the key's own seat whatever `X-By` says, size- and count-capped (300), stored in the room's research folder
   (0600); the public log says who answered a survey, never what. *remote_authority_test.*
12. **The laptop is addressed as itself.** Host-level trust needs a loopback peer, a loopback `Host`, and no forwarding
   header (a tunnel keeps its own host name, so trust never depends on a proxy remembering to add one). A POST's
   `Origin` must be this table's own address, port included, so another page on a localhost port is as foreign as
   another site. *remote_authority_test, security_review_test.*

## Known limits (accepted for now)

- **A held spell names who may respond.** While a seat's spell waits for a counter, the public stream and
  `/api/phase` (`waiting_on`) list the seats that hold a castable answer, which is information a real table keeps
  hidden. Fix options: list every opponent as waited-on while only the holders are asked, or always open the window.
  Not changed yet (it alters what players see).
- **`/api/deckdecl` lists a virtual seat's whole deck** under the default `--decklists auto` (Commander-style open
  lists). A table where a deck list is an edge (draft tables, agents) starts with `--decklists declared`.
- **Held connections cost file descriptors.** The server raises its own limit to 8192; a flood of idle long-polls from
  a tunnel can still crowd out other requests. Put a rate limit in front of a table you expose widely.

- **Seats are first come.** Anyone with a room's link can claim an unclaimed seat. Rooms are public by design;
  a private-room option would add an invite per room.
- **A person can still play their seat wrongly.** The engine checks a pilot's moves; the rules for a person's
  physical cards are the table's honour system, as at any real table.
- **The AI key is in the room container's environment.** A code-execution bug in a table server would expose it.
  Use a Divinci API key that can only chat with the table's releases, with its own spend cap.
- **The per-seat AI cap counts inside the container**, so it restarts if a room's container is replaced; the
  daily AI-room limit bounds that.
