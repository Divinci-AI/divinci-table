// Equip from the hand page: what a seat may try, which creatures light up, and which one a drop landed on.
// 2026-11-13 retro: a player with Type-2 Phaser and Tricorder on the battlefield and a new creature said he had no way to equip. The way existed
// (click the Equipment, then a button per creature) but: it was only on the Equipment, never on the creature; it vanished once the lands were
// tapped into the page's mana pool ("Can't equip now: needs {3}", though the page gives pooled mana back before the table pays); and there was
// no gesture. hand.html now offers three ways that all end in the SAME request (POST /api/brain/equip {equipment, creature}):
//   drag the Equipment onto a creature (pointer events: mouse, touch, pen), tap Equipment → "Equip …" → tap a creature, and a button on the creature.
// This file is the part with no page in it, so it can be tested in node (table/tests/equip_js_test.cjs). It decides NOTHING about the rules:
// cost, sorcery timing and legality are the server's. It only reads what the server said (equip_cost, equip_can, equip_why, equip_targets).
//   <script src="/assets/equip.js"></script>
(() => {
  const isCre = c => !!c && (c.face_down || /\bCreature\b/.test(c.type || ""));
  const isGear = p => !!p && !!p.equip_cost && !p.face_down;

  // The creatures this Equipment may go onto. The server's own list when it sends one (equip_targets); an older server sends none, and then it is
  // what the page always offered: the seat's creatures and face-down cards, not the Equipment itself, not the creature it is already on.
  function targets(perms, gear) {
    if (!isGear(gear)) return [];
    const mine = perms || [];
    if (Array.isArray(gear.equip_targets)) return gear.equip_targets.filter(id => mine.some(c => c.id === id));
    return mine.filter(c => c.id !== gear.id && c.id !== gear.attached_to && isCre(c)).map(c => c.id);
  }

  // May the page ASK the table to equip now? { ok, why, pooled }. The table said equip_can / equip_why from its own checks. One correction, which is
  // the page's own business: mana the player tapped into the page's pool is given back (untapped) before the request, so "needs {3}" computed
  // while those lands are tapped is not a refusal yet. The table still decides when the request arrives.
  function canTry(gear, pooledCount) {
    if (!isGear(gear)) return { ok: false, why: "that is not Equipment", pooled: false };
    if (gear.equip_can !== false) return { ok: true, why: "", pooled: false };
    const why = String(gear.equip_why || "the table says not now");
    if (/^needs \{/.test(why) && pooledCount > 0) return { ok: true, why: "", pooled: true };
    return { ok: false, why, pooled: false };
  }

  // Which of these rectangles holds the point: [{id, left, top, right, bottom}]. The last one drawn wins when cards overlap (it is the one on top).
  function pickAt(rects, x, y) {
    let hit = null;
    for (const r of rects || []) if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) hit = r.id;
    return hit;
  }

  // The Equipment a creature could be given, for the buttons on the CREATURE: [{gear, ok, why, pooled, on}]. `on`: it is on this creature already.
  function offersFor(perms, creature, pooledCount) {
    if (!isCre(creature)) return [];
    return (perms || []).filter(isGear).map(g => {
      const on = g.attached_to === creature.id, t = canTry(g, pooledCount);
      return { gear: g, on, ok: !on && t.ok && targets(perms, g).includes(creature.id), why: on ? "" : t.why, pooled: t.pooled };
    });
  }

  // How fast to scroll the page while a card is dragged near the top or bottom edge (px per frame; 0 in the middle). A phone's list is long.
  function edgeScroll(y, viewH, band = 70, max = 14) {
    if (y < band) return -Math.ceil(max * (band - Math.max(0, y)) / band);
    if (y > viewH - band) return Math.ceil(max * (Math.min(viewH, y) - (viewH - band)) / band);
    return 0;
  }

  window.TableEquip = { isCre, isGear, targets, canTry, pickAt, offersFor, edgeScroll };
})();
