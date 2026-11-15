// Screenshots of the three.js tables, for a person to look at (tests can't tell if a scene reads well).
//   PW=…/@playwright/test node table/tests/shots_3d.cjs dnd|chess|poker [out-dir]
// Starts its own table on a safe port (test_port.cjs), never a live one. Writes PNGs to out-dir (default: a new temp
// folder, printed at the end). Nothing here is committed: a game's screenshots can show card art.
const path = require("path"), os = require("os"), fs = require("fs");
const { spawn } = require("child_process");
const { chromium } = require(process.env.PW || "@playwright/test");
const { testPort } = require("./test_port.cjs");

const ROOT = path.resolve(__dirname, "../..");
const PY = path.join(os.homedir(), ".venvs", "table", "bin", "python");
const GAME = process.argv[2] || "dnd";
const OUT = process.argv[3] || fs.mkdtempSync(path.join(os.tmpdir(), `shots-${GAME}-`));
const PORT = testPort(), BASE = `http://127.0.0.1:${PORT}`;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const GL = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"];

const SERVERS = {
  dnd: ["table/dnd_server.py", "--players", "Ana,Ben", "--dm", "Michael"],
  chess: ["table/chess_server.py", "--white", "Ana", "--black", "ai:Leonardo:3", "--minutes", "10", "--increment", "5"],
  poker: ["table/poker_server.py", "--players", "Ana,Ben", "--ai", "ai:Leonardo,ai:Fusion", "--seed", "7"],
};

async function up(probe) {
  for (let i = 0; i < 80; i++) { try { const r = await fetch(BASE + probe); if (r.ok) return; } catch {} await sleep(250); }
  throw new Error("the table did not start");
}

async function shoot(browser, name, url, viewport, ready, before) {
  const ctx = await browser.newContext({ viewport, isMobile: viewport.width < 500, hasTouch: viewport.width < 500 });
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", e => errs.push(e.message));
  page.on("console", m => { if (m.type() === "error") errs.push(m.text()); });
  await page.goto(BASE + url);
  if (ready) await page.waitForFunction(ready, null, { timeout: 15000 }).catch(() => errs.push("not ready in 15 s: " + ready));
  if (before) await before(page);
  await sleep(1500);
  const f = path.join(OUT, name + ".png");
  await page.screenshot({ path: f });
  console.log(`  ${name}.png${errs.length ? "  ⚠ " + errs.slice(0, 2).join(" | ") : ""}`);
  return page;
}

(async () => {
  const srv = spawn(PY, SERVERS[GAME].concat(["--port", String(PORT)]), {
    cwd: ROOT, stdio: "ignore",
    env: { ...process.env, DIVINCI_FUSION_API_KEY: "", HF_HUB_OFFLINE: "1", TABLE_RESEARCH_DIR: fs.mkdtempSync(path.join(os.tmpdir(), "shots-research-")) },
  });
  let browser;
  try {
    browser = await chromium.launch({ headless: true, args: GL });
    if (GAME === "dnd") {
      await up("/api/dnd");
      const dm = await shoot(browser, "dnd-dm-desktop-start", "/?player=Michael", { width: 1280, height: 800 },
        "typeof S !== 'undefined' && S && S.map && window.TableSeat && TableSeat.name() === 'Michael'");
      const api = (u, b) => dm.evaluate(([u, b]) => post(u, b), [u, b]);
      for (const loc of ["tavern", "cave", "crypt", "bridge"]) {
        await api("/api/dnd/map/location", { id: loc });
        const st = await (await fetch(BASE + "/api/dnd")).json();
        const rows = st.map.layout, taken = new Set(Object.values(st.map.tokens).map(t => t.x + "," + t.y));
        const free = [];
        rows.forEach((r, y) => [...r].forEach((c, x) => { if (".,".includes(c) && !taken.has(x + "," + y)) free.push([x, y]); }));
        const mid = free[Math.floor(free.length / 2)] || [1, 1];
        await api("/api/dnd/map/place", { name: "Goblin 1", kind: "monster", x: mid[0], y: mid[1] });
        const near = free.find(([x, y]) => Math.abs(x - mid[0]) >= 2 && Math.abs(x - mid[0]) <= 4 && Math.abs(y - mid[1]) <= 2) || free[0];
        await api("/api/dnd/map/place", { name: "Ogre", kind: "monster", x: near[0], y: near[1], size: "large" });
        await shoot(browser, `dnd-${loc}-desktop-3d`, "/?player=Ana", { width: 1280, height: 800 },
          "window.DND3D && DND3D.mode() === '3d' && DND3D.frames.length > 30");
        if (loc === "cave") {
          await shoot(browser, "dnd-cave-phone", "/?player=Ben", { width: 390, height: 844 }, "typeof S !== 'undefined' && S && S.map");
          await shoot(browser, "dnd-cave-xr-preview", "/xr?player=Ana", { width: 1280, height: 800 }, "window.DNDXR && DNDXR.state && DNDXR.state()");
        }
      }
    } else if (GAME === "chess") {
      await up("/api/chess");
      const ana = await shoot(browser, "chess-desktop-start", "/?player=Ana", { width: 1280, height: 800 },
        "window.TableSeat && TableSeat.name() === 'Ana' && window.CHESS3D && CHESS3D.frames.length > 30");
      await ana.evaluate(() => post("/api/chess/start"));
      for (const mv of ["e2e4", "g1f3", "f1c4", "e1g1"]) {           // a few moves (castling included); the AI answers each
        await ana.waitForFunction(() => S && S.turn === "white" && S.started, null, { timeout: 20000 });
        const d = await ana.evaluate(m => post("/api/chess/move", { move: m }), mv);
        if (d.error) { const alt = await ana.evaluate(() => S.legal[0]); await ana.evaluate(m => post("/api/chess/move", { move: m }), alt); }
      }
      await ana.waitForFunction(() => S && S.turn === "white", null, { timeout: 20000 });
      await ana.evaluate(() => { const from = S.legal.find(m => m.startsWith("d")) || S.legal[0]; tapSquare(from.slice(0, 2)); });
      await sleep(1200);
      await ana.screenshot({ path: path.join(OUT, "chess-desktop-3d-midgame.png") }); console.log("  chess-desktop-3d-midgame.png");
      await ana.evaluate(() => document.querySelector('[data-view="2d"]').click()); await sleep(500);
      await ana.screenshot({ path: path.join(OUT, "chess-desktop-2d-midgame.png") }); console.log("  chess-desktop-2d-midgame.png");
      await ana.evaluate(() => document.querySelector('[data-view="3d"]').click());
      await shoot(browser, "chess-phone", "/?player=Ana", { width: 390, height: 844 }, "window.TableSeat && typeof S !== 'undefined' && S");
    } else if (GAME === "poker") {
      await up("/api/poker");
      await shoot(browser, "poker-desktop-ana", "/?player=Ana", { width: 1280, height: 800 }, "window.POKER3D && POKER3D.frames.length > 30");
      await shoot(browser, "poker-phone-ben", "/?player=Ben", { width: 390, height: 844 }, "window.POKER && POKER.state");
      await shoot(browser, "poker-watch", "/watch", { width: 1280, height: 720 }, "window.POKER3D && POKER3D.frames.length > 30");
    }
  } finally {
    if (browser) await browser.close();
    srv.kill();
  }
  console.log("shots in " + OUT);
})();
