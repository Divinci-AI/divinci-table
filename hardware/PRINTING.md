# Printing on the CR-6 Max from this Mac (what we learned on 2026-10-08)

Everything here was done the first night the printer was set up. Tools are in `scripts/printer/` (they use
`/dev/cu.usbserial-110`, 115200 baud; check `ls /dev/cu.usb*`).

## Before anything touches USB (the repo's own Step 6, which we skipped the first night)
`hardware/gantry/README.md` Step 6: some CR-6 boards put ~20 V on the USB shell. Read the board version (v4.5.3 is fixed),
Kapton under the bed clips, **measure with a multimeter before plugging in**. We had no multimeter (the move) and ran over
USB anyway; nothing broke. A print from the SD card avoids the Mac cable entirely.

## Loading filament (CR-6 Max, Bowden)
The PTFE tube is long: the filament reaches the nozzle after about 600-700 mm of feed. Hold the black lever, push the
filament well past the gear, then extrude slowly at 2-3 mm/s with the nozzle at 200 C. The gear turns slowly; at 2 mm/s you can
miss it. Firmware accepts the move whether or not the filament is gripped.

## Level
`G28` then `G29` (about 6.5 minutes, 349 points, saved with `M500`, then `M420 S1`). Error 203 "Probing Failed" happened
four times: once for a cable lying on the plate, and the rest while the nozzle was dirty or cables tugged the head. Keep
the cable sleeve off the plate and tied to the Z column, wipe the nozzle (warm it to 170 C; `scripts/printer/warm.py`).
`G28` printing "Invalid mesh" afterwards is not a failure: run `G29` again. Do not abort `G29` with `M410`.

## Slicing
OrcaSlicer 2.4.2 from the command line (see `hardware/test-prints/orca/README.md`). Orca's stock CR-6 Max profile and its
defaults ask for 1000-10000 mm/s2 on a heavy bed; use the gentle profile in that folder. The CLI does not resolve
`inherits` for the machine file: use the flattened machine JSON and the raw process/filament files.

## Streaming a print
    scripts/printer/sender.py plate.gcode          # one "ok" per line; Error: turns the heaters off
    scripts/printer/watchdog.py <sender log> 600   # dead-man: kills the sender and turns heaters off if the log stalls
    scripts/printer/idleguard.py                   # 12 h: heaters on with no job -> off + ALERT
    scripts/printer/watch.sh 60                    # a photo a minute into scripts/printer/watch/
Control files: `/tmp/printer-pause`, `/tmp/printer-stop`, `/tmp/printer-cmd` (inject one G-code line, e.g. `M290 Z-0.05`).
Opening the serial port while the sender runs steals its "ok" lines: never poll the printer while a print streams.
Keep the Mac awake: `caffeinate -dimsu -t <seconds>` (do not `pkill caffeinate`: other sessions use it).

## Camera (Canon T3i = EOS 600D)
`scripts/camera-snap.sh out.jpg 1400`. macOS's `ptpcamerad` grabs the camera and relaunches within a second of every kill,
even after `launchctl disable`; the script kills it every 10 ms during the shot. Auto power-off must be Off on the camera.
The camera cannot see the bed centre from the current tripod position (the head and a frame post are in the way).

## Start G-code lessons
- The nozzle drools while it waits at 205 C for the bed. Next time: retract a little after the wait, purge and wipe at a bed
  edge, or raise the nozzle temperature only for the last 30 s.
- First print: wipe the nozzle when it is at 170 C, before the job.
- Printing six small parts on one plate is the right call (layer time lets each cool); the hand rack is 20 h / 234 g, so look at
  layer height and infill before committing the printer to it.
