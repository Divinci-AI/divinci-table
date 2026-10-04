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
   player's life, and a pilot's combat damage counts only their own lifelink. *remote_guard_test.*
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
7. **Surveys and photos.** Seat-gated, size- and count-capped, stored in the room's research folder (0600); the
   public log says who answered a survey, never what.

## Known limits (accepted for now)

- **Seats are first come.** Anyone with a room's link can claim an unclaimed seat. Rooms are public by design;
  a private-room option would add an invite per room.
- **A person can still play their seat wrongly.** The engine checks a pilot's moves; the rules for a person's
  physical cards are the table's honour system, as at any real table.
- **The AI key is in the room container's environment.** A code-execution bug in a table server would expose it.
  Use a Divinci API key that can only chat with the table's releases, with its own spend cap.
- **The per-seat AI cap counts inside the container**, so it restarts if a room's container is replaced; the
  daily AI-room limit bounds that.
