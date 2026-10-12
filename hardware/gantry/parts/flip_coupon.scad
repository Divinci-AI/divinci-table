// Flip coupon: three short lips on one plate, to test BY HAND which lip catches a sleeved card's edge and lets it topple
// (README, "Flipping a card" > "Bench test you can do TODAY"). Print it BEFORE the full flip_fence: the fence is blocked by the
// pre-print gate (it needs a hinged head that does not exist); this needs nothing but a hand-held magnet. Units: mm.
//
// Plan view (bed Y up the page, the lips run along X; all three share one landing zone on the -Y side):
//     card waits here (+Y)
//     [ 1 ]   [ 2 ]   [ 3 ]        <- lips; notches on the rear strip say which (1, 2 or 3 notches)
//     ==========================   <- one rear strip joins them into a single piece
//     landing zone (-Y): free bed, no rails (the full fence's rails are not what is being tested)
// Variants (undercut = degrees the catching face leans over the card; h = lip height):
//     1  8 deg, 3.5 mm   the baseline in flip_fence.scad
//     2  16 deg, 3.5 mm  a steeper undercut: does a harder lean stop the edge slipping?
//     3  8 deg, 6.0 mm   a taller lip: does it catch better, and how much more lift does the washer need?
// Every lip has a 1 mm deep groove at the foot of the face for a 1 mm self-adhesive silicone strip, flush with the face. Test each with the strip and without.

lip_l    = 95;        // each lip's length along X: longer than the card's long edge (92.5) so a card never rests on the next lip (Opus review 2026-10-11)
gap      = 10;        // between lips
base_d   = 5;         // lip thickness behind the catching face
ramp_d   = 6;         // rear slope
strip_h  = 1.2;       // the strip joining the lips, UNDER the lip bodies (y -base_d..0), so it leaves no ledge on the rear slope
groove_h = 1.2;       // at the FOOT of the face (z 0.2..1.4): that is where a flat card's edge touches it (the card is ~1.6 mm thick)
variants = [[8, 3.5], [16, 3.5], [8, 6.0]];     // [undercut deg, height mm]

rear  = base_d + ramp_d;
total = len(variants) * lip_l + (len(variants) - 1) * gap;

module lip(u, h) {                                 // profile in (y, z); y = 0 is the catching face at the bed
    uc = h * tan(u);
    difference() {
        rotate([90, 0, 90]) linear_extrude(height = lip_l)
            polygon([[-rear, 0], [0, 0], [uc, h], [-base_d, h]]);
        translate([-1, -1, 0.2]) cube([lip_l + 2, 2, groove_h]);   // 1 mm deep into the face: a 1 mm strip sits flush
    }
}

// Origin: x = 0 is the left end of lip 1, y = 0 the catching face, z = 0 the bed.
difference() {
    union() {
        for (i = [0 : len(variants) - 1])
            translate([i * (lip_l + gap), 0, 0]) lip(variants[i][0], variants[i][1]);
        translate([0, -base_d, 0]) cube([total, base_d, strip_h]);        // joins the lips; inside their bodies
    }
    // identification: 1, 2, 3 notches cut into the strip's rear edge, under each lip
    for (i = [0 : len(variants) - 1]) for (n = [0 : i])
        translate([i * (lip_l + gap) + lip_l / 2 + (n - i / 2) * 6 - 1.5, -rear - 1, -1]) cube([3, 2.5, 5]);
}
