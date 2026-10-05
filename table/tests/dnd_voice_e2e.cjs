// The table speaks (docs/THEATER-GOAL.md T2), in the browser: a theater-mode table with the AI DM on the script
// backend (a written reply, streamed word by word) and two phones whose speechSynthesis is a recorder. Checks what
// each phone says, in which voice, in what order, and how soon: the DM's sentences in order, one steady voice per
// speaker, nothing said twice, never the TABLE line, others' actions in their own voice (not your own read back),
// rolls announced, and a page that opens later doesn't read the history out.
//
//   PW=/path/to/node_modules/@playwright/test node table/tests/dnd_voice_e2e.cjs
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");
const os = require("os");
const { chromium } = require(process.env.PW || "@playwright/test");

const ROOT = path.join(__dirname, "..", "..");
const PY = path.join(os.homedir(), ".venvs", "table", "bin", "python");
const PORT = 8000 + Math.floor(Math.random() * 900) + 70;
const BASE = `http://127.0.0.1:${PORT}`;
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "dnd-voice-"));
const SCRIPT = path.join(TMP, "dm.txt");
fs.writeFileSync(SCRIPT, 'The tavern falls silent. A hooded figure rises from the corner table!\nLeonardo: Stay close. I sense old magic here.\n' +
  'NPC Barkeep: No trouble in my house, friends.\nThe fire crackles\nTABLE: {"scene":"The Sleeping Griffin"}');
const results = [];
const check = (ok, what) => { results.push([!!ok, what]); console.log((ok ? "  ✅ " : "  ❌ ") + what); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (f, ms = 8000) => { const t = Date.now(); while (Date.now() - t < ms) { if (await f()) return true; await sleep(100); } return false; };

// A recorder in place of the device's speech engine: what was said, by which voice, when.
const FAKE_TTS = () => {
  const voices = ["Daniel", "Samantha", "Karen", "Moira", "Fred", "Tessa", "Rishi"].map((n, i) => ({ name: n, lang: i % 2 ? "en-US" : "en-GB", default: i === 0 }));
  window.__said = [];
  const fake = { getVoices: () => voices, speak: u => { if (u.text) window.__said.push({ text: u.text, voice: u.voice && u.voice.name, pitch: u.pitch, t: Date.now() }); },
                 cancel() {}, onvoiceschanged: null, speaking: false };
  Object.defineProperty(window, "speechSynthesis", { value: fake, configurable: true });   // a read-only accessor: plain assignment is ignored
  window.SpeechSynthesisUtterance = function (text) { this.text = text; this.voice = null; this.pitch = 1; this.rate = 1; };
};

(async () => {
  const srv = spawn(PY, ["table/dnd_server.py", "--players", "Ana,Ben,Cy", "--companions", "ai:Leonardo:chatty:wizard",
                         "--mode", "theater", "--dm-backend", "script:" + SCRIPT, "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, DIVINCI_FUSION_API_KEY: "", TABLE_RESEARCH_DIR: TMP }, stdio: "ignore" });
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { await (await fetch(BASE + "/api/dnd")).json(); break; } catch { await sleep(250); } }
    browser = await chromium.launch({ headless: true });
    const errors = [];
    const phone = async who => {
      const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
      await ctx.addInitScript(FAKE_TTS);
      const page = await ctx.newPage();
      page.on("pageerror", e => errors.push(`${who}: ${e.message}`));
      await page.goto(`${BASE}/?player=${who}`);
      await page.waitForFunction(n => typeof S !== "undefined" && S && S.map && window.TableSeat && TableSeat.name() === n && !DNDVOICE.firstBatch, who);
      return page;
    };
    const ana = await phone("Ana"), ben = await phone("Ben");
    check(await ana.evaluate(() => document.getElementById("speak").textContent.includes("on")), "theater mode: the voice is on by default");
    const said = p => p.evaluate(() => window.__said);
    const t0 = Date.now();
    await ana.evaluate(() => post("/api/dnd/act", { text: "I look around the tavern." }));
    const heard = await until(async () => (await said(ben)).some(x => /fire crackles/.test(x.text)), 15000);
    check(heard, "the DM's reply is spoken on Ben's phone");
    const b = await said(ben), a = await said(ana);
    const dmLines = b.filter(x => !/look around/.test(x.text));
    const order = ["The tavern falls silent.", "A hooded figure rises", "Stay close.", "I sense old magic", "No trouble in my house", "The fire crackles"];
    check(order.every((s, i) => dmLines.findIndex(x => x.text.startsWith(s)) === dmLines.findIndex(x => x.text.startsWith(order[0])) + i),
          "the DM's sentences, in order, one by one: " + dmLines.map(x => x.text.slice(0, 18)).join(" | "));
    const v = s => (dmLines.find(x => x.text.startsWith(s)) || {}).voice;
    check(v("The tavern") === v("A hooded") && v("The tavern") === v("The fire"), `the narrator keeps one voice (${v("The tavern")})`);
    check(v("Stay close") === v("I sense old") && v("Stay close") !== v("The tavern"), `Leonardo has his own steady voice (${v("Stay close")})`);
    check(v("No trouble") && v("No trouble") !== v("The tavern"), `the barkeep (an NPC line) has another (${v("No trouble")})`);
    const dup = b.map(x => x.text).filter((t, i, all) => all.indexOf(t) !== i);
    check(!dup.length, "nothing is said twice (not the full reply, not Leonardo's line again)" + (dup.length ? ": " + dup.join(" | ") : ""));
    check(!b.concat(a).some(x => /TABLE|\{/.test(x.text)), "the TABLE line is never spoken");
    check(b.some(x => /look around the tavern/.test(x.text)) && !a.some(x => /look around the tavern/.test(x.text)),
          "Ana's action is spoken on Ben's phone, in her voice, and not read back to Ana");
    const first = dmLines.find(x => x.text.startsWith("The tavern"));
    console.log(`  ⏱  act → first spoken DM word on Ben's phone: ${first.t - t0} ms (script DM; includes the page's poll)`);
    await ben.evaluate(() => post("/api/dnd/roll", { dice: "d20", why: "perception" }));
    check(await until(async () => (await said(ana)).some(x => /^Ben, perception: \d+/.test(x.text))), "Ben's roll is announced on Ana's phone");
    // same room: Ana's device speaks for the table; Ben's goes quiet until he asks for his own copy
    await ana.evaluate(() => post("/api/dnd/speaker", { on: true }));
    await until(() => ben.evaluate(() => S.speaker === "Ana"));
    const n0 = (await said(ben)).length, a0 = (await said(ana)).length;
    await ben.evaluate(() => post("/api/dnd/roll", { dice: "d20", why: "stealth" }));
    await until(async () => (await said(ana)).length > a0, 6000);
    await sleep(1800);
    check((await said(ana)).some(x => /^Ben, stealth/.test(x.text)) && (await said(ben)).length === n0,
          "same room: the table speaker announces Ben's roll; Ben's own phone stays quiet");
    await ben.click("#speak");                                    // Ben wants his own copy in earbuds
    await ben.evaluate(() => post("/api/dnd/roll", { dice: "d20", why: "luck" }));
    check(await until(async () => (await said(ben)).some(x => /^Ben, luck/.test(x.text))), "…until Ben turns his own voice on");
    const cy = await phone("Cy");
    await sleep(2500);
    const c = await said(cy);
    check(!c.some(x => /tavern falls silent|perception/.test(x.text)), `a page opened later doesn't read the history out (${c.length} lines said)`);
    check(!errors.length, "no page errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  } catch (e) {
    check(false, "crashed: " + e.message);
  } finally {
    if (browser) await browser.close();
    srv.kill();
    fs.rmSync(TMP, { recursive: true, force: true });
  }
  const bad = results.filter(r => !r[0]).length;
  console.log(`\n${results.length - bad}/${results.length} voice checks passed`);
  process.exit(bad ? 1 : 0);
})();
