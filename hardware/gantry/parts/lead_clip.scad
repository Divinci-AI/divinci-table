// Lead clip: holds the magnet's leads to the bracket with a loop of slack, so the swivel can turn a quarter for
// taps without tugging the wires off the magnet. Two on the bracket, more along the carriage. Taped. Units: mm.

wire_d = 5;        // the two-core lead, or two singles side by side
foot_w = 14;
foot_l = 16;
t      = 1.6;
$fn = 48;

difference() {
    union() {
        cube([foot_w, foot_l, t]);                                              // tape foot
        translate([foot_w / 2, foot_l / 2, t + wire_d / 2]) rotate([0, 90, 0])
            cylinder(d = wire_d + 2 * t, h = foot_w, center = true);
    }
    translate([foot_w / 2, foot_l / 2, t + wire_d / 2]) rotate([0, 90, 0]) cylinder(d = wire_d, h = foot_w + 2, center = true);
    translate([-1, foot_l / 2 - wire_d * 0.35, t + wire_d / 2]) cube([foot_w + 2, wire_d * 0.7, wire_d]);  // snap-in mouth
}
