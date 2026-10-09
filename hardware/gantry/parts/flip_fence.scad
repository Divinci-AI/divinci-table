// Flip fence: a low rigid lip that catches one long edge of a card so the magnet can turn the card over like a page
// (README, "Flipping a card (edge flip)"). Tape it to the bed; it is NOT a hand-rack or deck part. Units: mm.
//
// Plan view (bed Y is up the page, the fence runs along X):
//        card waits here  (+Y)        catching face  y = 0   (leans 8 deg over the card: an undercut)
//        ==================================================  <- lip, fence_h tall
//        landing zone (-Y): two guide rails the card falls between, and a short ramp behind the lip
// The origin is the middle of the catching face at the bed (x = 0, y = 0, z = 0), so the STL drops straight onto its spot.
//
// card_l is the card's LONG edge, which lies along the fence; the gap between the rails is card_l + 2 * clear.

card_l    = 92.5;     // sleeved card, long edge (same numbers as deck_box.scad / hand_rack.scad: measure yours)
clear     = 1.0;      // each side of the card in the landing zone
fence_h   = 3.5;      // lip height; higher catches the edge better but needs a higher lift to pass over
undercut  = 8;        // degrees the catching face leans over the card
base_d    = 5;        // lip thickness behind the face
ramp_d    = 6;        // the rear slope: a card edge that comes to rest on it slides off
rail_t    = 2.4;
rail_len  = 51;       // rails run from the face into the landing zone (about a card's half width plus the ramp)
tab_w     = 12;       // tape-down tabs at each end, outside the card's reach
tab_t     = 1.0;
groove_h  = 1.5;      // a groove in the face for a self-adhesive silicone or grip strip (1 mm deep)

inner = card_l + 2 * clear;
total = inner + 2 * rail_t;
uc    = fence_h * tan(undercut);
rear  = base_d + ramp_d;

module profile() { polygon([[-rear, 0], [0, 0], [uc, fence_h], [-base_d, fence_h]]); }   // (y, z)

difference() {
    union() {
        translate([-total / 2, 0, 0]) rotate([90, 0, 90]) linear_extrude(height = total) profile();
        for (s = [-1, 1])
            translate([s * (inner / 2 + rail_t / 2) - rail_t / 2, -rail_len, 0]) cube([rail_t, rail_len + uc * 0 + 0.01, fence_h]);
        for (s = [-1, 1])                                             // tabs lie on the bed OUTSIDE the rails
            translate([s > 0 ? total / 2 : -total / 2 - tab_w, -rear, 0]) cube([tab_w, rear, tab_t]);
    }
    translate([-inner / 2, -1, (fence_h - groove_h) / 2]) cube([inner, 1 + uc / 2 + 0.01, groove_h]);   // 1 mm into the face
}
