// Swivel plug: closes the open side of the magnet swivel after the bolt is in (README Step 4, item 2).
// The swivel's hex channel and the keyhole slot above it are both open on +X so the bolt can be slid in. Once the bolt is seated, nothing but the
// spring's preload kept it from sliding back out (fit_check 'swivel-retention'). This T-shaped plug slides in the same way the bolt did, wide base in
// the hex channel (blocks the head) and narrow neck in the slot (blocks the thread), and ends flush with the body.
// The numbers below are COPIED from magnet_swivel.scad: fit_check.py sweeps the plug through the real swivel STL, so a drift fails there.
// Units: mm.
body_d      = 24;
floor_t     = 3;
m3_head_h   = 3.5;
hex_af      = 11.11 + 0.5;
hex_h       = 4.2 + 0.4;
top_hole    = 7.0;
top_t       = 4;
head_corner = 11.11 / cos(30) / 2;       // the 1/4"-20 bolt head's corner radius: the plug stops short of it
plug_gap    = 0.6;                       // between the bolt head's corner and the plug's inner end

z0 = floor_t + m3_head_h;                // the hex channel's floor
z1 = z0 + hex_h;                         // its ceiling = the slot's floor
h  = z1 + top_t;                         // the swivel's top

// fit: clearance per side around the base (in the channel) and the neck (in the slot). Printed holes come out small, so three grades are printed and
// the one that goes in with a firm push is kept. ribs: two small crush ribs on the neck that grip the slot's walls (off for the digital sweep: they are interference by design).
module plug(fit = 0.15, ribs = true) {
    xi = head_corner + plug_gap;         // inner end
    xo = body_d / 2;                     // outer face: flush with the body's curve
    intersection() {
        union() {
            translate([xi, -(hex_af / 2 - fit), z0 + fit]) cube([xo - xi, hex_af - 2 * fit, hex_h - 2 * fit]);                    // base, in the channel
            translate([xi, -(top_hole / 2 - fit), z1 - fit - 0.01]) cube([xo - xi, top_hole - 2 * fit, h - z1 + fit + 0.01]);      // neck, up through the slot, flush on top
            if (ribs) for (s = [-1, 1]) translate([xi + 2.2, s * (top_hole / 2 - fit), z1 + 0.6]) cylinder(d = 0.9, h = top_t - 1.0, $fn = 16);
        }
        translate([0, 0, z0 - 1]) cylinder(d = body_d, h = h - z0 + 2, $fn = 64);        // the swivel's own $fn, so the outer face coincides with its wall
    }
    translate([xo - 0.6, -2.5, h - 1.8]) cube([2.8, 5, 1.8]);                  // a small ear to push and pull it by
}
