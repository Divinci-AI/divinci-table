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

## A model's blind scores (2026-10-05)

Michael asked for Cloudflare's GLM 5.3 flash (`@cf/zai-org/glm-5.3-flash`, Workers AI) to fill in the blind sheet
while his own scoring waits. `table/tests/t4_judge.py` gives it only what the sheet shows (scene, player's line,
reply; never the model behind it), one pass at temperature 0, and writes `glm-scores.json` (blind ids). **These are a
model's scores, not Michael's; his still decide.** Only the table's own test transcripts were sent.

| Model | Vivid (1–5) | Consistent | Would keep playing |
|---|---|---|---|
| gemma4:e2b | 3.00 | 4/10 | 7/10 |
| gemma4:e2b +think | 2.90 | 3/10 | 6/10 |

Read, not just averaged (the judge's one-line reasons are in the JSON): the two variants are indistinguishable on
quality, so thinking's 6–10 s buys nothing and **thinking stays off**. Vividness clusters at 3: competent, rarely
striking. The faults the judge names are the DM's, not the speech loop's:
- **Deciding for the players:** narrating a player character's gaze, wait or reaction (R01, R05, R08, R10, R12, R14, R18).
- **Resolving without a roll:** a stealth approach or an escape from a foe succeeds on the page, and the opportunity
  attack is skipped (R04, R07, R09, R19).
- **Ignoring the question:** the barkeep never answers about the caravan (R10, R12).
- **Stray bookkeeping read aloud:** "Goblin 1" / "Bandit 1" labels (R09, R17).

The first two are prompt-and-checker work (the table already refuses illegal moves; it can also refuse prose that
speaks for a player). A larger local model is the other lever, and needs Michael's OK for the download.
