// Sleeving jig: puts every steel washer in the same place, the centre of the card's back, so the magnet finds it.
// The inner-sleeved card goes face-down into the tray; the bridge sits across the tray's walls; drop a washer through
// the bridge's hole, lift the bridge off, put a small piece of clear tape over the washer, then slide the outer sleeve
// on. Two prints (set `part`). Units: mm.

part     = "tray";    // "tray" or "bridge"
card_w   = 66.5;      // one inner sleeve; a little roomier than double-sleeved is fine
card_l   = 92.5;
clear    = 0.8;
wall     = 2.4;
floor_t  = 1.6;
wall_h   = 5;
washer_d = 11.1;      // #10 SAE washer OD (0.438"); the hole is a little bigger
bridge_w = 24;
$fn = 64;

iw = card_w + 2 * clear;
il = card_l + 2 * clear;

if (part == "tray") {
    union() {
    difference() {
        cube([iw + 2 * wall, il + 2 * wall, floor_t + wall_h]);
        translate([wall, wall, floor_t]) cube([iw, il, wall_h + 1]);
        for (y = [-1, il + wall - 0.01])                                                              // thumb notches in the two END
            translate([(iw + 2 * wall) / 2, y, floor_t + wall_h]) rotate([-90, 0, 0])                 // walls only: the old cylinder ran
                cylinder(d = 16, h = wall + 1.02);                                                    // the whole length and cut the floor in two
    }
    for (x = [0, iw + wall], y = [-bridge_w / 2 - 2.4, bridge_w / 2 + 0.4])                           // stops: the bridge sits between them,
        translate([x, (il + 2 * wall) / 2 + y, floor_t + wall_h]) cube([wall, 2, 3]);                  // so its hole is on the card's centre
    }
} else {
    // across the tray's long walls, centred; two keys drop inside the walls so it can't slide
    W = iw + 2 * wall;
    difference() {
        union() {
            translate([0, -bridge_w / 2, 0]) cube([W, bridge_w, 2]);
            for (x = [wall + 0.2, W - wall - 0.2 - 2]) translate([x, -bridge_w / 2, -3]) cube([2, bridge_w, 3]);
        }
        translate([W / 2, 0, -5]) cylinder(d = washer_d + 0.8, h = 10);
    }
}
