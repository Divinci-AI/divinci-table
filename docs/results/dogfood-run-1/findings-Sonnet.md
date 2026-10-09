# Sonnet findings
1. `tc begin` -> "refused: the game hasn't started" (turn 0, phase step=untap, begun=false). Yet `tc land "Jungle Shrine"` succeeded in the same state (played tapped, turn 0). Inconsistent gating.
2. Game start needed 'highroll quantum' (not mentioned in brief); I waited ~2 min for Opus first. Land played pre-game succeeded while begin refused.
3. Turn 0 pre-game land did not count against turn 1 land drop (state showed land played: False) - effectively 2 land drops before turn 1 ended.
4. Gruul Turf ETB: engine auto-bounced my untapped Mountain (no choice offered, no chance to tap it first); cost me a mana this turn (3->2), so I could not cast a 3-mana morph.
5. Opus took 2 (T2) and 5 (T3): life 38 -> 33. Table matched expectations.
6. `end` shows "journal due" requests; I did not answer them (not in brief).
7. No command to choose land to bounce, and `tc --help` does not mention highroll as a game-start prerequisite.
