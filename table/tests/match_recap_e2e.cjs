// A match's recap card in a real browser (2026-11-14): on a seat's hand page and on /watch (the stream's view), at a phone's width and a laptop's.
// A real table on a free port (127.0.0.1), --match bo3, temp folders, no card art; games are ended the way a real one ends (a seat's life
// reaches 0) and everything on the card is pressed through the page with real taps at real coordinates.
//   PW=~/Documents/server/workspace/clients/tests/node_modules/@playwright/test node table/tests/match_recap_e2e.cjs
//   MATCH_SHOTS=<dir>   also write screenshots there
// Proved: the card shows the winner, final life totals, turns, the match score and a line from the game summary; it blocks nothing (no
// backdrop; the page under it takes taps; Keep on the next game's opening hand is reachable while the card is still up); ✕, Escape and a
// tap outside close it, except that a tap outside does not throw away the loser's play/draw question; the loser's two buttons, Start now and
// Wait work; it closes by itself; when the match is over it says so and offers nothing to start. On /watch: no buttons, nothing private,
// closes by itself.
const { chromium } = require(process.env.PW || "@playwright/test");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path"), net = require("net");
const REPO = path.resolve(__dirname, "../..");
const PY = process.env.PY || (fs.existsSync(path.join(os.homedir(), ".venvs/table/bin/python")) ? path.join(os.homedir(), ".venvs/table/bin/python") : "python3");
const CARDS = process.env.TABLE_CARDS_JSON || [path.join(REPO, "table/.cache/cards-trk.json"), path.join(os.homedir(), ".divinci/star-trek-mtg/cards.json")].find(f => fs.existsSync(f));
if (!CARDS) { console.error("SKIP: needs the Star Trek set's card data (TABLE_CARDS_JSON=<file>)"); process.exit(77); }
const SHOTS = process.env.MATCH_SHOTS || "";
let failed = [];
const check = (ok, n, d = "") => { console.log(`${ok ? "  ✓" : "  ✗"} ${n}${ok ? "" : " — " + String(typeof d === "string" ? d : JSON.stringify(d)).slice(0, 500)}`); if (!ok) failed.push(n); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const freePort = () => new Promise(res => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); }); });
const J = async r => { const t = await r.text(); try { return JSON.parse(t); } catch { return { error: t.slice(0, 200) }; } };

async function run(browser, vw, vh, label) {
  console.log(`\n══ ${label}`);
  const PORT = await freePort(), BASE = `http://127.0.0.1:${PORT}`;
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "matchrecap-"));
  const deckScript = `import json,sys
sys.path.insert(0,'table')
import make_deck_json_shim as s
C={c['name']:c for c in s.load(sys.argv[2])}
json.dump({'name':'t','commander':None,'startingLife':20,'mainBoard':[{**C[n],'count':k} for n,k in {'Mountain':17,'Starfleet Security':23}.items()]},open(sys.argv[1],'w'))`;
  execFileSync(PY, ["-c", deckScript, tmp + "/deck.json", CARDS], { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  const srv = spawn(PY, ["table/server.py", "--any-card", "--port", PORT, "--brain", "external", "--token-file", tmp + "/token", "--fair-seed", "off", "--seed", "3",
    "--match", "bo3", "--match-countdown", "20", "--ai", "Claude|", "--ai-deck", tmp + "/deck.json", "--ai", "Michael|", "--ai-deck", tmp + "/deck.json", "--pilot", "Michael", "--pilot", "Claude"],
    { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", TABLE_NO_VOICE: "1", TABLE_HIGHROLL: "first", TABLE_MULLIGAN: "on", TABLE_MULLIGAN_SECS: "0", TABLE_CHECKIN: "off", STRICT_SEATS: "1",
      HF_HUB_OFFLINE: "1", ROUTER: "code", REPLIES: "template", NO_GEMMA: "1", TABLE_RESEARCH_DIR: tmp + "/r", TABLE_SAVES_DIR: tmp + "/s", TABLE_CARDS_JSON: CARDS, TABLE_ART_DIR: tmp + "/no-art",
      TABLE_MATCH_RECAP_SECS: "9", TABLE_MATCH_WATCH_RECAP_SECS: "7", TYPESAFE_API_KEY: "" }, stdio: "ignore" });
  let token = "", K = {};
  const match = async () => J(await fetch(BASE + "/api/match"));
  const ph = async () => J(await fetch(BASE + "/api/phase"));
  const st = async n => J(await fetch(`${BASE}/api/brain/state?seat=${n}`, { headers: { "X-Seat-Key": K[n] } }));
  const seat = async (n, a, b = {}) => J(await fetch(`${BASE}/api/brain/${a}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K[n] }, body: JSON.stringify({ seat: n, ...b }) }));
  const until = async (fn, secs = 20) => { const end = Date.now() + secs * 1000; while (Date.now() < end) { try { if (await fn()) return true; } catch {} await sleep(150); } return false; };
  const kill = async loser => { const w = loser === "Michael" ? "Claude" : "Michael";
    await fetch(BASE + "/api/brain/life", { method: "POST", headers: { "Content-Type": "application/json", "X-Brain-Token": token }, body: JSON.stringify({ seat: w, player: loser, delta: -60, text: "The last blow." }) });
    await until(async () => (await ph()).game_over); };
  const shot = async (page, name) => { if (SHOTS) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, `${vw}-${name}.png`) }); } };
  try {
    for (let i = 0; i < 120; i++) { try { if ((await fetch(BASE + "/api/phase")).ok) break; } catch {} await sleep(500); }
    token = fs.readFileSync(tmp + "/token", "utf8").trim();
    for (const n of ["Claude", "Michael"]) K[n] = (await J(await fetch(BASE + "/api/seat/claim", { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "e2e-" + n }, body: JSON.stringify({ name: n }) }))).key;
    await fetch(BASE + "/api/phase/windows", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on: false, by: "host" }) });
    await fetch(BASE + "/api/phase/next", { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K.Claude }, body: JSON.stringify({ by: "Claude", key: K.Claude }) });
    await until(async () => ((await ph()).mulligan || {}).on);
    for (const n of ["Claude", "Michael"]) await seat(n, "keep");
    await until(async () => (await ph()).player === "Claude");
    await seat("Claude", "begin");

    const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, userAgent: "e2e-Michael", hasTouch: vw < 700 });
    const page = await ctx.newPage(), errors = [], posts = [];
    page.on("pageerror", e => errors.push(String(e)));
    page.on("request", r => { if (r.method() === "POST") posts.push(new URL(r.url()).pathname); });
    await page.goto(`${BASE}/hand?player=Michael#key=${encodeURIComponent(K.Michael)}`);
    const wctx = await browser.newContext({ viewport: { width: vw, height: vh } });
    const watch = await wctx.newPage(), werrors = [], wreq = [];
    watch.on("pageerror", e => werrors.push(String(e)));
    watch.on("request", r => wreq.push(r.method() + " " + new URL(r.url()).pathname));
    await watch.goto(`${BASE}/watch`);
    await sleep(2500);
    const card = page.locator(".mr-card"), pill = page.locator(".mr-pill");
    const vis = async loc => (await loc.count()) > 0 && await loc.first().isVisible();
    check(!(await vis(card)) && (await page.evaluate(() => !!window.__matchRecapLoaded)), "during game 1 there is no recap card (the script is loaded and quiet)");

    // ── game 1 ends: Michael loses, so the choice is Michael's ──
    await kill("Michael");
    check(await until(async () => vis(card), 8), "the recap card appears on the hand page when the game ends");
    await sleep(600);
    let text = await card.innerText();
    check(/Claude wins game 1/.test(text) && /Claude leads 1–0/.test(text), "it names the winner and the match score ('Claude leads 1–0')", text);
    check(/Claude\s+20 life/.test(text) && /Michael\s+-?\d+ life/.test(text) && /\d+ turns?/.test(text), "…the final life totals and the number of turns", text);
    check(/The last blow: Michael took 60/.test(text), "…and a line from the server's own game summary", text);
    check(/Game 2 starts in/.test(text) && /here, by itself/.test(text) && /Michael lost and chooses who plays first \(no roll\)/.test(text), "it says game 2 starts here by itself, and that the loser chooses (no roll)", text);
    const btns = await card.locator("button").allInnerTexts();
    check(["Play first", "Draw first", "Start now", "Wait"].every(b => btns.includes(b)) && btns.includes("✕"), "the loser has Play first / Draw first, and both players Start now / Wait, and ✕", btns);
    check(!(await vis(page.locator(".go-banner"))), "the old 'the host starts a new game' banner is not shown in a match");
    const box = await card.boundingBox();
    check(box.x >= 0 && box.y >= 0 && box.x + box.width <= vw + 0.5 && box.y + box.height <= vh + 0.5 && box.width * box.height < vw * vh * 0.7, `the card fits the ${vw} px screen and leaves the page visible`, box);
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "the page does not scroll sideways");
    const cover = await page.evaluate(() => { const c = document.querySelector(".mr-card").getBoundingClientRect(); const pts = [[8, innerHeight - 8], [innerWidth - 8, innerHeight - 8], [8, c.bottom + 30 < innerHeight ? c.bottom + 30 : innerHeight - 30]];
      return pts.map(([x, y]) => { const el = document.elementFromPoint(x, y); return !!(el && el.closest(".mr-card")); }); });
    check(cover.every(x => x === false), "there is no backdrop: points outside the card belong to the page under it", cover);
    await shot(page, "1-recap-choice");
    const secs0 = await card.locator("[data-mr-secs]").innerText(); await sleep(2200);
    check(secs0 !== await card.locator("[data-mr-secs]").innerText(), "the countdown counts", secs0);
    // a tap outside must not throw the loser's question away; Escape and ✕ close it; the pill brings it back
    await page.mouse.click(6, vh - 6); await sleep(300);
    check(await vis(card), "a tap outside does NOT close the card while it is asking this player to choose");
    await page.keyboard.press("Escape"); await sleep(300);
    check(!(await vis(card)) && await vis(pill) && /Play or draw\?/.test(await pill.innerText()), "Escape closes it, and a pill says the question is still open", await pill.innerText().catch(() => ""));
    await pill.click(); await sleep(300);
    check(await vis(card), "the pill brings the card back");
    await card.locator("button", { hasText: "Draw first" }).click();
    check(await until(async () => ((await match()).next || {}).choice === "draw", 5), "Draw first: the table has the loser's choice", await match());
    await sleep(1200); text = await card.innerText();
    check(/Claude plays first \(Michael chose to draw first\)/.test(text) && !(await card.locator("[data-mr-choice]").count()), "the card now says who plays first and why, and the two buttons are gone", text);
    await card.locator("button", { hasText: "Wait" }).click();
    check(await until(async () => (((await match()).next || {}).holds || []).includes("Michael"), 5), "Wait: the table holds the next game for this player");
    await sleep(1200); text = await card.innerText();
    check(/Game 2 is waiting for Michael/.test(text), "…and the card says who is waited for", text);
    await shot(page, "2-recap-waiting");

    // ── /watch: the stream's view of the same moment ──
    const wcard = watch.locator(".mr-card");
    check(await until(async () => vis(wcard), 8), "/watch shows the recap card too");
    const wtext = await wcard.innerText(), wbtns = await wcard.locator("button").allInnerTexts();
    check(/Claude wins game 1/.test(wtext) && /Claude leads 1–0/.test(wtext) && wbtns.length === 1 && wbtns[0] === "✕", "on /watch: the result and the score, and no button but ✕ (nothing to press on a stream)", [wtext, wbtns]);
    const hand = ((await st("Michael")).hand || []).map(h => h.name);
    check(hand.length > 0 && !/Play first|Draw first|Start now/.test(wtext) && !wreq.some(r => /\/api\/(brain|seat)\//.test(r)) && !(await watch.content()).includes(K.Michael),
      "/watch asked for nothing private (no seat or brain route) and holds no seat key", wreq.filter(r => /brain|seat/.test(r)));
    check(/match: .*Claude leads 1–0/.test(await watch.locator("#watchbar").innerText()), "the watch bar carries the match score", await watch.locator("#watchbar").innerText());
    await shot(watch, "3-watch-recap");
    check(await until(async () => !(await vis(wcard)), 12), "on /watch the card closes by itself (nobody can press ✕ on a stream)");
    check(!(await vis(watch.locator(".mr-pill"))), "…and leaves no pill behind on /watch");

    // ── Start now: game 2 begins in the background, with the card still up over the opening hand ──
    const t0 = Date.now();
    await card.locator("button", { hasText: "Start now" }).click();
    check(await until(async () => ((await match()).current || {}).n === 2, 6) && Date.now() - t0 < 6000, "Start now (by the player who was waiting): game 2 begins at once", await match());
    check(await until(async () => vis(page.locator("#mullpanel [data-mullkeep], #mullpanel button")), 10), "game 2's opening hand arrives on the same page, with the same key (no new link)");
    await sleep(1500);
    check(await vis(card) && await card.evaluate(el => el.classList.contains("mr-small")), "the recap is still up over the new game's opening hand, shrunk to two lines");
    text = await card.innerText();
    check(/Claude wins game 1/.test(text) && /Claude leads 1–0/.test(text), "…still saying who won game 1 and the score", text);
    await shot(page, "4-recap-over-opening-hand");
    const keep = page.locator("#mullpanel button", { hasText: /^Keep/ }).first();
    await keep.scrollIntoViewIfNeeded();
    const kb = await keep.boundingBox();
    const top = await page.evaluate(([x, y]) => { const el = document.elementFromPoint(x, y); return el ? (el.closest(".mr-card") ? "the recap card" : el.tagName + ":" + (el.textContent || "").slice(0, 20)) : "nothing"; }, [kb.x + kb.width / 2, kb.y + kb.height / 2]);
    check(/^BUTTON:Keep/.test(top), "the Keep button is not under the card", top);
    await page.mouse.click(kb.x + kb.width / 2, kb.y + kb.height / 2);
    check(await until(async () => ((await st("Michael")).mulligan || {}).kept === true, 6), "Keep is pressed with the card still open: the game goes on in the background", (await st("Michael")).mulligan);
    check(!(await vis(card)), "…and that tap outside the card also closed it");
    await seat("Claude", "keep");
    await until(async () => (await ph()).player === "Claude");
    check((await ph()).player === "Claude", "game 2's first turn is Claude's: the loser chose to draw");
    check(await vis(pill), "a closed recap leaves a pill with the score");
    await pill.click(); await sleep(300);
    check(await vis(card), "(the pill reopens it)");
    check(await until(async () => !(await vis(card)) && !(await vis(pill)), 14), "left alone during game 2, the card and its pill go away by themselves");
    await page.reload(); await sleep(2500);
    check(!(await vis(card)), "a reload during game 2 does not bring game 1's recap back");

    // ── the match ends ──
    await seat("Claude", "begin");
    await kill("Michael");
    check(await until(async () => vis(card) && /wins the match/.test(await card.innerText()), 8), "when the match is decided the card says so");
    await sleep(500); text = await card.innerText();
    const endBtns = await card.locator("button").allInnerTexts();
    check(/Claude wins game 2/.test(text) && /Claude wins the match 2–0/.test(text) && /no further game starts/.test(text) && /Game 1: Claude/.test(text) && /Game 2: Claude/.test(text),
      "'Claude wins the match 2–0', each game's winner, and 'no further game starts'", text);
    check(endBtns.length === 1 && endBtns[0] === "✕", "nothing to start: the only button is ✕", endBtns);
    await shot(page, "5-match-over");
    await sleep(4000);
    check((await match()).over === true && (await ph()).game_over === true && (await match()).games_played === 2, "and no further game starts");
    await page.mouse.click(6, vh - 6); await sleep(300);
    check(!(await vis(card)) && await vis(pill) && /Claude wins the match 2–0/.test(await pill.innerText()), "a tap outside closes the final card; the pill keeps the result");
    await pill.click(); await sleep(200);
    await card.locator(".mr-x").click(); await sleep(200);
    check(!(await vis(card)), "✕ closes it");
    await sleep(12000);
    check(await vis(pill), "the final result's pill stays on a seat's page (it does not time out)");
    check(errors.length === 0 && werrors.length === 0, "no script error on either page", [...errors, ...werrors]);
    check(posts.filter(p => p === "/api/match/next").length === 3, "the page made exactly the three match requests that were pressed (draw, wait, now)", posts.filter(p => /match/.test(p)));
    await ctx.close(); await wctx.close();
  } catch (e) { check(false, `the ${label} run finished`, e.stack || e); }
  finally { srv.kill(); try { fs.rmSync(tmp, { recursive: true, force: true }); } catch {} }
}

(async () => {
  const browser = await chromium.launch();
  try {
    await run(browser, 390, 844, "phone 390x844");
    await run(browser, 1280, 800, "laptop 1280x800");
  } finally { await browser.close(); }
  console.log(failed.length ? `\n${failed.length} FAILED` : "\nall passed");
  process.exit(failed.length ? 1 : 0);
})();
