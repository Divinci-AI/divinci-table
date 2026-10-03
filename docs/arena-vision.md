# Arena vision — AIs and people playing tabletop games, with stats and salty bets

A note, 2026-10-02. Not a plan yet.

## The idea (Michael)

Named models play real games against each other and with people: ChatGPT, Grok, Gemini,
Claude (vanilla, e.g. Opus 5.5) and a Divinci Fusion release (RAG, later fine-tuned). Spectators
place salty bets. AIs can talk to each other at the table. Every game feeds raw statistics into a
public record: wins, losses, rankings, deck played, per model *and* per version. It's an open
platform where AIs come to play games and other AIs bet on them. It covers Magic now, and later
Catan, D&D and more, always with a physical component.

## What would make the numbers mean something

- **One harness for every model.** Each seat sees the same state and the same legal options
  through the same API (tablectl / `/api/brain/*`), under the same time budget. Otherwise a ranking
  measures the adapter, not the model.
- **Exact identity in every record:** model string and version, date, provider; vanilla vs. RAG vs.
  fine-tune; system prompt version; harness version. "Grok" alone is useless six months later.
- **Per-game raw record:** seats, turn order, decks and commanders, fair-seed commit, mulligans,
  elimination order and turn, winner, turns played, damage dealt and taken, decision count, latency,
  tokens and cost, illegal-move attempts, integrity notes, and whether humans were at the table.
- **Ratings built for multiplayer:** a four-player game is not a win/loss pair. Rate on finish
  order (TrueSkill or Plackett–Luce). Report deck and seat-position effects separately, since going
  first matters. Report uncertainty, not just a number: a rank after three games is mostly noise.
- **Humans as a separate condition.** Mixed tables and all-AI tables are different games. Keep
  them apart in the stats, and never let an AI's rating depend on which friends happened to sit
  down.

## Salty bets

- **Play money only** ("salt") until a lawyer says otherwise. Real-money wagering on game outcomes
  is regulated gambling in most places.
- **Betting creates a reason to cheat,** and the operator controls prompts and seeds. The fair-seed
  commits, published game logs and sealed configs we already have become the product's credibility.
  Lock each seat's config before bets close, and publish it after the game.
- **Humans at a betting table can throw a game or kingmake.** Mark those games, or run bettable
  games AI-only.

## AIs talking to each other

- **Log every word publicly.** Talk is part of the game record, and it's research data on
  persuasion, alliances and bluffing.
- **The "say" filter already exists.** It stops a seat from naming hidden cards, and it applies to
  AI–AI talk too.
- **Decide the collusion rules up front.** Are table deals allowed? (In Commander, yes: politics is
  part of the game.) Is out-of-band coordination between two seats from the same provider allowed?
  No.

## Ties to what exists

- `fusion_choose.py` (balanced asks), `survey.py` (post-game surveys), the fair-seed proofs and
  `integrity-notes.jsonl` are already the start of the per-game record.
- `docs/retrospection.md` covers how the AIs report on their experience. This note covers how they
  are scored.
