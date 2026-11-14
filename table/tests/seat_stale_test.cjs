// assets/seat.js and assets/choicealert.js against a fake page and a fake table: no browser. Run: node table/tests/seat_stale_test.cjs
//
// Seen at the table (2026-11-13 retro):
//   B4. A tab left open after the table was REPLACED showed "Claim your seat (👤)" with an empty hand and no reason: the new table refused the old
//       key on every call and nothing said so. Now the page asks the table (/api/seat/check), puts up a banner that names the fix, and stops
//       polling with the dead key; the banner survives a reload.
//   B2. The red "Waiting on you" bar had "show it" only for an attack, a held spell and the mulligan; a pending choice, surveil or scry had no
//       link. Now every kind of wait names the panel that answers it.
// Before the fix every check below marked [new] fails (TableSeat.verify / dead / ChoiceAlert do not exist): checked with `git stash` on the two assets.
const fs = require("fs"), path = require("path"), vm = require("vm");
const SEAT = fs.readFileSync(path.join(__dirname, "..", "assets", "seat.js"), "utf8");
const ALERT = fs.readFileSync(path.join(__dirname, "..", "assets", "choicealert.js"), "utf8");
let ok = true; const check = (n, c) => { console.log((c ? "PASS " : "FAIL ") + n); ok = ok && !!c; };
const json = (status, d) => ({ ok: status < 400, status, json: async () => d });
const settle = (ms = 30) => new Promise(r => setTimeout(r, ms));
const mkStore = () => { const m = new Map(); return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k) }; };

function tab(store, server, extraSrc = []) {
  const listeners = {}, made = [], calls = [];
  const el = () => { const e = { style: {}, children: [], attrs: {}, classList: { add() {}, remove() {} }, addEventListener() {}, appendChild(c) { this.children.push(c); }, prepend(c) { this.children.unshift(c); }, remove() { this.removed = true; },
    setAttribute(k, v) { this.attrs[k] = v; }, querySelector: () => ({}), set innerHTML(v) { this._h = v; }, get innerHTML() { return this._h || ""; }, set textContent(v) { this._t = v; }, get textContent() { return this._t || ""; }, offsetHeight: 40 }; made.push(e); return e; };
  const body = el(), head = el();
  const win = { location: { origin: "https://t.example", search: "", hash: "", pathname: "/hand", href: "https://t.example/hand" }, history: { replaceState() {} },
    localStorage: store, document: { body, head, readyState: "complete", createElement: el, addEventListener() {}, getElementById: () => null, querySelector: () => null, documentElement: { style: { setProperty() {} } } },
    addEventListener: (t, f) => (listeners[t] ||= []).push(f), dispatchEvent: e => { (listeners[e.type] || []).forEach(f => f(e)); return true; },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = (init || {}).detail; } }, URL, URLSearchParams, Headers, console, navigator: {}, setTimeout: () => 0, scrollTo() {},
    fetch: async (u, init = {}) => { calls.push({ u: String(u), init }); return server(String(u), init); } };
  win.window = win; win.fetch = win.fetch.bind(win);
  const ctx = Object.assign(win, { document: win.document, localStorage: store });
  vm.createContext(ctx);
  for (const src of [SEAT, ...extraSrc]) vm.runInContext(src, ctx);
  return { win, calls, made, body, fire: (t, e) => (listeners[t] || []).forEach(f => f(e)), banners: () => made.filter(e => e.attrs["data-seat-stale"] && !e.removed) };
}
const hdr = (init, k) => { const h = init.headers || {}; return typeof h.get === "function" ? h.get(k) : h[k]; };

(async () => {
  // ── B4.1 the table was replaced while this tab was open: the page's own call is refused, it asks the table, the banner goes up
  let store = mkStore(); store.setItem("table.seat", JSON.stringify({ name: "Sam", key: "GOOD" }));
  let replaced = false;
  const server = async (u, init) => {
    if (u.endsWith("/api/seat/claim")) return json(200, { name: "Sam", key: JSON.parse(init.body).key });
    if (u.endsWith("/api/seat/check")) return replaced ? json(403, { error: "not a seated player" }) : json(200, { seat: "Sam" });
    return json(200, {});
  };
  const t = tab(store, server); await t.win.TableSeat.ready; await settle();
  check("[new] a good key is checked once on load and is not called stale", typeof t.win.TableSeat.dead === "function" && t.win.TableSeat.dead() === false && t.banners().length === 0);
  replaced = true;                                                  // a new table now answers on this address
  let heard = 0; t.win.addEventListener("seat-stale", () => heard++);
  const v = t.win.TableSeat.verify && await t.win.TableSeat.verify();
  check("[new] verify() says the key is dead on the new table", v === false && t.win.TableSeat.dead() === true);
  check("[new] the banner says where the new link is", t.banners().length === 1 && /earlier table/.test(t.banners()[0].textContent) && /Join the match/.test(t.banners()[0].textContent) && /ask the host/.test(t.banners()[0].textContent));
  check("[new] the page is told (seat-stale), so it can stop polling", heard === 1);
  check("[new] the dead key is forgotten, the reason is kept", (() => { const s = JSON.parse(store.getItem("table.seat") || "{}"); return !s.key && s.stale === "Sam"; })());
  check("[new] the banner and the stored note never contain the key", !/GOOD/.test(t.banners()[0].textContent) && !/GOOD/.test(store.getItem("table.seat")));
  const before = t.calls.length; await t.win.TableSeat.verify(); await t.win.TableSeat.verify();
  check("[new] once dead, verify() asks the table nothing more", t.calls.length === before);
  check("[new] the key went in the X-Seat-Key header of /api/seat/check, never in the address", t.calls.filter(c => c.u.endsWith("/api/seat/check")).every(c => hdr(c.init, "X-Seat-Key") === "GOOD" && !/GOOD/.test(c.u)));

  // ── B4.2 reload of that tab: no key is left, but the reason still shows (this was the bare "Claim your seat" screen)
  const t2 = tab(store, async u => u.endsWith("/api/seat/claims") ? json(200, { this_device: [], humans: { Sam: true } }) : json(403, { error: "not a seated player" }));
  await t2.win.TableSeat.ready; await settle();
  check("[new] after a reload the banner is still there", t2.banners().length === 1 && t2.win.TableSeat.dead() === true && t2.win.TableSeat.name() === "");

  // ── B4.3 the new seat link is opened in another tab: this tab comes back to life
  store.setItem("table.seat", JSON.stringify({ name: "Sam", key: "FRESH" })); t2.fire("storage", { key: "table.seat" });
  check("[new] a fresh key from another tab clears the banner", t2.banners().length === 0 && t2.win.TableSeat.dead() === false && t2.win.TableSeat.body().key === "FRESH");

  // ── B4.4 offline or a server error is NOT "stale": say nothing
  store = mkStore(); store.setItem("table.seat", JSON.stringify({ name: "Sam", key: "K" }));
  const t4 = tab(store, async u => { if (u.endsWith("/api/seat/check")) throw new Error("offline"); return u.endsWith("/api/seat/claim") ? json(200, { name: "Sam", key: "K" }) : json(200, {}); });
  await t4.win.TableSeat.ready; await settle();
  check("[new] a failed check (offline) raises no banner and keeps the key", t4.win.TableSeat.dead() === false && t4.banners().length === 0 && JSON.parse(store.getItem("table.seat")).key === "K");
  const t5 = tab(store, async u => u.endsWith("/api/seat/check") ? json(403, { error: "remote callers need a seat" }) : u.endsWith("/api/seat/claim") ? json(200, { name: "Sam", key: "K" }) : json(200, {}));
  await t5.win.TableSeat.ready; await settle();
  check("[new] a 403 for another reason is not called stale", t5.win.TableSeat.dead() === false && t5.banners().length === 0);

  // ── B2 every kind of wait the bar announces names a panel on the hand page
  store = mkStore(); store.setItem("table.seat", JSON.stringify({ name: "Sam", key: "K" }));
  const t6 = tab(store, async u => u.endsWith("/api/seat/claim") ? json(200, { name: "Sam", key: "K" }) : json(200, {}), [ALERT]);
  const CA = t6.win.ChoiceAlert;
  check("[new] choicealert exposes what it waits on", !!CA && typeof CA.waits === "function");
  if (CA) {
    const kinds = (d, ph) => CA.waits(d, ph, "Sam").map(i => i.kind), first = (d, ph) => CA.panelsFor(CA.waits(d, ph, "Sam"))[0];
    check("[new] a pending choice links to the choice panel", first({ pending_choices: [{ source: "Q Continuum", text: "choose one" }] }, {}) === "#choicepanel");
    check("[new] a surveil links to the surveil panel", first({ pending_surveil: 2 }, {}) === "#effpanel");
    check("[new] a scry links to the same panel", first({ pending_scry: 1 }, {}) === "#effpanel");
    check("[new] the mulligan links to its panel", first({ mulligan: { open: true, kept: false } }, {}) === "#mullpanel");
    check("[new] an attack comes first and links to the block panel", first({ pending_choices: [{ source: "X", text: "t" }], incoming: [{ creature: "Horta" }] }, {}) === "#blockpanel");
    check("[new] a held spell comes before everything", first({ incoming: [{ creature: "Horta" }] }, { held: { waiting: ["Sam"], by: "Ann", spell: "Red Alert!" } }) === "#heldpanel");
    check("[new] 'countered unless you pay' links to the choice that pays", first({}, { held: { paying: true, by: "Sam", spell: "S", seconds_left: 9 } }) === "#choicepanel");
    check("[new] the damage step links to its button", first({ combat: { you: { deal: true } } }, {}) === "#dmg");
    const all = { pending_choices: [{ source: "X", text: "t" }], pending_surveil: 1, pending_scry: 1, mulligan: { open: true }, incoming: [{ creature: "c" }], combat: { you: { window: true } } };
    const ks = [...kinds(all, { held: { waiting: ["Sam"], by: "A", spell: "s" } }), ...kinds({ combat: { you: { window: true } } }, {}), ...kinds({ combat: { you: { deal: true } } }, { held: { paying: true, by: "Sam", spell: "s" } })];
    check("[new] every kind of wait except BACK (which has its own button in the bar) names at least one panel", ks.length >= 9 && ks.every(k => k === "back" || (CA.PANEL[k] || []).length > 0));
    check("[new] a BACK someone else asked for is in the bar, with no panel", kinds({}, { back_request: { by: "Ann", to: "main 1" } }).join() === "back" && CA.panelsFor(CA.waits({}, { back_request: { by: "Ann", to: "main 1" } }, "Sam")).length === 0);
    check("[new] my own BACK request is not a wait on me", kinds({}, { back_request: { by: "Sam", to: "main 1" } }).length === 0);
  }
  console.log(ok ? "ALL PASS" : "SOME FAILED"); process.exit(ok ? 0 : 1);
})();
