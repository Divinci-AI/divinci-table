# Camera gantry + magnet hand — assembly manual

A Creality **CR-6 Max** 3D printer turned into eyes and a hand for the table. A **Canon EOS Rebel T5i** looks straight
down at the cards on the bed, and a small **24 V electromagnet** on the print head picks up the AI's cards, which
carry a steel disc inside the sleeve. The printer only *moves*: it never heats or extrudes (`table/gantry.py`
refuses those G-codes). This is the first step toward the "Jumanji table" in [docs/roadmap.md](../../docs/roadmap.md):
an AI that moves its own cards.

**Only the AI's cards have steel in them, so the magnet physically cannot lift a person's card.**

Open source (Apache-2.0, like the rest of this repository). Diagrams are hand-made SVGs in [`img/`](img/). Every part
below is listed in [`hardware/bom.json`](../bom.json), and `table/tests/bom_test.py` fails if this page and that file
disagree. **Status (2026-10-05): designed from the parts' specs, not yet assembled; reviewed by Hermes.** Step 9 is the test that
decides whether the printer is the hand or the SO-101 arm is ([docs/ARM-GANTRY-GOAL.md](../../docs/ARM-GANTRY-GOAL.md),
section M). The suction design this replaced is in git history.

![The whole rig](img/overview.svg)

## Safety first

- **The CR-6 has a known USB fault.** Users have measured about 20 V on the USB connector's *shell* and lost the USB
  ports of the computer they plugged in (a short from the 24 V heated bed near the bed clips; fixed on board v4.5.3).
  Taping the cable's +5 V pin does **not** fix that; only the bed-clip fix does. Do Step 6, including the voltage
  measurement, *before* the printer's USB cable ever touches the Mac, and make the first connection through a
  spare hub or an old laptop.
- **Never run a normal print with the camera or magnet head attached.** `gantry.py` allows only motion and status
  commands (and, once added, magnet on/off).
- **Z homing drives the head down.** With anything hanging below the nozzle it can crash into the bed. Use
  `gantry.py home` (X and Y only) until `min_z` is measured (Step 7).
- **The magnet is full-on or off, never in between.** The fan output can run at partial power (PWM); a part-powered
  magnet holds weakly and can drop a card mid-move. Only `M106 S255` and `M107` are ever sent.
- **The magnet must not stay on without a card.** The maker rates it for continuous use *under load* and warns that
  long unloaded energising overheats it. The software caps its on-time, and because a cap dies with the process that
  enforces it, the driver also sends `M107` when it exits and the table server sends it when it starts.
- **People's cards and hands stay off the bed.** The bed is the AI's side of the table; the person plays beside the
  printer.

## Parts

Prices as seen on 2026-10-04; any equivalent part works.

**Already on hand**

| Part | Qty | Used for |
|---|---|---|
| Canon EOS Rebel T5i with 18–55 mm lens | 1 | the eyes |
| Creality CR-6 Max 3D printer | 1 | prints the Step 3 parts, then becomes the gantry |
| PLA+ filament | — | the Step 3 parts |
| Zip ties | ~10 | backing up the tape, tidying cables |

**Printed (Step 3, before the printer becomes the gantry)**

| Part | Qty | Used for |
|---|---|---|
| Printed magnet swivel | 1 (+1 spare) | holds the magnet and turns in its bracket |
| Printed deck box with a one-card exit slot | 1 | the AI's shuffled deck, face-up and covered; one card out at a time |
| Printed deck box follower plate | 1 | rides on four springs, keeping the top card at the exit slot |
| Printed discard chute | 1 | graveyard and exile go off the bed's edge into a tray |
| Printed privacy wall | 1 | hides the AI's face-up hand zone from the person |

**Check you have these (buy only if missing)**

| Part | Qty | Used for |
|---|---|---|
| M3 × 6 mm screw | 1 | magnet to the printed swivel (the magnet has an M3 thread in its back) |
| 22 AWG two-core wire, about 1 m, with heat-shrink or lever connectors | 1 | extending the magnet's leads to the part-fan connector |
| Kapton (polyimide) tape | 1 roll | under the bed clips, and over the USB cable's +5 V pin |
| Micro-USB cable for the printer (came with it) | 1 | printer to the Mac |
| USB-C to USB-A adapter | 1 | the Mac has only USB-C; both cables end in USB-A |
| Card sleeves, inner and outer, for the AI's deck | 2 per card | the steel disc sits between them |
| Non-slip shelf liner, cut to the bed | 1 | stops the moving bed from sliding the AI's cards |
| Multimeter | 1 | the USB-shell voltage check (Step 6) and the 24 V check (Step 5) |

**Hardware store (The Home Depot, via Instacart) — $46.49**

| Part | Qty | Used for |
|---|---|---|
| Everbilt aluminium angle 1" × 3 ft × 1/8" | 1 | camera bracket (~6") and magnet bracket (~3") |
| Everbilt 1/4"-20 × 1" zinc hex bolt | 4 | camera bracket; the swivel's axle; spares |
| Everbilt 1/4"-20 wing nut (4-pack) | 1 | setting the swivel's drag |
| Everbilt nylon spacer 1/2" × 1" (0.257" bore) | 2 | cut down: a low-friction washer under the bracket |
| Everbilt 1/4" rubber flat washers (10-pack) | 1 | damping under the ball head; the tap station's grippy foot |
| Everbilt spring assortment kit (84-pack) | 1 | one compression spring for the swivel's drag |
| Scotch Extreme double-sided mounting tape 1" × 48" | 1 | brackets to the beam and the carriage (no drilling into the printer) |

**Online (Amazon) — the exact parts.** Only the first three are in the cart ($33.97): the M1 test kit. The rest
are in *Save for later* until the deck-stack test in Step 9 passes 19 times out of 20; M1 uses the table's
existing fixed camera, so the Canon mount waits too.

| Part | Link | Price |
|---|---|---|
| Heschen 24 V holding electromagnet HS-P20x15 | [amazon.com/dp/B078K3TKFZ](https://www.amazon.com/dp/B078K3TKFZ) | $7.19 |
| totalElement 1/2" steel discs, no adhesive (250-pack) | [amazon.com/dp/B081J52GY6](https://www.amazon.com/dp/B081J52GY6) | $20.79 |
| 1N4007 rectifier diodes (125-pack) | [amazon.com/dp/B0FC2CQF24](https://www.amazon.com/dp/B0FC2CQF24) | $5.99 |
| Gonine LP-E8 dummy battery / ACK-E8 AC kit | [amazon.com/dp/B01EMNB8P6](https://www.amazon.com/dp/B01EMNB8P6) | $19.99 |
| SCOVEE 10 ft Mini-USB camera cable for Canon Rebel | [amazon.com/dp/B078SRKRLK](https://www.amazon.com/dp/B078SRKRLK) | $8.55 |
| CAMVATE 1/4"-20 mini ball head (2-pack) | [amazon.com/dp/B07D9JCP18](https://www.amazon.com/dp/B07D9JCP18) | $9.80 |
| RSHTECH 7-port powered USB hub (USB-C and USB-A) | [amazon.com/dp/B0CGX8LNMC](https://www.amazon.com/dp/B0CGX8LNMC) | $31.99 |

The Amazon cart reads **$33.97**. Save for later holds the other four parts above ($70.33; the hub can be dropped if
a USB-C adapter is on hand) and the SO-101 arm, its wrist camera and its kill switch ($226.96, listed in the goal
doc), all waiting on Step 9's result.

**Tools:** drill with a 1/4" bit, hacksaw, file, screwdrivers, multimeter, wire strippers, a lighter or heat gun for
heat-shrink.

## Step 1 — Cut the brackets

Cut the aluminium angle into a **~6" piece** (camera) and a **~3" piece** (magnet head). File the edges smooth.
Drill a **1/4" hole** about 1" from one end of each piece, centred on one leg.

## Step 2 — Mount the camera on the X beam

![Camera mount](img/camera-mount.svg)

1. **First, run the carriage from one end of the X beam to the other by hand** (printer off). On the CR-6 Max the
   carriage rides on the beam's **front** face, so nothing may be stuck there. Stick the 6" angle's *undrilled* leg to
   the **top (or back) of the X beam, outboard of the carriage's travel** (the left end), with mounting tape, the
   drilled leg sticking out over the bed. Back it up with two zip ties around the beam, and run the carriage end to
   end again: it must not touch the bracket, the ball head, the camera or its cable.
2. From the top: **bolt** down through the hole → **rubber washer** → screw the bolt into the **ball head's
   base**. Hand-tight.
3. Screw the ball head's top screw into the camera's tripod socket. Fit the **dummy battery** and plug in its AC
   adapter. Plug the **Mini-USB cable** into the camera, and route it along the left upright (it reaches the Mac
   in Step 6).
4. Set the lens to **18 mm**, autofocus off (manual focus on the bed), and point the camera straight down.

*Why the beam:* the camera and lens weigh ~800 g. On the X carriage they would shake and strain the X motor; on
the beam they only ride up and down with Z.

## Step 3 — Print the parts (while it's still a printer)

Everything here is PLA+, printed *before* Step 4, because after that the printer is the gantry. The sources are
parametric OpenSCAD files in [`parts/`](parts/) with ready STLs in [`parts/stl/`](parts/stl/); measure your cards,
sleeves and bed edge, change the numbers at the top of a file, and re-render with `scripts/build_parts.sh`.

- **Printed magnet swivel** ([`magnet_swivel.scad`](parts/magnet_swivel.scad); print a spare): 24 mm across,
  100% infill, top hole up. The magnet screws on underneath with the M3 screw, dropped in through the top hole;
  the 1/4" bolt's head slides into the sideways hex slot, so the bolt turns *with* the swivel.
- **Printed deck box with a one-card exit slot** ([`deck_box.scad`](parts/deck_box.scad)): a card magazine. A lid
  over the back hides the deck; the front strip is open for the magnet; side lips hold the top card down, and the
  front wall stops exactly one card below them, so only the top card can slide out. Set `card_t` from ten sleeved
  cards with their discs, measured together.
- **Printed deck box follower plate** (the same file, `part = "follower"`): sits on four springs from the
  assortment in the box's floor sockets and pushes the stack up as cards are drawn. Pick springs that a full deck
  compresses to about `spring_room` (14 mm).
- **Printed discard chute** ([`discard_chute.scad`](parts/discard_chute.scad)): hooks over the bed's front edge
  (set `bed_t`) and drops cards into a tray: graveyard and exile need no bed space.
- **Printed privacy wall** ([`privacy_wall.scad`](parts/privacy_wall.scad)): a low wall with taped feet along the
  bed's front edge, in front of the AI's hand zone, so the person can't read the AI's face-up hand.

## Step 4 — Build the magnet head

![Magnet head](img/magnet-head.svg)

1. Stick the 3" angle to the side of the **X carriage** with mounting tape plus zip ties, the drilled leg
   horizontal and sticking out under the carriage.
2. Screw the **magnet** to the swivel's floor from inside with the **M3 × 6 mm screw** (snug, not tight: PLA
   cracks), then slide a **1/4" bolt**'s head into the hex slot, thread up.
3. Cut ~6 mm off a **nylon spacer** with the hacksaw: it's a low-friction washer. The stack, from the bottom: swivel →
   nylon washer → up through the bracket's hole → a short **spring** from the assortment → **wing nut**. The bolt
   turns in the bracket's hole. The wing nut sets the drag: tight enough that a card doesn't spin while moving, loose
   enough that it turns when its corner is held (Step 9). If the thread runs out, use a 1.5" bolt.
4. The **magnet's face must be the lowest point** of the carriage: at least 5 mm below the nozzle and everything else.

## Step 5 — Wire the magnet to the part-fan output

![Magnet wiring](img/magnet-wiring.svg)

The part-cooling fan output is 24 V and switched by G-code: **M106 S255** = magnet on, **M107** = off. No extra
board, power supply or switch.

1. **Printer off and unplugged.** Unplug the part-cooling fan at its connector and note which wire is **+**
   (usually red).
2. Extend the magnet's leads with the **22 AWG two-core wire**, magnet **+** to the fan connector's **+**.
3. Put a **1N4007 diode** across the magnet's leads: the **striped end (cathode) to +**, the other to −. Without it the
   switch-off spike can kill the board's fan transistor. Insulate with heat-shrink.
4. Measure before trusting it: the magnet draws about **0.25 A**, the stock fan about 0.10 A. Read the board's
   version and the fan output's transistor, and if in doubt check that it stays cool after a minute of `M106 S255`
   with a card held. If it can't carry the magnet, the fallback is a MOSFET module switched by that output
   (see the goal doc).

## Step 6 — USB: make it safe, then connect

1. Read the version printed on the control board. v4.5.3 has Creality's fix; v4.5.2 and earlier are affected.
2. Put **Kapton tape** under the bed clips, wherever a clip or screw sits near the heater's traces. This is the fix
   for the shell fault.
3. **Measure before plugging in.** Printer on, cable plugged into the printer only: with the **multimeter** on DC
   volts, measure between the cable's USB-A plug *shell* and the Mac's ground (the metal of a USB-C port's shell, or
   any unpainted metal on a cable already plugged into the Mac). Over about **1 V**: don't plug in; recheck the bed
   clips. Hermes's suggestion: make the very first connection through a spare hub or an old laptop anyway.
4. On the printer's **micro-USB cable**, cover the **+5 V pin** (the outermost contact on one side of the USB-A
   plug) with a sliver of Kapton tape. That stops the Mac and the printer powering each other; it does nothing for
   the shell fault.
5. Connect the printer to the Mac through the **USB-C to USB-A adapter**, or the powered hub (the RSHTECH hub above)
   if it's bought later.
6. `gantry.py ports` should list the printer.

## Step 7 — Measure the rig and set limits

With the printer powered and connected, home X and Y only:

```bash
~/.venvs/table/bin/python table/gantry.py home
~/.venvs/table/bin/python table/gantry.py info
```

Cut the **non-slip shelf liner** to the bed and lay it flat: the bed moves in Y, and bare it lets cards slide.

Raise Z well above the bed, then lower it in small steps (`gantry.py goto <x> <y> <z>`) until the magnet *just*
touches a card. Write what you measured to `table/.cache/gantry.json`:

```json
{
  "min_z": 12,
  "lens_at_z0_mm": 85,
  "cam_offset_mm": [0, 0],
  "focal_mm": 18,
  "y_feed": 1200
}
```

- `min_z`: the lowest Z at which the magnet still clears the bed by ~2 mm (the touch-down height + 2).
- `lens_at_z0_mm`: lens front to bed at Z = 0 (measure at some Z and subtract).
- `y_feed`: the bed carries the cards, so Y moves slowly or the cards slide.

## Step 8 — Sleeve the AI's deck

Only the AI's deck. For each card: the card into its **inner sleeve**, one **steel disc** centred on the inner
sleeve's *back*, then the **outer sleeve** over both. The disc sits behind the card's back, so the magnet picks the
card face-up through the card. Never put steel in a person's cards.

## Step 9 — First picks and the magnet test (M1)

M1 uses the table's existing fixed camera; the Canon on the beam waits until the result says the printer is the hand.

1. **Single picks:** place one sleeved card, move the magnet over its disc, lower to the touch-down height,
   `M106 S255`, raise 20 mm, move, pause, lower, `M107`. Repeat in different spots; note the reliable touch-down
   height. Pause briefly after every move so a card that turned stops before it's set down.
2. **Doubles, the test that decides the route:** 20 draws from a 40-card stack in the deck box with the one-card
   exit slot, sliding each top card out. Count how often a second card comes too. **Pass: at least 19 of 20.**
   - Hermes's cheap first try: a staple or a paperclip in a few sleeves instead of a disc. Less steel may couple
     less to the card below.
   - Also try tape on the magnet's face and lifting it straight out of an open box, for comparison.
3. **Tap by arc:** glue a **rubber washer** to the bed as the tap station. Put a card's corner on it, pick the card at
   its disc, lower so the corner presses on the washer, then move the magnet a quarter circle around that corner
   (G2/G3). The card turns 90°. Ten tries: within ±5° and ±3 mm? If the disc slips against the magnet instead of the
   card turning, the MG90S servo fallback is in the Amazon list.
4. Record the numbers in the goal doc's section M. Nothing else gets ordered until the doubles test passes.

## Game rules on the magnet route

- **The AI never shuffles or searches its own deck physically.** Anything that shuffles the AI's library or has it
  search for a card is done by the person, without looking, and the table logs it.
- **The software never reads the AI's face-up deck before a draw.** The camera doesn't look into the deck box, and
  the table logs every frame it reads from the AI's zones, so a person can check it.
- **Two cameras:** the bed (the AI's side) is seen from above; the person's side, beside the printer, by the table's
  fixed camera. Board state merges both.

## Software notes (what still needs code)

- `gantry.py`'s gate refuses fans today. The magnet needs one narrow exception, **M106 S255 / M107 only** (no partial
  power), an on-time cap, `M107` on exit, and `M107` when the table server starts. Heaters stay refused. Tapping needs
  **G2/G3** added to the allowed moves, and every move ends with a short settle pause.
- `gantry.py scan` assumes the camera rides on the **carriage**. With the camera on the **X beam**, X moves don't move
  the camera: use `snap` at the top of Z, and the scan needs a `camera_on: "beam"` setting so it moves only Z and Y.

## Troubleshooting

- **The magnet doesn't hold a card:** check the disc is centred under the magnet, the magnet's face sits flat on the
  sleeve, and that the fan output actually reads ~24 V during `M106 S255`.
- **The card sticks after M107:** the diode may be missing; leftover magnetism on a light card is otherwise rare
  (the maker rates it near zero). A strip of tape on the magnet's face helps.
- **Two cards come up from the stack:** lower the touch-down force, add the sideways wiggle, or try the fallback
  receptive sheet (goal doc, M0).
- **The card spins while moving:** tighten the wing nut a quarter turn.
- **Photos are blurry:** autofocus off, manual focus on the bed at the Z you shoot from; shake from Y moves settles
  after ~0.5 s (gantry.py waits for moves to finish).
