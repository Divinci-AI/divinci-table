// Game 4 retro, the page level (2026-11-08): Station, Equip, Next step, the Attack shortcut, the end-turn warning, the state chips under an enlarged card, aiming a
// spell. A real server on a free port, a real browser, every press through the page.
//   PW=~/Documents/server/workspace/clients/tests/node_modules/@playwright/test SHOTS=/some/dir node table/tests/hand_mechanics_e2e.cjs
const { chromium } = require(process.env.PW || "@playwright/test");
const { spawn, execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path"), net = require("net");
const REPO = path.resolve(__dirname, "../..");
const PY = process.env.PY || path.join(os.homedir(), ".venvs/table/bin/python");
const SHOTS = process.env.SHOTS || fs.mkdtempSync(path.join(os.tmpdir(), "hand-shots-"));
let failed = [];
const check = (ok, n, d = "") => { if (typeof n !== "string") throw new Error("check(condition, name, detail)"); console.log(`${ok ? "  ✓" : "  ✗"} ${n}${ok ? "" : " — " + String(d).slice(0, 400)}`); if (!ok) failed.push(n); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const freePort = () => new Promise(res => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => res(p)); }); });
(async () => {
  const PORT = await freePort(), BASE = `http://127.0.0.1:${PORT}`;
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "hand-"));
  const deckScript = `import json,os,sys
sys.path.insert(0,'table')
import make_deck_json_shim as s
C={c['name']:c for c in s.load(os.path.expanduser('~/.divinci/star-trek-mtg/cards.json'))}
P={'Syndicate Liquidators':1,'Open Communications':2,'Island':8,'Plains':14,'Forest':14,'Mountain':14,'Class-F Shuttlecraft':2,'Tricorder':2,'Combadge':1,'Horta':2,'Xindi Surveyors':3,'Common Goal':2,'Fugitive Listener':3,'Elim Garak, Simple Tailor':1,'Tal Shiar Operative':2,'Captain Michael Burnham':1}
json.dump({'name':'t','commander':None,'startingLife':20,'mainBoard':[{**C[n],'count':k} for n,k in P.items()]},open(sys.argv[1],'w'))`;
  execFileSync(PY, ["-c", deckScript, tmp + "/deck.json"], { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } });
  const srv = spawn(PY, ["table/server.py", "--any-card", "--port", PORT, "--brain", "external", "--token-file", tmp + "/token", "--fair-seed", "off", "--seed", "3",
    "--ai", "Michael|", "--ai-deck", tmp + "/deck.json", "--ai", "Claude|", "--ai-deck", tmp + "/deck.json", "--pilot", "Michael", "--pilot", "Claude"],
    { cwd: REPO, env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", TABLE_NO_VOICE: "1", TABLE_HIGHROLL: "first", TABLE_CHECKIN: "off", STRICT_SEATS: "1", HF_HUB_OFFLINE: "1", ROUTER: "code",
      REPLIES: "template", NO_GEMMA: "1", TABLE_RESEARCH_DIR: tmp + "/r", TABLE_SAVES_DIR: tmp + "/s", TABLE_ART_DIR: path.join(os.homedir(), ".divinci/star-trek-mtg/images/normal"), TYPESAFE_API_KEY: "" }, stdio: "ignore" });
  let browser;
  const J = async r => { const t = await r.text(); try { return JSON.parse(t); } catch { return { error: t.slice(0, 200) }; } };
  let token = "", K = {};
  const host = async (a, b) => J(await fetch(`${BASE}/api/brain/${a}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Brain-Token": token }, body: JSON.stringify(b) }));
  const seat = async (n, a, b = {}) => { for (let i = 0; i < 40; i++) { const r = await fetch(`${BASE}/api/brain/${a}`, { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K[n] }, body: JSON.stringify({ seat: n, ...b }) });
    const d = await J(r); if (r.status === 409 && /waiting on/.test(d.error || "") && !/defenders/.test(d.error || "")) { await sleep(250); continue; } return { code: r.status, ...d }; } };
  const st = async n => J(await fetch(`${BASE}/api/brain/state?seat=${n}`, { headers: { "X-Seat-Key": K[n] } }));
  const ph = async () => J(await fetch(`${BASE}/api/phase`));
  const settle = async () => { for (const n of Object.keys(K)) for (let i = 0; i < 4; i++) { const p = (await st(n)).pending_choices || []; if (!p.length) break;
    const o = (p[0].slot.options || [])[0] || {}; delete o.label; await seat(n, "choose", { id: p[0].id, target: o }); } };   // set-up answers what set-up raised
  const onto = async (n, name) => { await host("search", { seat: n, name }); await host("put", { seat: n, name, quiet: true }); await settle(); };
  const permOf = async (n, name) => (await st(n)).permanents.filter(p => p.name === name).pop();
  const untilMain = async who => { for (let i = 0; i < 60; i++) { const p = await ph(); if (p.player === who && p.begun && p.step === "main 1") return; await sleep(250); } };
  try {
    for (let i = 0; i < 120; i++) { try { if ((await fetch(BASE + "/api/phase")).ok) break; } catch {} await sleep(500); }
    token = fs.readFileSync(tmp + "/token", "utf8").trim();
    for (const n of ["Claude", "Michael"]) K[n] = (await J(await fetch(BASE + "/api/seat/claim", { method: "POST", headers: { "Content-Type": "application/json", "User-Agent": "e2e-" + n }, body: JSON.stringify({ name: n }) }))).key;
    await fetch(BASE + "/api/phase/windows", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on: false, by: "host" }) });
    await fetch(BASE + "/api/phase/next", { method: "POST", headers: { "Content-Type": "application/json", "X-Seat-Key": K.Claude }, body: JSON.stringify({ by: "Claude", key: K.Claude }) });
    await untilMain("Claude");
    await onto("Claude", "Fugitive Listener");
    await onto("Claude", "Elim Garak, Simple Tailor");
    await seat("Claude", "end", { text: "over" });
    await untilMain("Michael");
    for (const n of ["Plains", "Plains", "Forest", "Forest", "Forest"]) await onto("Michael", n);
    await onto("Michael", "Xindi Surveyors"); await onto("Michael", "Xindi Surveyors"); await onto("Michael", "Horta"); await onto("Michael", "Class-F Shuttlecraft"); await onto("Michael", "Tricorder");
    await seat("Michael", "end", { text: "over" }); await untilMain("Claude"); await seat("Claude", "end", { text: "over" }); await untilMain("Michael");   // past summoning sickness

    browser = await chromium.launch();
    const ctx = await browser.newContext({ viewport: { width: 1500, height: 1050 }, userAgent: "e2e-Michael" });
    const page = await ctx.newPage(), errors = [];
    page.on("pageerror", e => errors.push(String(e)));
    await page.goto(`${BASE}/hand?player=Michael#key=${encodeURIComponent(K.Michael)}`);
    await page.waitForSelector("#mat .canvas", { timeout: 15000 }); await sleep(1500);
    const shot = n => page.screenshot({ path: path.join(SHOTS, n + ".png") });
    const mat = id => page.locator(`.mc[data-mc="${id}"][data-mine="1"]`).first();
    const ship = await permOf("Michael", "Class-F Shuttlecraft"), trico = await permOf("Michael", "Tricorder"), horta = await permOf("Michael", "Horta");
    const xs = (await st("Michael")).permanents.filter(p => p.name === "Xindi Surveyors");

    console.log("the step bar");
    check(await page.locator("#stepbar span.now").textContent() === "Main 1", "the step bar lights the step you're on");
    check((await page.locator("#next").textContent()).includes("Combat"), "Next names where it goes", await page.locator("#next").textContent());
    check(await page.locator("#attackgo").isVisible(), "an Attack shortcut shows in main 1 with creatures ready");
    await shot("1-main1");

    console.log("the end-turn warning");
    await page.click("#end"); await sleep(300);
    const warn = await page.locator("#err").textContent();
    check(/attack with \d+ ready/.test(warn), "End turn in main 1 warns about ready attackers", warn);
    check((await ph()).player === "Michael", "…and doesn't end the turn");
    await page.locator("#err").evaluate(e => { e.textContent = ""; });

    console.log("Station from the page");
    await mat(ship.id).click(); await sleep(250);
    const pop = page.locator("#matpop");
    check(await pop.locator(`[data-station="${ship.id}"][data-crew="${xs[0].id}"]`).isVisible(), "the popup offers the crew");
    await shot("2-station-popup");
    await pop.locator(`[data-station="${ship.id}"][data-crew="${horta.id}"]`).click(); await sleep(800);
    let s1 = await permOf("Michael", "Class-F Shuttlecraft");
    check(s1.charge === 7 && s1.pt === "2/4" && s1.keywords.includes("Flying"), "pressing it stations for real: 7 charge, a 2/4 flier", JSON.stringify(s1));
    check((await permOf("Michael", "Horta")).tapped, "…the crew tapped");
    const log1 = await (await fetch(BASE + "/api/events?since=0")).json();
    check(!(log1.events || []).some(e => e.type === "say" && /put \d+ charge counters? on/.test(e.text || "")), "…and the log carries the table's own line, not a line anyone counts from");
    await sleep(2800);
    check((await mat(ship.id).textContent()).includes("⚡7"), "the mat badge reads the engine's 7", await mat(ship.id).textContent());
    await shot("3-stationed");

    console.log("Equip from the page");
    await mat(trico.id).click(); await sleep(250);
    check(await pop.locator(`[data-equip="${trico.id}"][data-eqon="${xs[0].id}"]`).isVisible(), "Tricorder's popup offers each creature, with its {1}");
    await shot("4-equip-popup");
    await pop.locator(`[data-equip="${trico.id}"][data-eqon="${xs[0].id}"]`).click(); await sleep(800);
    let x1 = (await st("Michael")).permanents.find(p => p.id === xs[0].id);
    check(x1.pt === "4/3" && x1.attached_names.includes("Tricorder"), "Equip for real: +1/+1, attached", JSON.stringify(x1));
    await sleep(2800);
    check((await mat(xs[0].id).textContent()).includes("⚔1"), "the creature's mat card shows what it carries", await mat(xs[0].id).textContent());
    check(await mat(trico.id).isVisible(), "the attached Equipment stays on the mat, clickable");
    await shot("5-equipped");

    console.log("A land and the counter badge (G4-8)");
    await host("search", { seat: "Michael", name: "Plains" }); await sleep(600);
    await seat("Michael", "land", { name: "Plains" }); await sleep(3000);
    const x2 = (await st("Michael")).permanents.find(p => p.id === xs[0].id);
    check(x2.counters === 1, "Landfall put a +1/+1 counter on the Xindi", JSON.stringify(x2));
    check((await mat(xs[0].id).locator(".bd.ct").textContent()) === "+1", "own mat cards now show the counter badge", await mat(xs[0].id).textContent());

    console.log("Chips on every way of enlarging");
    const chips = async () => { await page.waitForSelector("#artinfo", { timeout: 4000 }); return page.locator("#artinfo").textContent(); };
    await mat(ship.id).click(); await sleep(250);
    await pop.locator("[data-zoom]").click(); await sleep(250);
    let c1 = await chips();
    check(c1.includes("⚡ 7 charge (creature)") && c1.toLowerCase().includes("flying"), "from Read it: charge and station keywords", c1);
    await shot("6-zoom-mat");
    await page.locator("#artbox").click(); await sleep(150);
    await mat(xs[0].id).click(); await sleep(250); await pop.locator("[data-zoom]").click(); await sleep(200);
    c1 = await chips();
    check(c1.includes("+1/+1 counter") && c1.includes("⚔ Tricorder"), "a creature's chips: its counter and its Equipment", c1);
    await page.locator("#artbox").click(); await sleep(150);
    await mat(trico.id).click(); await sleep(250); await pop.locator("[data-zoom]").click(); await sleep(200);
    c1 = await chips();
    check(c1.includes("attached to Xindi Surveyors"), "an Equipment's chips say what it's on", c1);
    await page.locator("#artbox").click(); await sleep(150);
    const opp = (await (await fetch(BASE + "/api/board3d")).json()).seats.find(x => x.name === "Claude").permanents.find(p => p.name === "Elim Garak, Simple Tailor");
    await page.locator(`.mc[data-mc="${opp.id}"]:not([data-mine="1"])`).first().click(); await sleep(250);
    c1 = await chips();
    check(c1.includes("menace"), "an opponent's card: its keywords, from the public board", c1);
    await page.locator("#artbox").click(); await sleep(150);
    await page.evaluate(() => { document.getElementById("fieldpanel").style.display = "block"; document.getElementById("field").scrollIntoView(); }); await sleep(150);
    await page.locator(`#field [data-zid="${ship.id}"] img.cardimg`).first().click({ force: true }); await sleep(300);
    c1 = await chips();
    check(c1.includes("⚡ 7 charge"), "the battlefield list's picture too (it showed bare art before)", c1);
    await shot("7-zoom-field");
    await page.locator("#artbox").click(); await sleep(150);

    console.log("Attack shortcut, Next step");
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.locator("#attackgo").click(); await sleep(300);
    check(/Attack with [1-9]/.test(await page.locator("#attackgo").textContent()), "first press ticks everything ready", await page.locator("#attackgo").textContent());
    await shot("8-attackers-ticked");
    const life0 = (await (await fetch(BASE + "/api/board3d")).json()).seats.find(x => x.name === "Claude").life;
    await page.locator("#attackgo").click(); await sleep(800);
    const p1 = await ph();
    check(p1.step === "declare attackers" || p1.step === "declare blockers", "second press attacks: the turn is at combat", p1.step);
    check(await page.locator("#stepbar span.now").textContent() !== "Main 1", "the bar moved with it");
    const res = (await st("Michael")).pending_surveil;
    check(res === 1, "Tricorder's trigger asks for its surveil", res);
    await sleep(500);
    check(await page.locator("[data-survdone]").isVisible(), "…with a panel on the page");
    await shot("9-surveil");
    await page.locator("[data-survdone]").click(); await sleep(500);
    check(!(await st("Michael")).pending_surveil, "Done resolves it");
    for (const e of (await st("Claude")).incoming || []) await seat("Claude", "block", { attacker: e.ref });
    await sleep(800);
    for (let i = 0; i < 6; i++) { const q = await ph(); if (q.step === "main 2") break; await page.click("#next"); await sleep(900); }
    const p2 = await ph();
    check(p2.step === "main 2" && p2.player === "Michael", "Next walks on to main 2, a step at a time", JSON.stringify([p2.step, p2.player]));
    const life1 = (await (await fetch(BASE + "/api/board3d")).json()).seats.find(x => x.name === "Claude").life;
    check(life1 < life0, "and the attack's damage was dealt on the way", [life0, life1]);
    await shot("10-main2");

    console.log("A trigger asks on the page; the turn waits");
    await host("search", { seat: "Michael", name: "Syndicate Liquidators" }); await host("put", { seat: "Michael", name: "Syndicate Liquidators", quiet: true });
    await sleep(600);
    await seat("Michael", "end", { text: "over" }); await untilMain("Claude"); await seat("Claude", "end", { text: "over" }); await untilMain("Michael");
    await host("search", { seat: "Michael", name: "Plains" }); await sleep(2600);
    await page.reload(); await page.waitForSelector("#fan .fc", { timeout: 15000 }); await sleep(1500);
    const hd = (await st("Michael")).hand, li = hd.findIndex(c => c.name === "Plains");
    await page.locator(`#fan .fc[data-fan="${li}"]`).click({ force: true }); await sleep(300);
    await page.locator("[data-fanland]").first().click(); await sleep(900);
    check(await page.locator("#choicepanel [data-choose]").isVisible(), "playing a land brings up the choice on the page");
    const hOpts = await page.locator("#choicepanel select option").allTextContents();
    check(hOpts.length >= 3 && hOpts.every(t => /^your /.test(t)), "it lists my legal creatures, not a default", hOpts.join("|"));
    await page.evaluate(() => window.scrollTo(0, 0)); await shot("12-choice");
    await page.click("#next"); await sleep(500);
    check(/choose first/.test(await page.locator("#err").textContent()) && (await ph()).step === "main 1", "Next is refused and says why", await page.locator("#err").textContent());
    const hortaNow = await permOf("Michael", "Horta"), xsNow = (await st("Michael")).permanents.find(p => p.id === xs[1].id);
    const hBefore = hortaNow ? hortaNow.counters : null, xBefore = xsNow.counters;
    await page.locator("#choicepanel select").selectOption(`Michael|#${xs[1].id}`);
    await page.locator("#choicepanel [data-choose]").click(); await sleep(900);
    const xsAfter = (await st("Michael")).permanents.find(p => p.id === xs[1].id);
    check(xsAfter.counters === xBefore + 2, "pressing Choose puts the counters where I said", JSON.stringify(xsAfter));
    check(!(await page.locator("#choicepanel [data-choose]").count()), "…the panel goes away");

    console.log("Beam me up, from the page");
    await host("search", { seat: "Michael", name: "Open Communications" }); await host("discard", { seat: "Michael", name: "Open Communications", quiet: true });
    for (const n of ["Island", "Island", "Island"]) await onto("Michael", n);
    await sleep(2800);
    await page.reload(); await page.waitForSelector("#beampanel [data-beam]", { timeout: 15000 }); await sleep(800);
    await page.evaluate(() => document.getElementById("beampanel").scrollIntoView());
    await shot("13-beam");
    const crew0 = (await st("Michael")).permanents.filter(p => /Creature/.test(p.type || "")).length;
    await page.locator("#beampanel [data-beam]").first().click(); await sleep(900);
    const sb = await st("Michael");
    check(sb.permanents.filter(p => /Creature/.test(p.type || "")).length === crew0 - 1 && !sb.graveyard.includes("Open Communications"), "pressing Beam it up: the creature returns, the spell is exiled", await page.locator("#err").textContent());

    console.log("Aiming a spell (Common Goal), from the page");
    await seat("Michael", "end", { text: "over" }); await untilMain("Claude"); await seat("Claude", "end", { text: "over" }); await untilMain("Michael");
    await host("search", { seat: "Michael", name: "Common Goal" }); await sleep(2500);
    await page.reload(); await page.waitForSelector("#fan .fc", { timeout: 15000 }); await sleep(1500);
    const hand = (await st("Michael")).hand, i = hand.findIndex(c => c.name === "Common Goal");
    await page.locator(`#fan .fc[data-fan="${i}"]`).click({ force: true }); await sleep(400);
    const nSel = await page.locator(`#fanact [data-slot="fan"]`).count();
    check(nSel === 3, "selecting it offers its three choices", nSel);
    await page.locator(`#fanact [data-slot="fan"][data-sloti="0"]`).selectOption({ index: 0 });
    const optTxt = await page.locator(`#fanact [data-slot="fan"][data-sloti="2"] option`).allTextContents();
    check(optTxt.some(t => /Claude's/.test(t)) && !optTxt.some(t => /^your /.test(t)), "its last choice lists only an opponent's creatures", optTxt.join("|"));
    await shot("11-common-goal");
    const f0 = (await st("Claude")).permanents.filter(p => /Creature/.test(p.type || "")).length;
    await page.locator(`#fanact [data-slot="fan"][data-sloti="1"]`).selectOption({ index: 2 });
    await page.locator(`#fanact [data-slot="fan"][data-sloti="2"]`).selectOption({ index: 0 });
    await page.locator("[data-fancast]").click(); await sleep(1000);
    check((await st("Claude")).permanents.filter(p => /Creature/.test(p.type || "")).length === f0 - 1, "pressing Cast kills it, one-sided", await page.locator("#err").textContent());
    check(!errors.length, "no page errors", errors.join("; "));
  } catch (e) { check(false, "the test ran", e.stack || e); }
  finally { if (browser) await browser.close(); srv.kill(); }
  console.log(`screenshots: ${SHOTS}`);
  console.log(failed.length ? `\n${failed.length} FAILED` : "\nall passed");
  process.exit(failed.length ? 1 : 0);
})();
