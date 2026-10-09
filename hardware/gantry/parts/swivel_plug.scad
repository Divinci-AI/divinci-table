// One swivel plug, in print orientation (base on the bed, neck up). Change `fit` for another grade (see swivel_plug_lib.scad).
include <swivel_plug_lib.scad>
fit = 0.15;
translate([-(head_corner + plug_gap), 0, -(z0 + fit)]) plug(fit);
