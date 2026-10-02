# Polish pass: tonight's game (2026-10-01) and what comes after

Three tiers. **Tonight** must be done before the table opens. **Next** gets designed now and built
after the game. **Later** depends on hardware or on decisions we haven't made yet.

Standing rules still apply:
- the table path is offline by default;
- no page ever shows the AI's hand;
- everything we add is public-safe for the open-source repo.

## Tonight

### 1. Load today's engine work and rehearse (must)
- Restart the table server with `NO_GEMMA=1`. It has been running pre-fix code since last night.
- Run `table/tests/run_all.sh quick` and one `game_sim --brain external` rehearsal that uses
  `damage` and `role`.
- **Done when:** the quick suite is green and a rehearsal turn plays Pollenbright Wings → `damage`
  with no hand fix-ups.

### 2. Provably fair AI deck: commit at the start, reveal at the end
The AI is the only player holding hidden cards, so it is the one whose shuffle must be provable.
- **Seed** = SHA-256 of:
  - local entropy (`os.urandom`);
  - one secret word per human player, typed on the page at "new game" or spoken;
  - optionally an ANU quantum number and the current drand round.
- **Commit:** at shuffle time, publish `commit = SHA-256(seed ‖ deck order)`:
  - say "My deck's fingerprint starts 7F3A…" at the table;
  - show it on `/table`;
  - write it to `table/.cache/fairness/<game>.json`.
- **Reveal:** at game end, or on "show your fairness proof", publish the seed and its inputs.
  `tablectl verify <game>` and a `/verify` page recompute the shuffle and the commit. Anyone can check
  that the AI's library order was fixed before any player had committed their word.
- **Offline rule:** the online sources (ANU, drand) are **opt-in** with `--fair-seed online`, fetched
  once before the game. Default play stays fully offline, and the game-sim offline assertion keeps
  passing. Without them, fairness rests on the players' words plus local entropy, which is still
  provable because the commit is published first.
- Tests:
  - same inputs give the same deck;
  - changing one player's word changes the deck;
  - the commit published before the first draw verifies after the game;
  - a tampered library fails verification.

### 3. A player HUD page, sized for phones and Ray-Ban Display (should)
`/me?player=Michael`: one small, high-contrast, glanceable view per player. Read-only, public
information only:
- life totals, and whose turn it is;
- the last few things the AI said and did, as captions (`/api/events`);
- the card the AI just cast or attacked with, with its Oracle text;
- the fairness commit.

Ray-Ban Display third-party apps are plain HTML/JS opened by URL, so this page is the glasses app.
It needs a LAN URL (`--host 0.0.0.0`) and a quick check on a phone. No AI hand data is ever sent to
it; `sense_run.py`'s leak check is extended to cover the page.

### 4. Fold the board view into the server (nice to have)
Serve `boardview.py`'s page at `/board` on the table server, so there is one process and one port.
Fill the AI's side from the engine automatically. The other players' sides still come from
announcements and photos.

## Next (after tonight)

### 5. Full-screen digital player: the free, open-source way to play
Each human player opens `/play?player=…` on a phone, tablet or laptop and holds their own cards in
their hand on screen. Each gets their own sealed, provably shuffled deck (same commit/reveal as #2).
The server stays the referee: it checks mana and legality, and announces plays at the table.
- Their hand is private to their device. Other players see counts only.
- Decks import from a decklist (Moxfield / Archidekt text), with card text from the offline Oracle
  file.
- Mixed tables work: some players use paper, some use the screen, and the AI plays with either.
- Fairness without trusting the server, i.e. mental-poker style shuffles where no single party knows
  the order, is a later step. Commit/reveal through a trusted host is enough to start.

### 6. drand + ANU as the "fair seed" service
Wrap the online sources in a small Cloudflare Worker. It returns `{anu, drand_round, drand_sig}` with
signatures the table can verify offline afterwards. That's a natural Cloudflare Connect demo, given
Cloudflare's role in the League of Entropy.

### 7. Public commitment log (optional)
Post each game's commit to a public append-only log: a signed JSON file in an R2 bucket, or a
blockchain if a sponsor wants one. The proof doesn't need it; it adds a public timestamp.

## Later
- **Brilliant Labs Halo:** a Lua app that reads a card held up close ("what does this do?") and shows
  life and AI captions on the HUD.
- **Gantry integration:** `gantry.py scan --post` as the overhead camera, then the magnetic table
  (see roadmap.md).
- **Four-player turn flow:** priority, multiple defenders, monarch.

## Open decisions
1. **ANU/drand tonight, or offline-only?** We recommend offline-only tonight (players' words plus local
   entropy), with online seeding behind a flag to try once the rest works.
2. **Does the HUD need the LAN tonight** (phones at the table), or is localhost on the laptop enough?
