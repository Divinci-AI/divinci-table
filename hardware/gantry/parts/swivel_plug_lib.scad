// Swivel plug v3: closes the open side of the magnet swivel after the bolt is in (README Step 4, item 2).
// The swivel's hex channel and the keyhole slot above it are both open on +X so the bolt can be slid in. This T-shaped plug slides in after it: a wide base in the hex channel
// (blocks the head), a narrow neck in the slot (blocks the thread, and, reaching in to x = 3.5, centres the thread), a full-height handle outside the body, and a vertical PIN HOLE
// (a 1.75 mm filament pin, glued, into the matching hole in the swivel's floor) that is the actual lock: the fit only holds the plug while you assemble.
// History: v1 had crush ribs and bound (the digital sweep left them out, so it could not see it). v2 dropped them but graded the wrong axis. Opus 5.5 review of v2: the ceiling over the channel is two
// unsupported ledges that droop, and the channel's faces sit on layer mid-planes, so Z is the uncertain axis. v3: Z clearance is FIXED (about 0.3 per side, faces on 0.2 mm layer boundaries) and only the
// Y width is graded; a relief chamfer under the ledges; a lead-in; the handle prints from the bed up (no overhang); the grade is a count of notches in the handle's outer face.
// The numbers below are COPIED from magnet_swivel.scad; fit_check.py sweeps the plug through the real swivel STL, so a drift fails there. Units: mm.
body_d      = 24;
floor_t     = 3;
m3_head_h   = 3.5;
hex_af      = 11.11 + 0.5;
hex_h       = 4.2 + 0.4;
top_hole    = 7.0;
top_t       = 4;
head_corner = 11.11 / cos(30) / 2;       // the 1/4"-20 bolt head's corner radius: the base stops short of it
plug_gap    = 0.6;
thread_r    = 6.35 / 2;

z0 = floor_t + m3_head_h;                // the hex channel's floor (a layer mid-plane: +-0.1)
z1 = z0 + hex_h;                         // its ceiling = the slot's floor
h  = z1 + top_t;                         // the swivel's top

zb0 = 6.8;                               // base bottom: 0.3 above the channel floor, on a 0.2 mm layer boundary
zb1 = 10.8;                              // base top: 0.3 under the ceiling
zt  = 15.0;                              // neck and handle top: 0.1 under the swivel's top
xn  = thread_r + 0.33;                   // neck's inner end, beside the thread (3.5)
xi  = head_corner + plug_gap;            // base's inner end
xo  = body_d / 2;                        // outer face of base and neck: flush with the body's curve
lip = 2.2;  lip_hw = 2.5;                // the handle: sticks out of the body, full height, 5 mm wide
pin_d = 1.9;  pin_x = 9.8;               // pin hole through the plug; the swivel has the matching 1.9 mm hole in its floor
cb = 0.4;  ct = 0.6;  lead = 0.5;        // chamfer under the base (elephant foot), relief at its top edges (ledge droop), lead-in at its inner end

module octagon(w, a, b, c0, c1) polygon([[-(w/2-c0), a], [w/2-c0, a], [w/2, a+c0], [w/2, b-c1], [w/2-c1, b], [-(w/2-c1), b], [-w/2, b-c1], [-w/2, a+c0]]);
module along_x(x0, x1) translate([x0, 0, 0]) rotate([90, 0, 90]) linear_extrude(height = x1 - x0) children();

// fy: clearance per side in Y (base in the channel, neck in the slot). gzt/gzb: grow the base up/down, only for the digital check (ceiling droop / floor rounding).
module plug(fy = 0.15, mark = 0, gzt = 0, gzb = 0) {
    bw = hex_af - 2 * fy; nw = top_hole - 2 * fy;
    difference() {
        union() {
            intersection() {
                union() {
                    hull() {                                                                              // base with a lead-in at its inner end
                        along_x(xi, xi + 0.01) octagon(bw - 2 * lead, zb0 - gzb + lead, zb1 + gzt - lead, cb, ct);
                        along_x(xi + lead, xi + lead + 0.01) octagon(bw, zb0 - gzb, zb1 + gzt, cb, ct);
                    }
                    along_x(xi + lead, xo + 0.5) octagon(bw, zb0 - gzb, zb1 + gzt, cb, ct);
                    translate([xn, -nw / 2, zb1]) cube([xi - xn + 0.01, nw, zt - zb1]);                     // neck, inner part: from beside the thread, ABOVE the bolt head (its top is 10.7)
                    translate([xi, -nw / 2, zb1 - 0.2]) cube([xo + 0.5 - xi, nw, zt - zb1 + 0.2]);          // neck, outer part: overlaps the base so the two print as one
                }
                translate([0, 0, z0 - 1]) cylinder(d = body_d, h = h - z0 + 2, $fn = 64);                 // the swivel's own $fn: the outer face coincides with its wall
            }
            translate([xo - 1.0, -lip_hw, zb0 - gzb]) cube([lip + 1.0, 2 * lip_hw, zt - zb0 + gzb]);       // the handle, full height from the base's level (prints from the bed up, no overhang)
        }
        translate([pin_x, 0, zb0 - 2]) cylinder(d = pin_d, h = zt - zb0 + 4, $fn = 24);                    // the pin hole
        for (i = [0 : mark - 1]) translate([xo + lip, (i - (mark - 1) / 2) * 0.9, zb0 - 1]) cylinder(d = 0.7, h = zt - zb0 + 2, $fn = 12);   // the grade: notches in the handle's outer face
    }
}
