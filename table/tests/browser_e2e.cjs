// Browser end-to-end: the real scan pad and voice pages, with Chrome's fake camera and fake mic
// fed from files (make_fake_media.py). Both pages run at once against the live table server.
//
//   PW=/path/to/node_modules/@playwright/test node table/tests/browser_e2e.cjs
//
// Checks: cards shown to the camera fill slots (and a card not in the deck does not); no card name
// ever appears in either page; the voice page hears, classifies and answers the table talk.
const path = require("path");
const { chromium } = require(process.env.PW || "@playwright/test");

const BASE = "http://localhost:8800";
const FIX = path.join(__dirname, "fixtures");
const HIDDEN = ["Island", "Sol Ring", "Mulldrifter"];            // shown to the camera, must stay hidden
const results = [];
const check = (ok, what) => { results.push([!!ok, what]); console.log((ok ? "  ✅ " : "  ❌ ") + what); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function api(p, body) {
  const r = await fetch(BASE + p, body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const t = await r.text();
  try { return JSON.parse(t); } catch { throw new Error(`${p} returned non-JSON (HTTP ${r.status}): ${t.slice(0, 200)}`); }
}

(async () => {
  await api("/api/reset", {});
  const browser = await chromium.launch({
    headless: true,
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
           `--use-file-for-fake-video-capture=${path.join(FIX, "camera.y4m")}`,
           `--use-file-for-fake-audio-capture=${path.join(FIX, "mic.wav")}%noloop`,
           "--autoplay-policy=no-user-gesture-required"],
  });
  const ctx = await browser.newContext({ permissions: ["camera", "microphone"] });
  const scan = await ctx.newPage();
  const voice = await ctx.newPage();
  const showPg = await ctx.newPage();
  const errors = [];
  for (const [name, pg] of [["scan", scan], ["voice", voice], ["show", showPg]]) {
    pg.on("pageerror", e => errors.push(`${name}: ${e.message}`));
    pg.on("console", m => { if (m.type() === "error") errors.push(`${name} console: ${m.text()}`); });
  }

  await scan.goto(BASE + "/");
  await voice.goto(BASE + "/voice");
  await showPg.goto(BASE + "/show");
  const t0 = Date.now();
  await voice.click("#mic");          // start the mic first: the audio file plays once (%noloop)
  await scan.click("#cam");
  await showPg.click("#cam");

  // Watch both pages. Record every status the scan pad shows and any hidden card name on either page.
  const statuses = new Set();
  let leak = null;
  while (Date.now() - t0 < 60000) {
    statuses.add(await scan.textContent("#status"));
    for (const [n, pg] of [["scan", scan], ["voice", voice]]) {
      const text = await pg.textContent("body");
      for (const c of HIDDEN) {
        // "Island" can't appear in either page's own copy; if it does, a hand card leaked.
        if (text.includes(c) && !leak) leak = `${n} page shows "${c}"`;
      }
    }
    const feed = await voice.$$eval("#feed li", els => els.length);
    const st = await api("/api/state");
    if (feed >= 4 && st.hand >= 3 && Date.now() - t0 > 22000) break;
    await sleep(500);
  }
  const elapsed = ((Date.now() - t0) / 1000).toFixed(0);

  console.log(`\nscan pad (${elapsed} s of fake camera)`);
  const st = await api("/api/state");
  // The fake video loops, so by now it may have started a second pass: count the FIRST pass only.
  check(st.hand >= 3, `cards shown to the camera filled ${st.hand} slot(s) (want at least 3)`);
  const tokenFile = path.join(__dirname, "..", ".brain-token");
  const token = require("fs").readFileSync(tokenFile, "utf8").trim();
  const hand = await (await fetch(BASE + "/api/hand", { headers: { "X-Brain-Token": token } })).json();
  const cards = hand.hand.map(h => h.card);
  check(JSON.stringify(cards.slice(0, 3)) === JSON.stringify(["Island", "Sol Ring", "Mulldrifter"]),
        `first three slots hold Island, Sol Ring, Mulldrifter (got ${JSON.stringify(cards.slice(0, 3))})`);
  check(!cards.includes("Lightning Bolt"), "Lightning Bolt (not in the deck) never entered the hand");
  check(statuses.has("reading…") && statuses.has("remove card"),
        `the pad went through reading → added → remove card (saw: ${[...statuses].join(" | ")})`);
  check(!leak, `no hidden card name appeared on either page${leak ? ": " + leak : ""}`);

  console.log("\nvoice page");
  const items = await voice.$$eval("#feed li", els => els.map(e => e.innerText));
  check(items.length >= 4, `heard ${items.length} utterance(s) (want 4)`);
  const all = items.join("\n");
  check(/Talrand: /.test(all), "Talrand answered the question addressed to it");
  const cfg = await api("/api/voice-config");
  if (cfg.ai_players.some(p => p.name === "Krenko")) check(/Krenko: \S/.test(all), "Krenko answered the deal (in character)");
  else {
    const krenkoLine = items.find(t => /Krenko, /.test(t)) || "";
    check(!/Krenko: /.test(all) && !/Talrand: /.test(krenkoLine),
          "a line addressed to Krenko (not an AI here) gets no AI answer at all");
  }
  check(/chatter/.test(all), "the pizza question was classed as chatter");
  check(/board: .*Sheoldred/.test(all), "Sam's Sheoldred went onto the board");
  for (const it of items.slice().reverse()) console.log("    · " + it.replace(/\s+/g, " ").slice(0, 150));

  console.log("\nshow page (same camera, public)");
  const shown = await showPg.$$eval("#history li", els => els.map(e => e.innerText));
  const shownCards = shown.map(t => t.split(" — ")[0]).reverse();
  check(["Island", "Sol Ring", "Mulldrifter", "Lightning Bolt"].every(c => shownCards.includes(c)),
        `all four cards held up were recognised (${shownCards.join(", ")})`);
  check(shown.every(t => /— Talrand: \S/.test(t)), "Talrand reacted to every card shown");
  for (const t of shown.slice(0, 4).reverse()) console.log("    · " + t.slice(0, 150));

  console.log("\npage health");
  check(errors.length === 0, `no page errors${errors.length ? ": " + errors.slice(0, 3).join(" / ") : ""}`);

  await browser.close();
  const bad = results.filter(r => !r[0]);
  console.log(`\n${results.length - bad.length}/${results.length} browser checks passed`);
  process.exit(bad.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
