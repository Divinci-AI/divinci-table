// Game retro 2026-11-15/16, wave 2, the page level: (1) the loser of the last game chooses play or draw on their own page (the launcher passed
// TABLE_FIRST=Michael:choose; no high roll, and TABLE_HIGHROLL=first is NOT set: the mulligan step runs as in a real game); (2) after a mulligan the
// Keep button is never a dead button: it says "Tap 1 card to put on the bottom", the cards pulse, pressing it early points at them and sends
// nothing; (4) counters show with their kind ("quest 5", "+2/+2") on the hand page's mat, the board page and the watch page. A real server on a
// free port (never a live game's), a real browser, every press through the page.
//   PW=~/Documents/server/workspace/clients/tests/node_modules/@playwright/test node table/tests/hand_1115b_e2e.cjs
const { chromium } = require(process.env.PW || "@playwright/test");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path"), net = require("net");
const REPO = path.resolve(__dirname, "../..");
const PY = process.env.PY || path.join(os.homedir(), ".venvs/table/bin/python");
const LIVE = [8800, 8801, 8820, 8830, 8883];
let failed = [];
const check = (ok, n, d = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${n}${ok ? "" : " — " + String(d).slice(0, 400)}`); if (!ok) failed.push(n); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const freePort = () => new Promise(res => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); }); });
(async () => {
  let PORT = await freePort(); while (LIVE.includes(PORT)) PORT = await freePort();
  const BASE = `http://127.0.0.1:${PORT}`, tmp = fs.mkdtempSync(path.join(os.tmpdir(), "hand1115b-"));
  const deckScript = `import json,os,sys
sys.path.insert(0,'table/tests'); sys.path.insert(0,'table')
import cards_path, make_deck_json_shim as s
C={c['name']:c for c in s.load(cards_path.cards_json())}
P={'Forest':20,'Plains':20,'Xenobotanist':4,'Tal Shiar Operative':6,'Horta':4,'Starfleet Security':14,'Support Mission':4}
json.dump({'name':'t','commander':None,'startingLife':20,'mainBoard':[{**C[n],'count':k} for n,k in P.items()]},open(sys.argv[1],'w'))`;
  execFileSync(PY, ["-c", deckScript, tmp + "/deck.json"], { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  const env = { ...process.env, PYTHONDONTWRITEBYTECODE: "1", TABLE_NO_VOICE: "1", TABLE_CHECKIN: "off", STRICT_SEATS: "1", HF_HUB_OFFLINE: "1", ROUTER: "code",
    REPLIES: "template", NO_GEMMA: "1", TABLE_RESEARCH_DIR: tmp + "/r", TABLE_SAVES_DIR: tmp + "/s", TYPESAFE_API_KEY: "",
    TABLE_FIRST: "Michael:choose", TABLE_FIRST_REASON: "Michael lost game 1", TABLE_MULLIGAN_SECS: "0" };
  delete env.TABLE_HIGHROLL; delete env.TABLE_MULLIGAN;
  const srv = spawn(PY, ["table/server.py", "--any-card", "--port", String(PORT), "--brain", "external", "--token-file", tmp + "/token", "--fair-seed", "off", "--seed", "3",
    "--ai", "Claude|", "--ai-deck", tmp + "/deck.json", "--ai", "Michael|", "--ai-deck", tmp + "/deck.json", "--pilot", "Claude", "--pilot", "Michael", "--pilot-auto-begin", "off"],
    { cwd: REPO, env, stdio: "ignore" });
  let browser, token = "", K = {};
  const J = async r => { const t = await r.text(); try { return JSON.parse(t); } catch { return { error: t.slice(0, 200) }; } };
  const host = async (a, b) => J(await fetch(`${BASE}/api/brain/${a}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Brain-Token": token }, body: JSON.stringify(b) }));
  const seat = async (n, a, b = {}) => { const r = await fetch(`${BASE}/api/brain/${a}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K[n] }, body: JSON.stringify({ seat: n, ...b }) }); return { code: r.status, ...(await J(r)) }; };
  const st = async n => J(await fetch(`${BASE}/api/brain/state?seat=${n}`, { headers: { "X-Seat-Key": K[n] } }));
  const ph = async () => J(await fetch(`${BASE}/api/phase`));
  const until = async (pred, secs = 30) => { for (let i = 0; i < secs * 4; i++) { const p = await ph(); if (pred(p)) return p; await sleep(250); } return ph(); };
  try {
    for (let i = 0; i < 120; i++) { try { if ((await fetch(BASE + "/api/phase")).ok) break; } catch {} await sleep(500); }
    token = fs.readFileSync(tmp + "/token", "utf8").trim();
    for (const n of ["Claude", "Michael"]) K[n] = (await J(await fetch(BASE + "/api/seat/claim", { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "e2e-" + n }, body: JSON.stringify({ name: n }) }))).key;
    await fetch(BASE + "/api/phase/windows", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on: false, by: "host" }) });

    browser = await chromium.launch();
    const ctx = await browser.newContext({ viewport: { width: 1400, height: 1000 }, userAgent: "e2e-Michael" });
    const page = await ctx.newPage(), errors = [];
    page.on("pageerror", e => errors.push(String(e)));
    await page.goto(`${BASE}/hand?player=Michael#key=${encodeURIComponent(K.Michael)}`);
    await sleep(1500);

    console.log("play or draw, on the loser's page (1)");
    await fetch(BASE + "/api/phase/next", { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K.Claude }, body: JSON.stringify({ by: "Claude", key: K.Claude }) });
    const play = page.locator("#playdrawpanel [data-playdraw=play]");
    await play.waitFor({ timeout: 10000 }).catch(() => {});
    check(await play.isVisible() && await page.locator("#playdrawpanel [data-playdraw=draw]").isVisible(), "Michael's page asks him: Play first, or Draw",
      await page.locator("#playdrawpanel").textContent().catch(() => ""));
    check(/No high roll this game \(Michael lost game 1\)/.test(await page.locator("#playdrawpanel").textContent()), "…and says why there was no high roll", await page.locator("#playdrawpanel").textContent());
    check(!(await page.locator("#startrow").isVisible()), "…with no Start button beside it");
    await play.click();
    const p0 = await until(p => p.mulligan && p.mulligan.on, 10);
    check(p0.mulligan && p0.mulligan.first === "Michael" && !p0.player, "pressing Play first: Michael is first, and the opening-hand step opens (no turn yet)", JSON.stringify(p0.mulligan || p0).slice(0, 200));

    console.log("keep after a mulligan (2)");
    await page.locator("#mullpanel [data-mullagain]").waitFor({ timeout: 10000 });
    check(!(await page.locator("#playdrawpanel [data-playdraw]").count()), "the play/draw question is gone");
    await page.click("#mullpanel [data-mullagain]"); await sleep(200); await page.click("#mullpanel [data-mullagain]");
    await page.waitForFunction(() => /bottom/.test((document.querySelector("#mullpanel [data-mullkeep]") || {}).textContent || ""), null, { timeout: 10000 }).catch(() => {});
    const keep = page.locator("#mullpanel [data-mullkeep]");
    check((await keep.textContent()).trim() === "Tap 1 card to put on the bottom" && await keep.isEnabled(),
      "after one mulligan the button reads 'Tap 1 card to put on the bottom', and is not a dead (disabled) button", await keep.textContent());
    const pulse = await page.locator("#mullpanel .mull-hand.picking .mull-c").count(), anim = await page.locator("#mullpanel .mull-c").first().evaluate(e => getComputedStyle(e).animationName);
    check(pulse === 7 && anim === "mullpulse", "…the seven cards are marked selectable (outlined, pulsing)", `${pulse} ${anim}`);
    await keep.click(); await sleep(500);
    check(await page.locator("#mullpanel .mull-nudge").isVisible() && /First tap the card below/.test(await page.locator("#mullpanel .mull-nudge").textContent()),
      "pressing it before choosing says what to do", await page.locator("#mullpanel").textContent());
    check(!(await st("Michael")).mulligan.kept, "…and sends nothing: not kept");
    const name0 = (await page.locator("#mullpanel .mull-c b").first().textContent()).trim();
    await page.locator("#mullpanel .mull-c").first().click(); await sleep(300);
    check((await keep.textContent()).trim() === `Keep six (bottom: ${name0})` && !(await page.locator("#mullpanel .mull-hand.picking").count()),
      "tapping a card: the button reads Keep six (bottom: that card), the pulse stops", await keep.textContent());
    await keep.click(); await sleep(800);
    const m1 = (await st("Michael")).mulligan;
    check(m1 && m1.kept && (await st("Michael")).hand.length === 6, "Keep: Michael keeps six", JSON.stringify(m1));
    await seat("Claude", "keep");
    const p1 = await until(p => p.player === "Michael", 15);
    check(p1.player === "Michael", "both kept: Michael's first turn", p1.player);
    await seat("Michael", "begin");
    await until(p => p.player === "Michael" && p.begun && p.step === "main 1");

    console.log("counters with their kind (4)");
    for (const n of ["Support Mission", "Tal Shiar Operative"]) { await host("search", { seat: "Michael", name: n }); await host("put", { seat: "Michael", name: n, quiet: true }); }
    const s1 = await st("Michael"), sm = s1.permanents.find(p => p.name === "Support Mission"), op = s1.permanents.find(p => p.name === "Tal Shiar Operative");
    await host("counter", { seat: "Michael", ref: "#" + sm.id, n: 5 }); await host("counter", { seat: "Michael", ref: "#" + op.id, n: 2 });
    await sleep(3500);
    const mat = id => page.locator(`.mc[data-mc="${id}"][data-mine="1"]`).first();
    check(/quest 5/.test(await mat(sm.id).textContent()) && !/\+5/.test(await mat(sm.id).textContent()), "the hand page's mat: Support Mission reads 'quest 5' (not +5)", await mat(sm.id).textContent());
    check(/\+2/.test(await mat(op.id).textContent()) && !/quest/.test(await mat(op.id).textContent()), "…and Tal Shiar Operative its +2 (+1/+1 counters), apart", await mat(op.id).textContent());
    const bp = await ctx.newPage(); bp.on("pageerror", e => errors.push("board: " + e));
    await bp.goto(`${BASE}/board`); await sleep(3500);
    const cardText = async (pg, name) => pg.locator(".card", { hasText: name }).first().textContent();
    check(/quest 5/.test(await cardText(bp, "Support Mission")) && !/\+5/.test(await cardText(bp, "Support Mission")), "the board page: Support Mission 'quest 5'", await cardText(bp, "Support Mission"));
    check(/\+2\/\+2/.test(await cardText(bp, "Tal Shiar Operative")), "…and Tal Shiar Operative '+2/+2'", await cardText(bp, "Tal Shiar Operative"));
    await seat("Michael", "end", { text: "over" });                   // the watch page is a turn behind: the turn's end publishes this board to it
    await until(p => p.player === "Claude", 15);
    const wp = await ctx.newPage(); wp.on("pageerror", e => errors.push("watch: " + e));
    await wp.goto(`${BASE}/watch`); await sleep(4000);
    check(/quest 5/.test(await cardText(wp, "Support Mission").catch(() => "")), "the watch page (a turn behind): Support Mission 'quest 5'", await cardText(wp, "Support Mission").catch(e => String(e)));
    check(!errors.length, "no page errors", errors.join(" | "));
  } catch (e) {
    check(false, "the run threw", e && e.stack || e);
  } finally {
    if (browser) await browser.close();
    srv.kill();
    console.log(failed.length ? `\nFAILED: ${failed.join("; ")}` : "\nall good");
    process.exit(failed.length ? 1 : 0);
  }
})();
