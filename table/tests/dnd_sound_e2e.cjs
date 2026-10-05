// Sound instead of pictures (docs/THEATER-GOAL.md T5), in the browser: the synthesized ambience plays for the place,
// follows the location, and ducks under speech, measured on the actual output (an AnalyserNode, in dBFS): it must
// drop at least 9 dB while a voice speaks (the design is 12) and come back after. The 🎵 switch turns it off.
//
//   PW=/path/to/node_modules/@playwright/test node table/tests/dnd_sound_e2e.cjs
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");
const os = require("os");
const { chromium } = require(process.env.PW || "@playwright/test");

const ROOT = path.join(__dirname, "..", "..");
const PY = path.join(os.homedir(), ".venvs", "table", "bin", "python");
const PORT = 8000 + Math.floor(Math.random() * 900) + 95;
const BASE = `http://127.0.0.1:${PORT}`;
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "dnd-sound-"));
const SCRIPT = path.join(TMP, "dm.txt");
fs.writeFileSync(SCRIPT, "The tavern hums. A long story begins, told slowly by the fire, of roads and rain and lost caravans.\n" +
  'TABLE: {"location":"cave"}');
const results = [];
const check = (ok, what) => { results.push([!!ok, what]); console.log((ok ? "  ✅ " : "  ❌ ") + what); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (f, ms = 8000) => { const t = Date.now(); while (Date.now() - t < ms) { if (await f()) return true; await sleep(100); } return false; };
const FAKE_TTS = () => {                                  // speaks for 2 s per line, calling onstart/onend like the real one
  const voices = ["Daniel", "Samantha"].map(n => ({ name: n, lang: "en-GB" }));
  window.__said = [];
  Object.defineProperty(window, "speechSynthesis", { configurable: true, value: { getVoices: () => voices, cancel() {}, speaking: false,
    speak: u => { if (!u.text) return; window.__said.push(u.text); u.onstart && u.onstart(); setTimeout(() => u.onend && u.onend(), 2000); } } });
  window.SpeechSynthesisUtterance = function (text) { this.text = text; };
};

(async () => {
  const srv = spawn(PY, ["table/dnd_server.py", "--players", "Ana,Ben", "--mode", "theater", "--dm-backend", "script:" + SCRIPT, "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, DIVINCI_FUSION_API_KEY: "", TABLE_RESEARCH_DIR: TMP }, stdio: "ignore" });
  let browser;
  const errors = [];
  try {
    for (let i = 0; i < 80; i++) { try { await (await fetch(BASE + "/api/dnd")).json(); break; } catch { await sleep(250); } }
    browser = await chromium.launch({ headless: true, args: ["--autoplay-policy=no-user-gesture-required"] });
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
    await ctx.addInitScript(FAKE_TTS);
    const page = await ctx.newPage();
    page.on("pageerror", e => errors.push(e.message));
    await page.goto(`${BASE}/?player=Ana`);
    await page.waitForFunction(() => typeof S !== "undefined" && S && S.map && window.TableSeat && TableSeat.name() === "Ana" && !DNDVOICE.firstBatch);
    await page.mouse.click(30, 200);                       // the first tap starts audio, as on a phone
    check(await until(() => page.evaluate(() => !!(window.DNDSOUND && DNDSOUND.on))), "theater mode: the ambience starts after the first tap");
    const theme0 = await page.evaluate(() => S.map.theme);
    check(await page.evaluate(t => DNDSOUND.theme === t, theme0), `it plays this place's sound (${theme0})`);
    const level = async (ms = 1200) => page.evaluate(async ms => { const v = []; const t = Date.now(); while (Date.now() - t < ms) { v.push(DNDSOUND.rms()); await new Promise(r => setTimeout(r, 60)); } v.sort((a, b) => a - b); return v[Math.floor(v.length / 2)]; }, ms);
    await sleep(1200);
    const normal = await level();
    check(normal > -60, `it's actually audible: ${normal.toFixed(1)} dBFS`);

    const ben = (await (await fetch(BASE + "/api/seat/claim", { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "ben" },
      body: JSON.stringify({ name: "Ben" }) })).json()).key;
    await fetch(BASE + "/api/dnd/act", { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "ben" },
      body: JSON.stringify({ by: "Ben", key: ben, text: "Tell us a story, barkeep." }) });
    check(await until(() => page.evaluate(() => window.__said.length > 0)), "a voice speaks (Ben's line, then the DM)");
    await sleep(500);
    const ducked = await level(900);
    check(normal - ducked >= 9, `under speech it drops ${(normal - ducked).toFixed(1)} dB (${normal.toFixed(1)} → ${ducked.toFixed(1)} dBFS; at least 9)`);
    await until(async () => (await page.evaluate(() => window.__said.length)) >= 3, 8000);
    await sleep(4500);
    const after = await level();
    check(Math.abs(after - normal) < 4, `after the voices stop it comes back (${after.toFixed(1)} dBFS)`);
    check(await until(() => page.evaluate(() => S.map.theme === "cave" && DNDSOUND.theme === "cave"), 6000),
          `the DM moves the scene to the cave and the sound follows (${await page.evaluate(() => DNDSOUND.theme)})`);
    await page.click("#soundBtn");
    await sleep(1200);
    const off = await level(600);
    check(off < normal - 30, `the 🎵 switch turns it off (${off.toFixed(1)} dBFS)`);
    const cred = await (await fetch(BASE + "/api/dnd/credits")).text();
    check(/Ambient sound: synthesized in the browser/.test(cred), "the credits say where the sound comes from (no recordings to license)");
    check(!errors.length, "no page errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  } catch (e) {
    check(false, "crashed: " + e.message);
  } finally {
    if (browser) await browser.close();
    srv.kill();
    fs.rmSync(TMP, { recursive: true, force: true });
  }
  const bad = results.filter(r => !r[0]).length;
  console.log(`\n${results.length - bad}/${results.length} sound checks passed`);
  process.exit(bad ? 1 : 0);
})();
