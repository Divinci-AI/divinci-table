// Hand rack: holds the AI's hand on the bed's back edge, in front of the AI, like a player holding their cards.
// Seven slots in a staircase. Each card lies flat on its own shelf, one pitch right of and one rise above the card
// before it, so its left strip (with the steel washer under its centre) stays open to the magnet and the camera,
// while its right part slides under the next card's shelf. A card leaves sideways: the magnet grips it, slides it
// one pitch left over the lower card, then lifts (gantry.py rack_slot). The fence along the front, the person's side,
// hides the faces; it replaces the separate privacy wall. Feet take the double-sided mounting tape. Units: mm.
//
// Print it standing on the fence (print = true, the default): the slots become vertical slits and need no supports.

print     = true;
slots     = 7;        // a full hand; an eighth card waits in the battlefield's spare spot until the AI discards
card_w    = 66.5;     // double-sleeved card, measure yours
card_l    = 92.5;
card_t    = 1.6;      // one sleeved card with its washer: measure 10 and divide (same number as deck_box.scad)
gap       = 0.8;      // slot height = card_t + gap
shelf_t   = 1.2;
pitch     = 46;       // the open strip of each card: must uncover the card's centre plus the magnet's radius (10)
clear     = 1.0;      // around a card in its slot
wall      = 2.4;      // front and back rails
base_t    = 2.0;
fence_h   = 40;       // above the bed: hides the faces from the person's seat; set gantry.json travel_z above it
foot      = 12;       // tape foot behind the back rail
rise      = card_t + gap + shelf_t;
assert(pitch >= card_w / 2 + 10 + 2, "pitch must uncover the card's centre and the magnet's radius");

sw = card_w + 2 * clear;                   // a slot's width
sl = card_l + 2 * clear;                   // a slot's depth
D  = sl + 2 * wall;
L  = (slots - 1) * pitch + sw + wall;      // the last slot gets an end wall on its right
function top(k) = base_t + k * rise;       // the shelf a card in slot k lies on

module rack() {
    difference() {
        union() {
            for (k = [0 : slots - 1])         // a step: solid from the bed up to slot k's shelf
                translate([k * pitch, 0, 0]) cube([(k == slots - 1 ? sw + wall : sw), D, top(k)]);
            translate([-wall, 0, 0]) cube([L + wall, wall, fence_h]);             // the fence (person's side)
            translate([-wall, D - wall, 0]) cube([L + wall, wall, top(slots - 1)]); // the back rail
            translate([-wall, D, 0]) cube([L + wall, foot, wall]);                  // tape foot
            translate([(slots - 1) * pitch + sw, 0, 0]) cube([wall, D, top(slots - 1) + card_t + gap + 2]);  // end stop
        }
        for (k = [0 : slots - 1])            // slot k: open above its left strip, under the next shelf on the right
            translate([k * pitch, wall, top(k)]) cube([sw, sl, card_t + gap]);
        for (k = [0 : slots - 1])            // and open to the sky over its left strip
            translate([k * pitch, wall, top(k)]) cube([(k == slots - 1 ? sw : pitch), sl, 200]);
    }
}

if (print) rotate([90, 0, 0]) rack();     // fence on the bed; front face down
else rack();
