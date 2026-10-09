# Findings from Opus

One bullet per thing in the interface that confused you, refused you without a clear reason, or broke. Quote the command and the answer.


## Turns 1-3
- `tc phase` before start said to run `highroll quantum`; the roll (Opus 16, Sonnet 15) decided seating. The brief says I go first; it only matched because I won the roll. A roll could have contradicted the agreed order.
- Starting player DREW on turn 1: `tc begin` on turn 1 -> `🔒 private: {"drew": "Ainok Survivalist"}`, hand 8. In a two-player game the starting player skips the first draw (rule 103.8a).
- `tc journal-due` (listed in --help; events say `📓 journal due for Opus (end_of_turn): tablectl journal-due`) -> `refused: this page is only available on the table's laptop`. The table asks the seat to do something it refuses.
- `tc state` shows NO opponents section and no `incoming`, although the brief says it does. I can only learn Sonnet's board from `events` SAID lines ("I play Forest", "I play Island").
- Ransom Note ETB "surveil 1" did not happen: `tc cast "Ransom Note"` -> `🗣  I cast Ransom Note.` and nothing else; no prompt, no event. Trying to do it by hand: `tc peek 1` -> `refused: 'peek' isn't available to a pilot seat: it has no card behind it (a pilot plays: attack, begin, block, cast, damage, end, land, life, pass, say, tap, turn-up, untap); the table's host can do it`. So surveil cannot be done at all from a pilot seat.
- Battlefield display: on turn 2 `state` listed only `#2 Wild Growth → on #1` and `#5 Kaust`; Command Tower #1 itself vanished from the list while untapped (it appears again when tapped). `mana sources untapped: Command Tower (BGRUW), Command Tower (G)` shows the aura's extra G as a second "Command Tower" source.
- Combat turn 3: `tc attack "Kaust, Eyes of the Glade=Sonnet"` -> damage applied when Sonnet declined to block (`LIFE Sonnet -2 → 38 (by combat)`) while phase still said `declare attackers`; afterwards `phase` showed `{'need': ['Sonnet'], 'passed': ['Sonnet']}` (contradictory) and never advanced to blockers/damage on its own. `tc end` moved straight to cleanup.
- Battlefield permanents show no rules text (Kaust's own abilities are not visible anywhere in `state`); I learned Kaust's {T} turn-up only from `turn-up --help` ("Kaust's {T} ability").

## Turns 4-5
- Turn 4 attack with two creatures: `tc attack "Kaust, Eyes of the Glade=Sonnet" "#8=Sonnet"` -> resolved as two separate block decisions ~3 s apart (`LIFE Sonnet -2 → 36`, then `-2 → 34`). Fine, but slow (~30 s).
- Turn 5 `tc turn-up "#8" --free` (Kaust's {T}) -> `I turn #8 face up: Ainok Survivalist.` / `Ainok Survivalist gets a +1/+1 counter.` / `🔒 private: {"todo": ["Ainok Survivalist: destroy target artifact or enchantment an opponent controls"]}`.
  (a) Kaust was NOT tapped by `--free`; I tapped it by hand with `tc tap "Kaust, Eyes of the Glade"`. Nothing stops a player using Kaust's {T} repeatedly.
  (b) The megamorph +1/+1 counter was given although the megamorph cost was not paid (counter should come only when turned up for its megamorph cost).
  (c) The turn-up trigger is only a private "todo"; there is no way to target. `tc destroy "Kestia, the Cultivator"` -> `refused: 'destroy' isn't available to a pilot seat: it has no card behind it (...); the table's host can do it`. I asked with `say`; Sonnet ignored it and then blocked with Kestia. So a removal trigger I was entitled to never happened.
- Combat disagreement: Sonnet: `I block Ainok Survivalist with Kestia, the Cultivator.` / `Kestia, the Cultivator deals 4 back — Ainok Survivalist dies.` but my `state` still shows `#8 Ainok Survivalist 3/2 [tapped]` on the battlefield and graveyard `—`. No record of Ainok's 3 damage to Kestia either. Board and narration disagree, and a pilot seat has no command to move its own creature to the graveyard.
- I still cannot see Sonnet's board in `state` at all (Kestia's P/T, whether it has counters, etc. come only from SAID lines).

## Turns 5 (opp) - 6
- During Sonnet's turn `phase` said `{'need': ['Opus'], 'passed': ['Opus']}` and `tc pass` -> `refused: you already passed declare attackers: waiting on None` (also for main 1). "need" lists me while the server says it waits on None.
- I was attacked (`⚑ FOR YOU (attacked): "Sonnet attacks you with Kestia, the Cultivator (4)..."`) but `tc state` had no `incoming` section; only `events` showed it. `tc block` (no creature) -> `No blocks. I take 4; I'm at 36.` worked.
- No command to activate an ability: Mirror Entity's `{X}: creatures become X/X` cannot be activated by a pilot (no `activate`; `damage`/`attack` read power from the table). With 5 open mana this was a ~15-damage swing I could not take. Same for Ransom Note's `{2}, sacrifice` and Kessig Wolf Run's pump.
- Whisperwood Elemental's end-step manifest did not happen: `tc end` -> `That's my turn.`, no manifest, no todo. `tc manifest` -> `refused: 'manifest' isn't available to a pilot seat ...`. So the trigger is lost every turn.
- I treated Ainok as dead (per Sonnet's block narration) and did not attack with it, though the table still lists it.

## Turns 7-8 and summary
- Kestia carried Righteous Authority (+1/+1 per card in its controller's hand) from Sonnet's turn 6, yet the table attacked me for `Kestia, the Cultivator (4)` and on my turn 8 Yedora (5 power) killed it (`Kestia, the Cultivator dies.` / `deals 4 back`). The table appears to use printed power and ignore the aura. I cannot check Sonnet's hand size, so I cannot be sure.
- Damage dealt to my creatures in combat (Kestia's 4 to Yedora, Slime's 1 to Boltbender) shows nowhere in `state`; fine because they survived, but nothing would track it if a second source hit them that turn.
- Gruul Turf: `tc land "Gruul Turf"` picked which land to return for me (`Gruul Turf returns Mountain to my hand.`), with no choice offered. It happened to be the one I wanted.
- Showstopping Surprise's rules text in `state` reads "...deals damage equal to its power to each other creature." I did not cast it.
- `events` (no --since) sometimes printed old lines (#295-#300) as its tail while the last event was #404; `events --since N` was reliable.
- Pilot seat allowed commands: `attack, begin, block, cast, damage, end, land, life, pass, say, tap, turn-up, untap`. Missing for a real game: activate an ability, surveil/scry, manifest, destroy/exile/move my own dead creature, target a trigger, see the opponent's board, see my own permanents' rules text.
- Final (table): Sonnet 5, Opus 32 after my turn 8.
