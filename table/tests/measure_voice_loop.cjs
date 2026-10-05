// T2's measurement (docs/THEATER-GOAL.md): the whole spoken loop with the real pieces on this Mac, from a player
// letting go of 🎙 to the first word their phone speaks. A fake microphone plays a line (macOS `say`), Whisper
// transcribes it, the local DM (Ollama) streams its reply, and the phone's speech engine is a recorder.
// Breaks each trial down: speech-to-text, the DM's first sentence, and the page picking it up.
//
//   PW=/path/to/@playwright/test node table/tests/measure_voice_loop.cjs [model=gemma4:e2b] [trials=5] [out.json]
const path = require("path");
const fs = require("fs");
const { spawn, execFileSync } = require("child_process");
const os = require("os");
const { chromium } = require(process.env.PW || "@playwright/test");

const ROOT = path.join(__dirname, "..", "..");
const PY = path.join(os.homedir(), ".venvs", "table", "bin", "python");
const MODEL = process.argv[2] || "gemma4:e2b", TRIALS = +(process.argv[3] || 5), OUT = process.argv[4];
const PORT = 8000 + Math.floor(Math.random() * 900) + 80, BASE = `http://127.0.0.1:${PORT}`;
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), "voice-loop-"));
const LINES = ["I walk up to the bar and ask the barkeep about the missing caravan.",
               "I draw my sword and step between the bandit and the old woman.",
               "I search the hearth for anything hidden in the ashes.",
               "I whisper to Leonardo that we should leave by the back door.",
               "I toss a silver coin to the bard and ask for a song about dragons."];
const sleep = ms => new Promise(r => setTimeout(r, ms));
const FAKE_TTS = () => {
  const voices = ["Daniel", "Samantha", "Fred"].map(n => ({ name: n, lang: "en-GB" }));
  window.__said = [];
  Object.defineProperty(window, "speechSynthesis", { configurable: true, value: { getVoices: () => voices, cancel() {}, speaking: false,
    speak: u => { if (u.text) window.__said.push({ text: u.text, t: Date.now() }); } } });
  window.SpeechSynthesisUtterance = function (text) { this.text = text; };
};

(async () => {
  const srv = spawn(PY, ["table/dnd_server.py", "--players", "Ana,Ben", "--companions", "ai:Leonardo:chatty:wizard", "--mode", "theater",
                         "--dm-backend", "ollama:" + MODEL, "--port", String(PORT)],
                    { cwd: ROOT, env: { ...process.env, DIVINCI_FUSION_API_KEY: "", HF_HUB_OFFLINE: "1", TABLE_RESEARCH_DIR: TMP }, stdio: "ignore" });
  const rows = [];
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { await fetch(BASE + "/api/dnd"); break; } catch { await sleep(250); } }
    // warm the model once so trial 1 isn't a cold load (a real table would have it loaded)
    await fetch("http://127.0.0.1:11434/api/chat", { method: "POST", body: JSON.stringify({ model: MODEL, stream: false, keep_alive: "30m",
      messages: [{ role: "user", content: "ready" }] }) });
    for (let i = 0; i < TRIALS; i++) {
      const wav = path.join(TMP, `line${i}.wav`), aiff = path.join(TMP, `line${i}.aiff`);
      execFileSync("say", ["-o", aiff, LINES[i % LINES.length]]);
      execFileSync("ffmpeg", ["-loglevel", "error", "-y", "-i", aiff, "-ar", "48000", "-ac", "1", wav]);
      const dur = parseFloat(execFileSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", wav]).toString());
      browser = await chromium.launch({ headless: true, args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
        `--use-file-for-fake-audio-capture=${wav}%noloop`] });
      const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, permissions: ["microphone"] });
      await ctx.addInitScript(FAKE_TTS);
      const page = await ctx.newPage();
      await page.goto(`${BASE}/?player=Ana`);
      await page.waitForFunction(() => typeof S !== "undefined" && S && S.map && window.TableSeat && TableSeat.name() === "Ana" && !DNDVOICE.firstBatch);
      const since = (await (await fetch(BASE + "/api/events?since=0")).json()).last;
      await page.locator("#talk").scrollIntoViewIfNeeded();
      const tb = await page.locator("#talk").boundingBox();
      await page.mouse.move(tb.x + tb.width / 2, tb.y + tb.height / 2);
      await page.mouse.down(); await sleep(dur * 1000 + 400);
      const released = Date.now(); await page.mouse.up();
      let first = null;
      for (let k = 0; k < 600 && !first; k++) { first = (await page.evaluate(() => window.__said.find(x => !/^(I |Ana)/.test(x.text)))); if (!first) await sleep(100); }
      const ev = (await (await fetch(BASE + "/api/events?since=" + since)).json()).events;
      const heard = ev.find(e => e.type === "say" && e.by === "Ana"), part = ev.find(e => e.type === "dm_part");
      const r = { line: LINES[i % LINES.length], heard: heard && heard.text,
                  stt_ms: heard ? Math.round(heard.ts * 1000 - released) : null,
                  dm_first_sentence_ms: heard && part ? Math.round((part.ts - heard.ts) * 1000) : null,
                  page_pickup_ms: part && first ? Math.round(first.t - part.ts * 1000) : null,
                  release_to_first_word_ms: first ? first.t - released : null, first_words: first && first.text.slice(0, 80) };
      rows.push(r);
      console.log(`trial ${i + 1}: ${r.release_to_first_word_ms} ms total = STT ${r.stt_ms} + DM first sentence ${r.dm_first_sentence_ms} + page ${r.page_pickup_ms}  · heard "${(r.heard || "").slice(0, 50)}" · "${r.first_words}"`);
      await browser.close(); browser = null;
      await sleep(4000);                                  // let the DM finish before the next trial
    }
  } finally {
    if (browser) await browser.close();
    srv.kill();
    fs.rmSync(TMP, { recursive: true, force: true });
  }
  const tot = rows.map(r => r.release_to_first_word_ms).filter(x => x !== null).sort((a, b) => a - b);
  const med = tot.length ? tot[Math.floor(tot.length / 2)] : null;
  console.log(`\n${MODEL}: release → first spoken word, median ${med} ms over ${tot.length}/${rows.length} trials (target ≤ 3000)`);
  if (OUT) fs.writeFileSync(OUT, JSON.stringify({ model: MODEL, when: new Date().toISOString(), median_ms: med, rows }, null, 1));
})();
