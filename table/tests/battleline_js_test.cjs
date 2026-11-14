// assets/battleline.js on its own: no browser, no table. Run: node table/tests/battleline_js_test.cjs
// Which cards stand on the battle line (declared attackers, declared blockers, nobody once the combat is over or the creature has left),
// which blocker belongs to which attacker, and where a row of blockers is put so that it faces its attackers and stays inside the row.
const fs = require("fs"), path = require("path"), vm = require("vm");
const win = {}; win.window = win;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "..", "assets", "battleline.js"), "utf8"), win);
const B = win.TableBattleLine;
let ok = true; const check = (n, c, d) => { console.log((c ? "PASS " : "FAIL ") + n + (c ? "" : "  " + JSON.stringify(d))); ok = ok && !!c; };
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// the shape /api/phase publishes (server.py combat_public)
const atk = (over = {}) => ({ attacker: "Michael", stage: "blocks", locked: true, damage_dealt: false, attackers: [
  { ref: "#5", name: "Ghost of Illyria", defender: "Claude", answered: false, blocked: false, blockers: [] },
  { ref: "#8", name: "Targ Wrangler", defender: "Claude", answered: false, blocked: false, blockers: [] },
  { ref: "#9", name: "Horta", defender: "Sam", answered: false, blocked: false, blockers: [] }], ...over });
const all = () => true;

check("the API is there", B && ["plan", "fan", "label", "idOf"].every(k => typeof B[k] === "function"));
check("a ref is read as an id", B.idOf("#12") === 12 && B.idOf("12") === 12 && B.idOf(7) === 7 && B.idOf("") === null && B.idOf(null) === null && B.idOf("Horta") === null);

let p = B.plan({ attacks: [] }, all);
check("no combat: nobody on the line", p.any === false && !p.attackers.length && !p.blockers.length);
check("nothing published at all is no combat", B.plan(null, all).any === false && B.plan({}, all).any === false && B.plan(undefined).any === false);

p = B.plan({ attacks: [atk()] }, all);
check("declared attackers stand on the line, in the order declared, each facing its own defender",
  same(p.attackers.map(a => [a.id, a.defender, a.lane]), [[5, "Claude", 0], [8, "Claude", 1], [9, "Sam", 2]]) && p.any, p.attackers);
check("before the blocks are in nobody is 'unblocked' and nobody is 'blocked'", p.attackers.every(a => !a.unblocked && !a.blocked && !a.answered));
check("…and no blocker stands there", p.blockers.length === 0);
check("the label before blocks names the defender; the seat looking is 'you'", B.label(p.attackers[0], "Sam").short === "⚔ → Claude" && B.label(p.attackers[2], "Sam").short === "⚔ → you" && B.label(p.attackers[0], "x").cls === "atk");

// blocks are in: Ghost double-blocked, Targ unblocked, Horta blocked by one of Sam's
const blocked = atk({ stage: "window", attackers: [
  { ref: "#5", name: "Ghost of Illyria", defender: "Claude", answered: true, blocked: true, blockers: [{ id: 21, name: "Moopsy", seat: "Claude" }, { id: 22, name: "Xindi Surveyors", seat: "Claude" }] },
  { ref: "#8", name: "Targ Wrangler", defender: "Claude", answered: true, blocked: false, blockers: [] },
  { ref: "#9", name: "Horta", defender: "Sam", answered: true, blocked: true, blockers: [{ id: 31, name: "Pet Sehlat", seat: "Sam" }] }] });
p = B.plan({ attacks: [blocked] }, all);
check("each blocker is paired with the attacker it blocks", same(p.blockers.map(b => [b.id, b.seat, b.blocks, b.slot, b.of, b.lane]), [[21, "Claude", 5, 0, 2, 0], [22, "Claude", 5, 1, 2, 0], [31, "Sam", 9, 0, 1, 2]]), p.blockers);
check("two blockers on one attacker are slots 0 and 1 of 2", p.byId["Claude:21"].of === 2 && p.byId["Claude:22"].slot === 1);
check("an attacker nobody blocked is unblocked once its defender has answered", p.byId["Michael:8"].unblocked === true && p.byId["Michael:5"].unblocked === false && p.byId["Michael:9"].unblocked === false);
check("labels: unblocked, blocked (how many), and the blocker names its attacker",
  B.label(p.byId["Michael:8"], "Claude").short === "⚔ unblocked" && B.label(p.byId["Michael:8"], "Claude").cls === "unb" && B.label(p.byId["Michael:5"], "x").short === "⚔ ⛨2" &&
  /Moopsy and Xindi Surveyors/.test(B.label(p.byId["Michael:5"], "x").long) && B.label(p.byId["Claude:21"], "x").short === "⛨ Ghost of Illyr" && B.label(p.byId["Claude:21"], "x").cls === "blk", [B.label(p.byId["Michael:5"], "x"), B.label(p.byId["Claude:21"], "x")]);
check("byId finds every card on the line and no other", same(Object.keys(p.byId).sort(), ["Claude:21", "Claude:22", "Michael:5", "Michael:8", "Michael:9", "Sam:31"]));

// one defender answered, the other has not: unblocked is per attacker
const half = atk({ attackers: [{ ...blocked.attackers[1] }, { ref: "#9", name: "Horta", defender: "Sam", answered: false, blocked: false, blockers: [] }] });
p = B.plan({ attacks: [half] }, all);
check("with one defender still to answer, only the answered attacker is marked unblocked", p.byId["Michael:8"].unblocked && !p.byId["Michael:9"].unblocked);

// leaving combat
p = B.plan({ attacks: [blocked] }, (seat, id) => !(seat === "Claude" && id === 21));
check("a blocker that has left the battlefield is off the line; the attacker is still 'blocked' (it was), with the blocker that remains",
  !p.byId["Claude:21"] && p.byId["Michael:5"].blocked && same(p.byId["Michael:5"].blockers.map(b => b.id), [22]) && p.byId["Claude:22"].of === 1 && p.byId["Claude:22"].slot === 0, p.byId["Michael:5"]);
p = B.plan({ attacks: [blocked] }, (seat, id) => !(seat === "Michael" && id === 5));
check("an attacker that has left takes its blockers off the line with it; the other attackers close up (lanes 0, 1)",
  !p.byId["Michael:5"] && !p.byId["Claude:21"] && !p.byId["Claude:22"] && same(p.attackers.map(a => [a.id, a.lane]), [[8, 0], [9, 1]]) && p.byId["Sam:31"].lane === 1, p);
check("when the damage has been dealt (stage done) nobody stands on the line", B.plan({ attacks: [{ ...blocked, stage: "done" }] }, all).any === false && B.plan({ attacks: [{ ...blocked, damage_dealt: true }] }, all).any === false);
check("an entry the table has no card for (no ref: a real-deck attack said aloud) is not put on the line", B.plan({ attacks: [atk({ attackers: [{ name: "Something", defender: "Claude", blockers: [] }] })] }, all).any === false);
check("without a 'has' the published combat is taken as it is", B.plan({ attacks: [blocked] }).attackers.length === 3);
const two = B.plan({ attacks: [atk({ attackers: [blocked.attackers[0]] }), { attacker: "Sam", stage: "blocks", attackers: [{ ref: "#40", name: "Mugato", defender: "Michael", answered: false, blocked: false, blockers: [] }] }] }, all);
check("two seats attacking at once (two-headed): both are planned, lanes counted per attacking seat", same(two.attackers.map(a => [a.seat, a.id, a.lane]), [["Michael", 5, 0], ["Sam", 40, 0]]));

// fan: where the blockers stand. cw 100, the row runs 100..1000
const cw = 100, f = (g, a = 100, b = 1000) => B.fan(g, cw, a, b);
check("one blocker stands exactly opposite its attacker", same(f([{ x: 400, n: 1 }]), [[400]]));
check("two blockers stand side by side, centred on the attacker", same(f([{ x: 400, n: 2 }]), [[346, 454]]), f([{ x: 400, n: 2 }]));
check("three blockers: the middle one opposite the attacker", f([{ x: 400, n: 3 }])[0][1] === 400 && f([{ x: 400, n: 3 }])[0][2] - f([{ x: 400, n: 3 }])[0][0] === 216);
check("an attacker with no blocker has no place in the row", same(f([{ x: 400, n: 0 }, { x: 600, n: 1 }]), [[], [600]]));
const adj = f([{ x: 400, n: 2 }, { x: 510, n: 2 }]);
check("neighbouring attackers with two blockers each: the groups are pushed apart, none overlaps, the order is kept", adj[0][1] + cw <= adj[1][0] && adj[0][0] + cw <= adj[0][1] && adj[1][0] + cw <= adj[1][1], adj);
check("…and the result does not depend on the order the groups are given in", same(f([{ x: 510, n: 2 }, { x: 400, n: 2 }]), [adj[1], adj[0]]));
const left = f([{ x: 100, n: 3 }]);
check("at the left edge the group stays inside the row", left[0][0] === 100 && left[0][2] === 316, left);
const right = f([{ x: 900, n: 3 }]);
check("at the right edge the group is slid back inside the row", right[0][2] + cw <= 1000 && right[0][0] >= 100 && right[0][1] - right[0][0] === 108, right);
const crowd = f([{ x: 200, n: 4 }, { x: 310, n: 4 }, { x: 420, n: 4 }]);
const flat = crowd.flat();
check("more blockers than the row can hold side by side: they overlap evenly (a fan) and still none leaves the row", flat.length === 12 && Math.min(...flat) >= 100 && Math.max(...flat) + cw <= 1000 && flat.every((x, i) => i === 0 || x > flat[i - 1]), flat);
check("…with every card still showing at least 30% of its width", flat.every((x, i) => i === 0 || x - flat[i - 1] >= cw * 0.3 - 0.001));
const phone = B.fan([{ x: 60, n: 2 }, { x: 140, n: 1 }], 64, 47, 382);
check("at phone width (a 64 px card, a row from 47 to 382) nothing is off the screen", phone.flat().every(x => x >= 47 && x + 64 <= 382), phone);
const narrow = B.fan([{ x: 50, n: 5 }], 64, 47, 200).flat();
check("a row narrower than the cards need still keeps every card inside it", narrow.every(x => x >= 47 && x + 64 <= 200) && narrow.length === 5, narrow);
check("no groups: nothing to place", same(B.fan([], cw, 100, 1000), []));

console.log(ok ? "\nall passed" : "\nFAILED");
process.exit(ok ? 0 : 1);
