// Two small fit gauges, printed BEFORE the parts that depend on a measurement (written 2026-10-08).
//
// part = "slits": eight through-slots of rising height. Push a real sleeved card (with its washer) edge-on through each slot, long side first,
//   so the washer passes through the middle of the slot. The narrowest slot that lets it through WITHOUT force is the deck box's exit slot
//   height (card_t + slot_play) and the hand rack's slot height minus its gap. Set card_t = that number (in mm, engraved next to the slot) minus 0.15.
// part = "comb": seven notches of rising width. Hold it upright with the teeth pointing at the bed's front edge and slide a notch over the edge:
//   the narrowest notch that fits is the bed + build plate thickness (bed_t in discard_chute.scad).
// Both print flat, with no supports. Units: mm.
part  = "slits";
$fn   = 32;
hs    = [1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 3.2];     // slot heights
gaps  = [6, 7, 8, 9, 10, 11, 12];                       // notch widths
w     = 1.6;                                            // wall between slots
slit_l = 70;                                            // a double-sleeved card is 66.5 wide
function sum_to(i) = i == 0 ? 0 : sum_to(i - 1) + hs[i - 1] + w;
function csum(i) = i == 0 ? 0 : csum(i - 1) + gaps[i - 1] + 4;
module label(s, x, y, size = 3) { translate([x, y, 0]) linear_extrude(0.6) text(s, size = size, halign = "center", valign = "center"); }

if (part == "slits") {
    L = w + sum_to(len(hs) - 1) + hs[len(hs) - 1] + w;
    difference() {
        cube([L, slit_l + 10, 8]);
        for (i = [0 : len(hs) - 1]) translate([w + sum_to(i), 5, -1]) cube([hs[i], slit_l, 10]);
        for (i = [0 : len(hs) - 1]) translate([0, 0, 7.4]) label(str(hs[i]), w + sum_to(i) + hs[i] / 2, 2.5, 2.4);
    }
} else {
    L = 4 + csum(len(gaps) - 1) + gaps[len(gaps) - 1] + 4;
    difference() {
        cube([L, 25, 3]);
        for (i = [0 : len(gaps) - 1]) translate([4 + csum(i), 5, -1]) cube([gaps[i], 21, 5]);
        for (i = [0 : len(gaps) - 1]) translate([0, 0, 2.4]) label(str(gaps[i]), 4 + csum(i) + gaps[i] / 2, 2.5, 3);
    }
}
