// The opening ceremony in a real browser: START on /me throws a d20 per player onto the felt, tags show each number, and
// "<winner> goes first" appears; ?nodice=1 keeps a tab quiet. Screenshot: DICE_SHOT=/path.png
//   PW=/path/to/@playwright/test node table/tests/dice_ui_e2e.cjs
const { chromium } = require(process.env.PW || "@playwright/test");
const { spawn } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path");
const REPO = path.resolve(__dirname, "../..");
const PORT = 8833, BASE = `http://127.0.0.1:${PORT}`;
const PY = process.env.PY || path.join(os.homedir(), ".venvs/table/bin/python");
let failed = [];
const check = (n, ok, d = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${n}${ok ? "" : " — " + String(d).slice(0, 300)}`); if (!ok) failed.push(n); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "dice-"));
  const srv = spawn(PY, ["table/server.py", "--any-card", "--port", PORT, "--brain", "external", "--token-file", tmp + "/token",
    "--ai", "Claude|Kaust, Eyes of the Glade|", "--ai-deck", "decks/kaust.json", "--pilot", "Claude", "--human", "Michael|", "--human", "Sam|",
    "--order", "Claude,Michael,Sam", "--priority-window", "0.2", "--fair-seed", "off"],
    { cwd: REPO, env: { ...process.env, TABLE_HIGHROLL: "table", HF_HUB_OFFLINE: "1", ROUTER: "code", REPLIES: "template", TABLE_RESEARCH_DIR: tmp + "/research", TYPESAFE_API_KEY: "" }, stdio: "ignore" });
  let browser;
  try {
    for (let i = 0; i < 180; i++) { try { if ((await fetch(BASE + "/api/phase")).ok) break; } catch {} await sleep(500); }
    browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
    const ctx = await browser.newContext({ viewport: { width: 900, height: 700 } });
    const watcher = await ctx.newPage(); await watcher.goto(BASE + "/stage"); await sleep(1500);
    const quiet = await ctx.newPage(); await quiet.goto(BASE + "/me?player=Sam&nodice=1"); await sleep(1500);
    const errors = []; watcher.on("pageerror", e => errors.push(String(e)));
    const mk = (await (await fetch(BASE + "/api/seat/claim", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "Michael" }) })).json()).key;
    const r = await fetch(BASE + "/api/phase/next", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ by: "Michael", key: mk }) });
    check("START answers", r.ok, r.status);
    await watcher.waitForSelector("#d3-win", { timeout: 20000 }).catch(() => {});
    check("the dice overlay appears on the stage page", await watcher.$("#d3-title") !== null);
    await sleep(4500);
    const tags = await watcher.evaluate(() => [...document.querySelectorAll("#d3-tags > div")].map(d => d.textContent.replace(/\s+/g, " ").trim()));
    check("each player's die shows a number", tags.length >= 3 && tags.every(t => /\d/.test(t)), JSON.stringify(tags));
    if (process.env.DICE_DEBUG) console.log(JSON.stringify(await watcher.evaluate(() => [...document.querySelectorAll("#d3-tags > div")].map(d => ({ t: d.textContent.trim(), l: d.style.left, tp: d.style.top, o: d.style.opacity, cs: getComputedStyle(d).opacity })))));
    if (process.env.DICE_DEBUG) console.log(await watcher.evaluate(() => { const el = document.getElementById("d3-title").parentElement; return [...el.children].map(c => c.tagName + "#" + c.id + " z=" + getComputedStyle(c).zIndex + " pos=" + getComputedStyle(c).position).join(" | "); }));
    if (process.env.DICE_SHOT) await watcher.screenshot({ path: process.env.DICE_SHOT });
    await watcher.waitForFunction(() => /goes first/.test((document.getElementById("d3-win") || {}).textContent || ""), null, { timeout: 15000 }).catch(() => {});
    const win = await watcher.evaluate(() => (document.getElementById("d3-win") || {}).textContent || "");
    const ph = await (await fetch(BASE + "/api/phase")).json();
    check("it names the winner, and that is who has the turn", win.startsWith(ph.player) && /goes first/.test(win), JSON.stringify([win, ph.player]));
    check("?nodice=1 keeps a tab quiet", await quiet.$("#d3-title") === null);
    check("no page errors", errors.length === 0, errors.join(" | "));
  } catch (e) { check("the run completed", false, e && e.stack || e); }
  finally { if (browser) await browser.close(); srv.kill(); await sleep(500); }
  console.log(failed.length ? `\n${failed.length} FAILED: ${failed.join("; ")}` : "\nALL PASS");
  process.exit(failed.length ? 1 : 0);
})();
