# The physical AI player — gantry eyes + SO-101 hand, as a goal

*Run with `/goal docs/ARM-GANTRY-GOAL.md` (from the divinci-table repo). Written 2026-10-04. Tick a box only when its
check has been run and its output read. Hardware steps need a person at the printer; software steps can run without it
(simulated) and are checked again on the hardware.*

## The idea

The AI player gets a body at the table: **eyes overhead and a hand at its own seat.**

- **Eyes: the CR-6 Max gantry** carries the Canon T5i over the bed (`hardware/gantry/`). It photographs the whole
  duel table, both players' battlefields, and feeds `/api/board` like the fixed camera does today.
- **Hand: an SO-101 arm** (open source, Hugging Face LeRobot; we print its parts on the CR-6 Max) clamped at the AI's
  side of the bed, with a **suction cup** instead of the gripper (flat cards) and a 32 mm wrist camera. Its wrist roll
  does the 90° turn for tapping, so the arm needs no extra servo.
- **One rule above all, enforced in code:** the arm touches **only the AI's own cards**, in the AI's own zone. People's
  cards and people's hands are out of bounds, and the overhead camera confirms every move.

```
            ┌───────────────── CR-6 Max (top view) ─────────────────┐
            │   X beam ═══════[Canon, on top of the beam]═══════    │
            │   ┌───────────────── bed 400 × 400 ───────────────┐   │
            │   │  NORTH half: the person's battlefield         │   │   (bed moves in Y;
            │   │  ───────────────────────────────────────────  │   │    parked at a fixed Y
            │   │  SOUTH half: the AI's battlefield  ◄── reach  │   │    whenever the arm moves)
            │   └───────────────────────────────────────────────┘   │
            └───────────────────────────────────────────┬───────────┘
                                         SO-101 (clamped)│  AI's hand-holder + library tray beside it
```

## Rules for every milestone

1. **Measure, don't assume.** Every box names its check.
2. **Safety is code, not a promise.** Every motion goes through one gate per machine (`gantry.py safe()`, `arm.py
   safe()`): workspace box, joint limits, speed caps, the AI-zone rule. Mutation-check each guard.
3. **One machine moves at a time.** A shared lock: the gantry parks (Z up, X away, bed at the arm's Y) before the arm
   moves, and the arm returns to its rest pose before the gantry moves.
4. **Stop is always available:** a big physical switch on the arm's power, and a software stop (`OFF` / torque off)
   that every client can send. The arm goes limp, it doesn't freeze holding a card mid-air over someone's hand.
5. **Live play comes first:** no hardware tests during a game; the table server keeps working with the hardware absent.
6. **Spend:** parts beyond the carts already filled need Michael's yes, with the amount.
7. **Open source:** designs, firmware, diagrams and code in this repo (Apache-2.0); SO-101 parts are TheRobotStudio's
   (Apache-2.0) and credited.
8. Commits are local unless a milestone says to push.

---

## G0 — Parts and the print queue (before the printer becomes a gantry)
- [ ] Buy: SO-101 **follower** electronics (6 × STS3215 7.4 V 1:345, driver board, 5 V/12 V supply per the vendor,
      table clamps), a **32 × 32 mm UVC wrist camera**, PLA+ filament if short. The gantry carts (Home Depot + the
      Amazon list "Divinci Table – gantry") are already filled. *The leader arm waits for G7.*
      *2026-10-04, in the Amazon cart and the private list "Divinci Table – rig" (not yet checked out):*
      ForgeMotion Labs SO-101 follower kit with printed frame — 6 × STS3215 12 V 1:345 (C018), board, power supply
      ([B0GRPJ2Q8F](https://www.amazon.com/dp/B0GRPJ2Q8F), $197.98); innomaker 1080p 32 × 32 mm UVC wrist camera
      ([B0CNCSFQC1](https://www.amazon.com/dp/B0CNCSFQC1), $18.99); RSHTECH 7-port powered USB hub
      ([B0CGX8LNMC](https://www.amazon.com/dp/B0CGX8LNMC), $31.99 — Canon, printer, Nano, arm board and wrist camera
      outnumber the Mac's ports); KarlKers 20 A inline 12–24 V switch as the arm's kill switch
      ([B0CD4Q36LW](https://www.amazon.com/dp/B0CD4Q36LW), $9.99). Amazon cart total with the gantry parts: $410.59 (MG90S servos and 5 V buck pack removed 2026-10-04 — not needed with suction on the arm; still in the list). Later the same day the suction set and its 12 V supply came out too (−$84.33, magnet route) and the $37.97 magnet test kit went in: cart $364.23.
      Buying the frame printed frees the CR-6 Max to become the gantry at once; PLA+ (in hand) is for the suction
      wrist adapter and camera mount only.
- [ ] **Print our suction wrist adapter** (G4) **and the wrist-camera mount** while the CR-6 Max is still a printer
      (the SO-101 frame comes printed with the kit).
- [ ] Decide the suction hardware split: **phase 1 puts the one suction set (pump, valve, cups, Nano) on the arm**; the
      gantry is camera-only. A second set for a gantry head only if G8 shows we need long moves.
- [ ] **Check:** every part in hand; prints pass a fit test (servo horns press on, screws bite).

## G1 — Eyes: the camera gantry
- [ ] Fix the manual first: on the CR-6 Max the carriage rides on the X beam's **front** face, so the camera bracket goes
      on **top of (or behind) the beam, outboard of the carriage's travel** — run the carriage end to end by hand
      before taping anything down.
- [ ] Mount the Canon (dummy battery, tethered USB), measure the rig into `gantry.json`.
- [ ] `gantry.py`: `camera_on: "beam"` — snaps move only Z (and the bed in Y), never X; `snap` at the top of Z covers
      the bed; `--post` feeds `/api/board`.
- [ ] **Check:** a stitched (or single) overhead frame of both halves; `/api/board` recognises the cards on each half;
      `gantry_test.py` grows checks for beam mode (no X move ever sent while snapping).

## G2 — The shared frame: one set of coordinates for camera, bed and arm
- [ ] Four ArUco markers on the bed corners; the overhead frame → **bed millimetres** by homography.
- [ ] Zones in bed mm, written once (`table/.cache/rig.json`): the AI's battlefield, the AI's hand-holder and library
      tray, the person's half (forbidden to the arm).
- [ ] **Check:** a card's centre from the camera vs a ruler, within 3 mm at 5 positions; the zones drawn on a photo.

## G3 — The arm: assemble, calibrate, teleoperate
- [ ] Assemble the follower (LeRobot guide), motor IDs, `lerobot-calibrate`.
- [ ] Clamp it at the AI's corner; cable-manage so the gantry can't snag anything.
- [ ] Teleoperate without a leader arm (keyboard / gamepad end-effector control) to feel the reach.
- [ ] **Check:** reach map — which bed squares (25 mm grid) the cup can touch flat; the AI zone must be inside it.
      Record the numbers.

## G4 — The hand: suction on the wrist
- [ ] A printed **suction wrist adapter** replacing the gripper jaw: the M5 bellows cup on the wrist roll's axis, a
      compliant mount (the bellows), the 4 mm tube along the arm.
- [ ] The vacuum line and Nano firmware from `hardware/gantry/` (PICK / DROP / OFF) move to the arm; the MG90S isn't
      needed (wrist roll turns the card).
- [ ] **Check:** 20 pick-and-place cycles of a sleeved card, success rate recorded; a tap (90° wrist roll) and untap.

## G5 — `table/arm.py`: a safe driver and card primitives
- [ ] Inverse kinematics from the SO-101 URDF (bed mm + yaw → joints), solved and limit-checked before any move.
- [ ] `safe()` gate: workspace box, joint limits, speed/acceleration caps, **only targets inside the AI zones**, the
      shared lock with the gantry, torque-off stop.
- [ ] Primitives: `pick(slot|xy)`, `place(xy, yaw)`, `tap(card)`, `untap(card)`, `rest()`.
- [ ] Simulation mode (no hardware): the same calls against a fake bus, so all of this is tested on any machine.
- [ ] **Check:** `table/tests/arm_test.py` — IK round-trips, every forbidden target refused (the person's half, outside
      the reach map, through the gantry lock), torque-off works; the zone rule mutation-checked.

## G6 — Closed loop: the AI plays its own cards
- [ ] Brain → body: when the AI casts a permanent, the table server asks `arm.py` to move it from the hand-holder to a
      free spot in the AI zone; taps and untaps follow the engine's state.
- [ ] Eyes verify: after each move the gantry snaps and `/api/board` must show the card where it should be; a mismatch
      stops the arm and asks a person (never retries blindly).
- [ ] **Check:** a scripted ten-turn game against Fusion on the bed: every AI cast placed, every tap shown, zero moves
      outside the AI zone (logged), mismatches handled.

## G7 — Learning (optional): demonstrate instead of program
- [ ] Add the **leader arm**; record demonstrations of hard cases (cards against the tray wall, stacked cards).
- [ ] Train a LeRobot policy (ACT) for those cases only; the safety gate still wraps it.
- [ ] **Check:** success rate scripted vs learned on the same 20 trials.

## G8 — A real game, people watching
- [ ] A full duel: a person vs the AI, the AI's hand on the table. Survey afterwards.
- [ ] **Check:** results in the ledger; what broke becomes the next goal.

## M — The magnet route (cheaper; decided by a test, not by argument)

*Added 2026-10-04.* An electromagnet instead of suction, and **steel in the AI's sleeves only**. Aluminium does not
work: it isn't magnetic. Fridge-magnet sheet doesn't either: it's already magnetised, so cards would stick to each
other and to the magnet even with the power off. Use an unmagnetised iron-filled ("receptive") sheet or a steel disc.

Why it's worth testing:
- **Safety becomes physical:** only the AI's cards carry steel, so the magnet *cannot* lift a person's card. The
  AI-zone rule is then enforced by the hardware as well as by `safe()`.
- **Cost:** the suction set is $75.34 (pump, valve, cups, fittings, tubing) plus the PTFE tape; one electromagnet
  is about $7.
- **The printer can be the hand.** Put the AI's whole side on the CR-6 Max bed and the gantry does the moving. That
  drops the SO-101 kit ($197.98), the wrist camera ($18.99) and probably the hub ($31.99). Estimated total ~$105–140
  vs $460.27 for arm + suction.

| Route | Approx. cost | Note |
|---|---|---|
| Arm + suction (current carts) | $460.27 | Amazon $410.59 + Home Depot $49.68 |
| Arm + magnet | ~$355 | Suction set swapped for a magnet and steel |
| **Printer as the hand + magnet** | **~$105–140** | $0 for tapping with the arc trick, ~$31 with servo + Nano + 5 V |
| Screen proxy (tablet shows the AI's battlefield) | $0 | No physical hand |

Design notes for the printer-as-hand version:
- **Magnet on the part-fan output** (24 V, M106/M107): no Nano, MOSFET or 12 V supply. Check the board's
  fan-output current rating first. `gantry.py` gains one narrow exception (magnet on/off); heaters stay refused.
  The magnet must never stay on without a card: the maker warns that long unloaded energising overheats it, so
  cap the on-time in code the way the pump was capped.
- **Never flip a card.** The person shuffles the AI's deck face-down, turns the stack face-up into a covered deck
  box, and every move after that is face-up: box → hand zone (behind a printed privacy wall) → battlefield. Turning
  the stack over keeps the order random; vision ignores the box until a draw. *Flipping was an unsolved gap in the
  arm plan too.*
- **Tapping with no extra motor:** the magnet sits on a free swivel with light drag. At a tap station a rubber foot
  on the bed pins one corner of the card, and an arc move (G2/G3) a quarter circle around that corner turns the
  card 90°. If that isn't reliable, the MG90S goes back on (it's saved in the list).
- **Discards and exile:** a printed chute off the bed's edge into a tray, so they take no bed space.
- **Bed space is the limit:** about 4 × 4 card spots plus the hand zone and the deck box. Lower the Y acceleration
  and put a mat on the bed so the moving bed doesn't slide the cards around.
- People's cards and hands stay off the bed, and so out of the machine's reach.

### M0 — Test kit (≈ $38, Amazon, needs Michael's yes before it goes in the cart)
- Heschen 24 V holding electromagnet, 20 × 15 mm, rated 2.5 kg on thick steel
  ([B078K3TKFZ](https://www.amazon.com/dp/B078K3TKFZ), $7.19)
- Ferrous receptive vinyl, 11 × 17" ([B0D8HKWWSL](https://www.amazon.com/dp/B0D8HKWWSL), $9.99). Heavy: about
  13 g per card-sized piece, so it's the test's worst case.
- 1/2" steel discs, 250-pack ([B081J52GY6](https://www.amazon.com/dp/B081J52GY6), $20.79): the light option, one
  per sleeve, behind the card.

### M1 — The test (decides the route)
- [ ] 20 single-card picks and drops with each insert type, magnet driven by the 12 V supply, then by the fan output.
- [ ] 20 picks off a 40-card stack: count how often it lifts two (stacked discs pull on each other through the
      stack; the sheet may not). Try tape on the magnet's face and a sideways wiggle as it lifts.
- [ ] 10 swivel-arc taps: within ±5° and ±3 mm?
- [ ] **Decision, with the numbers:** printer as the hand, arm + magnet, or keep suction. Only then change the carts.

## Done log
