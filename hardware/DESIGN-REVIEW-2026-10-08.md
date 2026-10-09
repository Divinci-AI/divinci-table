# Design review of the printed parts and the magnet head (2026-10-08)

Done the day the first parts came off the printer. Every STL was checked for loose pieces, and the bolt was swept into the
swivel with OpenSCAD (`intersection()` of the part and the moving bolt: an empty result means nothing touches).

## Found and fixed

| # | Part | Defect | Fix | How it was checked |
|---|---|---|---|---|
| 1 | Magnet swivel | The bolt (thread up) cannot slide in from the side: its thread would have to pass through the **solid ceiling** over the hex channel to reach the round hole. | A keyhole slot (7.0 mm wide) from the top hole out to the open side. The 12.8 mm head is wider than the slot, so the ceiling still holds it down. | Old STL: collides at x = 8 and 3 mm. New STL: free from x = 20 to 0. |
| 2 | Magnet swivel | Hex pocket was only 0.3 mm over the bolt head's 11.11 mm across flats and the thread hole only 0.25 mm over the thread: printed holes come out small. | Hex pocket +0.5 mm, thread hole and slot 7.0 mm. | Sweep above, with the real bolt dimensions. |
| 3 | Sleeving jig tray | The "thumb notch" was a cylinder along the whole length: it cut the floor in two, so the tray printed as **two separate pieces** (29 x 99 x 7 mm each). | The notches are only in the two end walls. | Connected-pieces count: was 2, now 1. |
| 4 | Sleeving jig | Nothing stopped the bridge sliding along the tray, so the washer's position along the card was by eye. | Four 3 mm stops on top of the long walls, either side of the bridge. | Bridge on tray: touching, not overlapping; hole 49.45 mm from the end = the card's centre. |

| 5 | Deck box | The magnet could not reach the top card's washer. The jig puts the washer at the card's centre (49.6 mm from the front) and the magnet needs 59.6 mm of opening, but the lid covered the rear 62 %, so the opening ended 38.3 mm from the front. Found by `fit_check.py` (section 4), not by eye. | `lid_frac` is now derived from the card's centre + the magnet's radius + 2 mm, so the opening ends at 61.6 mm and the lid covers only what is behind that. **See the open question below about the deck's orientation.** | `fit_check.py`: FAIL before, ok after. The next card down is still stopped by the front wall. |

## The tool: `hardware/gantry/fit_check.py`

Run it before slicing anything (`python3 hardware/gantry/fit_check.py`, needs OpenSCAD). It reads the numbers at the top of each `.scad`,
so it follows the design, and checks: one piece per part and the bed; each insertion swept through the real STL (bolt into the swivel, the M3
screw down its hole, the jig bridge on the tray, a card out of the deck box, a card in and out of every rack slot, the magnet onto each washer),
at the nominal size and grown 0.3 mm; the card behind the top card is stopped (that one must collide); and the bolt stack-up.
Result on 2026-10-08 after the fixes: 19 ok, 1 warn (the 1 inch bolt has no margin), 0 fail. The tool does **not** replace test-fitting
the first print: it knows the nominal hardware sizes, not your washers or your cards.

## Open question for Michael: the deck's orientation

Step 8 puts the washer **behind** the card and says the magnet "picks the card face-up through the card". So the deck is face-up and the magnet lands on the
card's FACE, over the washer. Fix 5 (the opening has to reach the washer at the card's centre) means the top card's middle is now visible from above, through the
opening, to anyone looking down at the box: it shows the top card of the AI's library. Options: (a) accept it; (b) narrow the opening to the magnet's width (about 22 mm)
so only a strip of the card shows, and arrange the cards so that strip holds no card name; (c) stack the deck face-down with the washer on top (the magnet then
touches the washer side), at the price of turning each card over somewhere before it is played, which the magnet cannot do; (d) a flap or a cover the magnet's
head lifts. I have not chosen. An earlier note here and in the README said "stack the deck backs up"; that contradicted Step 8 and has been removed.

## Open, with numbers (nothing to fix in the files yet)

- **Bolt length is marginal.** From the bolt head (seated on the pocket floor) the 1" bolt's thread ends about 20.8 mm above the swivel's top.
  The stack on it is nylon washer 6 + bracket 3.2 + spring + wing nut about 5.6 = 14.8 mm + the spring. So the spring may
  be at most about 6 mm (7.8 mm with only three threads in the nut). Springs rarely compress that far and still push. **Buy 1-1/2" (or 1-1/4") bolts**;
  the manual already says to use 1.5" if the thread runs out. Do the fit with the real spring before trusting 1".
- **`card_t` is a placeholder (1.6 mm).** Measure ten sleeved cards **at the washer** with their washers, divide by ten. The hand rack's slot height is `card_t + 0.8`
  and the deck box's exit slot `card_t + 0.25`: a card with a washer that is thicker than `card_t` will not pass.
- **Discard chute:** `bed_t = 6 mm` is a guess for the bed edge, and the under-bed tab assumes a flat underside. The CR-6 Max's underside has the
  heater plate and levelling parts. Measure before printing it.
- **Hand rack:** 347 mm long, 111 mm tall when printed standing on its fence, 234 g and about 20 hours on a bed that moves. It is one
  piece, the steps are solid blocks, and a failure at hour 15 loses everything. Consider two halves (4 + 3 slots) and a lower infill.
- **Lead clip:** its mouth is 3.5 mm for a 5 mm lead, so it snaps by flexing a 1.6 mm PLA arm. Test with the real wire; widen the mouth if it cracks.
- **Plate C (jig tray) was sliced before fix 3**: the tray in that plate prints as two pieces. Re-slice with the new STL.

## Edge flip of real cards: design and decision (added later the same day)

The user wants the arm to flip the AI's real, washered cards. Decision: **design an edge flip with a printed flip fence**, check it digitally, and bench-test it
by hand before building any electronics. Written up in `hardware/gantry/README.md` ("Flipping a card (edge flip)"), checked in `fit_check.py` section 6, with
waypoints in `flip_path.py` and a 6-step guide in the 3D manual (`guide=flip`).

What the numbers changed in the first idea: the magnet's flat, non-pitching face holds a card to only about 20 degrees of tilt, so the page turn (110 degrees) **needs a
passive pitch hinge at the head's centre of mass**; a hinge at 34 mm fails the torque check. The washer sits at the card's centre, so the pivot is a LONG edge (33.25 mm arm,
33 mm lift). The fixed head's path is deliberately rejected by the checks.

Open risks (nothing here is proven physically):
1. **The friction pivot is unproven.** Whether a sleeved card's edge grips the lip and wedges instead of slipping, and whether it topples cleanly at 110 degrees, only a bench test shows.
2. **The hinged head does not exist.** It changes the head (balanced hinge, light return spring), hangs the face about 28 mm below the nozzle, and the assumed head mass, centre of mass and
   spring torque are guesses. The magnet's pull through the sleeve (3 N, TBD) and `card_t` are also unmeasured.
3. **Landing accuracy.** A toppled card lands within a few mm at best; the design relies on rails and a re-pick of the washer to register it. Static, two stuck cards, and cards bent from long storage are not modelled.

Collisions and reach pass (the head swept along the path clears the fence, deck box, rack, chute and bed, also grown 1 mm; the negative controls fail as they should).


## Swivel plug: first print failed, v3 after an Opus 5.5 review (2026-10-09)

The bolt can slide back out of the swivel along the open side it went in by; nothing but spring preload held it. A T-shaped plug closes it (`parts/swivel_plug_lib.scad`). **v1** (three fits, two crush ribs on the neck) would not go into the printed swivel:
the digital sweep had left the ribs out, so it could not see the bind. **v2** removed the ribs and added five fits. The Opus review of v2 found: (1) a press fit alone cannot hold it (a knock moves it outward and nothing pushes it back), (2) the uncertain
axis is Z, not Y (the ceiling over the channel is two unsupported ledges that droop; the channel faces sit on layer mid-planes) and the grades scaled Z with Y, (3) the insertion sweep only tested the seated position, (4) a missing STL read as "no collision",
(5) the "sliver" rule used a bounding box, (6) the ear overhung and was the weakest feature, (7) the 0.9 mm grade pits would not print legibly, (8) the plate's rows were too close.

**v3 fixes:** Z clearance fixed at about 0.3 per side with faces on 0.2 mm layer boundaries and only the Y width graded (0.30 / 0.22 / 0.15 / 0.08 / 0.00); a 0.6 mm relief chamfer under the ledges, a 0.4 mm chamfer under the base, a 0.5 mm lead-in; the handle is a full-height block
that prints from the bed up; the grade is 1-5 vertical notches in the handle's outer face; the neck reaches in to x = 3.5 beside the thread (it centres the bolt, and the neck is split so its inner part sits above the bolt head: the check caught my first version
touching the head); a **1.9 mm pin hole** through the plug and a matching blind hole in the swivel's channel floor take a 1.75 mm filament pin: that is the lock. `fit_check.py` now sweeps the whole insertion path, models the droop, checks the pin, has a control that an
oversized plug does NOT fit, fails on a missing file, and judges collisions by overlap volume.

**Not done / still open:** printed holes are an estimate (about 0.3 mm small in total; no calipers yet); whether grades 3-4 grip well enough for the pin to be a backup rather than the only lock; the ear/handle clearance inside the hinged head (section 6 models the swivel as a plain
cylinder); the magnet's leads (if they leave the back face the swivel's solid floor has no notch for them); threadlocker on the M3 screw (re-snugging it later means taking the plug and bolt out).
