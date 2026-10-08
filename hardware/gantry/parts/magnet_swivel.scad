// Magnet swivel (hardware/gantry/README.md, Step 3). Holds the 20 x 15 mm electromagnet underneath and the
// 1/4"-20 swivel bolt's head in a sideways hex slot, so the bolt turns WITH the swivel in the bracket's hole.
// The screw is a FLAT-head (countersunk) M3 x 6 -- Home Depot's M3 x 6 pack is flat head -- so the floor is
// countersunk 90 degrees and the head seats flush in it. A flat head's 6 mm is the overall length, so with the
// head flush the tip still stands 3 mm below the floor: the same 3 mm of thread in the magnet as before.
// Assembly: drop the M3 x 6 screw in through the top hole, screw it down into the magnet's M3 thread
// (screwdriver through the top hole), then slide the bolt, head first and thread up, in from the side: the hex channel takes the
// head and the keyhole slot in the ceiling takes the thread.
// Print: 100% infill, upright (top hole up), PLA+. Units: mm.

body_d      = 24;      // a little over the magnet's 20 mm
floor_t     = 3;       // the M3 x 6 screw passes through this into the magnet (3 mm of thread engaged)
m3_hole     = 3.2;
m3_head_d   = 6.2;     // pocket for the M3 head, reached through the top hole
m3_head_h   = 3.5;
cs_depth    = (m3_head_d - m3_hole) / 2;   // 90-degree countersink: 1.5 mm deep
hex_af      = 11.11 + 0.3;   // 7/16" across flats + clearance
hex_h       = 4.2 + 0.4;     // 1/4"-20 hex head height (5/32") + clearance
top_hole    = 6.6;     // the bolt's thread out; also the screwdriver's way in
top_t       = 4;
$fn = 64;

h = floor_t + m3_head_h + hex_h + top_t;

difference() {
    cylinder(d = body_d, h = h);
    translate([0, 0, -1]) cylinder(d = m3_hole, h = floor_t + 2);                       // M3 through the floor
    translate([0, 0, floor_t - cs_depth])                                               // countersink for the flat head
        cylinder(d1 = m3_hole, d2 = m3_head_d, h = cs_depth + 0.01);
    translate([0, 0, floor_t]) cylinder(d = m3_head_d, h = m3_head_h + 0.01);           // M3 head pocket
    translate([0, 0, floor_t + m3_head_h])                                              // hex slot, open to +X
        hull() {
            cylinder(d = hex_af / cos(30), h = hex_h, $fn = 6);
            translate([body_d, 0, 0]) cylinder(d = hex_af / cos(30), h = hex_h, $fn = 6);
        }
    translate([0, 0, floor_t]) cylinder(d = top_hole, h = h);                           // top hole, all the way down
    // The top hole is a KEYHOLE: a slot of the same width runs from it out to the +X side, through the ceiling over the hex
    // channel. Without it the bolt (thread up) cannot be slid in from the side: its thread would have to pass through solid
    // ceiling to reach the round hole (found 2026-10-08 on the first printed swivel). The head (12.8 mm across the corners)
    // is still wider than the slot, so the ceiling holds it down.
    translate([0, -top_hole / 2, floor_t + m3_head_h]) cube([body_d, top_hole, h]);
}
