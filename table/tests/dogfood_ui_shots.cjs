// The UI observer for a dogfood game (harness/dogfood_game.py): loads each player's real /hand page (with that seat's key, as a remote device) and the public
// /stage, takes a screenshot of each, and reports what /hand says (title line, life, hand size, battlefield count), so the interface itself is dogfooded:
// does it show what the server holds?
//   PW=/path/to/@playwright/test node table/tests/dogfood_ui_shots.cjs RUN_DIR LABEL
const { chromium } = require(process.env.PW || "@playwright/test");
const fs = require("fs"), path = require("path");
const run = process.argv[2], label = process.argv[3] || "shot";
const meta = JSON.parse(fs.readFileSync(path.join(run, "meta.json"))), BASE = `http://127.0.0.1:${meta.port}`;
const FWD = "203.0.113.7";
(async () => {
  const b = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
  fs.mkdirSync(path.join(run, "shots"), { recursive: true });
  const out = {};
  for (const seat of Object.keys(meta.decks)) {
    const key = fs.readFileSync(path.join(run, seat, "key"), "utf8").trim();
    const ctx = await b.newContext({ viewport: { width: 390, height: 844 }, extraHTTPHeaders: { "X-Forwarded-For": FWD } });
    await ctx.addInitScript(([n, k]) => { try { localStorage.setItem("table.seat", JSON.stringify({ name: n, key: k })); } catch {} }, [seat, key]);
    const pg = await ctx.newPage(); const errs = []; pg.on("pageerror", e => errs.push(String(e).slice(0, 140)));
    await pg.goto(`${BASE}/hand?mute=1`, { waitUntil: "load" }); await pg.waitForTimeout(2500);
    out[seat] = await pg.evaluate(() => ({ who: document.getElementById("who")?.textContent, life: document.getElementById("life")?.textContent, handCards: document.querySelectorAll("#hand .card").length, fieldCards: document.querySelectorAll("#field .card").length, err: document.getElementById("err")?.textContent || "" }));
    out[seat].pageErrors = errs;
    await pg.screenshot({ path: path.join(run, "shots", `${label}-${seat}-hand.png`), fullPage: true });
    await ctx.close();
  }
  const sp = await b.newContext({ viewport: { width: 1100, height: 760 }, extraHTTPHeaders: { "X-Forwarded-For": FWD } });
  const st = await sp.newPage(); await st.goto(`${BASE}/stage`, { waitUntil: "load" }); await st.waitForTimeout(3000);
  await st.screenshot({ path: path.join(run, "shots", `${label}-stage.png`) });
  console.log(JSON.stringify(out, null, 1)); await b.close();
})().catch(e => { console.error("FAILED", e.message); process.exit(1); });
