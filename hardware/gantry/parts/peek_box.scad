// Peek box: where the AI reads a card it has just drawn, WITHOUT ever turning it face-up (README, "Reading a drawn card").
// The card lies FACE-DOWN on a window; a second small camera inside the box looks up through the window and reads the face.
// A person standing at the table sees only the card's back (the washer, up) and the box's walls: the face is against the window.
// The washer is on top, so the magnet lands on it directly, with no flip. Cards are turned face-up only when they are cast.
// Three parts, one file (set `part`): "top" (window + card pocket), "base" (camera cradle, cable notch), "ring" (print 1 to 4:
// each adds `ring_h` mm between the camera and the window; how many depends on the camera's MEASURED field of view, Step 2).
// The pieces stack on tongue-and-lip joints (no glue, no screws), sit on the bed, and are taped down. Units: mm.

part      = "top";    // "top", "base" or "ring"
card_w    = 66.5;     // one inner-sleeved card, as sleeve_jig.scad
card_l    = 92.5;
clear     = 0.8;      // around the card in its pocket
wall      = 2.4;
ledge     = 3.0;      // the window is this much smaller than the pocket on every side; the card's edge rests on it
plate_t   = 3.0;
skirt_h   = 7.0;      // the top piece's walls under the plate
pocket_d  = 3.5;      // how far the rim rises above the plate: a deeper pocket catches a card that lands off-centre (Opus review 2026-10-11)
rim_extra = 2.0;      // the plate and rim overhang the skirt by this much all round (with a 45 degree chamfer under it), so the rim can be thick enough for a wide lead-in
leadin    = 2.5;      // the pocket's mouth is this much wider on every side at the top
ring_h    = 30;       // one ring
base_h    = 28;       // the camera cradle and its walls
floor_t   = 2.0;
tongue_h  = 3.0;      // the joint: an inner tongue on top of each piece, an outer lip under the next
joint_gap = 0.25;     // total play in the joint (wall - tongue - lip)
cam_w     = 32.4;     // the 32 x 32 mm UVC board (innomaker), plus play
cam_hole  = 27.0;     // M2 hole pitch of that board: TBD, measure yours (calipers) and set; the holes are 2.2 mm
notch_w   = 14;       // USB cable notch in the base's short wall
notch_h   = 6;
$fn = 48;

pw = card_w + 2 * clear;          // the pocket
pl = card_l + 2 * clear;
ow = pw + 2 * wall;               // outside
ol = pl + 2 * wall;
half = (wall - joint_gap) / 2;    // tongue and lip thickness

module rect_ring(w, l, t) {       // a rectangular tube cross-section, 2D: outer w x l, wall t
    difference() { square([w, l]); translate([t, t]) square([w - 2 * t, l - 2 * t]); }
}
module tongue(h) { translate([0, 0, 0]) linear_extrude(height = h) translate([wall - half, wall - half]) rect_ring(ow - 2 * (wall - half), ol - 2 * (wall - half), half); }
// the tongue sits on the INSIDE half of the wall; the lip below is the OUTSIDE half, the inside half cut away

module walls(h, lip_below = true, tongue_above = true) {
    difference() {
        linear_extrude(height = h) rect_ring(ow, ol, wall);
        if (lip_below) translate([0, 0, -0.01]) linear_extrude(height = tongue_h + 0.01)
            translate([half, half]) square([ow - 2 * half, ol - 2 * half]);   // everything inside the outer lip is removed at the foot
    }
    if (tongue_above) translate([0, 0, h]) tongue(tongue_h);
}

module top() {
    e = rim_extra;
    difference() {
        union() {
            walls(skirt_h, true, false);
            hull() {                                                               // the plate, overhanging the skirt by `e`, with a 45 degree underside
                translate([0, 0, skirt_h + e]) translate([-e, -e, 0]) cube([ow + 2 * e, ol + 2 * e, plate_t - e]);
                translate([0, 0, skirt_h]) cube([ow, ol, 0.01]);
            }
            translate([-e, -e, skirt_h + plate_t - 0.01]) linear_extrude(height = pocket_d + 0.01) rect_ring(ow + 2 * e, ol + 2 * e, wall + e);   // the pocket's rim
        }
        hull() {                                                                   // the window: straight for its top 1.2 mm, then flared 45 degrees underneath so the ledge does not droop
            translate([ow / 2 - (pw - 2 * ledge) / 2, ol / 2 - (pl - 2 * ledge) / 2, skirt_h + plate_t - 1.2]) cube([pw - 2 * ledge, pl - 2 * ledge, 1.3]);
            translate([ow / 2 - (pw - 2 * ledge) / 2 - (plate_t - 1.2), ol / 2 - (pl - 2 * ledge) / 2 - (plate_t - 1.2), skirt_h - 0.01]) cube([pw - 2 * ledge + 2 * (plate_t - 1.2), pl - 2 * ledge + 2 * (plate_t - 1.2), 0.01]);
        }
        hull() {                                                                   // the lead-in chamfer at the pocket's mouth
            translate([wall, wall, skirt_h + plate_t + pocket_d - 0.6]) cube([pw, pl, 0.01]);
            translate([wall - leadin, wall - leadin, skirt_h + plate_t + pocket_d + 0.01]) cube([pw + 2 * leadin, pl + 2 * leadin, 0.01]);
        }
    }
}

module ring() { walls(ring_h, true, true); }

module base() {
    difference() {
        union() {
            cube([ow, ol, floor_t]);
            walls(base_h, false, true);
            for (dx = [-1, 1], dy = [-1, 1])                           // four posts: the board stands off the floor on them; M2 nylon screws go in from ABOVE into blind pilot holes
                translate([ow / 2 + dx * cam_hole / 2, ol / 2 + dy * cam_hole / 2, floor_t - 0.01]) cylinder(d = 7, h = 4.01);
            // the cradle: a stop in the middle of each edge (the old corner cubes touched the board only at a point)
            for (s_ = [-1, 1]) {
                translate([ow / 2 + s_ * (cam_w / 2 + 1.5) - 1.5, ol / 2 - 5, floor_t - 0.01]) cube([3, 10, 6]);
                translate([ow / 2 - 5, ol / 2 + s_ * (cam_w / 2 + 1.5) - 1.5, floor_t - 0.01]) cube([10, 3, 6]);
            }
        }
        for (dx = [-1, 1], dy = [-1, 1])
            translate([ow / 2 + dx * cam_hole / 2, ol / 2 + dy * cam_hole / 2, floor_t + 0.5]) cylinder(d = 1.8, h = 4);   // blind: nothing sticks out under the floor
        translate([ow / 2 - notch_w / 2, -1, floor_t]) cube([notch_w, wall + 2, notch_h]);                                   // cable out of the short wall
    }
}

if (part == "top") top();
else if (part == "ring") ring();
else base();
