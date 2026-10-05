# T4: the local DM, measured (2026-10-05)

`table/tests/t4_dm_eval.py` runs ten fixed theater scenes through the prompt the table really sends, streamed.
`replies.json` holds every reply; `score.html` is the **blind** sheet for Michael (vivid 1–5, consistent, keep
playing?), with the models hidden (`blind-key.json`, open only after scoring).

| Model | First sentence (median) | Rule faults (table-checkable) |
|---|---|---|
| gemma4:e2b (thinking off, the default) | 0.36–1.31 s across runs | 1–3 of 10 per run: moved a player in TABLE, echoed the state into TABLE, **missed the death saving throw**; in one run it **spoke and acted for a player** |
| gemma4:e2b +think | 6.0–10.0 s | 0–1 of 10: **missed the death saving throw** |

Read, not just counted (the metric-only first pass missed three kinds of fault, so the checker was extended):
- Neither variant asks for a death saving throw when a character is down, the one thing the rules require there.
- Without thinking it sometimes decides what players say or do ("Ben: I see nothing…", "Ben sits in the wagon").
- With thinking, agency is better and companions answer, though once in the third person.
- Prose is vivid enough for both; scenes stayed consistent once each scene's history was its own (the first run
  leaked earlier scenes' lines into later ones: an evaluation bug, fixed).

**Conclusion so far:** e2b without thinking meets T2's speed but not the rules; with thinking it's better but too
slow to speak (6–10 s). Next: a larger local model (download size for Michael's OK), and Michael's blind scores.
If no local model is both quick and rule-abiding, the Fusion cloud DM becomes a decision with its cost per session.

## After two prompt rules (same day)

The table now tells the DM, from its own state: who the players are ("never write their words or decide what they
do") and, when a downed character's turn comes, to ask them for a death saving throw. Re-measured:

| Model | First sentence (median) | Rule faults |
|---|---|---|
| gemma4:e2b | 0.23–1.01 s (4 runs) | 0, 1, 2, 0 of 10: only TABLE-line hygiene (echoing the state, a player move the table drops). **The death save is asked every time** ("Ana, do you roll a death saving throw?"); no lines written for players. |
| gemma4:e2b +think | 6.6 s | 0 of 10 |

Players never hear the TABLE line, and the table already refuses anything illegal in it, so what's left is untidy,
not harmful. The fast default now follows the checkable rules; how vivid it is stays Michael's call (`score.html`).
