// Discard chute: hooks over the bed's front edge; the magnet drags a card onto it and it slides off into a
// tray, so graveyard and exile take no bed space. Units: mm.
card_w   = 66.5;       // double-sleeved
bed_t    = 6;          // bed + build plate thickness at the edge: measure it
wall     = 2.4;
ramp_l   = 80;
angle    = 35;         // steeper slides better; sleeves are slippery
w = card_w + 8;

union() {
    // the hook over the bed edge
    cube([w, 20, wall]);                                          // on top of the bed
    translate([0, 20 - wall, -bed_t - wall]) cube([w, wall, bed_t + 2 * wall]);   // front face
    translate([0, 8, -bed_t - wall]) cube([w, 12, wall]);         // under the bed
    // the ramp, down and away from the bed
    translate([0, 20, wall]) rotate([-angle, 0, 0]) union() {
        cube([w, ramp_l, wall]);
        cube([wall, ramp_l, 8]); translate([w - wall, 0, 0]) cube([wall, ramp_l, 8]);   // side rails
    }
}
