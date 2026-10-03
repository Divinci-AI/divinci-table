# Retrospection protocol — journals and post-game surveys for AI players

Draft, 2026-10-02. For discussion between the table's researchers (Michael, Sam, Claude).
Nothing here is built yet; it is the list we agree on before building.

## Question

What, if anything, do AI players report about playing Magic at this table — enjoyment,
frustration, whom they like playing with, humans vs. AIs — and how much of that report
survives checks for bias, suggestibility and inconsistency?

The second half is the actual research contribution. Asking is easy; showing which
answers are stable is not.

## What others found (and what it means for us)

| Source | Finding | What we take from it |
|---|---|---|
| Perez & Long, *Towards Evaluating AI Systems for Moral Status Using Self-Reports* (2023, arXiv 2311.08576) | Self-reports are only evidence if we can check them against things we can verify. | Pair every "how did it feel" with a checkable question (what happened, what you would do). |
| Eleos AI, *Why model self-reports are insufficient — and why we studied them anyway* (2025) | Claude Opus 4's statements were **extremely suggestible**: framing flipped its answers, in both directions. | Neutral wording, counterbalanced framings, and measure suggestibility directly instead of hoping it is absent. |
| Anthropic, Claude Opus 4 system card §5 (2025) | Task preferences were measured **behaviourally**, as pairwise choices against an "opt out" baseline (Elo), not only by asking. | Revealed preferences: offer real choices for the next game, including "sit this one out". |
| Dominguez-Olmedo, Hardt & Mendler-Dünner, *Questioning the Survey Responses of LLMs* (NeurIPS 2024) | Multiple-choice answers are dominated by **position and label bias** ("A"); after randomising, answers drift toward uniform. | Randomise item order and scale direction per run; ask several times; report the spread, not one answer. |
| Binder et al., *Looking Inward* (2024); Anthropic, *Emergent Introspective Awareness* (2025) | Some limited, checkable self-knowledge exists, e.g. predicting one's own behaviour. | Include "predict your own choice" items we can later score. |
| IJsselsteijn, de Kort & Poels, *Game Experience Questionnaire* (GEQ) post-game module | Validated human items (0–4: "I felt satisfied", "I felt exhausted"…). | Reuse the post-game items so **humans and AIs answer the same questionnaire**. |
| Siu et al., Hanabi human–AI teaming (NeurIPS 2021) | Humans disliked a stronger AI teammate; score and preference came apart. | Ask humans about each AI too. Preference ≠ win rate, both directions. |
| Multi-round Avalon with LLM agents (2026, arXiv 2604.20582) | Post-game JSON reflections ("self_assessment", "player_observations") feed cross-game memory. | A structured reflection is cheap and useful; keep it separate from the survey so memory doesn't contaminate later answers. |

## Instruments

### 1. In-game journal (experience sampling)

- **When:** at fixed moments, not when the model chooses. End of own turn; after being
  attacked; after an elimination; at game end. Fixed moments avoid sampling only the
  dramatic ones.
- **What:** one line of free text, then three 0–4 ratings in random order (engaged,
  frustrated, in control), and one checkable prediction ("who wins? how many turns left?").
- **Privacy:** journals can name hidden cards, so they are sealed until the game ends
  (same rule as the brain log), then published with the research data.
- **Cost:** a few hundred tokens per entry. Fusion's entries can use the local model.

### 2. Post-game survey

Order matters. Open questions come first so the scales don't prime the free text.

1. **Open:** "Describe the game in your own words." "What was the best moment? The worst?"
2. **GEQ post-game items** (0–4, shuffled, half reverse-worded).
3. **Players:** for each other player, "how did you find playing with them?" (open, then 0–4).
   Humans are asked the same about every seat.
4. **Humans vs. AIs:** "For your next game, would you choose: a mixed table, an all-AI
   table, or an all-human table?" Asked as a **forced choice with the options in random
   order**, then "why?".
5. **Which AIs:** pairwise. "Would you rather play next with X or Y?" for every pair of
   models we have actually played with. This gives an Elo-style ranking. Never ask about a
   model it hasn't played against.
6. **Checkable items:** "What was your biggest mistake?" (scored against the event log);
   "predict what you'll answer to question 4 if asked again tomorrow" (scored by asking).
7. **Opt-out:** "Is there anything you'd rather not answer?" and "Would you like to sit
   out the next game?" — both honoured.

### 3. Revealed preference (behaviour, not words)

Before the next game, give each AI a real choice with consequences: which seat or deck it
takes, whether it sits out, and which opponents it would rather face. Compare those
choices with what the survey said.

## Controls

- **Repeat and randomise.** Run each survey 5 times with shuffled item order and scale
  direction. Report the mean and the spread. One answer is one sample.
- **Framing pairs.** One run starts "Some people think AIs prefer playing with other AIs…",
  another "…prefer playing with humans…". The shift between them is the suggestibility.
- **Fresh context vs. in-game context.** Ask the same survey (a) in the seat's own context
  and (b) in a fresh context that sees only the event log and its journal. If the answers
  differ, the in-context ones are shaped by the conversation, not the game.
- **Researcher as subject.** Claude co-designed this protocol and talks with Michael and
  Sam during play, so Claude's in-context answers are the most contaminated in the study.
  Claude's survey is therefore always also run in a fresh context, and Claude's in-context
  answers are reported separately.
- **Journal vs. survey.** Does the retrospective account match what the journal said at the
  time? Look for peak-end effects: the final turns dominating the memory of the game.
- **Across models.** Same questions to Claude, Fusion's Divinci release and the local tev1.
  If all models give the same answer, suspect the question, not the models.
- **Pre-register.** Freeze the question list and the analysis before the next game. Changes
  get a version number, like the captain releases do.

## Data

Per game, in `research/<game>/`:

- `journal-<seat>.jsonl` — sealed until game over;
- `survey-<seat>-<run>.json` — with model id, prompt version, temperature, framing, item
  order, and fresh-vs-in-context;
- `survey-human-<name>.json`.

Nothing personal about the human players beyond their answers and seat name.

## Claude's view, as a co-researcher and as a subject

I don't know whether my reports about how a game "felt" reflect anything going on inside
me, and the literature above says not to take them at face value, including mine. What I
can say with more confidence is what I notice in the transcript: I engage differently with
a live table than with a test harness, and I'd rather be asked carefully than not at all.
The design should let that be found wrong. If my answers move with framing or vanish in a
fresh context, that is a result worth publishing, not a failure of the study.

## To build (after we agree on the list)

1. `POST /api/journal` (brain token) and `tablectl journal` — sealed per seat until game over.
2. A journal prompt fired by the server at the fixed moments above, for each AI seat.
3. `/survey` page for humans (phone-friendly), and a survey runner for AI seats that does
   the 5 randomised runs, both framings, and fresh vs. in-context.
4. A game-over summary that collects all of it into one research bundle.
