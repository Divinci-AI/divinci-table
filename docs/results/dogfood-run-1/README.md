# Dogfood run 1 (2026-10-09): Sonnet 5.5 against Opus 5.5, 3 turns each, through the table's own interface

**What ran.** A local table (the Stage 1 rules, remote-device rules on every request, pilot clock raised to 240 s), two pilot seats named for the models (Sonnet: Kaust, Eyes of the Glade; Opus: Tuvasa the Sunlit), played over `tablectl`
by two sub-agents (Sonnet 5.5 and Opus 5.5) given the identical brief (`docs/dogfood-brief.md`), concurrently. A Playwright observer loaded each player's real `/hand` page and the public `/stage` (`table/tests/dogfood_ui_shots.cjs`).
Result: Sonnet 40, Opus 33 life after Sonnet's three turns (Sonnet attacked for 2, then 5; Opus never attacked). Sonnet used 28 tool calls (about 207k tokens), Opus 92 (about 239k). Files here: `findings-Sonnet.md`, `findings-Opus.md`
(the players' own notes), `events.txt` (217 table events), `shots/`.

**What worked (confirmed by the players and the observer).** Own state was right throughout: mana (including Wild Growth), one land a turn, enters-tapped lands, draws, life after hits. The Stage 1 interface changes show in the real page: Begin greyed unless legal, tapped permanents
have no Untap button, Cast greyed when unaffordable. The `/hand` page's life, library, hand and battlefield counts matched the server.

## Findings, ranked by how much they would hurt a real remote game

1. **Combat damage is self-reported.** `tc block` refuses to run without `--amount`, so the DEFENDER types how much damage they take; nothing checks it against the attackers' power, and the attack itself reached the opponent only as free text ("I attack Opus with Kaust, 2 damage").
   Life moved immediately during declare blockers. This is the honor system the project exists to remove: the server must compute combat from the declared attackers, blockers and their power.
2. **The opponent's board is invisible.** `state` shows "others' announced cards: -" even after "I cast Kaust"; the `/hand` page has no opponent section. Both boards are real engine state on the server. A remote player cannot see lands, creatures or mana they are playing against.
3. **A land can be played before the game starts** (Sonnet played Jungle Shrine before the high roll), which both players counted as an extra land drop (3 lands in 2 turns). Stage 1 closed `begin` but not `land`.
4. **Nobody can discover how to start the game.** `begin` answers "the game hasn't started" until someone runs `tc highroll quantum`, which the help does not point to; Sonnet waited about 2.5 minutes and guessed it.
5. **A silent seat stalls the table, and automatic passes look like real ones.** After Sonnet stopped answering (its turn cap), each of Opus's priority windows waited about 4 minutes for the table to pass for it; Opus's third `end` took from 03:54 to 04:22. The auto-pass is logged exactly like a real pass,
   `phase` showed `seconds_left: 0.0` the whole time (no countdown), and `begin`/`end` print "waiting on passes: Sonnet, Sonnet" (the name twice). The plan's turn-clock item, with real numbers behind it.
6. **Starting player drew on turn 1 in a 1v1 game** (Sonnet's first `begin` drew Command Tower; the library went 92 to 89 over three turns). The two-player rule is that the starting player skips that draw. Needs confirming against the rules text and the engine.
7. **The engine got a card's size wrong.** Yavimaya Enchantress showed 2/2 with three of its owner's enchantments in play (it should be at least 5/5), and the battlefield list omitted two untapped Forests while saying "lands: 3 (2 untapped)". Static effects are not all computed.
8. **Gruul Turf's bounce chose for the player** and picked an untapped Mountain, costing a mana and the turn's play; the player gets no choice.
9. **Journal requests cannot be answered remotely.** The table posts "journal due for Opus"; `tc journal-due` answers "only available on the table's laptop".
10. **Smaller:** about 7 manual passes per opponent turn; no response window tied to a spell (a pass is per step: Sonnet's main-1 pass was logged before Opus's Wild Growth); `pass` confirms only the player's own summary, not which window it answered;
    `tc block`'s help says "no REF = no block" but the command requires `--amount`; `end` walks every step and takes about 45 s with a silent opponent; the red Pass-priority panel covers the page title and the life total on `/hand`.

**Not tested (and why):** illegal plays (one land a turn, mana), instants, real blocks (no untapped creature on the right turn), the turn clock under a responsive opponent, three or more players, and any card effect the Stage 1 allowlist forbids (draw/search/token).
The allowlist did get exercised by Ground Seal's draw ("I draw a card" worked through the engine's own path), so cards that draw for themselves are fine.

## What this changes in the plan (`docs/hud-plan.md`)
- Stage 2 (turn state machine) must include server-computed combat and a start-the-game flow, not just passing.
- A public opponent-board view is needed before the dock and hand arc make sense (the hand arc shows my hand; I also need to see theirs).
- The turn clock needs a visible countdown and an `auto: true` mark on every automatic pass.
- Close `land` before the game starts; confirm the first-draw rule.
- Engine correctness items (7, 8) are card-level work for the engine, separate from the interface.
