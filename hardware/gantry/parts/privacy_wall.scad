// Privacy wall: a low wall along the bed's front edge in front of the AI's face-up hand zone, so the person
// can't read the AI's hand. Feet take the double-sided mounting tape. Units: mm.
len    = 200;          // the hand zone's width
height = 45;           // above a lying card, below the camera's view of the zone
t      = 2.4;
foot   = 12;
union() {
    cube([len, t, height]);
    cube([len, foot, t]);                // tape foot
    for (x = [0, len / 2 - t / 2, len - t]) translate([x, 0, 0]) cube([t, foot, height * 0.6]);   // braces
}
