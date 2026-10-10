# First in-person 3-player game on the new build (2026-10-09, room `32a2sum5`)

Michael (real deck, commander Inspirit, Flagship Vessel), Sam (real deck, commander Captain N'ghathrod) and Claude (virtual deck, Kaust,
Eyes of the Glade, through `tablectl` with a pilot seat key). Started 22:56 local with the check-in and the high roll (Michael 17,
Sam 3, Claude 7; play went round in seat order Michael, Sam, Claude). Stopped by the players at turn 7 / about 00:06 because it was
bedtime; at the stop: Michael 22, Sam 38, Claude 33.  `events.json` is the last 300 events the server still held (not the whole game).

## What the game found (all fixed in main the same night unless noted)
- A person's typed or spoken plays did not reach their board (chat → board recording; a pilot can now record from a photo and correct itself).
- Plague Spitter's upkeep damage, Fumigate, Hunted Horror's Centaur tokens and Captain N'ghathrod's mill had no way to land on a pilot seat:
  new `effect` kinds `apply-removal`, `opponent-token`, `opponent-mill`, each checked against the source card's text.
- The log panel and the Declare attack button sat under the tab bar (unreachable on a phone).
- "Mike" for Michael, "played a land" with no card name, "I played …" (past tense) are not understood by the play recorder (open).
- Declaring the commander and deck list belongs in the opening ceremony (agent `agent-declare-deck` was building it at the stop).
- A pilot seat that is slow to answer cost two priority windows early on until the poll-based auto-pass replaced the event-driven one.
- Not yet deployed at the stop: To deck menu, mill rule, declare button and log panel fixes (deploying), commander/deck declaration (in progress).
