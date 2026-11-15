# D&D with AI players — two sessions, what broke, what changed (2026-11-15)

Michael asked for the immersive, generative, headset version of the fantasy one-shot to be "nice and polished", and for
Claude to play it with Fusion and other AIs "to really get a feel for it". This is the record of that: two sessions on a
local table, the problems they surfaced, and the fixes, each with its check.

## The table

| Seat | Who | How it plays |
|---|---|---|
| Dungeon Master | **Fusion** (the Divinci release behind the AI DM) | `dnd_server.py`, the AI DM as in cloud rooms |
| Claude, high elf wizard | Claude (this session) | `table/dndctl.py` with its own seat key |
| Gemma, human fighter | **Gemma 4** (`gemma4:e2b`, on this laptop through Ollama) | `table/dnd_agent.py` (new) |
| GLM, halfling rogue | **GLM 5.3** (Ollama's cloud) | `table/dnd_agent.py` |
| Fusion, dwarf cleric | an AI companion the DM voices | `--companions ai:Fusion:normal:cleric` |

Every seat played by the table's rules: its own key, its own token, its own hit points, fair dice in the public log.

**Session 1** (the old table): three wolves in a forest clearing. Paused after round 1 because the table, not the game,
was the problem (see below): 18 DM calls. **Session 2** (the fixed table): the keep's chapel, a caretaker, a warded crypt
lock (an Arcana 24 read the ward, the courier's key undid it), two skeleton archers, a sealed inner door. 40 DM calls.

Spend: 58 DM calls (~129k tokens in, ~7k out) plus a few latency probes; Gemma's 17 calls were local; GLM's 15 went to
Ollama's cloud.

## What session 1 found, and what changed

| # | Finding (session 1) | Fix | Check |
|---|---|---|---|
| 1 | **Nobody rolled for the monsters.** The DM never rolls (the table's rule), so the AI DM asked *players* to roll its wolf's bite against themselves. | The AI DM puts its creatures' dice in its `TABLE` line (`"rolls":[{"for":"Wolf 1","dice":"d20+4","why":"bite vs Claude"}]`); the table rolls them fairly and in public and calls the DM back to resolve them. Never for a player; at most three call-backs in a row without a player acting. | `dnd_test`: rolled for the goblin, never for a player or a stranger, chain cap. Mutation-checked. Session 2: every skeleton attack and save rolled this way. |
| 2 | **Turns never passed.** After the DM ran a monster's turn, someone had to press *Next turn*; the DM narrated "it's Gemma's turn" while the marker sat elsewhere, and the AI players followed the words. | `"next":true` ends a monster's or companion's turn (never a player's). A monster that is unconscious, stunned, paralyzed, petrified, dead or gone loses its turn with no DM call. The prompt tells the DM the table moves initiative. | `dnd_test` (mutation-checked: without the guard the DM ends a player's turn). Session 2: round 1 ran with no human chores. |
| 3 | **"1d6+3+2d6" was refused** — how every rogue writes sneak attack. Two tries, two DM calls, no damage. | Sums of dice groups roll (`1d6+3+2d6`, at most 20 dice; still no subtraction of dice); physical dice for a sum are checked die by die. | `dnd_test`; session 2's crit Scorching Ray rolled `2d6+2d6+4d6 = 29`. |
| 4 | Sleep put two wolves to sleep, but the table had no way to say so: each still took a turn, each turn a DM call. | Monsters carry `conditions` (`asleep` is read as unconscious); they stay until the DM changes them; tags show 💤. | `dnd_test`; `dnd_xr_e2e` (the 💤 tag). |
| 5 | Monsters rolled initiative on a bare d20. | Monsters keep a `dex` (from the DM's line) and add it. | `dnd_test` (Dex 30 → +10). |
| 6 | The opening jumped straight into a fight (wolves, initiative). | The opening prompt asks for people and a place first. | Session 2 opened in the chapel with a caretaker. |
| 7 | Pressing *Roll initiative* mid-fight re-rolled the whole fight (with several AI seats answering "roll initiative!" at once, it would). | Refused (409) while combat is on. | `dnd_test`. |
| 8 | An AI player over-acted (8 actions in 30 s), described attacks without rolling, ignored damage, never ended its turn. | `dnd_agent.py`: a gap between actions, three model calls per combat turn then the turn passes, declare-and-roll together, damage/healing read off the narration, initiative rolled in code. | Dry runs with a scripted DM; `dnd_agent_test.py`. |

## What session 2 found, and what changed

| # | Finding (session 2) | Fix | Check |
|---|---|---|---|
| 9 | The DM used the new `rolls` line to roll **initiative** for its creatures (four DM calls before combat began). | Initiative is never a DM roll. The DM starts and ends fights itself: `"initiative":"start"` (the table rolls its creatures, players their own) and `"end"`. | `dnd_test`. |
| 10 | A skeleton attacked on Gemma's turn. | In a fight a creature's attack is rolled only on its own turn; saves and reactions any time. | `dnd_test`. |
| 11 | The dead skeletons kept their turns after the fight (two DM calls to say so). | Gone/dead monsters lose their turns silently; combat ends by itself when no foe is left standing. | `dnd_test`. |
| 12 | **The fastest player took the spotlight.** Gemma's local model answers in ~1 s, so the DM kept addressing Gemma; Claude's Detect Magic went unanswered for several beats. | Outside combat the DM is told who has had least to do lately (`SPOTLIGHT:`), when the gap is clear. | `dnd_test`. Not yet seen in play (session 2 ran the old server). |
| 13 | Gemma answered "roll 1d8 plus 3 for your damage" with another attack roll. | When the DM names the dice (or the save/skill), the agent rolls exactly that, with no model call. | `dnd_agent_test.py` (14 cases from the sessions' own sentences). |
| 14 | **The DM takes 36–42 s to answer** (median; p90 48 s), and the time grows with the prompt: the same real prompt took 54 s at 3.2k tokens and 22 s at 1.4k. | Table side: the DM's own older narration comes back as its first sentence only (RECENT was 2,200 of 3,200 tokens): ~44% fewer tokens late in a game, by replaying session 2's log. The hosted DM's own latency is being traced separately. | Offline replay of the session's events. |

## The headset (D6) — what playing it showed

Looking at the headset page during play: the diorama floated in a black void, and **nothing of the story reached the
headset** (no narration on screen, none spoken, no turn order): a remote player in VR saw the map and none of the game.

- **A world around the table** (VR and the preview; never over AR's passthrough): sky, ground and fog in the location's
  colours (tavern, road, forest, cave, crypt, keep, bridge, ruins), and a wooden table with a brass rim under the diorama.
- **The story parchment**: the DM's narration as it streams in (scrolling), companion and NPC lines under their names, the
  last thing a player did, the last roll, whose turn it is or "the DM is thinking…". It floats beyond the map facing you
  (two metres ahead at life size). Reopening the page shows the story so far without speaking it again.
- **The headset speaks** (Voice on the wrist panel; remembered, shared with the desktop's setting): narrator, companions
  and players in their own voices, rolls and turns in the table's voice — on the device, no audio over the network.
- **Wrist panel**: the turn order and the party's hit points and conditions, and the Voice button.
- **Tags**: a condition beside the name (💤 for asleep/unconscious); tags of creatures standing together step out of each
  other's way on screen, nearest first (desktop and headset).
- Cost, measured in the headless preview (software GL): the sky and ground fill the view (~9 ms there); a cheaper ground
  material brought it to ~6 ms. A Quest's GPU draws an unlit sky for almost nothing, but the 72 fps check on the
  headset itself (D6) is still Michael's to run.

`dnd_xr_e2e.cjs` grew from 8 to 18 checks (world, theme, story, 💤, story faces you, tags step aside, voice off until
chosen and not re-speaking the backlog, a new line spoken in the narrator's voice).

## Still open

- **The DM's latency (#14)**: the table's AI DM can't feel immersive at 40 s a beat.
- **Generative assets** are where they were: location art and establishing shots wait for an NVIDIA key; monster minis
  wait for a decision on Meshy credits; walk-in splats wait for GPU spend (docs/DND-3D-GOAL.md D2, D3, D8).
- The Quest check (72 fps with the world and the parchment on; presence for 30 minutes).
- Spotlight (#12) and DM-called initiative (#9) in a live session.

## Reproduce

```
python table/dnd_server.py --players "Claude,Gemma,GLM" --companions "ai:Fusion:normal:cleric" --port 9437
python table/dnd_agent.py --base http://127.0.0.1:9437 --name Gemma --model gemma4:e2b --pregen fighter --key-file <0600 file>
DND_BASE=http://127.0.0.1:9437 DND_NAME=Claude DND_KEY_FILE=<0600 file> python table/dndctl.py claim | watch | say … | roll …
```

The AI DM needs `DIVINCI_FUSION_API_KEY` and a release id in the server's environment only (from a 0600 file, never argv);
`--dm-backend ollama:<model>` or `script:<file>` run it offline.
