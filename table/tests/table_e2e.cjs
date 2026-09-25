// Browser end-to-end for the combined /table page: ONE page with the fake camera AND fake mic
// (make_fake_media.py) running at once against the live table server.
//
//   PW=/path/to/node_modules/@playwright/test node table/tests/table_e2e.cjs
const path = require("path");
const { chromium } = require(process.env.PW || "@playwright/test");

const BASE = "http://localhost:8800";
const FIX = path.join(__dirname, "fixtures");
const results = [];
const check = (ok, what) => { results.push([!!ok, what]); console.log((ok ? "  ✅ " : "  ❌ ") + what); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  await fetch(BASE + "/api/reset", { method: "POST", body: "{}" });
  const browser = await chromium.launch({
    headless: true,
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
           `--use-file-for-fake-video-capture=${path.join(FIX, "camera.y4m")}`,
           `--use-file-for-fake-audio-capture=${path.join(FIX, "mic.wav")}%noloop`,
           "--autoplay-policy=no-user-gesture-required"],
  });
  const ctx = await browser.newContext({ permissions: ["camera", "microphone"] });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(BASE + "/table");
  await page.waitForFunction(() => document.querySelectorAll("#to option").length > 0);
  const t0 = Date.now();
  await page.click("#start");

  const camStates = new Set(), micStates = new Set();
  while (Date.now() - t0 < 60000) {
    camStates.add(await page.textContent("#camStatus"));
    micStates.add(await page.textContent("#micStatus"));
    const shown = await page.$$eval("#feed li.shown", els => els.length);
    const heard = await page.$$eval("#feed li:not(.shown)", els => els.length);
    if (shown >= 4 && heard >= 4 && Date.now() - t0 > 22000) break;
    await sleep(500);
  }
  const items = await page.$$eval("#feed li", els => els.map(e => ({ shown: e.classList.contains("shown"), text: e.innerText })));
  const shown = items.filter(i => i.shown).map(i => i.text).reverse();
  const heard = items.filter(i => !i.shown).map(i => i.text).reverse();

  console.log(`\none page, camera + mic together (${((Date.now() - t0) / 1000).toFixed(0)} s)`);
  const shownCards = shown.map(t => (t.match(/shows (.+?)\n/) || [])[1]).filter(Boolean);
  check(["Island", "Sol Ring", "Mulldrifter", "Lightning Bolt"].every(c => shownCards.includes(c)),
        `cards held up were recognised (${shownCards.join(", ")})`);
  check(shown.every(t => /Talrand: \S/.test(t)), "Talrand reacted to every card shown");
  check(heard.length >= 4, `heard ${heard.length} line(s) of table talk (want 4)`);
  const all = heard.join("\n");
  check(/who are you attacking[\s\S]*Talrand: \S/.test(all), "Talrand answered the question addressed to it");
  const krenko = heard.find(t => /Krenko, /.test(t)) || "";
  check(krenko && !/Talrand: /.test(krenko), "a line addressed to Krenko (not an AI here) got no answer");
  check(/pizza[\s\S]*?chatter/.test(all), "the pizza question was chatter");
  const boardText = await page.textContent("#board");
  check(/Sheoldred/.test(boardText) && /Lightning Bolt/.test(boardText),
        "the public board holds both a spoken play (Sheoldred) and a shown card (Lightning Bolt)");
  check(camStates.has("camera ready") && [...camStates].some(s => s.startsWith("seen ")),
        `camera status moved through ready → seen (${[...camStates].slice(0, 6).join(" | ")})`);
  check([...micStates].some(s => s === "listening") && [...micStates].some(s => s === "thinking…"),
        `mic status moved through listening → thinking (${[...micStates].join(" | ")})`);
  check(errors.length === 0, `no page errors${errors.length ? ": " + errors.slice(0, 3).join(" / ") : ""}`);
  for (const t of [...shown.slice(0, 2), ...heard.slice(0, 2)]) console.log("    · " + t.replace(/\s+/g, " ").slice(0, 140));

  await browser.close();
  await fetch(BASE + "/api/reset", { method: "POST", body: "{}" });   // leave the table clean
  const bad = results.filter(r => !r[0]);
  console.log(`\n${results.length - bad.length}/${results.length} /table checks passed`);
  process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
