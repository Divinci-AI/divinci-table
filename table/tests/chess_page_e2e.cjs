// The chess page: the 3D board on a desktop (raycast tap-to-move), the 2D board on a phone, each screen following the
// other's moves within a second (long poll), Black sitting on Black's side, reduced motion → 2D, no page errors.
//   PW=…/@playwright/test node table/tests/chess_page_e2e.cjs
const path = require("path");
const { spawn } = require("child_process");
const os = require("os");
const { chromium } = require(process.env.PW || "@playwright/test");
const { testPort } = require("./test_port.cjs");

const ROOT = path.resolve(__dirname, "../..");
const PY = path.join(os.homedir(), ".venvs", "table", "bin", "python");
const PORT = testPort(), BASE = `http://127.0.0.1:${PORT}`;
const results = [];
const check = (ok, what) => { results.push([!!ok, what]); console.log((ok ? "  ✅ " : "  ❌ ") + what); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const state = async () => (await fetch(BASE + "/api/chess")).json();
const until = async (f, ms = 5000) => { const t = Date.now(); while (Date.now() - t < ms) { if (await f()) return Date.now() - t; await sleep(50); } return null; };

(async () => {
  const srv = spawn(PY, ["table/chess_server.py", "--white", "Ana", "--black", "Ben", "--minutes", "10", "--increment", "5", "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, TABLE_RESEARCH_DIR: os.tmpdir() }, stdio: "ignore" });
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
      await page.waitForFunction(n => typeof S !== "undefined" && S && window.TableSeat && TableSeat.name() === n, who);
      return page;
    };
    const ana = await open("Ana", { width: 1280, height: 900 });
    const ben = await open("Ben", { width: 390, height: 844 }, { isMobile: true, hasTouch: true });

    // ── Ana's desktop: the 3D board
    const in3d = await until(() => ana.evaluate(() => window.CHESS3D && CHESS3D.mode() === "3d" && CHESS3D.frames.length > 30), 10000);
    check(in3d !== null, "a desktop opens the 3D board and renders frames");
    const dbg = await ana.evaluate(() => CHESS3D.board.debug());
    check(Object.keys(dbg.pieces).length === 32 && dbg.pieces.e1 === "K" && dbg.pieces.d8 === "q", "all 32 pieces stand on their squares");
    check(dbg.side === "white", "White's screen sits on White's side");
    const ft = await ana.evaluate(() => { const f = CHESS3D.frames.slice(-120); return f.reduce((a, b) => a + b, 0) / f.length; });
    console.log(`  3D frame time (headless, software GL): ${ft.toFixed(1)} ms`);
    const r0 = await ana.evaluate(() => CHESS3D.renders()); await sleep(1000); const r1 = await ana.evaluate(() => CHESS3D.renders());
    check(r1 - r0 <= 1, `an idle board isn't redrawn (${r1 - r0} draws in 1 s)`);
    const tapSq = async sq => {                        // click the centre of a square on the 3D board
      const [x, y] = await ana.evaluate(sq => {
        const c = CHESS3D.camera, r = document.querySelector("#board3d canvas").getBoundingClientRect();
        const f = "abcdefgh".indexOf(sq[0]) - 3.5, z = 3.5 - (+sq[1] - 1);
        const v = new c.position.constructor(f, 0.02, z).project(c);
        return [r.left + (v.x + 1) / 2 * r.width, r.top + (1 - v.y) / 2 * r.height];
      }, sq);
      await ana.mouse.click(x, y);
    };
    await tapSq("e2");
    check((await ana.evaluate(() => sel)) === null && /Start/.test(await ana.locator("#err").textContent()), "tapping before Start says to press Start");
    await ana.locator("#start").click();
    await until(async () => (await state()).started);
    await ana.waitForFunction(() => S && S.started);   // the page has the started game too (not only the server)
    await tapSq("e2");
    check((await ana.evaluate(() => sel)) === "e2", "in 3D, tapping a pawn selects it (raycast)");
    const marks = await until(() => ana.evaluate(() => { const m = CHESS3D.board.debug().marks; return m.e3 === "target" && m.e4 === "target"; }), 3000);
    check(marks !== null, "…and its two squares are marked on the 3D board");
    await tapSq("e4");
    const played = await until(async () => (await state()).lastmove === "e2e4");
    check(played !== null, "…then tapping e4 plays e2e4");
    const benSaw = await until(() => ben.evaluate(() => !!document.querySelector('#board [data-sq="e4"] .pc.w')), 3000);
    check(benSaw !== null && benSaw < 1000, `Ben's phone shows the pawn on e4 within a second (${benSaw} ms after the server had it)`);

    // ── Ben's phone: the 2D board
    check(await ben.evaluate(() => !window.CHESS3D && getComputedStyle(document.getElementById("board")).display !== "none"), "a phone opens the 2D board (and never starts WebGL)");
    check(await ben.evaluate(() => [...document.querySelectorAll("#board .sq")].map(e => e.dataset.sq)[0] === "h1"), "Black's 2D board is turned round (h1 at the top left)");
    await ben.locator('#board [data-sq="e7"]').click(); await ben.locator('#board [data-sq="e5"]').click();
    const benMoved = await until(async () => (await state()).lastmove === "e7e5");
    check(benMoved !== null, "on the phone, tap a pawn then a square");
    const anaSaw = await until(() => ana.evaluate(() => { const d = CHESS3D.board.debug(); return d.pieces.e5 === "p" && !d.pieces.e7 && !d.busy; }), 4000);
    check(anaSaw !== null, `Ana's 3D board walks Ben's pawn to e5 (${anaSaw} ms)`);
    await ben.locator('#board [data-sq="d7"]').click();
    check(/Ana's move/.test(await ben.locator("#err").textContent()), "tapping out of turn says whose move it is");
    check(await ben.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), "no horizontal scrolling at phone width");

    // ── a capture in 3D: the taken piece leaves the board
    for (const [who, mv] of [[ana, "g1f3"], [ben, "d7d5"]]) { await who.evaluate(m => post("/api/chess/move", { move: m }), mv); await sleep(150); }
    await until(async () => (await state()).turn === "white");
    await tapSq("e4"); await tapSq("d5");
    const took = await until(() => ana.evaluate(() => { const d = CHESS3D.board.debug(); return d.pieces.d5 === "P" && !d.busy; }), 4000);
    check(took !== null && (await state()).lastmove === "e4d5", "a capture in 3D: exd5, and Black's pawn leaves the board");
    check(await ana.evaluate(() => Object.keys(CHESS3D.board.debug().pieces).length === 31), "31 pieces left on the 3D board");

    // ── Black's desktop and reduced motion
    const ben3d = await open("Ben", { width: 1280, height: 900 });
    await until(() => ben3d.evaluate(() => window.CHESS3D && CHESS3D.frames.length > 10), 8000);
    check(await ben3d.evaluate(() => CHESS3D.board.debug().side === "black"), "Black's desktop sits on Black's side of the 3D board");
    const calm = await open("Ana", { width: 1280, height: 900 }, { reducedMotion: "reduce" });
    await sleep(800);
    check(await calm.evaluate(() => !window.CHESS3D && getComputedStyle(document.getElementById("board")).display !== "none"),
          "a desktop that asks for reduced motion gets the 2D board");
    await calm.locator('[data-view="3d"]').click();
    check(await until(() => calm.evaluate(() => window.CHESS3D && CHESS3D.mode() === "3d"), 5000) !== null, "…and can still switch to 3D by choice");
    check(!errors.length, "no page errors" + (errors.length ? ": " + errors.slice(0, 3).join(" | ") : ""));
  } finally {
    if (browser) await browser.close();
    srv.kill();
  }
  const ok = results.filter(r => r[0]).length;
  console.log(`\n${ok}/${results.length} chess page checks passed`);
  process.exit(ok === results.length ? 0 : 1);
})();
