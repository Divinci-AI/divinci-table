// Six plugs for one small plate: two each of three grades (fit 0.25 loose, 0.15 snug, 0.05 tight). Print, try them in a swivel, keep the grade that needs a firm push.
include <swivel_plug_lib.scad>
grades = [0.25, 0.15, 0.05];
for (i = [0 : 2], j = [0 : 1])
    translate([i * 22, j * 14, 0]) translate([-(head_corner + plug_gap), 0, -(z0 + grades[i])]) plug(grades[i]);
