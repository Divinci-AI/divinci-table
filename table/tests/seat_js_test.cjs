// assets/seat.js against a fake page and a fake table: no browser needed. Run: node table/tests/seat_js_test.cjs
const fs = require("fs"), path = require("path"), vm = require("vm");
const SRC = fs.readFileSync(path.join(__dirname, "..", "assets", "seat.js"), "utf8");
let ok = true; const check = (n, c) => { console.log((c ? "PASS " : "FAIL ") + n); ok = ok && !!c; };

function tab(store, server) {                         // one tab: its own window + document, the SHARED localStorage
  const listeners = {}, el = () => ({ style: {}, addEventListener() {}, appendChild() {}, querySelector: () => ({}), set innerHTML(v) {}, set textContent(v) { this._t = v; }, get textContent() { return this._t; } });
  const win = { location: { origin: "https://t.example", search: "", pathname: "/hand", href: "https://t.example/hand" }, history: { replaceState() {} },
    localStorage: store, document: { body: el(), createElement: el }, addEventListener: (t, f) => (listeners[t] ||= []).push(f), URL, URLSearchParams, Headers, console,
    fetch: async (u, init = {}) => server(String(u), init) };
  win.window = win; win.fetch = win.fetch.bind(win); win.document.createElement = el;
  vm.runInNewContext(SRC, Object.assign(win, { document: win.document, localStorage: store }));
  return { win, fire: (t, e) => (listeners[t] || []).forEach(f => f(e)) };
}
const mkStore = () => { const m = new Map(); return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: k => m.delete(k), _m: m }; };
const json = (status, d) => ({ ok: status < 400, status, json: async () => d });
const settle = () => new Promise(r => setTimeout(r, 20));

(async () => {
  // 1. an old room's key sits in storage; the new room has never seen it and issues a fresh one: the page must keep the fresh one
  let store = mkStore(); store.setItem("table.seat", JSON.stringify({ name: "Michael", key: "OLD" }));
  const server1 = async (u, init) => u.endsWith("/api/seat/claim") ? json(200, { name: "Michael", key: JSON.parse(init.body).key === "OLD" ? "NEW" : JSON.parse(init.body).key }) : json(200, {});
  const t1 = tab(store, server1); await t1.win.TableSeat.ready; await settle();
  check("a key the table did not know is replaced by the one it issued", JSON.parse(store.getItem("table.seat")).key === "NEW" && t1.win.TableSeat.body().key === "NEW");

  // 2. another tab changes the shared entry: this tab follows without a reload
  store = mkStore(); const t2 = tab(store, async () => json(200, { this_device: [], humans: {} })); await settle();
  check("a tab opened before any claim starts anonymous", t2.win.TableSeat.name() === "");
  store.setItem("table.seat", JSON.stringify({ name: "Sam", key: "K2" })); t2.fire("storage", { key: "table.seat" });
  check("…and follows a claim made in another tab (storage event)", t2.win.TableSeat.name() === "Sam" && t2.win.TableSeat.body().key === "K2");
  t2.fire("storage", { key: "something.else" });
  check("an unrelated storage change is ignored", t2.win.TableSeat.name() === "Sam");

  // 3. a stale tab's rejected check-in must not wipe a newer claim another tab has already saved
  store = mkStore(); store.setItem("table.seat", JSON.stringify({ name: "Michael", key: "STALE" }));
  let release;
  const server3 = async (u) => u.endsWith("/api/seat/claim") ? await new Promise(r => (release = () => r(json(409, { error: "Michael's seat is claimed on another device" })))) : json(200, {});
  const t3 = tab(store, server3); await settle();
  store.setItem("table.seat", JSON.stringify({ name: "Michael", key: "FRESH" }));       // another tab claimed while this one waited
  release(); await t3.win.TableSeat.ready; await settle();
  check("a rejected stale check-in does not erase a newer key saved by another tab", JSON.parse(store.getItem("table.seat") || "{}").key === "FRESH");

  // 4. a plain rejection with nothing newer still clears the dead identity
  store = mkStore(); store.setItem("table.seat", JSON.stringify({ name: "Michael", key: "DEAD" }));
  const t4 = tab(store, async (u) => u.endsWith("/api/seat/claim") ? json(409, { error: "claimed on another device" }) : json(200, { this_device: [], humans: {} }));
  await t4.win.TableSeat.ready; await settle();
  check("a rejected key with no replacement is forgotten", JSON.parse(store.getItem("table.seat") || "{}").name === undefined && t4.win.TableSeat.name() === "");
  console.log(ok ? "ALL PASS" : "SOME FAILED"); process.exit(ok ? 0 : 1);
})();
