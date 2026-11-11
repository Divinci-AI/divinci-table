// /3d in a real browser (Chromium, software WebGL): two bots play on a throwaway table while the page renders. Checks: frames
// render with no page errors, the cards appear as physics bodies and come to rest, a line an AI says becomes a bubble + feed
// entry, and /3d?watch=1 reads only the spectator feed (never /api/board3d or a seat route). Screenshots: SHOTS=/dir
//   PW=~/Documents/server/workspace/clients/tests/node_modules/@playwright/test node table/tests/table3d_e2e.cjs
const { chromium } = require(process.env.PW || "@playwright/test");
const { spawn } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path");
const REPO = path.resolve(__dirname, "../..");
const ROOT = path.join(os.homedir(), ".divinci/star-trek-mtg");
const PORT = 8834, BASE = `http://127.0.0.1:${PORT}`;
const PY = process.env.PY || path.join(os.homedir(), ".venvs/table/bin/python");
const SHOTS = process.env.SHOTS || fs.mkdtempSync(path.join(os.tmpdir(), "t3d-shots-"));
let failed = [];
const check = (n, ok, d = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${n}${ok ? "" : " — " + String(d).slice(0, 300)}`); if (!ok) failed.push(n); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const post = async (p, b, key) => (await fetch(BASE + p, { method: "POST", headers: { "Content-Type": "application/json", ...(key ? { "X-Seat-Key": key } : {}) }, body: JSON.stringify(b) })).json();
(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "t3d-"));
  const deck = process.env.TABLE_TEST_DECKS || path.join(ROOT, "live/decks");
  const srv = spawn(PY, ["table/server.py", "--any-card", "--port", PORT, "--brain", "external", "--token-file", tmp + "/token", "--fair-seed", "off", "--seed", "3",
    "--ai", "Michael|", "--ai-deck", deck + "/sam.json", "--ai", "Claude|", "--ai-deck", deck + "/claude.json", "--pilot", "Michael", "--pilot", "Claude"],
    { cwd: REPO, env: { ...process.env, TABLE_NO_VOICE: "1", TABLE_HIGHROLL: "first", TABLE_CHECKIN: "off", STRICT_SEATS: "1", HF_HUB_OFFLINE: "1", ROUTER: "code",
      REPLIES: "template", NO_GEMMA: "1", TABLE_RESEARCH_DIR: tmp + "/r", TABLE_SAVES_DIR: tmp + "/s", TABLE_ART_DIR: path.join(ROOT, "images/normal"), TYPESAFE_API_KEY: "" }, stdio: "ignore" });
  const bots = [];
  let browser;
  try {
    for (let i = 0; i < 120; i++) { try { if ((await fetch(BASE + "/api/phase")).ok) break; } catch {} await sleep(500); }
    const keys = {};
    for (const n of ["Michael", "Claude"]) {
      keys[n] = (await post("/api/seat/claim", { name: n })).key;
      const kf = path.join(tmp, n + ".key"); fs.writeFileSync(kf, keys[n], { mode: 0o600 });
      bots.push(spawn("python3", ["-u", path.join(ROOT, "claude_autopilot.py")], { env: { ...process.env, TABLE_URL: BASE, CLAUDE_SEAT: n, CLAUDE_KEY_FILE: kf, AUTOPILOT_GRACE: "2" }, stdio: "ignore" }));
    }
    await post("/api/phase/windows", { on: false, by: "host" });
    await post("/api/phase/next", { by: "Michael", key: keys.Michael }, keys.Michael);
    browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const errors = []; page.on("pageerror", e => errors.push(String(e))); page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
    await page.goto(BASE + "/3d?shot=1");
    await sleep(4000);
    const f0 = await page.evaluate(() => window.__frames);
    await sleep(2000);
    const f1 = await page.evaluate(() => window.__frames);
    check("the 3D table renders frames", f1 > f0 + 5, `${f0} -> ${f1}`);
    await page.screenshot({ path: path.join(SHOTS, "3d-early.png") });
    await sleep(20000);                                              // a few turns of play
    const n = await page.evaluate(() => window.__board.count);
    check("permanents on the battlefield are physics bodies on the table", n >= 4, `count=${n}`);
    await page.screenshot({ path: path.join(SHOTS, "3d-midgame.png") });
    await post("/api/brain/say", { seat: "Claude", text: "Nice block. I didn't see that coming at all." }, keys.Claude);
    await sleep(1200);
    const feedTxt = await page.evaluate(() => document.getElementById("feed").textContent);
    check("an AI's table talk becomes a bubble and a feed line", /didn't see that coming/.test(feedTxt), feedTxt);
    await page.screenshot({ path: path.join(SHOTS, "3d-bubble.png") });
    for (const b of bots) b.kill();
    await sleep(6000);
    const bodies = await page.evaluate(() => window.__board.bodies());
    check("with play paused, every card comes to rest (no endless jitter)", bodies.length && bodies.every(b => b.asleep), JSON.stringify(bodies.filter(b => !b.asleep)).slice(0, 200));
    check("no card has fallen through the felt or flown off the table", bodies.every(b => b.p[1] > -0.001 && Math.hypot(b.p[0], b.p[2]) < 1.0), JSON.stringify(bodies.map(b => b.p.map(v => +v.toFixed(3)))).slice(0, 300));
    const hover = await page.evaluate(() => { const hit = window.__board.bodies()[0]; return hit ? hit.key : null; });
    check("no page errors while rendering and animating", errors.length === 0, errors.slice(0, 3).join(" | "));
    // S18: a muted seat's talk isn't shown (or spoken) on this page
    const m = await browser.newPage({ viewport: { width: 1000, height: 700 } });
    await m.goto(BASE + "/3d?mute=Claude"); await sleep(3000);
    await post("/api/brain/say", { seat: "Claude", text: "This line should stay quiet on a muted page." }, keys.Claude);
    await post("/api/brain/say", { seat: "Michael", text: "But this one shows." }, keys.Michael);
    await sleep(1500);
    const mt = await m.evaluate(() => document.getElementById("feed").textContent);
    check("a muted seat's talk isn't shown on /3d; other seats' still is", !/stay quiet/.test(mt) && /this one shows/.test(mt), mt);
    await m.close();
    // the spectator view must only read the delayed feed
    const w = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const urls = []; w.on("request", r => urls.push(new URL(r.url()).pathname));
    await w.goto(BASE + "/3d?watch=1&shot=1"); await sleep(6000);
    const live = urls.filter(u => /^\/api\/(board3d|brain|events|seat|life|phase)/.test(u));
    check("/3d?watch=1 reads only /api/spectate (no live routes)", urls.includes("/api/spectate") && live.length === 0, live.join(", "));
    await w.screenshot({ path: path.join(SHOTS, "3d-watch.png") });
    // the AR/VR page's desktop preview now uses the same physics board
    const xr = await browser.newPage({ viewport: { width: 1100, height: 800 } });
    const xrErr = []; xr.on("pageerror", e => xrErr.push(String(e)));
    await xr.goto(BASE + "/xr?view=top"); await sleep(7000);
    const xrCount = await xr.evaluate(() => document.querySelectorAll("canvas").length);
    check("/xr (AR/VR preview) runs on the physics board with no page errors", xrCount > 0 && xrErr.length === 0, xrErr.slice(0, 2).join(" | "));
    await xr.screenshot({ path: path.join(SHOTS, "xr-top.png") });
    console.log("    screenshots: " + SHOTS);
  } catch (e) { check("the test ran", false, e.stack || e); }
  finally { for (const b of bots) try { b.kill(); } catch {} if (browser) await browser.close(); srv.kill(); }
  console.log(failed.length ? `\n${failed.length} FAILED` : "\nall passed");
  process.exit(failed.length ? 1 : 0);
})();
