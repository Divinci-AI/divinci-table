// Browser end-to-end for the D&D page's battle map: two people (a desktop and a phone-sized window) on
// their own D&D server. Michael taps his token, then a square; the server moves it; Sam's page sees it.
// A tap on Sam's token does nothing for Michael. No horizontal scrolling at phone width.
//
//   PW=/path/to/node_modules/@playwright/test node table/tests/dnd_page_e2e.cjs
const path = require("path");
const { spawn } = require("child_process");
const os = require("os");
const { chromium } = require(process.env.PW || "@playwright/test");

const ROOT = path.join(__dirname, "..", "..");
const PY = path.join(os.homedir(), ".venvs", "table", "bin", "python");
const PORT = 8000 + Math.floor(Math.random() * 900) + 30;
const BASE = `http://127.0.0.1:${PORT}`;
const results = [];
const check = (ok, what) => { results.push([!!ok, what]); console.log((ok ? "  ✅ " : "  ❌ ") + what); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const state = async () => (await fetch(BASE + "/api/dnd")).json();

(async () => {
  const srv = spawn(PY, ["table/dnd_server.py", "--players", "Michael,Sam", "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, DIVINCI_FUSION_API_KEY: "", TABLE_RESEARCH_DIR: os.tmpdir() }, stdio: "ignore" });
  let browser;
  try {
    for (let i = 0; i < 60; i++) { try { await state(); break; } catch { await sleep(250); } }
    browser = await chromium.launch({ headless: true });
    const errors = [];
    const open = async (who, viewport) => {
      const page = await (await browser.newContext({ viewport })).newPage();
      page.on("pageerror", e => errors.push(`${who}: ${e.message}`));
      page.on("console", m => { if (m.type() === "error") errors.push(`${who}: ${m.text()}`); });
      await page.goto(`${BASE}/?player=${who}`);
      await page.waitForFunction(() => typeof S !== "undefined" && S && S.map && document.getElementById("map").height > 0);
      await page.waitForFunction(n => window.TableSeat && TableSeat.name() === n, who);
      return page;
    };
    const mike = await open("Michael", { width: 1280, height: 860 });
    const sam = await open("Sam", { width: 390, height: 844 });
    const box = await mike.locator("#map").boundingBox();
    const cell = await mike.evaluate(() => MAP.cell);
    const tap = async (page, b, c, x, y) => page.mouse.click(b.x + (x + .5) * c, b.y + (y + .5) * c);

    const t0 = (await state()).map.tokens.michael;
    check(t0 && t0.owner === "Michael", `Michael's token is on the map at (${t0 && t0.x}, ${t0 && t0.y})`);
    await tap(mike, box, cell, t0.x, t0.y);
    check(await mike.evaluate(() => MAP.sel === "michael"), "tapping your character selects it");
    await tap(mike, box, cell, 4, 2);
    let t1;
    for (let i = 0; i < 20; i++) { t1 = (await state()).map.tokens.michael; if (t1.x === 4) break; await sleep(150); }
    check(t1.x === 4 && t1.y === 2, `…then a square moves it there (now ${t1.x}, ${t1.y})`);
    let seen = false;
    for (let i = 0; i < 20 && !seen; i++) { seen = await sam.evaluate(() => S.map.tokens.michael.x === 4); if (!seen) await sleep(250); }
    check(seen, "Sam's phone sees Michael's move");
    const s0 = (await state()).map.tokens.sam;
    await tap(mike, box, cell, s0.x, s0.y);
    check(await mike.evaluate(() => MAP.sel === null), "Michael can't pick up Sam's token");
    const wide = await sam.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
    check(wide, "no horizontal scrolling at phone width");
    const ms = await mike.evaluate(() => { const t = performance.now(); for (let i = 0; i < 50; i++) drawMap(); return (performance.now() - t) / 50; });
    console.log(`  map redraw: ${ms.toFixed(2)} ms`);
    check(!errors.length, "no page errors" + (errors.length ? ": " + errors.slice(0, 3).join(" | ") : ""));
  } finally {
    if (browser) await browser.close();
    srv.kill();
  }
  const ok = results.filter(r => r[0]).length;
  console.log(`\n${ok}/${results.length} D&D page checks passed`);
  process.exit(ok === results.length ? 0 : 1);
})();
