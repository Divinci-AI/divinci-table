# Measure mode: let the table measure its own parts (design v2, 2026-10-12, not built)

v2 applies the Opus 5.5 and Hermes reviews of v1 (both same day). The review changes are marked **[R]**.

Goal: stop hand-measuring every number the config and the .scad files guess (`card_t`, `bed_t`, `touch_z`, `peek.seat_z`, `peek.tower_h`, `peek.pocket`, `cam_hole`, rack and deck positions). Bare-bones and cheap: no new sensor beyond what is owned. Fully offline.

## Rejected: LiDAR
An iPhone's LiDAR is good to about ±1 cm; a hobby time-of-flight board about ±1-3 mm. We need 0.1-0.3 mm (a card plus washer is about 1.6 mm). Rejected, not deferred.

## Instruments and what each can honestly claim (all errors UNMEASURED until `measure validate` runs)

| Instrument | Measures | Honest limit |
|---|---|---|
| **Calipers** (Kynup, in the cart) | ground truth for everything | 0.02 mm resolution; the reference, not a fallback |
| **Head camera, calibrated** (`fov_test.py`) | flat XY **in the plane where its scale was taken** (the bed, or a marker lying in the part's own plane) | the claimed 0.2 mm fails the 1/3 rule for 0.3 mm tolerances; a feature 30 mm above the bed seen from 120 mm is off by about 25% **[R]**; `cam_offset_mm` is itself unmeasured and adds to every XY converted to bed coordinates **[R]** |
| **Touch-off**, printer axes | Z differences in ONE session; heights relative to that session's `zero_z` | `goto` formats axes to 0.1 mm (`gantry.py:338`), so 0.1 mm is the step, not 0.05 **[R]**. A human pressing Enter on "looks touching" is about ±0.2-0.5 mm **[R]**, so v1 of this feature is **a paper/feeler-shim drag test (the same one `zero_z` uses), ±0.1-0.2 mm**, until a contact signal exists. |
| **Contact signal** (later) | makes touch-off objective | first check whether `M119` (already allowed, `gantry.py:147`) reports the CR-6 probe outside homing: unverified on this firmware. A foil strip must NEVER touch a logic pin directly: the magnet's coil is 24 V; isolate with an optocoupler, and Python has no GPIO input today (something would have to poll the Uno) **[R]**. Not part of v1. |
| **iPhone overhead on the tripod** | whole-bed top-down view | **Deferred (both reviewers).** Needs a second calibration; the phone switches lenses and stabilises per shot; the bed moves in Y so its frame only matches bed coordinates at a known Y; the head camera already covers the bed by tiling. Keep as an occasional cross-check with a marker in the part's plane. Never on the print head (about 190 g on the X carriage). |

## Stamp method for the tool offsets (my idea after the reviews: UNREVIEWED, untested)
The offsets between the magnet and the camera matter more than any single height, because every pick hands a position from one to the other. Ink the magnet's face with a washable marker, press it on paper at a known XY (no card, magnet off), then park the head at the same XY and photograph: the mark appears away from the image centre, and that distance (converted with the calibrated scale) is `cam_offset_mm`. Needs only the camera and calibration, so it is as accurate as they are. Risk: the head must come down onto paper at `touch_z`, so the floor and no-go rules above apply.

## What "datum" means **[R]**
Every Z is relative to the last `zero_z` (`gantry.py:308`), and Z is unknown after a reconnect. So a Z value is only meaningful as a **difference inside one session**: `card_t` = (card top) minus (bed) at the same XY in the same session. Every log line carries `session` and `datum_id`; `measure apply` refuses an absolute Z from a session other than the current one.

## Software: `table/gantry.py measure …`

All commands go through `safe()`; none adds a G-code word; each checks `print_running()` and that the magnet is off before it moves, like every other command.

- `measure touch --at X Y [--tool magnet|camera]`: **start at `travel_z`**; descend in 0.1 mm steps; stop on the user's "touching" (paper drag) or at the floor; **the first move after contact is always up, never XY** **[R]**. `--at` is the TOOL's point, and the code converts with `magnet_offset_mm` / `cam_offset_mm` exactly as `pick`/`place` do **[R]**. It cannot measure `magnet_min_z` itself (`goto` refuses while it is `None`, `gantry.py:325`): set the floor by hand once with the paper test, then touch-off works above it **[R]**. Camera-tool touch-off uses `min_z`, which sits above cards by definition, so it cannot touch a card: it is for the bed only **[R]**.
  - **No-go zones [R]:** it refuses any (X,Y) inside `deck_box_mm`, the rack fence extent, or the peek tower footprint (the layout already has all three), and asks for an explicit `--yes` outside the bed's usual targets. XY edge touch-off is **not in v1**: a lateral probe drags belts and cards.
  - Repeat: approach from above, back off 1 mm, approach again, **five touches**; log the spread (min, max), not only the mean. Between sets, `zero_z` again so the datum error is in the spread.
- `measure photo A.jpg --calib camera.npz --marker ID` or `--px-per-mm N`: distance between two points in mm, plus the pixel error bound. Marker scale lying in the part's own plane is preferred over focal length.
- `measure apply KEY`: prints old and new, then writes only if all of these pass **[R]**:
  - the value is converted to the key's own meaning (a raw head Z is not `peek.seat_z`, which is a height above the bed);
  - it satisfies the code's own invariants (`touch_z ≥ magnet_min_z`, `travel_z ≥ tower_h + 10`, `gantry.py:425` and `:484`) and **never lowers `magnet_min_z` or `min_z`**;
  - it is within 10x of the old value in **both** directions, and `None` (every key this feature fills) is accepted only with `--first`;
  - nested keys (`peek`, `rack`) are **merged** into the existing dict (`config()` is a shallow merge, `gantry.py:110`), never replaced;
  - the write is atomic (temp file + rename), not `fov_test.patch_config`'s read-modify-rewrite in place;
  - `touch_z` is a scalar but the bed is not flat: apply needs touches at the deck, a rack slot and the peek seat, and **refuses if their spread exceeds 0.2 mm**; otherwise it reports "bed tilt: per-point values needed" and writes nothing.
- `measure validate`: runs the table below and writes `trusted_for` into the log header.

## Validation (the smallest honest set) **[R]**
A method is "trusted for X" only if its error is under one third of the tolerance X needs, with the tolerance declared first. Per method, at the **actual points of use** (rack slot 0, peek seat), not the bed centre, three repeats each, cross-checked with the calipers:
- **XY (camera):** the 63 x 88 mm card (two orthogonal dimensions) and the camera board's hole pitch.
- **Z (touch-off):** a 20 mm printed cube, the bed-to-card difference on a 10-card stack (compare to calipers), and one caliper-known gauge raised to the peek-seat height.
- **Repeatability:** 10 touches at one point, then the same after `zero_z` again.

## Not covered / risks
- Nothing here protects fixtures from a mistyped coordinate except the no-go zones above; `magnet_min_z` protects the bed only.
- The log needs `session`, `datum_id` and `tool` on every line.
- Writing `peek.tower_h` from a touch-off measures the box, not the physical limit of the gantry: `travel_z` still has to be set separately.


3D walkthrough: `hardware/gantry/manual3d/?view=steps&guide=measure` (8 steps, added 2026-10-12).
