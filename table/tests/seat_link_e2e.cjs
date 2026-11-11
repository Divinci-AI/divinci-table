// S2: a seat link carries its key after "#" (a browser never sends the fragment), so no server, tunnel edge or proxy log sees it.
// A second device opens /me?player=Michael#key=…: the seat is claimed, no request URL contains the key, and the address bar is
// cleaned. An old ?key= link still works (and is cleaned too).
//   PW=~/Documents/server/workspace/clients/tests/node_modules/@playwright/test node table/tests/seat_link_e2e.cjs
const { chromium } = require(process.env.PW || "@playwright/test");
const { spawn } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path");
const REPO = path.resolve(__dirname, "../..");
const DECKS = process.env.TABLE_TEST_DECKS || path.join(os.homedir(), ".divinci/star-trek-mtg/live/decks");
const PORT = 8835, BASE = `http://127.0.0.1:${PORT}`;
const PY = process.env.PY || path.join(os.homedir(), ".venvs/table/bin/python");
let failed = [];
const check = (n, ok, d = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${n}${ok ? "" : " — " + String(d).slice(0, 300)}`); if (!ok) failed.push(n); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "s2-"));
  const srv = spawn(PY, ["table/server.py", "--any-card", "--port", PORT, "--brain", "external", "--token-file", tmp + "/token", "--fair-seed", "off",
    "--ai", "Michael|", "--ai-deck", DECKS + "/sam.json", "--ai", "Claude|", "--ai-deck", DECKS + "/claude.json", "--pilot", "Michael", "--pilot", "Claude"],
    { cwd: REPO, env: { ...process.env, TABLE_NO_VOICE: "1", TABLE_CHECKIN: "off", STRICT_SEATS: "1", HF_HUB_OFFLINE: "1", ROUTER: "code", REPLIES: "template",
      NO_GEMMA: "1", TABLE_RESEARCH_DIR: tmp + "/r", TABLE_SAVES_DIR: tmp + "/s", TYPESAFE_API_KEY: "" }, stdio: "ignore" });
  let browser;
  try {
    for (let i = 0; i < 120; i++) { try { if ((await fetch(BASE + "/api/phase")).ok) break; } catch {} await sleep(500); }
    const XFF = { "X-Forwarded-For": "203.0.113.50" };     // devices reach the table through a tunnel, as a phone would
    const claim = async (n, ua) => (await (await fetch(BASE + "/api/seat/claim", { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": ua, ...XFF }, body: JSON.stringify({ name: n }) })).json()).key;
    const key = await claim("Michael", "first-device");
    browser = await chromium.launch();
    for (const [label, url] of [["#key", `/me?player=Michael#key=${encodeURIComponent(key)}`], ["?key (old link)", `/me?player=Michael&key=${encodeURIComponent(key)}`]]) {
      const ctx = await browser.newContext({ extraHTTPHeaders: XFF, userAgent: "second-device-" + label });
      const page = await ctx.newPage(), urls = [];
      page.on("request", r => urls.push(r.url()));
      await page.goto(BASE + url); await sleep(2500);
      const stored = await page.evaluate(() => { try { return JSON.parse(localStorage.getItem("table.seat") || "{}"); } catch { return {}; } });
      check(`${label}: the second device takes the seat`, stored.name === "Michael" && !!stored.key, JSON.stringify(stored).replace(key, "<key>"));
      check(`${label}: the address bar no longer shows the key`, !page.url().includes(key), page.url().replace(key, "<key>"));
      if (label === "#key") check("#key: no request URL the page made contains the key (the fragment never leaves the browser)",
        !urls.some(u => u.includes(key)), urls.filter(u => u.includes(key)).map(u => u.replace(key, "<key>")).join(" "));
      await ctx.close();
    }
    const html = await (await fetch(BASE + "/assets/seat.js")).text();
    check("new seat links are written with #key=, never &key=", html.includes("#key=${") && !html.includes("&key=${"));
  } catch (e) { check("the test ran", false, e.stack || e); }
  finally { if (browser) await browser.close(); srv.kill(); }
  console.log(failed.length ? `\n${failed.length} FAILED` : "\nall passed");
  process.exit(failed.length ? 1 : 0);
})();
