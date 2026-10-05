// Browser end-to-end for the D&D battle map: Michael on a desktop (3D view) and Sam on a phone (2D view), on their
// own D&D server. In 3D Michael taps his token, then a square (real raycasting); Sam taps-to-move on the phone and
// Michael's 3D table animates it; nobody can pick up another person's token; no horizontal scroll at phone width.
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
const until = async (f, ms = 5000) => { const t = Date.now(); while (Date.now() - t < ms) { if (await f()) return true; await sleep(150); } return false; };

(async () => {
  const srv = spawn(PY, ["table/dnd_server.py", "--players", "Michael,Sam", "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, DIVINCI_FUSION_API_KEY: "", TABLE_RESEARCH_DIR: os.tmpdir() }, stdio: "ignore" });
  let browser;
  try {
    for (let i = 0; i < 60; i++) { try { await state(); break; } catch { await sleep(250); } }
    browser = await chromium.launch({ headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
    const errors = [];
    const open = async (who, viewport, extra = {}) => {
      const page = await (await browser.newContext({ viewport, ...extra })).newPage();
      page.on("pageerror", e => errors.push(`${who}: ${e.message}`));
      page.on("console", m => { if (m.type() === "error") errors.push(`${who}: ${m.text()}`); });
      await page.goto(`${BASE}/?player=${who}`);
      await page.waitForFunction(() => typeof S !== "undefined" && S && S.map);
      await page.waitForFunction(n => window.TableSeat && TableSeat.name() === n, who);
      return page;
    };
    const mike = await open("Michael", { width: 1280, height: 900 });
    const sam = await open("Sam", { width: 390, height: 844 });

    // ── Michael's desktop: the 3D table
    const in3d = await until(() => mike.evaluate(() => window.DND3D && DND3D.mode() === "3d" && DND3D.frames.length > 30), 8000);
    check(in3d, "a desktop opens the 3D table and renders frames");
    const toks = await mike.evaluate(() => DND3D.dnd.debug());
    check(toks.michael && toks.sam && Object.keys(toks).length === 2, `every token is on the 3D table (${Object.keys(toks).join(", ")})`);
    const ft = await mike.evaluate(() => { const f = DND3D.frames.slice(-120); return f.reduce((a, b) => a + b, 0) / f.length; });
    console.log(`  3D frame time (headless, software GL): ${ft.toFixed(1)} ms`);
    const screen = (x, z) => mike.evaluate(([x, z]) => {           // a point on the table → where it is on screen
      const c = DND3D.camera, r = document.querySelector("#map3d canvas").getBoundingClientRect();
      const v = new c.position.constructor(x, 0.05, z).project(c);
      return [r.left + (v.x + 1) / 2 * r.width, r.top + (1 - v.y) / 2 * r.height];
    }, [x, z]);
    const m0 = (await state()).map.tokens.michael;
    let [sx, sy] = await screen(m0.x + 0.5, m0.y + 0.5); await mike.mouse.click(sx, sy);
    check(await mike.evaluate(() => MAP.sel === "michael"), "in 3D, tapping your character selects it (raycast)");
    [sx, sy] = await screen(m0.x + 2.5, m0.y + 0.5); await mike.mouse.click(sx, sy);
    const moved3d = await until(async () => { const t = (await state()).map.tokens.michael; return t.x === m0.x + 2 && t.y === m0.y; });
    check(moved3d, `…then a square on the table moves it there (${m0.x},${m0.y} → ${m0.x + 2},${m0.y})`);

    // ── Sam's phone: the 2D map
    check(await sam.evaluate(() => !window.DND3D && getComputedStyle(document.getElementById("map")).display !== "none"), "a phone opens the 2D map (and never starts WebGL)");
    const box = await sam.locator("#map").boundingBox(), cell = await sam.evaluate(() => MAP.cell);
    const tap = async (x, y) => sam.mouse.click(box.x + (x + .5) * cell, box.y + (y + .5) * cell);
    const s0 = (await state()).map.tokens.sam;
    await tap(s0.x, s0.y); await tap(s0.x + 1, s0.y + 2);
    const movedSam = await until(async () => { const t = (await state()).map.tokens.sam; return t.x === s0.x + 1 && t.y === s0.y + 2; });
    check(movedSam, "on the phone, tap your character then a square");
    const anim = await until(() => mike.evaluate(([x, z]) => { const d = DND3D.dnd.debug().sam; return Math.abs(d.at[0] - x) < 0.01 && Math.abs(d.at[1] - z) < 0.01; },
                                                  [s0.x + 1.5, s0.y + 2.5]), 6000);
    check(anim, "Michael's 3D table walks Sam's token to its new square");
    const mNow = (await state()).map.tokens.michael;
    await tap(mNow.x, mNow.y);
    check(await sam.evaluate(() => MAP.sel === null), "Sam can't pick up Michael's token");
    check(await sam.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), "no horizontal scrolling at phone width");
    const calm = await open("Sam", { width: 1280, height: 900 }, { reducedMotion: "reduce" });
    await sleep(800);
    check(await calm.evaluate(() => !window.DND3D && getComputedStyle(document.getElementById("map")).display !== "none"),
          "a desktop that asks for reduced motion gets the 2D map");
    check(!errors.length, "no page errors" + (errors.length ? ": " + errors.slice(0, 3).join(" | ") : ""));
  } finally {
    if (browser) await browser.close();
    srv.kill();
  }
  const ok = results.filter(r => r[0]).length;
  console.log(`\n${ok}/${results.length} D&D page checks passed`);
  process.exit(ok === results.length ? 0 : 1);
})();
