# CR-6 Max slicing settings (OrcaSlicer 2.4.2)

Orca's stock Creality CR-6 Max profile, plus these changes (made 2026-10-08 before the first print):

- Gentle motion: accelerations 200-800 mm/s², print speeds <= 60 mm/s, travel 120 mm/s, first layer 20 mm/s. The stock values
  (and Orca's defaults when a profile does not resolve) ask for 1000-10000 mm/s² on a heavy bed.
- Absolute extrusion (M82) with a start/end G-code written for this machine: home, wait for bed and nozzle, then print;
  end: heaters off, lift 10 mm, release X/Y/E motors. No prime line (the skirt, 3 loops, primes the nozzle).
- Machine limits match the printer's own firmware (Z 5 mm/s, Z accel 100).
- CR-PLA: nozzle 200 C (205 first layer), bed 55 C (60 first layer).

Slice from the command line (the files must be passed in this order; the process and filament files keep their `inherits`):

    OrcaSlicer --datadir ./data --load-settings "orca/cr6max-machine.json;orca/cr6max-process-0.20-gentle.json" \
      --load-filaments orca/cr-pla-filament.json --slice 1 --outputdir out cube20.stl

Note: the process and filament files inherit from OrcaSlicer's own system profiles; they were derived from copies in
`OrcaSlicer.app/Contents/Resources/profiles/Creality`. Check the printer's real bed/Z-offset on the first print.
