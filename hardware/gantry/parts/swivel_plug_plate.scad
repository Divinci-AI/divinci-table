// Ten plugs for one small plate (v2): two each of five grades, fit 0.45 (1 pit, loosest) .. 0.05 (5 pits, tightest). Print, try them in a swivel,
// keep the grade that goes in with a firm push and stays put when you tilt the swivel.
include <swivel_plug_lib.scad>
grades = [0.45, 0.35, 0.25, 0.15, 0.05];
for (i = [0 : 4], j = [0 : 1])
    translate([i * 18, j * 14, 0]) translate([-(head_corner + plug_gap), 0, -(z0 + grades[i])]) plug(grades[i], false, i + 1);
