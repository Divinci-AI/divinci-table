// The D&D headset page (/xr) outside a headset: it loads, renders the table as a ~0.8 m diorama with every token,
// draws the wrist panel, and reads spoken dice ("roll a d20 for stealth"). The immersive parts (hit-test placement,
// controllers, mic in a session, 72 fps) need the Quest itself — docs/DND-3D-GOAL.md D6's check.
//
//   PW=/path/to/node_modules/@playwright/test node table/tests/dnd_xr_e2e.cjs
const path = require("path");
const { spawn } = require("child_process");
const os = require("os");
const { chromium } = require(process.env.PW || "@playwright/test");

const ROOT = path.join(__dirname, "..", "..");
const PY = path.join(os.homedir(), ".venvs", "table", "bin", "python");
const PORT = 8000 + Math.floor(Math.random() * 900) + 70;
const BASE = `http://127.0.0.1:${PORT}`;
const results = [];
const check = (ok, what) => { results.push([!!ok, what]); console.log((ok ? "  ✅ " : "  ❌ ") + what); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const srv = spawn(PY, ["table/dnd_server.py", "--players", "Michael,Sam", "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, DIVINCI_FUSION_API_KEY: "", TABLE_RESEARCH_DIR: os.tmpdir() }, stdio: "ignore" });
  let browser;
  try {
    for (let i = 0; i < 60; i++) { try { await fetch(BASE + "/api/dnd"); break; } catch { await sleep(250); } }
    browser = await chromium.launch({ headless: true, args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
    const page = await (await browser.newContext({ viewport: { width: 1280, height: 800 } })).newPage();
    const errors = [];
    page.on("pageerror", e => errors.push(e.message));
    page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
    await page.goto(`${BASE}/xr?player=Michael`);
    const ok = await page.waitForFunction(() => window.DNDXR && DNDXR.frames.length > 30, null, { timeout: 15000 }).then(() => true, () => false);
    check(ok, "the headset page renders (preview, no headset)");
    const toks = await page.evaluate(() => Object.keys(DNDXR.dnd.debug()));
    check(toks.includes("michael") && toks.includes("sam"), `every token is on the table (${toks.join(", ")})`);
    const across = await page.evaluate(() => { const { w, h } = DNDXR.dnd.size(); return Math.max(w, h) * 1.524 * DNDXR.table.scale.x; });
    check(Math.abs(across - 0.8) < 0.01, `as a diorama the map is ${across.toFixed(2)} m across`);
    const cases = await page.evaluate(() => [
      rollFromSpeech("Roll a d20 for stealth."), rollFromSpeech("I'm rolling 2d6 plus 3 for damage"),
      rollFromSpeech("roll a D 8"), rollFromSpeech("I attack the goblin"), rollFromSpeech("roll for initiative")]);
    check(JSON.stringify(cases[0]) === JSON.stringify({ dice: "d20", why: "stealth" }), `"Roll a d20 for stealth." → ${JSON.stringify(cases[0])}`);
    check(JSON.stringify(cases[1]) === JSON.stringify({ dice: "2d6+3", why: "damage" }), `"rolling 2d6 plus 3 for damage" → ${JSON.stringify(cases[1])}`);
    check(cases[2] && cases[2].dice === "d8", `"roll a D 8" → ${JSON.stringify(cases[2])}`);
    check(cases[3] === null && cases[4] === null, "talk that names no die is an action, not a roll");
    const ms = await page.evaluate(() => { const f = DNDXR.frames.slice(-120); return f.reduce((a, b) => a + b, 0) / f.length; });
    console.log(`  preview frame time (headless, software GL): ${ms.toFixed(1)} ms`);
    check(!errors.length, "no page errors" + (errors.length ? ": " + errors.slice(0, 3).join(" | ") : ""));
  } finally {
    if (browser) await browser.close();
    srv.kill();
  }
  const ok = results.filter(r => r[0]).length;
  console.log(`\n${ok}/${results.length} headset-page checks passed`);
  process.exit(ok === results.length ? 0 : 1);
})();
