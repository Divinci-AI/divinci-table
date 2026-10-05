# Playtest 1: a D&D one-shot, phones and a headset

*Written 2026-10-05. The real test of the table: everything built so far (cloud rooms, the kit-built 3D
locations, phones, the headset, fair dice, the map) used together by people, for about 90 minutes. Like the
magnet test for the hardware, its job is to produce a true bug list, not to show off.*

## Who and what

- **Dungeon Master:** Michael (a person behind the screen; the AI DM stays off unless decided otherwise, since
  public rooms cap it and it costs money).
- **Players:** 2–4 friends, each on their own phone. At least one plays a session on the **Quest** (the 3D table
  in AR or life size, /xr in the room).
- **Characters:** the pregens (fighter, rogue, cleric, wizard). Everyone keeps their own sheet; only you change
  your own hit points.
- **Dice:** real dice entered by the person who rolled, or the fair phone dice. Both should come up.

## Setup (15 minutes before)

1. On table.divinci.ai, **Start a table → D&D one-shot**, title "Playtest 1", names with the DM first.
2. Everyone opens the room link on their phone and claims their seat (👤).
3. The headset player opens the same room on the Quest, then /xr, and places the table.
4. On the laptop: `node scripts/cloud_smoke.cjs <live sha> --room <id>` should say SMOKE-OK. Note the time.

## The adventure (three scenes, three locations)

1. **The Sleeping Griffin tavern** (talk, a job offered, one bar brawl round with 2 Bandits): tests
   seats, chat, initiative, the map, moving tokens on phones.
2. **The king's road** (an ambush: 4 Goblins from the trees, a Wolf): tests the DM placing monsters, combat
   turns, speed limits on movement, difficult ground, the 3D view animating moves.
3. **The goblin cave** (the boss: a Bugbear and 2 Goblins, pools of water): tests switching location,
   the room loading on every device, conditions, someone going down.

Switch locations from the DM's seat; each switch should reach every phone and the headset within a few seconds.

## What to measure (write it down as it happens)

| What | How | Good enough |
|---|---|---|
| Room load | time from opening the room to the 3D room showing, per device | < 5 s on wifi |
| Location switch | DM switches → the new room on every device | < 5 s, no placeholder stuck |
| Moves | a phone moves its token → others see it | < 1 s |
| Headset | frame rate feel, whether reading the map is comfortable, any freeze | no freeze; comfortable |
| Dice | real-die entries and phone dice both recorded in the log | every roll public |
| Confusion | every "how do I…?" asked out loud | each one is a bug |
| Breakage | anything wrong, with the time | — |

## After

- Everyone fills in the room's **/survey** (fixed questions; answers stay in the room's research folder).
- The DM writes the bug list into `docs/DND-3D-GOAL.md` (a new milestone), each item with its time and device.
- Note the cloud cost of the session (Cloudflare dashboard: containers, R2 egress) for the ledger.

## Not this time

The magnet hand, the AI Dungeon Master, and strangers from the public lobby. One thing at a time.
