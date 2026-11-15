// Browser end-to-end for the Hold'em table: Ana on a desktop (3D table) and Ben on a phone (2D table) against two AI
// seats, on their own poker server. Each acts from their own page and the other screen follows; each sees their own
// two cards and never the other's (not in the DOM, not in the page's JS state, not in the 3D scene); a spectator
// sees neither; no page errors; no horizontal scroll at phone width; reduced motion gets the 2D table.
//
//   PW=/path/to/node_modules/@playwright/test node table/tests/poker_page_e2e.cjs
const path = require("path");
const { spawn } = require("child_process");
const os = require("os"), fs = require("fs");
const { chromium } = require(process.env.PW || "@playwright/test");

const ROOT = path.join(__dirname, "..", "..");
const PY = path.join(os.homedir(), ".venvs", "table", "bin", "python");
const PORT = require("./test_port.cjs").testPort();
const BASE = `http://127.0.0.1:${PORT}`;
const results = [];
const check = (ok, what) => { results.push([!!ok, what]); console.log((ok ? "  ✅ " : "  ❌ ") + what); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const state = async () => (await fetch(BASE + "/api/poker")).json();
const until = async (f, ms = 5000) => { const t = Date.now(); while (Date.now() - t < ms) { if (await f()) return true; await sleep(150); } return false; };
const RW = { A: "Ace", K: "King", Q: "Queen", J: "Jack", T: "Ten", 9: "Nine", 8: "Eight", 7: "Seven", 6: "Six", 5: "Five", 4: "Four", 3: "Three", 2: "Two" };
const SW = { s: ["♠", "spades"], h: ["♥", "hearts"], d: ["♦", "diamonds"], c: ["♣", "clubs"] };
const forms = c => [c, (c[0] === "T" ? "10" : c[0]) + SW[c[1]][0], `${RW[c[0]]} of ${SW[c[1]][1]}`];   // every way a page writes a card

(async () => {
  const srv = spawn(PY, ["table/poker_server.py", "--players", "Ana,Ben", "--ai", "ai:Leonardo,ai:Fusion", "--seed", "7", "--pause", "1.5",
                         "--ai-think", "0.25", "--ai-iters", "60", "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, TABLE_RESEARCH_DIR: fs.mkdtempSync(path.join(os.tmpdir(), "poker-e2e-")) }, stdio: "ignore" });
  let browser;
  try {
    for (let i = 0; i < 60; i++) { try { await state(); break; } catch { await sleep(250); } }
    browser = await chromium.launch({ headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
    const errors = [];
    const open = async (who, viewport, extra = {}) => {
      const page = await (await browser.newContext({ viewport, ...extra })).newPage();
      page.on("pageerror", e => errors.push(`${who}: ${e.message}`));
      page.on("console", m => { if (m.type() === "error") errors.push(`${who}: ${m.text()}`); });
      await page.goto(who ? `${BASE}/?player=${who}` : `${BASE}/watch`);
      if (who) await page.waitForFunction(n => window.TableSeat && TableSeat.name() === n, who);
      await page.waitForFunction(() => window.POKER && POKER.state && POKER.state.seats);
      return page;
    };
    const ana = await open("Ana", { width: 1280, height: 900 });
    const ben = await open("Ben", { width: 390, height: 844 }, { isMobile: true, hasTouch: true });
    const watch = await open("", { width: 1280, height: 800 });

    // ── the views
    const in3d = await until(() => ana.evaluate(() => window.POKER3D && POKER3D.mode() === "3d" && POKER3D.frames.length > 30), 15000);
    check(in3d, "a desktop opens the 3D table and renders frames");
    const ft = await ana.evaluate(() => { const f = POKER3D.frames.slice(-120); return f.reduce((a, b) => a + b, 0) / f.length; });
    console.log(`  3D frame time (headless, software GL): ${ft.toFixed(1)} ms`);
    check(await ana.evaluate(() => POKER3D.debug().seats === 4), "every seat is at the 3D table");
    check(await ben.evaluate(() => !window.POKER3D && getComputedStyle(document.getElementById("flat")).display !== "none"),
          "a phone gets the 2D table (and never starts WebGL)");
    check(await ben.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), "no horizontal scrolling at phone width (390 px)");

    // ── the first hand
    await ana.click("#start");
    check(await until(async () => (await state()).hand === 1, 5000), "Ana deals the first hand from her page");
    const holeOf = p => p.evaluate(() => (POKER.state.me && POKER.state.me.hole) || []);
    check(await until(async () => (await holeOf(ana)).length === 2 && (await holeOf(ben)).length === 2, 6000), "each player's page holds their own two cards");
    const anaCards = await holeOf(ana), benCards = await holeOf(ben);
    check(await until(() => ana.evaluate(h => JSON.stringify(POKER3D.debug().myCards.slice().sort()) === JSON.stringify(h.slice().sort()), anaCards), 8000),
          `Ana sees her cards face up on the 3D table (${anaCards.join(" ")})`);
    check(await ben.evaluate(h => { const t = document.getElementById("mine").innerHTML; return h.every(c => t.includes(c[0] === "T" ? "10" : c[0])); }, benCards)
          && await ben.evaluate(() => document.querySelectorAll("#mine .card:not(.back)").length === 2), "Ben sees his two cards big on his phone");

    // Nothing of another player's hand may reach a page before the showdown: not its HTML, not window.POKER, not the 3D scene.
    const leakCheck = async () => {
      const out = [];
      for (const [page, who, theirs] of [[ben, "Ben's page", anaCards], [ana, "Ana's page", benCards], [watch, "the spectator page", [...anaCards, ...benCards]]]) {
        const blob = await page.evaluate(() => {                 // the DOM without its own code (script text can look like a card: "2d")
          const d = document.documentElement.cloneNode(true); d.querySelectorAll("script,style").forEach(x => x.remove());
          return d.outerHTML + "\n" + JSON.stringify(window.POKER.state) +
                 "\n" + (window.POKER3D ? JSON.stringify(POKER3D.debug()) : ""); });
        for (const c of theirs) for (const f of forms(c)) if (new RegExp(`(?<![A-Za-z0-9])${f.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![A-Za-z0-9])`).test(blob)) out.push(`${f} in ${who}`);
      }
      return out;
    };
    const leaks = new Set();
    let leakChecks = 0;

    // ── play: whoever is to act among the people does it from their own page; the other screen must follow
    const acted = { Ana: 0, Ben: 0 }, followed = { Ana: 0, Ben: 0 };
    const t0 = Date.now();
    let s = await state();
    while (Date.now() - t0 < 90000 && s.hand === 1 && s.street !== "done") {
      if (s.street !== "done") { (await leakCheck()).forEach(x => leaks.add(x)); leakChecks++; }
      const who = s.to_act_name;
      if (who === "Ana" || who === "Ben") {
        const me = who === "Ana" ? ana : ben, other = who === "Ana" ? ben : ana;
        await me.waitForFunction(() => !document.getElementById("call").disabled, null, { timeout: 8000 }).catch(() => {});
        const before = (await state()).log.length;
        await me.click("#call");
        acted[who]++;
        let line = "";
        await until(async () => { const l = (await state()).log; line = l.slice(before).find(x => x.startsWith(who + " ")) || ""; return !!line; }, 5000);
        if (line && await until(() => other.evaluate(t => document.getElementById("hlog").innerText.includes(t), line), 5000)) followed[who]++;
      } else await sleep(120);
      s = await state();
    }
    check(acted.Ana > 0 && acted.Ben > 0, `both people acted from their own pages (Ana ${acted.Ana}×, Ben ${acted.Ben}×)`);
    check(followed.Ana === acted.Ana && followed.Ben === acted.Ben, `every action showed up on the other person's screen (${followed.Ana + followed.Ben}/${acted.Ana + acted.Ben})`);
    check(leakChecks > 0 && !leaks.size, `before the showdown no page held another player's cards (${leakChecks} checks of 3 pages)` + (leaks.size ? ": " + [...leaks].slice(0, 4).join(", ") : ""));
    check(await until(async () => (await state()).street === "done" || (await state()).hand > 1, 30000), "the hand plays through to its result");
    check(await ana.evaluate(() => /revealed when the game ends/.test(document.getElementById("verify").innerText) && !document.querySelector("[data-verify-all]")),
          "a finished hand's seed stays sealed while the game goes on (it would show the folded cards)");
    check(await until(async () => (await state()).hand >= 2, 8000), "the next hand deals itself after the pause");
    check(await until(() => ben.evaluate(() => POKER.state.hand >= 2), 8000) && await until(() => ana.evaluate(() => POKER.state.hand >= 2), 8000),
          "both screens move on to the next hand");
    await ana.click("#end"); await ana.click("#end");          // pressed twice: no browser dialog
    check(await until(async () => (await state()).over, 5000), "End game (pressed twice) stops play");
    await until(() => ana.evaluate(() => !!document.querySelector("[data-verify-all]")), 5000);
    await ana.click("[data-verify-all]");
    check(await until(() => ana.evaluate(() => (document.getElementById("vres") || {}).textContent?.startsWith("✅")), 15000),
          "…then the browser rebuilds every hand's deck from its seed and matches the commits it saw before each deal");
    check(await watch.evaluate(() => !!window.POKER3D && !document.getElementById("fold").offsetParent && !window.TableSeat),
          "the spectator page shows the table with no seat and no controls");
    const calm = await open("Ben", { width: 1280, height: 900 }, { reducedMotion: "reduce" });
    await sleep(800);
    check(await calm.evaluate(() => !window.POKER3D && getComputedStyle(document.getElementById("flat")).display !== "none"),
          "a desktop that asks for reduced motion gets the 2D table");
    check(!errors.length, "no page errors or console errors" + (errors.length ? ": " + errors.slice(0, 3).join(" | ") : ""));
  } finally {
    if (browser) await browser.close();
    srv.kill();
  }
  const ok = results.filter(r => r[0]).length;
  console.log(`\n${ok}/${results.length} poker page checks passed`);
  process.exit(ok === results.length ? 0 : 1);
})();
