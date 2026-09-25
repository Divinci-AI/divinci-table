// /table in --brain external mode: when the brain acts through the API (tablectl), the page must
// show it and queue it for speech on its own, and the life table must update — no reload.
//   PW=/path/to/node_modules/@playwright/test node table/tests/brain_page_e2e.cjs
const fs = require("fs");
const path = require("path");
const { chromium } = require(process.env.PW || "@playwright/test");
const BASE = "http://localhost:8800";
const TOKEN = fs.readFileSync(path.join(__dirname, "..", ".brain-token"), "utf8").trim();
const results = [];
const check = (ok, what) => { results.push(!!ok); console.log((ok ? "  ✅ " : "  ❌ ") + what); };
const brain = (action, body) => fetch(`${BASE}/api/brain/${action}`, {
  method: "POST", headers: { "Content-Type": "application/json", "X-Brain-Token": TOKEN }, body: JSON.stringify(body) });

(async () => {
  await brain("new-game", { quiet: true });
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  // count what the page hands to speech synthesis
  await page.addInitScript(() => {
    window.__spoken = [];
    const orig = window.speechSynthesis && window.speechSynthesis.speak.bind(window.speechSynthesis);
    if (window.speechSynthesis) window.speechSynthesis.speak = u => { window.__spoken.push(u.text); try { orig(u); } catch (e) {} };
  });
  await page.goto(BASE + "/table");
  await page.waitForFunction(() => document.querySelector("#aiPanel") && !document.querySelector("#aiPanel").hidden);
  await page.waitForTimeout(1500);                       // let the event poll take its "latest" mark

  await brain("say", { text: "Good evening, table. Shall we begin?" });
  await brain("begin", {});
  const st = await (await fetch(`${BASE}/api/brain/state`, { headers: { "X-Brain-Token": TOKEN } })).json();
  const land = st.hand.find(h => h.land);
  if (land) await brain("land", { name: land.name });
  await fetch(`${BASE}/api/life`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ player: "Michael", delta: -2, by: "test" }) });
  await page.waitForTimeout(3000);

  const feed = await page.$$eval("#feed li", els => els.map(e => e.innerText).join("\n"));
  const spoken = await page.evaluate(() => window.__spoken);
  check(/Claude: Good evening, table/.test(feed), "a brain 'say' appears in the page's feed");
  check(spoken.includes("Good evening, table. Shall we begin?"), "…and is handed to speech synthesis");
  check(!land || new RegExp(`I play ${land.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`).test(feed), `the land announcement (${land && land.name}) appears`);
  check(/turn 1/.test(await page.textContent("#aiStats")), "the AI panel refreshed to turn 1");
  check(/Michael\s*38/.test(await page.textContent("#lifeRow")), "the life table shows Michael at 38");
  const hand = st.hand.map(h => h.name).filter(n => n !== (land && land.name));
  const body = await page.textContent("body");
  check(!hand.some(n => body.includes(n)), "no card still in the AI's hand appears anywhere on the page");
  check(errors.length === 0, "no page errors" + (errors.length ? ": " + errors.join(" / ") : ""));
  await browser.close();
  await brain("new-game", { quiet: true });
  console.log(`\n${results.filter(Boolean).length}/${results.length} brain-page checks passed`);
  process.exit(results.every(Boolean) ? 0 : 1);
})().catch(e => { console.error(e); process.exit(2); });
