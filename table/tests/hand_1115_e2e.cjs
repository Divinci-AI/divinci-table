// Game retro 2026-11-15, the page level: (6) the "It's your turn" banner's button IS Begin my turn (it sends the same request as the step row's
// button), with a small ✕ to dismiss; (4) a summoning-sick creature's {T} mana ability offers no "Add … to pool" buttons on /hand: a disabled
// button says why; (9) every one of your creatures has a visible, clickable edge on the mat, however many there are (Collation Trimatrix, an
// artifact creature cast ninth, was placed under the artifacts grid). A real server on a free port (never a live game's), a real browser, every press through the page.
//   PW=~/Documents/server/workspace/clients/tests/node_modules/@playwright/test node table/tests/hand_1115_e2e.cjs
let chromium;
try { ({ chromium } = require(process.env.PW || "@playwright/test")); }
catch (e) { console.error("SKIP: needs Playwright (PW=<path to node_modules/@playwright/test>)"); process.exit(77); }
const { spawn, execFileSync, spawnSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path"), net = require("net");
const REPO = path.resolve(__dirname, "../..");
const PY = process.env.PY || path.join(os.homedir(), ".venvs/table/bin/python");
{ // the card data (rules text, and the Star Trek set the deck is built from): without it this SKIPS (77), as the Python tests do
  const r = spawnSync(PY, ["-c", "import sys; sys.path.insert(0,'table/tests'); import cards_path; cards_path.need_card_data(); cards_path.cards_json()"],
    { cwd: REPO, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  if (r.error || r.status !== 0) {
    console.error(r.status === 77 ? (r.stderr || "").trim() : `SKIP: no Python to build the test deck with (${PY}; set PY=<python>)`);
    process.exit(77);
  }
}
const LIVE = [8800, 8801, 8820, 8830, 8883];
let failed = [];
const check = (ok, n, d = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${n}${ok ? "" : " — " + String(d).slice(0, 400)}`); if (!ok) failed.push(n); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const freePort = () => new Promise(res => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); }); });
(async () => {
  let PORT = await freePort(); while (LIVE.includes(PORT)) PORT = await freePort();
  const BASE = `http://127.0.0.1:${PORT}`, tmp = fs.mkdtempSync(path.join(os.tmpdir(), "hand1115-"));
  const deckScript = `import json,os,sys
sys.path.insert(0,'table/tests'); sys.path.insert(0,'table')
import cards_path, make_deck_json_shim as s
C={c['name']:c for c in s.load(cards_path.cards_json())}
P={'Forest':30,'Plains':10,'Mountain':10,'Xenobotanist':4,'Tal Shiar Operative':4,'Horta':2,'Plasma Cascade':4,'Collation Trimatrix':2,'Starfleet Security':16,'Support Mission':2}
json.dump({'name':'t','commander':None,'startingLife':20,'mainBoard':[{**C[n],'count':k} for n,k in P.items()]},open(sys.argv[1],'w'))`;
  execFileSync(PY, ["-c", deckScript, tmp + "/deck.json"], { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  const srv = spawn(PY, ["table/server.py", "--any-card", "--port", String(PORT), "--brain", "external", "--token-file", tmp + "/token", "--fair-seed", "off", "--seed", "3",
    "--ai", "Claude|", "--ai-deck", tmp + "/deck.json", "--ai", "Michael|", "--ai-deck", tmp + "/deck.json", "--pilot", "Claude", "--pilot", "Michael", "--pilot-auto-begin", "off"],
    { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", TABLE_NO_VOICE: "1", TABLE_HIGHROLL: "first", TABLE_CHECKIN: "off", STRICT_SEATS: "1", HF_HUB_OFFLINE: "1", ROUTER: "code",
      REPLIES: "template", NO_GEMMA: "1", TABLE_RESEARCH_DIR: tmp + "/r", TABLE_SAVES_DIR: tmp + "/s", TYPESAFE_API_KEY: "" }, stdio: "ignore" });
  let browser, token = "", K = {};
  const J = async r => { const t = await r.text(); try { return JSON.parse(t); } catch { return { error: t.slice(0, 200) }; } };
  const host = async (a, b) => J(await fetch(`${BASE}/api/brain/${a}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Brain-Token": token }, body: JSON.stringify(b) }));
  const seat = async (n, a, b = {}) => { for (let i = 0; i < 40; i++) { const r = await fetch(`${BASE}/api/brain/${a}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K[n] }, body: JSON.stringify({ seat: n, ...b }) });
    const d = await J(r); if (r.status === 409 && /waiting on/.test(d.error || "")) { await sleep(250); continue; } return { code: r.status, ...d }; } };
  const st = async n => J(await fetch(`${BASE}/api/brain/state?seat=${n}`, { headers: { "X-Seat-Key": K[n] } }));
  const ph = async () => J(await fetch(`${BASE}/api/phase`));
  const until = async (pred, secs = 30) => { for (let i = 0; i < secs * 4; i++) { const p = await ph(); if (pred(p)) return p; await sleep(250); } return ph(); };
  try {
    for (let i = 0; i < 120; i++) { try { if ((await fetch(BASE + "/api/phase")).ok) break; } catch {} await sleep(500); }
    token = fs.readFileSync(tmp + "/token", "utf8").trim();
    for (const n of ["Claude", "Michael"]) K[n] = (await J(await fetch(BASE + "/api/seat/claim", { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "e2e-" + n }, body: JSON.stringify({ name: n }) }))).key;
    await fetch(BASE + "/api/phase/windows", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on: false, by: "host" }) });
    await fetch(BASE + "/api/phase/next", { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K.Claude }, body: JSON.stringify({ by: "Claude", key: K.Claude }) });
    await until(p => p.player === "Claude");
    await seat("Claude", "begin");
    await until(p => p.player === "Claude" && p.begun && p.step === "main 1");

    browser = await chromium.launch();
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 }, userAgent: "e2e-Michael" });
    const page = await ctx.newPage(), errors = [];
    page.on("pageerror", e => errors.push(String(e)));
    await page.goto(`${BASE}/hand?player=Michael#key=${encodeURIComponent(K.Michael)}`);
    await page.waitForSelector("#mat .canvas", { timeout: 15000 }); await sleep(1500);

    console.log("the turn banner (6)");
    await seat("Claude", "end", { text: "over" });
    await until(p => p.player === "Michael");
    const btn = page.locator("[data-beginturn]");
    await btn.waitFor({ timeout: 10000 }).catch(() => {});
    check(await btn.isVisible() && (await btn.textContent()).trim() === "Begin my turn", "the banner's button reads Begin my turn", await page.locator("body").textContent().then(t => t.slice(0, 200)));
    check(await page.locator("[data-dismiss]").first().isVisible(), "…with a small dismiss beside it");
    check(!(await ph()).begun, "…and the turn hasn't begun before it is pressed");
    await btn.click();
    const p1 = await until(p => p.player === "Michael" && p.begun && p.step === "main 1", 15);
    check(p1.begun && p1.step === "main 1", "pressing it begins the turn (untap, draw): main 1, as the step row's button does", JSON.stringify(p1).slice(0, 200));
    check(!(await btn.isVisible().catch(() => false)), "…and the banner goes away");

    console.log("a summoning-sick mana creature (4)");
    await host("search", { seat: "Michael", name: "Xenobotanist" }); await host("put", { seat: "Michael", name: "Xenobotanist", quiet: true });
    const xb = (await st("Michael")).permanents.filter(p => p.name === "Xenobotanist").pop();
    await sleep(3000);
    await page.locator(`.mc[data-mc="${xb.id}"][data-mine="1"]`).first().click(); await sleep(300);
    const pop = page.locator("#matpop");
    check(await pop.locator("[data-mpool]").count() === 0, "no Add … to pool buttons on a summoning-sick Xenobotanist", await pop.innerHTML());
    const dis = pop.locator("button[disabled]", { hasText: "Can't add mana: summoning sick" });
    check(await dis.count() === 1 && /summoning|came under your control/i.test(await pop.textContent()), "…a disabled button says why", await pop.textContent());
    check((await pop.textContent()).includes("Can't attack"), "…as the attack line already did", await pop.textContent());
    await page.keyboard.press("Escape").catch(() => {}); await page.mouse.click(5, 5);

    console.log("a burn spell at any target (7)");
    for (const n of ["Mountain", "Mountain", "Mountain"]) { await host("search", { seat: "Michael", name: n }); await host("put", { seat: "Michael", name: n, quiet: true }); }
    await host("search", { seat: "Michael", name: "Plasma Cascade" }); await sleep(3500);
    const vals = await page.locator("select[data-slot] option").evaluateAll(os => os.map(o => o.value));
    check(vals.includes("player|Claude"), "Plasma Cascade's target list on the page offers the opposing player", vals.join(", "));
    check(vals.includes("player|Michael"), "…and yourself (any target)", vals.join(", "));

    console.log("every creature is visible on the mat (9)");
    await host("search", { seat: "Michael", name: "Support Mission" }); await host("put", { seat: "Michael", name: "Support Mission", quiet: true });   // the right-hand grid is not empty
    for (let i = 0; i < 14; i++) { await host("search", { seat: "Michael", name: "Starfleet Security" }); await host("put", { seat: "Michael", name: "Starfleet Security", quiet: true }); }
    await host("search", { seat: "Michael", name: "Collation Trimatrix" }); await host("put", { seat: "Michael", name: "Collation Trimatrix", quiet: true });
    await page.evaluate(() => { try { localStorage.clear(); } catch {} });   // (no saved spots: the auto layout)
    await page.reload(); await page.waitForSelector("#mat .canvas", { timeout: 15000 }); await sleep(3500);
    const mine = (await st("Michael")).permanents.filter(p => /Creature/.test(p.type || ""));
    const hidden = await page.evaluate(ids => {
      const cv = document.querySelector("#mat .canvas").getBoundingClientRect(), bad = [];
      for (const id of ids) {
        const el = document.querySelector(`.mc[data-mc="${id}"][data-mine="1"]`); if (!el) { bad.push(id + ":missing"); continue; }
        el.scrollIntoView({ block: "center", inline: "center" });
        const r = el.getBoundingClientRect(), x = r.left + 6, y = r.top + r.height / 2, hit = document.elementFromPoint(x, y);
        if (r.right > cv.right + 1 || r.left < cv.left - 1 || !hit || !hit.closest || hit.closest(".mc") !== el) bad.push(id + ":" + (hit && hit.closest && hit.closest(".mc") ? "under #" + hit.closest(".mc").dataset.mc : "clipped"));
      }
      return bad; }, mine.map(p => p.id));
    check(mine.length >= 16 && !hidden.length, `all ${mine.length} of your creatures show a clickable edge, Collation Trimatrix among them`, hidden.join(", "));
    check(!errors.length, "no page errors", errors.join(" | "));
  } catch (e) {
    check(false, "the run threw", e && e.stack || e);
  } finally {
    if (browser) await browser.close();
    srv.kill();
    console.log(failed.length ? `\nFAILED: ${failed.join("; ")}` : "\nall good");
    process.exit(failed.length ? 1 : 0);
  }
})();
