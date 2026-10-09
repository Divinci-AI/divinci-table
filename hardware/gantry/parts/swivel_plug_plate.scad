// Ten plugs for one plate (v3): two each of five widths, fy = 0.30 (1 notch, loosest) .. 0.00 (5 notches, tightest). Z clearance is the same on all. Print, try them in a swivel,
// keep the one that goes in with a firm push, then pin it (README Step 4).
include <swivel_plug_lib.scad>
grades = [0.30, 0.22, 0.15, 0.08, 0.00];
for (i = [0 : 4], j = [0 : 1]) translate([i * 17, j * 18, 0]) translate([-xn, 0, -zb0]) plug(grades[i], i + 1);
