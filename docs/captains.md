# Captain voices

Every commander at the table speaks now and then: one short, in-character line when something
happens to its seat. The line is shown on every page (table, stage, phones, AR/VR) and played in
the commander's own voice.

| Commander | Seat | Voice (Aura-2) | Character |
|---|---|---|---|
| Kilo, Apogee Mind | Michael | `hermes` | precise robot inventor who thinks in increments |
| Captain N'ghathrod | Sam | `draco` | drowned dread-captain who covets your graveyard |
| Aminatou, the Fateshifter | Claude | `luna` | young seer who reads the threads of fate |
| Kaust, Eyes of the Glade | Fusion | `orpheus` | moss-green dryad detective with a brass monocle |

The personas are in `table/captains/personas.json`. They are our own sketches, written around each
card's rules text, not Wizards of the Coast story text. On the AI seats the captain is a separate
voice from the player: Claude and Fusion still do their own table talk.

## How it works

- **Text:** each captain is its own Divinci release (a draft, never published), whose system prompt
  is the persona plus the table rules. `table/captains.py` sends it one public EVENT and gets back
  one line of at most 15 words. Each captain keeps a transcript, so it remembers what it already said.
- **Voice:** Workers AI Aura-2 (`@cf/deepgram/aura-2-en`), one speaker per captain. Each release also
  records its voice in `ttsVoiceOverride`. The mp3s are cached by content under
  `table/.cache/captains/audio/`.
- **When:** a seat's turn starting, casting, attacking or turning a creature face up, and losing or
  gaining life. Each trigger has a chance, and there's a 25 s gap between any two lines and a 75 s
  cooldown per captain. `--chattiness 0.5` halves it.
- **Where:** the server's `POST /api/captain` (brain token) turns a line into a public `captain`
  event. The table page plays it once the AI voices are quiet; the stage plays it with `?sound=1`
  or the **S** key; the AR/VR view plays it in the headset; phones show the text.

## Hidden information

A captain only ever sees public events, so it can't know anyone's hand. As a second check, the
server refuses (409) a line from an AI seat's captain that names a card still in that seat's hand.

## Run it

`CAPTAINS=1 table/play.sh …` starts it with the server. On its own, or to try one line on this Mac:

```bash
infisical run --projectId="$INFISICAL_WORKSPACE_ID" --env=prod --path=/ -- \
  /usr/bin/python3 table/captains.py --try "Captain N'ghathrod" "Michael lost 4 life."
```

It needs `DIVINCI_FUSION_API_KEY`, `CLOUDFLARE_WORKER_AI_KEY` and `CLOUDFLARE_ACCOUNT_ID` from
Infisical, and the release IDs in the gitignored `table/.cache/captains.json`. Every prompt, line,
voice and timing is logged to `table/.cache/research/captains.jsonl` for the research data.

Each line takes about 6–8 s (release reply plus speech), so a captain reacts a beat after the play.
