// Browser end-to-end: the 3D table always ends up showing the location's built room, not the placeholder boxes,
// even when the first download of room.glb fails or the server isn't answering yet (a new cloud room booting).
// Seen once live (2026-10-05): a fresh room kept the placeholder until a refresh.
//
//   PW=/path/to/node_modules/@playwright/test node table/tests/dnd_room_load_e2e.cjs
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

// What the 3D table shows: "room" (the built glTF), "fallback" (placeholder boxes), or why neither.
const shown = page => page.evaluate(() => {
  if (!window.DND3D) return "no-3d";
  const c = DND3D.dnd.root.children[0]?.children[0];
  if (!c) return "empty";
  let boxes = 0; c.traverse(o => { if (o.isMesh && o.geometry?.type === "BoxGeometry") boxes++; });
  return boxes ? "fallback" : "room";
}).catch(e => "error: " + e.message.slice(0, 80));

async function waitRoom(page, ms) {
  const t = Date.now(); let s = "";
  while (Date.now() - t < ms) { s = await shown(page); if (s === "room") return [true, s, Date.now() - t]; await sleep(200); }
  return [false, s, Date.now() - t];
}

(async () => {
  const srv = spawn(PY, ["table/dnd_server.py", "--players", "Michael,Sam", "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, DIVINCI_FUSION_API_KEY: "", TABLE_RESEARCH_DIR: os.tmpdir() }, stdio: "ignore" });
  let browser;
  try {
    for (let i = 0; i < 60; i++) { try { await (await fetch(BASE + "/api/dnd")).json(); break; } catch { await sleep(250); } }
    const st = await (await fetch(BASE + "/api/dnd")).json();
    check(st.map && st.map.assets && st.map.assets.room, `the local server offers a built room for ${st.map?.location} (run scripts/dnd_rooms.py first)`);
    browser = await chromium.launch({ headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
    const open = async (setup) => {
      const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
      const page = await ctx.newPage();
      if (setup) await setup(page);
      await page.goto(`${BASE}/?player=Michael`);
      return [page, ctx];
    };

    {   // 1. the plain case
      const [page, ctx] = await open();
      const [ok, s, ms] = await waitRoom(page, 10000);
      check(ok, `a normal load shows the built room (${s}, ${ms} ms)`);
      await ctx.close();
    }
    {   // 2. the first room.glb download fails (a blip, an edge hiccup, a rollout)
      let failed = 0;
      const [page, ctx] = await open(p => p.route("**/room.glb", r => (failed++ === 0 ? r.abort("failed") : r.continue())));
      const [ok, s, ms] = await waitRoom(page, 12000);
      check(failed >= 1 && ok, `after the first room download fails, the room still appears (${s}, ${ms} ms, ${failed} request(s))`);
      await ctx.close();
    }
    {   // 2b. a room file that never arrives: the placeholder stays, and the retries are few and spaced out
      let n = 0;
      const [page, ctx] = await open(p => p.route("**/room.glb", r => { n++; return r.fulfill({ status: 404, body: "no" }); }));
      await sleep(6000);
      const s = await shown(page);
      check(s === "fallback" && n >= 2 && n <= 3, `a room that never loads keeps the placeholder, with ${n} requests in 6 s (backoff, not a loop)`);
      await ctx.close();
    }
    {   // 3. the server isn't answering yet (a new cloud room's container still starting)
      const t0 = Date.now();
      const [page, ctx] = await open(p => p.route("**/api/dnd*", r => (Date.now() - t0 < 3000 ? r.fulfill({ status: 503, body: "starting" }) : r.continue())));
      const [ok, s, ms] = await waitRoom(page, 15000);
      check(ok, `when the server answers only after ~3 s, the room still appears (${s}, ${ms} ms)`);
      await ctx.close();
    }
  } catch (e) {
    check(false, "crashed: " + e.message);
  } finally {
    if (browser) await browser.close();
    srv.kill("SIGTERM");
  }
  const bad = results.filter(r => !r[0]).length;
  console.log(`\n${results.length - bad}/${results.length} room-load checks passed`);
  process.exit(bad ? 1 : 0);
})();
