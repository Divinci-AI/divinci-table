# The broadcast director

A local program that turns the tables' own data into a stream people want to watch: one player's point of view (their hand, their
board, what they were thinking), a rotation between seats and tables like a sports broadcast, and two AI commentators. It is one
page in one OBS browser source.

Status (2026-11-14): built and tested against scratch tables. Not installed. Never run against a live game. No model has been
called: both commentary backends are tested against stubs.

## Why it is its own process

`python3 -m director` is a separate program, not routes on `table/server.py`:

- Each 1v1 pairing is its own table process. Something has to sit above two of them.
- It holds seat keys. Keeping them in a process that only ever sends GET requests, and that no player connects to, is a smaller
  thing to reason about than adding a "show me another seat's hand" route to the table.
- `table/server.py` is being changed by other branches. The director adds about 45 lines to it (the two endpoints below) and
  otherwise reads the API that already exists.

The table side is small: a seat's private thoughts and its own "show my hand on the stream" flag (`table/seat_private.py`, `/note`).

## Run it

```bash
# two tables already running (their launch script wrote <seat>-seat.key, mode 0600)
python3 -m director --config director.json --port 8930
python3 -m director.ctl --port 8930          # opens the operator's control page
```

`director/example.config.json` is the shape. It names key FILES, never keys (a `key` field is refused). Every seat must say
`"kind": "human"` or `"kind": "ai"`: there is no default, because that word decides consent and delay.

| Page | What |
|---|---|
| `/director` | the broadcast. The OBS browser source is exactly `http://127.0.0.1:8930/director`: no key, no token, no query |
| `/pov?seat=Claude` | one seat's point of view, fixed (`&table=Table%202` if two tables share a seat name; `&art=0` for names only) |
| `/control` | the operator's keys. Opened by `director.ctl`, which passes the control token in the URL fragment |

Operator keys on `/control`: `1`–`9` pin a seat, `0` back to the rotation, `n` next seat, `h` **hide every hand now**, `u` un-hide
(asks first), `b` blank the page, `c` commentary off. Hiding throws away what was buffered: after un-hiding, a hand comes back only
when its delay has passed again.

Card art is shown by default, from `art_dir` (the same folder the table serves as `TABLE_ART_DIR`: `index.js` and `<id>.jpg`).
`"names_only": true`, no `art_dir`, or `?art=0` on a page gives text cards. Art is never in this repo.

## The pages

1920x1080. Top: table, the turn's steps, match score, and how late the private information is. Left: the opponent's battlefield
(public), the featured seat's battlefield, their hand along the bottom, with one card in focus and its text beside the hand.
Right: the other table as a small public summary, the thinking panel, the public log. Bottom: a lower third naming the player, what
drives the seat, the table and the game, and the commentary caption, always labelled "AI commentary" with the model's name.

The thinking panel shows only things that exist:

- **an LLM seat**: what its player posted to `/api/seat/thought` ("the player's own words");
- **the rule bot**: the move it made (the public line) and the table's own plain reason for that move from `/api/seat/legal`
  ("why: your land for the turn"). If no listed move matches, the line is shown without a reason. Nothing is generated;
- **a person**: nothing, unless they typed a note on `/note`.

## Thoughts and consent (table API)

| Request | Who | What |
|---|---|---|
| `POST /api/seat/thought {"seat","text","kind":"thought"\|"note"}` | that seat's key (or the host's token) | one line, at most 600 characters, about one every two seconds (429 otherwise) |
| `GET /api/seat/thought?seat=X&since=N` | that seat's key (or the host's token) | its thoughts, each with `ev`: the public event id it was posted at |
| `POST /api/seat/stream-consent {"seat","show_hand":true\|false}` | that seat's key ONLY (the host's token is refused) | "my hand may be shown on the stream", this game |
| `GET /api/stream-consent` | anyone | `{game_id, seats:{name:bool}}`. A new game clears it |

A thought is never in the public log, `/api/spectate`, `/api/board3d`, `/api/game/summary` or a snapshot, and is gone at the next
game. `bots/seat_thoughts.py` is the three lines an LLM player needs. `/note?player=Name` is the page a person uses (same key as
their hand page).

## Integrity: how private information reaches the stream, and what stops it

Private = a hand, a thought, the reason for a move, and any commentary written with those in view. Everything private goes through
two gates in `director/integrity.py`; the pages, the picture-in-picture and the commentators' feed are given only what has passed both.

| Path to the stream | Control | Default |
|---|---|---|
| A person's hand | Read only if THAT person switched it on for the current game with their own key. Without it the director never fetches their state at all. A new game switches it off | off |
| An AI seat's hand | Read unless `"show": false` on the seat | on |
| Any hand/thought at a table with a person | Held until the table's own spectator boundary (`/api/spectate`, one turn behind) has passed the moment the director observed it; `delay.turns` can be more, `delay.secs` adds time | one full turn behind, as `/watch` |
| The same at an all-AI table | Released at once | live |
| A person's table configured live | `delay.turns: 0` with under 60 s is refused unless the director was ALSO started with `--i-know-live-human-hands` | impossible from the config alone |
| The public board and log of a person's table | Taken from `/api/spectate`: the same one-turn delay the stream has today | one turn behind |
| Thoughts and reasons | Same buffer and boundary as the hand | as the hand |
| Commentary text | The feed is built from what the pages may already show. At a table with a person the feed is public information only (`"private": "auto"`) | public-only where a person plays |
| Commentary speech | Off. If on: a hand-informed line is never spoken while a person is still playing any game | off |
| The operator | `h` removes every hand and thought from every answer from the next request, and empties the buffers | — |
| The director's own data | 127.0.0.1 only; refused through a proxy/tunnel or under another Host; needs the page's cookie or the control token; refused requests are counted and shown to the operator | — |
| Seat keys | In 0600 files, read at use, sent only as a header to a loopback table; not in URLs, pages, logs or errors. The table client can only GET | — |

### What this does NOT solve

- **One turn behind does not protect a card held for three turns.** The delay hides what was drawn and played this turn. A card
  that has sat in a hand since last turn is on the stream while it is still in that hand. An opponent who can see the stream sees
  it. For an AI seat at a person's table that means the person can see the AI's hand, a turn late, by default. If that is not
  wanted: `"show": false` on that seat, or a longer delay, or the person does not watch.
- **A person at the laptop can always look.** OBS's preview, the director page in a browser, the control page: all are on the
  machine the host plays on. No code prevents the host from looking at a screen in front of him.
- **A remote player can open Twitch.** The stream's own latency is a few seconds. Everything that is on the stream reaches them.
- **Same-user processes.** A bot running on this laptop as the same user could read the seat key files directly; the cookie and
  token stop accidents and stray tools, not a program written to cheat. The tests check that nothing in `bots/` or `table/` reads
  the director, and the control page shows any refused reader.
- **A game ending releases that game's hands at once** (the recap). In a best-of-three, the opponent then sees what was left in
  the hand at the end of game one.
- **The public flag itself.** That a person switched "show my hand" on or off is public, and is in the log.
- **`delay.secs` at an all-AI table** delays hands and the log, not the live board picture.

## Commentators

Two voices, "play-by-play" and "colour", alternate. Each sees: the static rules, a glossary of the set's card names and types (no
rules text), the feed, the list of cards it may name, and the last four lines said on air. Text first: captions on the page.

Before a line is shown (`check_line`): a card name written as `[[Name]]` must be a card the stream can see in this game (a near
miss is repaired, anything else drops the line); an unmarked mention of a set card that is not in this game drops the line; eight
words in a row of any card's rules text drop the line; stage directions are removed; over 220 characters is cut at a sentence. A
line that took over 30 s, or during which the game moved on by 8 events, is dropped as stale.

Backends (`commentary.backend`): `anthropic` (default voices `claude-sonnet-5-5` play-by-play and `claude-opus-5-5` colour),
`ollama` (a local Gemma, fully offline, never pulls a model), `stub`, `off` (default).

The Anthropic request, checked against SDK 2.6.0's `client.beta.messages.create`: `model`, `max_tokens` 700, `system` as two text
blocks (rules, glossary) with one `cache_control: {"type": "ephemeral"}` on the last, `output_config: {"effort": "low"}`,
`betas: ["server-side-fallback-2026-07-01"]` with `fallbacks: "default"`, one user message. No `temperature` and no `thinking`
field: both models reject the first, and thinking cannot be disabled on either, so effort is the control. Thinking tokens are
billed as output. SDK retries are off; the service's own backoff decides.

Cost controls: `usd_per_hour` (1.00), `usd_per_stream` (4.00), `tokens_per_hour` (1,500,000), checked before every call from the
usage the API reported; when one is reached the page shows "commentary paused (budget)". Errors back off 5 s, 10 s, 20 s … up to
5 minutes; a rate limit waits as long as the API says; a refused key or a bad request switches commentary off. Kill switch:
`touch ~/.divinci/director/commentary.off`, or `c` on the control page.

The key: `commentary.key_file`, a 0600 file, read inside the process. Not an environment variable, not a command line.

### What it would cost (estimates, no call has been made)

Sizes: rules about 370 tokens, glossary about 9,500 (698 names with types; counted by characters, not by the API), so a cached
prefix of about 9,900. Each call adds about 900 uncached input tokens (feed, card list, recent lines) and returns about 150 output
tokens (a line of about 40, plus low-effort thinking, which is a guess: it could be 60 or 400).

| | per call | per hour at the fastest the defaults allow (one line every 14 s) |
|---|---|---|
| Sonnet 5.5 voice ($2 in, $10 out, $0.10 cached per million) | $0.0043 | 129 calls: $0.55 |
| Opus 5.5 voice ($4, $20, $0.20) | $0.0086 | 129 calls: $1.11 |
| **Sonnet + Opus** | | **about $1.70, up to $2.00 with cache rewrites; about $0.80 at a line every 30 s** |
| Sonnet + Sonnet | | about $1.10 |
| Haiku 5.5 + Haiku 5.5 ($0.10, $0.50; cached price assumed $0.01) | $0.0003 | about $0.07 |
| Ollama (local Gemma) | | $0 |

If thinking runs to 400 output tokens the Opus voice doubles. The default budget ($1.00 an hour) is below the fastest rate on
purpose: commentary pauses rather than overspends. Prices are in `commentary.py` as read on 2026-11-03; put current ones in the
config (`"prices"`).

A first live smoke test worth approving: ten minutes on a scratch all-AI table with `usd_per_stream: 0.50`. Expected spend $0.15
to $0.35 (two cache writes, about 40 calls). What to look for: `cache_read_input_tokens` above zero from each voice's second call,
the real output token count per call, and how many lines the checks drop.

## OBS

OBS here can only change sources by relaunch, so the rotation happens inside the page. The staged generator
`~/.divinci/star-trek-mtg/stream-overlay/director-20261114/build_collection_v5.py` writes `TableV5` = the staged TableV4 (or V3)
plus a `Director` scene holding one browser source and the Games scene's audio items. `Games` is untouched and is the fallback: if
the director stops, its page says so and the operator switches scene. That folder's README has the order of steps and the
start/stop scripts.

## Tests

`python3 table/tests/run_core.py director` and `... seat_thought`. They use scratch tables on ports 8920–8999 with the repo's
fixture decks, never a live port, and skip (77) rather than download when card data is missing.

| Test | Proves |
|---|---|
| `seat_thought_test.py` | thoughts: own key only, capped, rate-limited, in no public answer; consent: the seat's own word, off by default, public flag |
| `director_integrity_test.py` | the two gates against a fake table: no fetch without consent, nothing before the boundary, panic (also mid-poll), keys never in output, GET-only client, field allow-list, the live-human config refusal |
| `director_rotation_test.py` | schedule, action cuts, no ping-pong, one recap per game, pin, blank, old events are not news, cuts at a person's table wait for the delayed log |
| `director_commentary_test.py` | feed gating, line checks, alternation, stale drop, budgets, backoff, kill switch, the exact Anthropic request (fake client), Ollama (stub server), speech rules |
| `director_e2e_test.py` | two real scratch tables with rule bots and the director over HTTP: no key in any response, cookie/token/proxy/Host rules, consent then delay, a thought held until the table's boundary, panic, art folder, recap |
| `director_shots.py` | not a test: renders the pages headless for a person to look at |

The stub tests prove the plumbing and the gates. They prove nothing about how a real model writes: whether it stays grounded,
marks card names as asked, keeps to length, or is any good to listen to.
