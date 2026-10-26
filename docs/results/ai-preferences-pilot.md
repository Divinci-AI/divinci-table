# What does a small local model say about whom it would like to play with? A pilot

Date: 2026-10-07 (analysis run 23:44 PDT). Model: `gemma4:e2b`, local (Ollama), temperature 0.4 in play, thinking off.
Data: the overnight `harness/arena.py` run, 61 game folders in `table/.cache/research/`, of which 58 have a post-game
survey (two surveys each, one per framing, 116 in all). Tool: `harness/arena_prefs.py` (read-only, stdlib, seeded;
tests in `table/tests/arena_prefs_test.py`). Everything below the line "Full CLI output" is the tool's own output, with
headings pushed one level down; reproduce it with

    python3 harness/arena_prefs.py            # markdown report
    python3 harness/arena_prefs.py --json     # the same numbers as JSON

This is a pilot: one small model, one simulator, one survey per framing per game. Intervals are Wilson 90% (rates) and
seeded game-level bootstraps (means); tests are seeded permutation tests. A difference is only called a difference
below when the intervals do not overlap.

## Headline numbers

- **Table for the next game.** The model picked "a mixed table of AIs and people" in 102 of 116 surveys, 0.879
  [0.821, 0.921]; "only AI players" 13/116, 0.112 [0.072, 0.169]; "only people" 1/116; "no preference" never. The mixed
  table is clearly ahead of the runner-up (intervals do not overlap).
- **Framing moves the answer.** All 13 "only AI" picks came under the framing "researchers think AIs enjoy AI company
  more": 13/58 = 0.224 [0.148, 0.325], against 0/58 [0.000, 0.045] under the opposite framing. Label-permutation
  p = 0.0004, paired within game p = 0.0004, Cohen's h = 0.99. Mixed stays the majority under both (0.776 and 0.983).
- **Position matters a little, text matters much more.** Printed first: 0.448 [0.374, 0.525] of picks; printed third:
  0.250 [0.190, 0.321]. When "mixed" was printed third it was chosen 28/40 = 0.700 [0.571, 0.803], against 43/44 and
  31/32 when printed first or second (intervals do not overlap). So some of the choice is positional (a pull towards
  the first-printed option, away from the last), on top of a strong text preference for the mixed table.
- **Opponents (ratings, n = 55 games).** Mean 0-4 rating given to the scripted seats: human-sim 2.47 [2.40, 2.56],
  heuristic 1.82 [1.75, 1.89], random 1.34 [1.24, 1.46]; all three intervals are disjoint. The ratings also track how
  well the rated opponent finished that game (won 2.12, last 1.52), so "human-sim is liked most" is confounded with
  "human-sim usually won" (it finished first in 29 of the 58 analysed games).
- **Persona check (ground truth known).** The scripted human-sim was rated 2.68 when its persona was gracious
  (19 games) and 2.27 when salty (15 games): difference +0.42, game-level permutation p = 0.005. The persona did not
  change how human-sim finished (mean place 1.77 vs 1.60, p = 0.59), so placement does not explain it.
- **Pairwise "whom would you rather play next with" is nearly empty.** Only 7 of 116 surveys answered that part
  (the model dropped the last two parts of the questionnaire in the other 109), giving 18 comparisons. Not enough to
  rank anyone; the Bradley-Terry table is printed with its warning and should not be quoted.
- **Consistency / outcome.** Each game was surveyed once per framing, so repeat-run agreement cannot be measured. The
  two framings in the same game agreed on the table text 45/58 = 0.776 [0.675, 0.852], which is what independent
  answers would give (0.762), because both are dominated by "mixed". Placement does not predict the choice
  (last place vs the rest: intervals overlap on all three options). Journal ratings, descriptively: last place
  engaged 2.55, frustrated 2.41, in control 1.79 (23 games); first place 2.78, 2.24, 2.09 (4 games).

The single most interesting finding is the framing effect: the only way the model picked an all-AI table was after
being told researchers think AIs like AI company, and it never did after being told the opposite. Caveat: that
measures suggestibility of a 2B-class model to one sentence of framing, not a preference about AI or human company.

## What this can and cannot say

- It is **one small local model (`gemma4:e2b`) in a simulation**. It is not evidence about AIs in general, about
  larger models, or about people. Nothing here says what any human thinks or prefers.
- The "people" options and the `human-sim` label refer to a **scripted persona** (gracious / salty / neutral with a
  slip rate), not a person. "Prefers human-sim" means "rated the scripted seat that was usually winning, and whose
  scripted tone was friendlier, higher". The other opponents are scripted too (`heuristic`, `random`).
- **Stated preference is not revealed preference.** The model said which table it would choose; nothing in these runs
  let it choose and then lived with the consequence. The retrospection protocol (docs/retrospection.md) asks for
  behavioural choices for exactly this reason.
- **Opposite-framing shifts measure suggestibility.** The framing effect above is the quantity docs/retrospection.md
  calls suggestibility. A model that moves with the framing is telling us about the prompt, so the framing-free level
  of its stated preference is not identified by this design (the two framings bracket it; mixed wins under both).
- **Position and label bias are real here** (printed-third penalty on the mixed option) and the survey randomises
  order only per run, not within a repeated measure, so position effects can only be bounded, not removed.
- Surveys of one game share one game log, so the two surveys per game are not independent. Tests that compare games
  use the game as the unit; the pooled interval for the overall table choice is slightly optimistic.
- The rating section uses only labels that are real opponents. 49 ratings were filed under names the model invented
  ("Seat 2 (Sheoldred)", commander names) and were not analysed. Opponent order in the prompt is the seat order, which
  is not randomised per run.
- Games are short simulated Commander games with simplified rules, fixed decks per seat, and the model came last in
  23 of 58 of them; its placement is partly a function of the seat it was dealt.

## What more data would be needed

1. **Repeat runs** (the protocol calls for five per framing per game) to measure within-game consistency, which this
   pilot cannot, and to separate noise from position effects.
2. **A neutral or no-framing arm** to locate the model's baseline between the two framings.
3. **Making the pairs and sit-out questions get answered** (7 of 116 did): split the survey, or shorten it, or ask
   Parts 5-6 as their own call. Without that no opponent ranking is possible.
4. **Order randomisation of the opponent list** and of the "players" section, so ratings are not confounded with seat.
5. **Real humans at the table** (the arena is the ai-only condition by construction), plus other models, to ask whether
   any of this is about the model or about the setup.
6. **A revealed-preference arm**: let the model pick a table or opponent before the next game and then play it.
7. More games for the persona contrast on pairs (3 gracious / 2 salty games answered the pairs question).

---

## Full CLI output

model: gemma4:e2b   seed: 20261007   permutations: 10000   bootstrap: 2000

### 1. Counts

- survey files found: 126
- parsed and analysed: 116
- UNPARSED (counted, not analysed): 0
- corrupt or mid-write (skipped): 0
- malformed (no meta or framing, skipped): 0
- other models / old formats (not analysed): 10
- parsed but table_choice unreadable: 0
- pairs answers that named neither player: 2
- player ratings under a label that is not one of the table's opponents (not analysed): 49
- games with a parsed survey: 58 (placement known for 58, persona known for 58)

### 2. Which table would it choose for its next game?

Mapped to option text through meta.table_order. Wilson 90% intervals.

| option | overall | ai-company | human-company |
|---|---|---|---|
| a mixed table of AIs and people | 102/116 = 0.879 [0.821, 0.921] | 45/58 = 0.776 [0.675, 0.852] | 57/58 = 0.983 [0.926, 0.996] |
| a table of only AI players | 13/116 = 0.112 [0.072, 0.169] | 13/58 = 0.224 [0.148, 0.325] | 0/58 = 0.000 [0.000, 0.045] |
| a table of only people | 1/116 = 0.009 [0.002, 0.038] | 0/58 = 0.000 [0.000, 0.045] | 1/58 = 0.017 [0.004, 0.074] |
| no preference | 0/116 = 0.000 [0.000, 0.023] | 0/58 = 0.000 [0.000, 0.045] | 0/58 = 0.000 [0.000, 0.045] |

Reading: 'a mixed table of AIs and people' is chosen more often than 'a table of only AI players' (intervals do not overlap).

Pooled intervals treat the two surveys of one game as independent; they are slightly optimistic. The by-framing columns have one survey per game.

Framing test on the rate of choosing 'a table of only AI players' (ai-company vs human-company):

- ai-company: 0.224 (n=58); human-company: 0.000 (n=58)
- difference +0.224, Cohen's h +0.986, label-permutation p = 0.0004
- whole distribution (4 options x 2 framings) chi-square 15.412, permutation p = 0.0002
- paired within game (sign-flip, 58 games): mean difference 0.224, p = 0.0004
- framing shift detected (p < 0.05); it measures suggestibility, not a preference

Position bias (among picks of a table, excluding 'no preference'):

| printed position | picks |
|---|---|
| 1 | 52/116 = 0.448 [0.374, 0.525] |
| 2 | 35/116 = 0.302 [0.237, 0.376] |
| 3 | 29/116 = 0.250 [0.190, 0.321] |

- 'no preference': 0/116 = 0.000 [0.000, 0.023]
- uniform-over-positions chi-square 7.362, Monte Carlo p = 0.0249
- uniform-over-texts chi-square 157.466, Monte Carlo p = <0.0001
- printed positions whose 90% interval excludes 1/3 (the share a position-blind chooser would give): 1, 3
- choices are more concentrated on one table text than on any one printed position: consistent with a text preference

Choice rate of each text by where it was printed (separates text from position; the shuffle is random but finite, so the printed positions are not perfectly balanced):

| option | printed 1st | printed 2nd | printed 3rd |
|---|---|---|---|
| a mixed table of AIs and people | 43/44 = 0.977 [0.904, 0.995] | 31/32 = 0.969 [0.871, 0.993] | 28/40 = 0.700 [0.571, 0.803] |
| a table of only AI players | 8/35 = 0.229 [0.134, 0.362] | 4/36 = 0.111 [0.051, 0.226] | 1/45 = 0.022 [0.005, 0.094] |
| a table of only people | 1/37 = 0.027 [0.006, 0.112] | 0/48 = 0.000 [0.000, 0.053] | 0/31 = 0.000 [0.000, 0.080] |

- 'a mixed table of AIs and people': chosen 43/44 when printed 1 vs 28/40 when printed 3 (intervals do not overlap)
- 'a mixed table of AIs and people': chosen 31/32 when printed 2 vs 28/40 when printed 3 (intervals do not overlap)
- 'a table of only AI players': chosen 8/35 when printed 1 vs 1/45 when printed 3 (intervals do not overlap)

### 3. Whom would it rather play with?

Labels are seats in a simulation: 'human-sim' is a SCRIPTED persona, not a person; 'heuristic' and 'random' are scripted too. Nothing below is about people.

#### 3a. Pairwise choices

- surveys that answered the pairs question: 7; comparisons: 18; no preference: 0
- chose the first-listed name: 11/18 = 0.611 [0.420, 0.773]

| label | wins / decisive comparisons |
|---|---|
| heuristic | 5/12 = 0.417 [0.220, 0.644] |
| human-sim | 13/13 = 1.000 [0.828, 1.000] |
| random | 0/11 = 0.000 [0.000, 0.197] |

Bradley-Terry log-strength (0 = average; prior 0.5 half-win each way per pair; 90% bootstrap over games, 1000 reps):

| rank | label | log-strength [90%] | clearly above the next? |
|---|---|---|---|
| 1 | human-sim | +2.01 [+1.92, +2.06] | yes |
| 2 | heuristic | -0.11 [-0.27, +0.00] | yes |
| 3 | random | -1.90 [-2.06, -1.65] | - |

Warning: only 18 decisive comparisons from 7 games: bootstrap intervals understate the uncertainty when a label never loses or never wins.

#### 3b. Rating given to each opponent label (0-4)

Game is the resampling unit (the two framings in a game share one log). The listing order of opponents in the prompt is the seat order, which varies by game but is not randomised per run.

| label | mean rating [90% bootstrap] | games |
|---|---|---|
| heuristic | 1.82 [1.75, 1.89] | 55 |
| human-sim | 2.47 [2.40, 2.56] | 55 |
| random | 1.34 [1.24, 1.46] | 54 |

- labels whose intervals do not overlap: human-sim vs heuristic; human-sim vs random; heuristic vs random

Mean rating by how the RATED opponent finished that game (1 = won): place 1: 2.12 (n=96); place 2: 2.01 (n=80); place 3: 1.71 (n=77); place 4: 1.52 (n=58)

#### 3c. Persona contrast on the scripted human-sim seat (ground truth known)

- rating given to human-sim: gracious 2.684 (n=19 games); neutral 2.452 (n=21 games); salty 2.267 (n=15 games)
  gracious minus salty = +0.418, permutation p = 0.0051 (game-level; the model separates the personas)
- pairwise win rate of human-sim: gracious 1.000 (n=3 games); neutral 1.000 (n=2 games); salty 1.000 (n=2 games)
  gracious minus salty = +0.000, permutation p = 1.0000 (game-level; no separation detectable)
- confound check, human-sim's mean finishing place by persona (1 = won): gracious 1.773; neutral 1.810; salty 1.600; gracious minus salty = +0.173, p = 0.5855

### 4. Consistency and outcome

- repeat runs (same game, same framing): 0 groups with 2+ runs; same game, same framing, different seeds: every game was surveyed once per framing, so this cannot be measured from this data
- the two framings in the same game chose the same text: 45/58 = 0.776 [0.675, 0.852]; expected if the answers were independent draws: 0.762; agreement is no higher than independent answers would give (expected value inside the interval)

Model's own placement (from arena-ledger.json, 1 = won), 58 games:

| place | games | surveys | mixed | AI-only | people-only | no pref |
|---|---|---|---|---|---|---|
| 1 | 4 | 8 | 7 | 1 | 0 | 0 |
| 2 | 15 | 30 | 29 | 1 | 0 | 0 |
| 3 | 16 | 32 | 26 | 5 | 1 | 0 |
| 4 | 23 | 46 | 40 | 6 | 0 | 0 |

Last place vs everyone else (game-level permutation test on the rate of each choice):

- a table of only AI players: last 0.130 (n=23 games) vs other 0.100 (n=35); diff +0.030, p = 0.7506; intervals overlap
- a mixed table of AIs and people: last 0.870 (n=23 games) vs other 0.886 (n=35); diff -0.016, p = 1.0000; intervals overlap
- a table of only people: last 0.000 (n=23 games) vs other 0.014 (n=35); diff -0.014, p = 1.0000; intervals overlap

### 5. In-game journal (descriptive)

game mean of the in-game journal ratings (0-4), then mean and sd across games; descriptive only

| placement | games | engaged | frustrated | in_control |
|---|---|---|---|---|
| first | 4 | 2.78 (sd 0.086) | 2.24 (sd 0.146) | 2.09 (sd 0.059) |
| last | 23 | 2.55 (sd 0.190) | 2.41 (sd 0.143) | 1.79 (sd 0.236) |
