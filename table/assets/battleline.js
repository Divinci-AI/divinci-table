// The battle line: which creatures stand on it, and where.
// 2026-11-13: "Can we have a better visual representation of creatures that are attacking and blocking? Maybe moving them up to the edge of the
// battle-line?" A declared attacker steps out of its row to the line, towards the defender; a declared blocker steps up to its own side of the
// line, opposite the attacker it blocks; several blockers of one attacker stand side by side; once blocks are in, an attacker nobody blocked is
// marked unblocked. When the combat is over, or a creature has left it (it died, it was removed), it is back in its row.
//
// It reads ONLY what the table already publishes to everyone: the `combat` of /api/phase (and of a seat's own state), which is declared
// attackers and declared blockers and nothing else. No hand, no intention, nothing the players at the table cannot see.
// No page in this file: it is tested in node (table/tests/battleline_js_test.cjs).   <script src="/assets/battleline.js"></script>
(() => {
  const idOf = ref => { const m = /^#?(\d+)$/.exec(String(ref ?? "")); return m ? +m[1] : null; };

  // plan(combat, has) → { any, attackers: [...], blockers: [...], byId: {"seat:id": entry} }
  //   combat  {attacks: [{attacker, stage, damage_dealt, attackers: [{ref, name, defender, answered, blocked, blockers: [{id, name, seat}]}]}]}
  //   has     (seat, id) → is that permanent on that seat's battlefield right now? (optional; a creature that has left is dropped)
  // An attack whose stage is "done" (damage dealt) is over: nobody stands on the line for it.
  //   attacker entry  {role: "attacker", seat, id, name, defender, stage, answered, blocked, unblocked, blockers: [{seat, id, name}], lane}
  //   blocker entry   {role: "blocker", seat, id, name, blocks: attacker id, blocksName, attackerSeat, slot, of, lane}
  // lane: the attacker's place in its controller's declaration (0, 1, 2…); a blocker has its attacker's lane. slot/of: this blocker is the
  // slot-th of `of` on that attacker. unblocked: the defender has answered and put nothing in front of it.
  function plan(combat, has) {
    const out = { any: false, attackers: [], blockers: [], byId: {} };
    const on = (seat, id) => id != null && (!has || !!has(seat, id));
    for (const a of (combat && combat.attacks) || []) {
      if (!a || a.stage === "done" || a.damage_dealt) continue;
      let lane = 0;
      for (const r of a.attackers || []) {
        const id = idOf(r.ref);
        if (!on(a.attacker, id)) continue;                      // it has left the battlefield (or was never a card the table tracks): not on the line
        const bl = (r.blockers || []).filter(b => on(b.seat, b.id)).map(b => ({ seat: b.seat, id: b.id, name: b.name }));
        const e = { role: "attacker", seat: a.attacker, id, name: r.name, defender: r.defender, stage: a.stage, answered: !!r.answered,
          blocked: !!r.blocked, unblocked: !!r.answered && !r.blocked, blockers: bl, lane };
        out.attackers.push(e); out.byId[a.attacker + ":" + id] = e;
        bl.forEach((b, i) => { const be = { role: "blocker", seat: b.seat, id: b.id, name: b.name, blocks: id, blocksName: r.name, attackerSeat: a.attacker, slot: i, of: bl.length, lane };
          out.blockers.push(be); out.byId[b.seat + ":" + b.id] = be; });
        lane++;
      }
    }
    out.any = out.attackers.length > 0;
    return out;
  }

  // fan(groups, cw, minX, maxX) → [[x, x, …], …]: where the blockers of each attacker stand, left edges, in the same order as `groups`.
  //   groups  [{x: the attacker's left edge, n: how many blockers}]   cw: a card's width   minX..maxX: the row they must stay inside
  // Each group is centred on its attacker, its cards side by side (a small gap). Groups that would overlap are pushed apart, left to right; if
  // the row is then too long it is slid back, and if it still does not fit the cards overlap evenly (a fan) rather than leave the row.
  function fan(groups, cw, minX, maxX, gap) {
    const g = Math.max(0, gap ?? cw * 0.08), room = Math.max(cw, maxX - minX);
    const idx = groups.map((_, i) => i).filter(i => groups[i].n > 0).sort((a, b) => groups[a].x - groups[b].x);
    const res = groups.map(() => []);
    if (!idx.length) return res;
    const total = idx.reduce((s, i) => s + groups[i].n, 0);
    let step = cw + g;
    if (total * step - g > room) step = Math.max(cw * 0.3, (room - cw) / Math.max(1, total - 1));   // too many for the row: overlap evenly
    const span = n => (n - 1) * step + cw, starts = [];
    let cursor = -Infinity;
    for (const i of idx) { const want = groups[i].x + cw / 2 - span(groups[i].n) / 2, s = Math.max(want, cursor); starts.push(s); cursor = s + span(groups[i].n) + (step >= cw ? g : step - cw); }
    const last = idx.length - 1, over = starts[last] + span(groups[idx[last]].n) - maxX;
    if (over > 0) { let limit = Infinity;                       // slide back from the right, keeping the order and the spacing
      for (let k = last; k >= 0; k--) { const i = idx[k], s = Math.min(starts[k], limit - span(groups[i].n)); starts[k] = s; limit = s - (step >= cw ? g : step - cw); }
      starts[last] = Math.min(starts[last], maxX - span(groups[idx[last]].n)); }
    let floor = minX;                                           // …and never past the left edge
    for (let k = 0; k < idx.length; k++) { const i = idx[k]; starts[k] = Math.max(starts[k], floor); floor = starts[k] + span(groups[i].n) + (step >= cw ? g : step - cw); }
    idx.forEach((i, k) => { for (let j = 0; j < groups[i].n; j++) res[i].push(Math.min(Math.max(minX, starts[k] + j * step), Math.max(minX, maxX - cw))); });
    return res;
  }

  // What a card on the line says in a few characters, and in a sentence (its tooltip). `me`: the seat looking, for "you".
  function label(e, me) {
    const who = n => (n && n === me ? "you" : n || "?");
    if (e.role === "blocker") return { short: "⛨ " + String(e.blocksName || "").slice(0, 14), long: `blocks ${e.blocksName || "the attacker opposite"}`, cls: "blk" };
    if (e.unblocked) return { short: "⚔ unblocked", long: `attacks ${who(e.defender)}: unblocked`, cls: "unb" };
    if (e.blocked) return { short: `⚔ ⛨${e.blockers.length || ""}`, long: `attacks ${who(e.defender)}: blocked by ${e.blockers.map(b => b.name).join(" and ") || "a creature that has left"}`, cls: "blkd" };
    return { short: `⚔ → ${who(e.defender)}`, long: `attacks ${who(e.defender)}: blocks are not in yet`, cls: "atk" };
  }

  window.TableBattleLine = { plan, fan, label, idOf };
})();
