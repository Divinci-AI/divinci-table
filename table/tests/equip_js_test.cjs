// assets/equip.js on its own: no browser, no table. Run: node table/tests/equip_js_test.cjs
// What it covers: which creatures an Equipment may be dragged onto (the server's list when it sends one, the old page rule when it does not),
// when the page may ASK the table to equip (the table's own equip_can / equip_why, plus mana sitting in the page's pool), which card a drop
// landed on, what a creature's own menu offers, and the edge scroll. The page glue (hand.html) is exercised in retro_1113_equip_e2e.cjs.
const fs = require("fs"), path = require("path"), vm = require("vm");
const win = {}; win.window = win;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "..", "assets", "equip.js"), "utf8"), win);
const E = win.TableEquip;
let ok = true; const check = (n, c, d) => { console.log((c ? "PASS " : "FAIL ") + n + (c ? "" : "  " + JSON.stringify(d))); ok = ok && !!c; };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// the board from the report: Type-2 Phaser on Ghost of Illyria, Tricorder loose, Targ Wrangler just arrived
const ghost = { id: 5, name: "Ghost of Illyria", type: "Creature — Spirit", pt: "6/3" };
const targ = { id: 8, name: "Targ Wrangler", type: "Creature — Klingon Warrior", pt: "3/2" };
const fd = { id: 9, name: "Plasma Cascade", type: "Instant", face_down: true, pt: "2/2" };
const land = { id: 1, name: "Mountain", type: "Basic Land — Mountain" };
const ship = { id: 11, name: "Oberth-Class Starship", type: "Artifact — Spacecraft" };                 // not a creature (no charge)
const shipC = { id: 12, name: "Oberth-Class Starship", type: "Artifact Creature — Spacecraft", pt: "2/2" };
const phaser = { id: 6, name: "Type-2 Phaser", type: "Artifact — Equipment", equip_cost: "{3}", attached_to: 5, equip_can: true, equip_why: null };
const tric = { id: 7, name: "Tricorder", type: "Artifact — Equipment", equip_cost: "{1}", attached_to: null, equip_can: true, equip_why: null };
const aura = { id: 13, name: "De-evolve", type: "Enchantment — Aura", attached_to: 5 };
const perms = [land, ghost, phaser, tric, targ, fd, ship, shipC, aura];

check("the API is there", E && ["isCre", "isGear", "targets", "canTry", "pickAt", "offersFor", "edgeScroll"].every(k => typeof E[k] === "function"));
check("Equipment is what carries an equip cost; an Aura, a land and a creature are not", E.isGear(phaser) && E.isGear(tric) && !E.isGear(aura) && !E.isGear(land) && !E.isGear(ghost) && !E.isGear(null));
check("a face-down Equipment is not offered as Equipment", !E.isGear({ ...tric, face_down: true }));

// targets: the old page rule (no list from the server)
check("no server list: Tricorder may go onto every creature and face-down card of mine, nothing else", same(E.targets(perms, tric), [5, 8, 9, 12]), E.targets(perms, tric));
check("…and Type-2 Phaser onto all of them except the creature it is already on", same(E.targets(perms, phaser), [8, 9, 12]), E.targets(perms, phaser));
check("a Spacecraft that is not a creature is not a target; one that is, is", !E.targets(perms, tric).includes(11) && E.targets(perms, tric).includes(12));
// targets: the server's own list wins
check("the server's equip_targets is used as sent", same(E.targets(perms, { ...tric, equip_targets: [8] }), [8]));
check("…an empty list means no target (not 'fall back to the page rule')", same(E.targets(perms, { ...tric, equip_targets: [] }), []));
check("…and an id that is no longer on my battlefield is dropped", same(E.targets(perms, { ...tric, equip_targets: [8, 99] }), [8]));
check("an Aura has no equip targets: the engine has no way to move one, so the page offers none", same(E.targets(perms, aura), []) && same(E.targets(perms, null), []));

// canTry: the table's word
check("the table says yes: the page may ask", same(E.canTry(tric, 0), { ok: true, why: "", pooled: false }));
check("an older table that sends no equip_can: the page may ask (the table answers)", E.canTry({ ...tric, equip_can: undefined }, 0).ok);
const timing = { ...tric, equip_can: false, equip_why: "Equip is sorcery speed: only in your own main phase" };
check("the table says not now (timing): the page does not ask, and has the table's reason", same(E.canTry(timing, 0), { ok: false, why: timing.equip_why, pooled: false }));
check("…mana in the page's pool does not change a timing refusal", !E.canTry(timing, 3).ok);
const needs = { ...phaser, equip_can: false, equip_why: "needs {3}: you have 1 untapped mana source" };
check("'needs {3}' with nothing in the pool: not now, with the reason", same(E.canTry(needs, 0), { ok: false, why: needs.equip_why, pooled: false }));
check("'needs {3}' while lands sit in the page's pool: the page may ask (it gives the pool back first), and says so", same(E.canTry(needs, 3), { ok: true, why: "", pooled: true }));
check("no creature to equip stays a refusal, pool or not", !E.canTry({ ...tric, equip_can: false, equip_why: "no creature to equip" }, 2).ok);
check("something that is not Equipment is never asked for", !E.canTry(ghost, 5).ok && !E.canTry(aura, 5).ok);

// pickAt
const rects = [{ id: 5, left: 100, top: 100, right: 180, bottom: 212 }, { id: 8, left: 200, top: 100, right: 280, bottom: 212 }, { id: 9, left: 260, top: 120, right: 340, bottom: 232 }];
check("a drop inside a card's rectangle is that card", E.pickAt(rects, 150, 150) === 5 && E.pickAt(rects, 210, 110) === 8);
check("…on the edge counts", E.pickAt(rects, 100, 100) === 5 && E.pickAt(rects, 180, 212) === 5);
check("…outside every card is nothing", E.pickAt(rects, 50, 50) === null && E.pickAt(rects, 190, 150) === null && E.pickAt([], 1, 1) === null && E.pickAt(null, 1, 1) === null);
check("where two cards overlap, the one drawn last (on top) wins", E.pickAt(rects, 270, 150) === 9);

// offersFor: the creature's own menu
const forTarg = E.offersFor(perms, targ, 0).map(o => [o.gear.name, o.ok, o.on]);
check("Targ Wrangler's menu offers both Equipment", same(forTarg, [["Type-2 Phaser", true, false], ["Tricorder", true, false]]), forTarg);
const forGhost = E.offersFor(perms, ghost, 0).map(o => [o.gear.name, o.ok, o.on]);
check("Ghost of Illyria's menu: the Phaser is on it already (no button), the Tricorder is offered", same(forGhost, [["Type-2 Phaser", false, true], ["Tricorder", true, false]]), forGhost);
const blocked = E.offersFor([ghost, targ, needs, tric], targ, 0);
check("an Equipment the table says cannot be paid for is listed with the table's reason, not as a button", blocked[0].ok === false && blocked[0].why === needs.equip_why && blocked[1].ok === true, blocked);
check("…and becomes a button again while the mana is in the page's pool", E.offersFor([ghost, targ, needs, tric], targ, 3)[0].ok === true && E.offersFor([ghost, targ, needs, tric], targ, 3)[0].pooled === true);
check("a creature the server did not list for that Equipment gets no button", E.offersFor([ghost, targ, { ...tric, equip_targets: [5] }], targ, 0)[0].ok === false);
check("a land, an Equipment and a non-creature Spacecraft have no such menu", same(E.offersFor(perms, land, 0), []) && same(E.offersFor(perms, tric, 0), []) && same(E.offersFor(perms, ship, 0), []));

// edgeScroll
check("no scrolling in the middle of the screen", E.edgeScroll(400, 844) === 0);
check("near the top it scrolls up, faster the nearer", E.edgeScroll(60, 844) < 0 && E.edgeScroll(5, 844) < E.edgeScroll(60, 844) && E.edgeScroll(0, 844) === -14);
check("near the bottom it scrolls down, faster the nearer", E.edgeScroll(790, 844) > 0 && E.edgeScroll(840, 844) > E.edgeScroll(790, 844) && E.edgeScroll(844, 844) === 14);
check("a finger past the edge is the edge", E.edgeScroll(-30, 844) === -14 && E.edgeScroll(900, 844) === 14);

console.log(ok ? "\nall passed" : "\nFAILED");
process.exit(ok ? 0 : 1);
