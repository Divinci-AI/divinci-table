// assets/art.js, the parts with no browser in them: where a card preview is put so that it never covers the option it belongs to, and the
// text-only card face. Run: node table/tests/cardpeek_js_test.cjs      (the page side is retro_1113_peek_e2e.cjs)
const fs = require("fs"), path = require("path"), vm = require("vm");
const el = () => ({ style: {}, dataset: {}, classList: { add() {}, remove() {}, contains: () => false }, appendChild() {}, addEventListener() {}, querySelector: () => null, set textContent(v) {}, set innerHTML(v) {} });
const calls = [];
const win = { CARD_ART: { Horta: "h1" }, document: { head: el(), body: el(), createElement: el, addEventListener() {} }, addEventListener() {}, innerWidth: 1200, innerHeight: 800,
  fetch: async u => { calls.push(String(u)); const n = decodeURIComponent(String(u).split("name=")[1] || ""); const db = { "Horta": { cost: "{4}{R}{R}", type: "Creature — Elemental", text: "Trample", power: "7", toughness: "6" }, "Stray from History": { cost: "{2}{B}", type: "Sorcery", text: "Choose one —", power: null, toughness: null } };
    return db[n] ? { ok: true, json: async () => db[n] } : { ok: false, status: 404, json: async () => ({ error: "no card" }) }; } };
win.window = win;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, "..", "assets", "art.js"), "utf8"), win);
const A = win.CardArt;
let ok = true; const check = (n, c, d) => { console.log((c ? "PASS " : "FAIL ") + n + (c ? "" : "  " + JSON.stringify(d))); ok = ok && !!c; };
const hit = (p, a) => !(p.left >= a.right || p.left + p.w <= a.left || p.top >= a.bottom || p.top + p.h <= a.top);
const inView = (p, vw, vh) => p.left >= 0 && p.top >= 0 && p.left + p.w <= vw && p.top + p.h <= vh;

(async () => {
  check("the old API is unchanged (has, src, img) and the new parts are there", A.has("Horta") && !A.has("Moopsy") && A.src("Horta") === "/art/h1.jpg" && A.src("Moopsy") === "" && /<img class="cardimg/.test(A.img("Horta", "")) && A.img("Moopsy", "") === ""
    && ["info", "face", "place", "peek", "unpeek", "show", "viewBtn", "selBtn", "guess"].every(k => typeof A[k] === "function"));

  // place: never over the anchor
  const W = 300, H = 420;
  let a = { left: 200, top: 300, right: 520, bottom: 344 }, p = A.place(a, W, H, 1500, 860);
  check("a wide screen, an option on the left: the card goes to its RIGHT, level with it", p.side === "right" && p.left === a.right + 10 && !hit(p, a) && inView(p, 1500, 860) && p.w === W && p.h === H, p);
  a = { left: 1150, top: 300, right: 1470, bottom: 344 }; p = A.place(a, W, H, 1500, 860);
  check("an option at the right edge: the card goes to its LEFT", p.side === "left" && !hit(p, a) && inView(p, 1500, 860), p);
  a = { left: 200, top: 4, right: 520, bottom: 48 }; p = A.place(a, W, H, 1500, 860);
  check("an option at the very top: the card is kept inside the screen and still beside it", p.top >= 10 && !hit(p, a) && inView(p, 1500, 860), p);
  a = { left: 200, top: 800, right: 520, bottom: 844 }; p = A.place(a, W, H, 1500, 860);
  check("an option at the very bottom: the same", p.top + p.h <= 850 && !hit(p, a) && inView(p, 1500, 860), p);
  // a phone: no room at the sides
  a = { left: 16, top: 120, right: 330, bottom: 170 }; p = A.place(a, 242, 339, 390, 844);
  check("a phone, an option near the top: the card goes BELOW it, full size", p.side === "below" && p.top === 180 && p.h === 339 && !hit(p, a) && inView(p, 390, 844), p);
  a = { left: 16, top: 700, right: 330, bottom: 750 }; p = A.place(a, 242, 339, 390, 844);
  check("a phone, an option near the bottom: the card goes ABOVE it", p.side === "above" && p.top + p.h === 690 && !hit(p, a) && inView(p, 390, 844), p);
  a = { left: 16, top: 300, right: 330, bottom: 350 }; p = A.place(a, 242, 339, 390, 600);
  check("a short screen with the option in the middle: the card is SHRUNK into the roomier side rather than laid over the option", p.h < 339 && p.w < 242 && Math.abs(p.w / p.h - 242 / 339) < 0.02 && !hit(p, a) && inView(p, 390, 600), p);
  let bad = 0;
  for (let vw of [320, 390, 768, 1280, 1512]) for (let vh of [480, 700, 844, 1080]) for (let top = 0; top + 44 <= vh; top += 37) for (let left of [0, 16, vw / 3, vw - 330])
    { const an = { left: Math.max(0, left), top, right: Math.min(vw, Math.max(0, left) + 314), bottom: top + 44 }, w = Math.round(Math.min(300, vw * 0.62)), q = A.place(an, w, Math.round(w * 1.4), vw, vh);
      if (hit(q, an) || q.left < 0 || q.top < 0 || q.left + q.w > vw + 0.5) bad++; }
  check("across five screen widths, four heights and every position of the option: the preview never overlaps the option and never leaves the screen sideways", bad === 0, bad);

  // the text-only face
  const f = A.face({ name: "Horta", cost: "{4}{R}{R}", type: "Creature — Elemental", text: "Trample\nBasic landcycling {2}", power: "7", toughness: "6" });
  check("the text face has the name, the cost, the type line, the rules text and the power/toughness", ["Horta", "{4}{R}{R}", "Creature — Elemental", "Trample\nBasic landcycling {2}", "7/6"].every(x => f.includes(x)), f);
  check("a card with no power/toughness has no P/T box", !/class="fpt"/.test(A.face({ name: "Stray from History", cost: "{2}{B}", type: "Sorcery", text: "Choose one —", power: null, toughness: null })));
  check("what the table sends is escaped (a name or text cannot inject markup)", !/<script/.test(A.face({ name: "<script>x</script>", cost: "", type: "<b>", text: "<img src=x onerror=1>" })) && A.face({ name: "a\"b", type: "", text: "" }).includes("a&quot;b"));
  check("no card: it says so instead of showing nothing", /no text for this card/.test(A.face(null)));
  check("the view button never carries a choice: it is its own button, labelled, with the card's name escaped", /^<button type="button" class="peekbtn" data-peekbtn="Q&quot;x" aria-label="View Q&quot;x"/.test(A.viewBtn('Q"x')) && A.viewBtn("") === "" && /data-peekbtn=""/.test(A.selBtn()));

  // the lookup: by name, public, cached
  calls.length = 0;
  const i1 = await A.info("Horta"), i2 = await A.info("Horta");
  check("a card's text is asked of /api/card BY NAME (the public Oracle lookup), once, then remembered", i1 && i1.type === "Creature — Elemental" && i1.name === "Horta" && i2 === i1 && calls.length === 1 && calls[0] === "/api/card?name=Horta", calls);
  check("the request carries the name and nothing about the game (no seat, no key, no zone)", calls.every(u => /^\/api\/card\?name=[^&]+$/.test(u)));
  check("a name the table does not know gives nothing", (await A.info("No Such Card")) === null && (await A.info("")) === null);
  // guess: the card a label names
  check("'Horta {4}{R}{R} (Creature 7/6)' names Horta", (await A.guess("Horta {4}{R}{R} (Creature 7/6)")) === "Horta");
  check("'Stray from History (to your hand)' names Stray from History", (await A.guess("Stray from History (to your hand)")) === "Stray from History");
  check("a label that names no card gets no preview", (await A.guess("Destroy all artifacts")) === "" && (await A.guess("None of them")) === "" && (await A.guess("")) === "" && (await A.guess("x".repeat(200))) === "");
  console.log(ok ? "\nall passed" : "\nFAILED");
  process.exit(ok ? 0 : 1);
})();
