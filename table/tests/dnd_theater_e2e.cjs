// Theater of the mind in the room (docs/THEATER-GOAL.md T3): a scene played by voice alone, never touching a board.
// The AI DM (script backend) sets the zones; "where am I?", "whose turn is it?", "how hurt am I?" are answered by code
// to the asker only (and the DM isn't called); dice by voice (a roll with advantage, a real die said aloud); one
// question spoken into a fake microphone and transcribed by Whisper on this Mac; the mode switch both ways.
//
//   PW=/path/to/node_modules/@playwright/test node table/tests/dnd_theater_e2e.cjs
const path = require("path");
const fs = require("fs");
const { spawn, execFileSync } = require("child_process");
const os = require("os");
const { chromium } = require(process.env.PW || "@playwright/test");

const ROOT = path.join(__dirname, "..", "..");
const PY = path.join(os.homedir(), ".venvs", "table", "bin", "python");
const PORT = 8000 + Math.floor(Math.random() * 900) + 90;
const BASE = `http://127.0.0.1:${PORT}`;
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "dnd-theater-"));
const SCRIPT = path.join(TMP, "dm.txt");
fs.writeFileSync(SCRIPT, 'The tavern hums with low talk and the smell of woodsmoke.\n' +
  'TABLE: {"scene":"The Sleeping Griffin","zones":[{"name":"the bar","desc":"a long oak counter","cover":"half"},' +
  '{"name":"the hearth"},{"name":"the door"}],"monsters":[{"name":"Bandit 1"}],"zone":{"Bandit 1":"the door"}}\n---\nThe bandit eyes you.');
const results = [];
const check = (ok, what) => { results.push([!!ok, what]); console.log((ok ? "  ✅ " : "  ❌ ") + what); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const until = async (f, ms = 8000) => { const t = Date.now(); while (Date.now() - t < ms) { if (await f()) return true; await sleep(100); } return false; };
const FAKE_TTS = () => {
  const voices = ["Daniel", "Samantha", "Fred", "Karen"].map(n => ({ name: n, lang: "en-GB" }));
  window.__said = [];
  Object.defineProperty(window, "speechSynthesis", { configurable: true, value: { getVoices: () => voices, cancel() {}, speaking: false,
    speak: u => { if (u.text) window.__said.push({ text: u.text, voice: u.voice && u.voice.name, t: Date.now() }); } } });
  window.SpeechSynthesisUtterance = function (text) { this.text = text; };
};

(async () => {
  execFileSync("say", ["-o", path.join(TMP, "q.aiff"), "Where am I?"]);
  execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-i", path.join(TMP, "q.aiff"), "-ar", "48000", "-ac", "1", path.join(TMP, "q.wav")]);
  const srv = spawn(PY, ["table/dnd_server.py", "--players", "Ana,Ben", "--mode", "theater", "--dm-backend", "script:" + SCRIPT,
                         "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, DIVINCI_FUSION_API_KEY: "", HF_HUB_OFFLINE: "1", TABLE_RESEARCH_DIR: TMP }, stdio: "ignore" });
  let browser, micBrowser;
  const errors = [];
  try {
    for (let i = 0; i < 80; i++) { try { await (await fetch(BASE + "/api/dnd")).json(); break; } catch { await sleep(250); } }
    browser = await chromium.launch({ headless: true });
    const phone = async (who, b = browser, extra = {}) => {
      const ctx = await b.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, ...extra });
      await ctx.addInitScript(FAKE_TTS);
      const page = await ctx.newPage();
      page.on("pageerror", e => errors.push(`${who}: ${e.message}`));
      await page.goto(`${BASE}/?player=${who}`);
      await page.waitForFunction(n => typeof S !== "undefined" && S && S.map && window.TableSeat && TableSeat.name() === n && !DNDVOICE.firstBatch, who);
      return page;
    };
    const ana = await phone("Ana"), ben = await phone("Ben");
    const said = p => p.evaluate(() => window.__said.map(x => x.text));
    const say = (p, text) => p.evaluate(t => post("/api/dnd/act", { text: t }), text);   // what the 🎙 sends after transcribing
    check(await ana.evaluate(() => document.body.classList.contains("theater") && getComputedStyle(document.getElementById("mapPanel")).display === "none"
                             && getComputedStyle(document.getElementById("cardPanel")).display !== "none"), "theater mode: no board on the page; the scene card panel instead");

    await say(ana, "I push open the tavern door and look around.");
    check(await until(async () => (await said(ben)).some(t => /woodsmoke/.test(t)), 10000), "the DM sets the scene, and it's spoken");
    check(await until(() => ana.evaluate(() => /the bar[\s\S]*Ana[\s\S]*the door[\s\S]*Bandit 1/.test(document.getElementById("card").textContent))),
          "the card panel shows the zones and who is where (Ana at the bar, Bandit 1 at the door)");
    const dmCalls0 = (await (await fetch(BASE + "/api/events?since=0")).json()).events.filter(e => e.type === "dm").length;

    const ask = async (q, re, label) => {
      const n = (await said(ana)).length, nb = (await said(ben)).length;
      const r = await say(ana, q);
      const ok = await until(async () => (await said(ana)).slice(n).some(t => re.test(t)), 5000);
      check(ok && re.test(r.answer || ""), `"${q}" → ${label}: "${(r.answer || "").slice(0, 90)}"`);
      await sleep(1700);
      check(!(await said(ben)).slice(nb).some(t => re.test(t)), `…spoken to Ana only, not on Ben's phone`);
    };
    await ask("Where am I?", /the bar.*oak counter.*half cover.*reach the hearth.*Bandit 1/, "from the scene card");
    await ask("whose turn is it", /not in a fight/, "nobody's, no fight");
    await ask("How hurt am I?", /You have \d+ of \d+ hit points/, "her own hit points");
    const dmCalls1 = (await (await fetch(BASE + "/api/events?since=0")).json()).events.filter(e => e.type === "dm").length;
    check(dmCalls1 === dmCalls0, "the questions didn't call the DM");

    const nA = (await said(ana)).length;
    await say(ben, "Roll stealth with advantage");
    const ev = async () => (await (await fetch(BASE + "/api/events?since=0")).json()).events;
    check(await until(async () => (await ev()).some(e => e.type === "roll" && e.by === "Ben" && e.roll.faces.length === 2 && /stealth/.test(e.text))),
          "\"Roll stealth with advantage\" rolls two d20s for Ben's stealth");
    check(await until(async () => (await said(ana)).slice(nA).some(t => /^Ben, stealth: \d+/.test(t))), "…and Ana's phone announces it");
    await say(ana, "I rolled fourteen for perception");
    check(await until(async () => (await ev()).some(e => e.type === "roll" && e.by === "Ana" && e.roll.physical && e.roll.total === 14 && /perception/.test(e.text))),
          "\"I rolled fourteen for perception\" records Ana's real die (14)");
    check(!(await ev()).some(e => e.type === "say" && /roll stealth|rolled fourteen/i.test(e.text)), "dice lines become rolls, not actions sent to the DM");

    // one question spoken into a (fake) microphone: Whisper on this Mac → the same answer, out loud
    micBrowser = await chromium.launch({ headless: true, args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${path.join(TMP, "q.wav")}%noloop`] });
    const mic = await phone("Ana", micBrowser, { permissions: ["microphone"] });
    const n0 = (await said(mic)).length;
    await mic.locator("#talk").scrollIntoViewIfNeeded();
    const tb = await mic.locator("#talk").boundingBox();
    await mic.mouse.move(tb.x + tb.width / 2, tb.y + tb.height / 2);
    await mic.mouse.down(); await sleep(2600); await mic.mouse.up();
    check(await until(async () => (await said(mic)).slice(n0).some(t => /^You're in the bar/.test(t)), 20000),
          "spoken \"Where am I?\" (fake mic → Whisper) is answered out loud");

    await ana.click("#modeBtn");
    check(await until(() => ana.evaluate(() => S.mode === "map" && getComputedStyle(document.getElementById("mapPanel")).display !== "none")),
          "the mode switch brings the battle map back");
    await ana.click("#modeBtn");
    check(await until(() => ben.evaluate(() => document.body.classList.contains("theater"))), "…and theater mode again, on every phone");
    check(!errors.length, "no page errors" + (errors.length ? ": " + errors.join(" | ") : ""));
  } catch (e) {
    check(false, "crashed: " + e.message);
  } finally {
    if (browser) await browser.close();
    if (micBrowser) await micBrowser.close();
    srv.kill();
    fs.rmSync(TMP, { recursive: true, force: true });
  }
  const bad = results.filter(r => !r[0]).length;
  console.log(`\n${results.length - bad}/${results.length} theater checks passed`);
  process.exit(bad ? 1 : 0);
})();
