// Deck box: a card magazine for the AI's face-up deck (README Step 3, goal doc section M).
// A follower plate on four springs pushes the stack up against the lid (rear) and two side lips (front).
// The front wall stops exactly one card below the lips, so the magnet can slide out the TOP card only:
// the card under it, even if its steel disc is pulled along, hits the front wall.
// Two prints: the box, and the follower plate (set `part`). Springs: four from the hardware-store
// assortment that fit `spring_d` and are compressed by a full deck. Units: mm.

part        = "box";   // "box" or "follower"
card_w      = 66.5;    // double-sleeved card, measure yours
card_l      = 92.5;
card_t      = 0.9;     // one sleeved card with its disc: measure 10 and divide
slot_play   = 0.25;    // the exit slot = one card + this
deck_n      = 60;
clear       = 1.0;     // around the stack
wall        = 2.4;
floor_t     = 2.4;
lip_w       = 6;       // side lips holding the top card down at the front
lid_frac    = 0.62;    // the lid covers the rear 62 % (hides the deck from the side; the front strip is the magnet's)
spring_d    = 6.5;     // post holes for the springs
follower_t  = 3;
spring_room = 14;      // springs fully compressed under a full deck, plus the follower
$fn = 48;

in_w = card_w + 2 * clear;
in_l = card_l + 2 * clear;
stack_h = deck_n * card_t;
H = floor_t + spring_room + follower_t + stack_h;          // the lid's underside
front_h = H - (card_t + slot_play);                          // the exit slot under the lips

module box() {
    difference() {
        cube([in_w + 2 * wall, in_l + 2 * wall, H + wall]);
        translate([wall, wall, floor_t]) cube([in_w, in_l, H - floor_t]);                    // the well, up to the lid
        translate([wall + lip_w, -1, H - 0.01])                                                // open front strip (top):
            cube([in_w - 2 * lip_w, wall + 1 + in_l * (1 - lid_frac), wall + 1]);              // where the magnet lands
        translate([wall, -1, front_h]) cube([in_w, wall + 2, H]);                               // exit slot, front
        for (x = [wall + 12, wall + in_w - 12], y = [wall + 15, wall + in_l - 15])            // spring sockets
            translate([x, y, 0.8]) cylinder(d = spring_d, h = floor_t);
    }
}

module follower() {
    difference() {
        cube([in_w - 1, in_l - 1, follower_t]);
        for (x = [12, in_w - 13], y = [15, in_l - 16]) translate([x, y, -0.01]) cylinder(d = spring_d, h = 1.2);
    }
}

if (part == "box") box(); else follower();
