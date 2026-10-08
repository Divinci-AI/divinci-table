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
